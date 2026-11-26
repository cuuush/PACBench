"""Claim a fresh document run and record its terminal evidence."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time


def claim_run(root, profile):
    """Run read-only preflight before exclusively claiming the path."""
    from .observational_receipts import check_cohort_stop
    from .openrouter_run import fetch_catalog

    if os.path.lexists(root):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), root)
    check_cohort_stop()
    fetch_catalog(profile=profile)
    check_cohort_stop()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)


def run_document_week(root, confirmation, *, provider, canary, after_canary, check_canary,
                      open_account, fresh_account_error, extra_environment=None, cleanup_after_direct=False):
    """Run a supported document provider in its own bound Week account."""
    from . import workflow_capability as cap
    from .canary_capability import binding, read
    from .context_isolation import validate_recall_environment
    from .gateway_client import client_settings
    from .live_dispatch import CONDITION
    from .model_calls import ModelClient
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .workflow_memcal_contract import verify_contract
    from .workflow_memcal_live import run_environment
    from .workflow_runner import run_current_week
    from .workflow_spec import compile_week

    root = Path(root).absolute()
    if (root.name != provider or root != root.resolve() or PROFILE_NAME != cap.PROFILE or os.environ.get(cap.ENV)
            or canary == (after_canary is not None)):
        raise ValueError(fresh_account_error)
    qualification = check_canary(after_canary) if after_canary is not None else None
    auth = binding(confirmation)
    cap.confirm_setting(read(auth))
    validate_recall_environment()
    _, documents, turns, frozen = compile_week('continuing-account')
    verify_contract(documents, turns, frozen)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'), PACBENCH_DISPATCH_TRACKING=CONDITION,
                    PACBENCH_ROLE_ROUTES='luna-all-roles-v1', PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',
                    PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768', PACBENCH_STOP_FILE=str(root/'STOP'),
                    PACBENCH_OBSERVATIONAL_COHORT_STOP=str(cap.campaign_stop(root, nested_run=True)),
                    PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'), PACBENCH_IMPORT_ROOT=str(root))
    if extra_environment is not None:
        settings.update(extra_environment)
    started = time.monotonic()
    with run_environment(settings):
        claim_run(root, PROFILE_NAME)
        bound = cap.create(root, provider, canary=canary, prepaid=credit_snapshot(),
                           auto_recharge_evidence=auth, qualification=qualification)
        with run_environment({cap.ENV: json.dumps(bound)}):
            scope = cap.load()
            ledger = cap.budget()
            with terminal_result(root, ledger, scope['run_id'], canary, started, provider=provider) as result:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root, scope, ledger) as (condition, account):
                        _, key = client_settings(scope['projects'][provider]['judge'])
                        judge = ModelClient(key=key, model=MODEL, record_dir=root/'workflow/model-calls',
                                            url=CHAT_COMPLETIONS_URL, json_mode=True, max_attempts=1)
                        run_current_week(condition, account, root/'workflow', judge=judge, canary=canary)
                    if not cleanup_after_direct:
                        result['cleanup'] = 'complete'
                if cleanup_after_direct:
                    result['cleanup'] = 'complete'
    return result


@contextmanager
def terminal_result(root, ledger, run_id, canary, started, *, provider):
    """Reconcile the closed account once and retain any partial evidence."""
    from .gateway_reuse import atomic
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary

    result = {'provider':provider, 'canary':canary, 'state':'incomplete', 'cleanup':'unverified',
              'workflow':None, 'billing':None}
    failure = None
    try:
        yield result
    except BaseException as error:
        failure = error
        result['error'] = type(error).__name__
        raise
    finally:
        report_failure = None

        def record_report_failure(error):
            nonlocal report_failure
            if report_failure is None:
                report_failure = error
                result['report_error'] = type(error).__name__
            primary = failure or report_failure
            if primary is not error:
                primary.add_note(f'Additional reporting failure: {type(error).__name__}')

        try:
            result['workflow'] = summarize(root/'workflow/workflow.jsonl')
        except BaseException as error:
            record_report_failure(error)
        try:
            rows = ledger.snapshot()
            result['billing'] = receipt_summary(root, rows, run_id)
        except BaseException as error:
            record_report_failure(error)

        if failure is None and report_failure is None:
            try:
                report = result['workflow']
                if report is None:
                    raise RuntimeError('Workflow report missing after provider execution')
                coverage = report['coverage']
                roles = {row['accounting_role'] for row in rows if row.get('upstream_request_id')}
                if (coverage['verified_turns'] == coverage['planned_turns']
                        and coverage['graded_criteria'] == coverage['planned_criteria']
                        and all(t['driver_attempt_coverage']['coverage'] == 'complete' for t in report['turns'])
                        and roles == {'hermes','judge','memory_provider'}
                        and (not canary or result['billing']['complete_cost_usd'] is not None)):
                    result['state'] = 'live-canary-complete' if canary else 'live-week-complete'
            except BaseException as error:
                record_report_failure(error)

        if failure is None and report_failure is not None:
            result['error'] = type(report_failure).__name__
        result['elapsed_s'] = time.monotonic() - started
        try:
            atomic(root/'result.json', result)
            # atomic() syncs the file before rename. Persist the directory entry too.
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException as error:
            primary = failure or report_failure
            if primary is None:
                raise
            primary.add_note(f'Could not write result.json: {type(error).__name__}')
        if failure is None and report_failure is not None:
            raise report_failure
