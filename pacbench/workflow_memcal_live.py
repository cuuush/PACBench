"""Run a first-party MemCal arm on an explicitly provisioned synthetic site."""
import argparse
from contextlib import contextmanager
from importlib import import_module
import json
import os
from pathlib import Path
import time


@contextmanager
def run_environment(settings):
    previous = {key:os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_canary(root):
    from .canary_capability import binding, read, sha
    from .observational_receipts import RunReceiptJournal
    from .workflow_capability import MEMCAL_SCOPE, schedule
    from .workflow_grading import retained_grade
    from .workflow_receipts import receipt_summary
    from .workflow_progress import WorkflowProgress
    from .workflow_spec import SPEC

    root = Path(root).resolve(strict=True)
    scope_binding = binding(root/'workflow-scope.json')
    scope = read(scope_binding)
    meta = json.loads((root/'workflow/run.json').read_text())
    result = json.loads((root/'result.json').read_text())
    if (root.name != 'memcal' or scope['provider'] != 'memcal' or scope['root'] != str(root)
            or scope['canary'] is not True or scope.get('admission') != MEMCAL_SCOPE or scope['spec_sha256'] != sha(SPEC)
            or result.get('scope') != scope_binding or result.get('canary') is not True
            or result.get('state') != 'live-canary-complete' or result.get('cleanup') != 'complete'):
        raise ValueError('a completed native MemCal canary on this frozen schedule is required')
    docs, turns, source_basis = schedule(sha(SPEC), True)
    with WorkflowProgress(root/'workflow', meta['run_key']) as progress:
        progress.audit_artifacts()
        identity = progress.binding('identity')
        activation = progress.binding('memcal_activation') or {}
        if (identity != meta['identity'] or not progress.execution_sealed or identity['canary'] is not True
                or identity['condition']['track'] != 'first-party-connector' or identity['evidence_kind'] != 'native'
                or identity['source_basis'] != source_basis or progress.binding('workflow_scope') != scope_binding
                or activation.get('scope') != scope_binding or not progress.binding('memcal_platform_capture')
                or activation.get('store') != identity['store'] or scope['frozen_inputs'] != source_basis
                or progress.last_cutoff() != docs[-1].cutoff):
            raise ValueError('canary scope, source, or native execution seal differs')
        saved = list(progress.iter_verified())
        if [t for t, _, _ in saved] != list(turns) or any(
                activity['driver_receipts']['coverage'] != 'complete' for _, _, activity in saved):
            raise ValueError('canary needs four native turns with complete driver lineage')
        for turn, surface, _ in saved:
            if turn.claims:
                grade = retained_grade(turn, surface, root/'workflow')
                if [vars(item) for item in grade.slots] != progress.grade_record(turn.id)['criteria']:
                    raise ValueError('canary criterion verdict differs from the original judge receipt')
        if sum(r['graded_criteria'] for r in progress.summary().values()) != sum(len(t.claims) for t in turns):
            raise ValueError('canary grades are incomplete')
    rows = RunReceiptJournal(root/'receipts.jsonl').snapshot()
    roles = {row['accounting_role'] for row in rows if row.get('upstream_request_id')}
    if (roles != {'hermes','memory_provider','judge'} or any(
            (row.get('upstream_request_id') or '').startswith('offline-native-') for row in rows)
            or not result['billing'] or result['billing'].get('complete_cost_usd') is None
            or receipt_summary(root, rows, scope['run_id']) != result['billing']):
        raise ValueError('canary transport receipts are inconsistent')
    return identity['store']


def run_live(campaign, site, open_account, billing_confirmation, *, canary=False, after_canary=None):
    """open_account owns site-specific upstream releases, a warm Hermes and native cleanup.

    It is a context manager factory called with root, site, scope and ledger. It
    yields (Condition, NativeAccount); the controller performs the qualification.
    """
    from .workflow_memcal import inspect_memcal_site
    from .workflow_memcal_contract import verify_contract
    from .workflow_spec import compile_week

    if canary == (after_canary is not None):
        raise ValueError('choose either a fresh live canary or a verified completed canary')
    _, docs, turns, frozen = compile_week('continuing-account')
    verify_contract(docs, turns, frozen)
    inspect_memcal_site(site)
    with run_environment({'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global'}):
        if after_canary is not None:
            if Path(check_canary(after_canary)).resolve() == Path(site.memcal_home).resolve():
                raise ValueError('full week requires a fresh store distinct from the canary')
            prior = json.loads((Path(after_canary)/'workflow-scope.json').read_text())
            if (prior['access'].get('source_condition') == 'synthetic-chatdb-imap-v1') != site.synthetic_chatdb:
                raise ValueError('canary and full week require the same source condition')
        return _run(Path(campaign).absolute(), site, open_account, Path(billing_confirmation), canary)


def claim_prepared_arm(campaign, site):
    """Claim an already provisioned synthetic store without modifying its source data."""
    root = campaign/'memcal'
    store = root/'memcal-home'
    siblings = {root}
    if getattr(site, 'synthetic_chatdb', False):
        siblings.update(campaign/name for name in ('source-home','private-fixtures','empty-mailbox.json',
                                                   'source-bindings.json','synthetic-source-provenance.json'))
    if (campaign != campaign.resolve() or not campaign.is_dir() or root != root.resolve()
            or Path(site.memcal_home) != store or store != store.resolve() or not store.is_dir()
            or set(campaign.iterdir()) != siblings or set(root.iterdir()) != {store}):
        raise ValueError('expected a fresh campaign containing only memcal/memcal-home')
    # The exclusive marker arbitrates two launchers observing the same pristine site.
    with (root/'launcher.claim').open('x') as stream:
        stream.write('memcal-pizza-week-v2\n')
        stream.flush()
        os.fsync(stream.fileno())
    return root


def _run(campaign, site, open_account, billing_confirmation, canary):
    from . import workflow_capability as cap
    from .canary_capability import binding, read
    from .context_isolation import validate_recall_environment
    from .gateway_client import client_settings
    from .gateway_reuse import atomic
    from .live_dispatch import CONDITION
    from .model_calls import ModelClient
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .workflow_receipts import receipt_summary
    from .workflow_metrics import coverage, export_verified_metrics
    from .workflow_native import NativeAccount
    from .workflow_progress import WorkflowProgress
    from .workflow_runner import run_current_week

    if (PROFILE_NAME != cap.PROFILE or campaign != campaign.resolve() or any(os.environ.get(name) for name in
            (cap.ENV, 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_R2_LIVE_SCOPE', 'PACBENCH_CHECKPOINT_FULL_RUN',
             'PACBENCH_CHECKPOINT_CANARY', 'PACBENCH_REMAINING_R2'))):
        raise ValueError('a fresh, isolated Luna run process is required')
    confirmation = read(binding(billing_confirmation))
    cap.confirm_setting(confirmation)
    validate_recall_environment()
    root = claim_prepared_arm(campaign, site)
    started = time.monotonic()
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'), PACBENCH_DISPATCH_TRACKING=CONDITION,
                    PACBENCH_ROLE_ROUTES='luna-all-roles-v1', PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',
                    PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768', PACBENCH_STOP_FILE=str(root/'STOP'),
                    PACBENCH_OBSERVATIONAL_COHORT_STOP=str(cap.campaign_stop(root)),
                    PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'), PACBENCH_IMPORT_ROOT=str(root))
    with run_environment(settings):
        atomic(root/'verified-setting.json', confirmation)
        route, inventory = fetch_catalog(profile=cap.PROFILE)
        if 'temperature' in inventory['supported_parameters']:
            raise ValueError('Luna route signature changed')
        atomic(root/'catalog.json', {'route':route, 'supported_parameters':inventory['supported_parameters']})
        scope = cap.create_memcal_scope(root, site, canary=canary, prepaid=credit_snapshot(),
                                        auto_recharge_evidence=binding(root/'verified-setting.json'))
        with run_environment({cap.ENV:json.dumps(scope)}):
            ledger = cap.budget()
            result = {'state':'incomplete', 'cleanup':'unverified', 'billing':None, 'scope':scope, 'canary':canary}
            failure = None
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root=root, site=site, scope=scope, ledger=ledger) as (condition, account):
                        if (type(account) is not NativeAccount or account.provider != 'memcal' or
                                condition.provider != 'memcal' or condition.track != 'first-party-connector' or
                                condition.state != 'continuing-account' or set(condition.channels) != {'email','imessage'}):
                            raise ValueError('deployment did not bind the reviewed mixed MemCal account')
                        _, key = client_settings(cap.load()['projects']['memcal']['judge'])
                        judge = ModelClient(key=key, model=MODEL, record_dir=root/'workflow/model-calls',
                                            url=CHAT_COMPLETIONS_URL, json_mode=True, max_attempts=1)
                        run_current_week(condition, account, root/'workflow', judge=judge, canary=canary)
                    result['cleanup'] = 'complete'
            except BaseException as error:
                failure = error
                result['error'] = type(error).__name__
                raise
            finally:
                try:
                    result['elapsed_s'] = time.monotonic() - started
                    result['billing'] = receipt_summary(root, ledger.snapshot(), campaign.name)
                    if (root/'workflow/run.json').is_file():
                        meta = json.loads((root/'workflow/run.json').read_text())
                        with WorkflowProgress(root/'workflow', meta['run_key']) as progress:
                            result['by_measure'] = progress.summary()
                            result['coverage'] = coverage(result['by_measure'], complete=progress.complete)
                            result['grade_states'] = progress.grade_states()
                            result['turn_metrics'] = export_verified_metrics(progress, root/'turn-metrics.jsonl')
                            result['full_score_available'] = progress.complete
                            if failure is None and progress.execution_sealed:
                                all_graded = all(r['graded_criteria'] == r['planned_criteria'] for r in result['by_measure'].values())
                                if canary and all_graded:
                                    result['state'] = 'live-canary-complete'
                                elif progress.complete:
                                    result['state'] = 'live-week-complete'
                except Exception as report_error:
                    result['report_error'] = type(report_error).__name__
                    if failure is not None:
                        failure.add_note(f'Additional reporting failure: {type(report_error).__name__}')
                    else:
                        raise
                finally:
                    try:
                        atomic(root/'result.json', result)
                    except Exception as write_error:
                        if failure is None:
                            raise
                        failure.add_note(f'Could not write result.json: {type(write_error).__name__}')
    return result


def main(argv=None):
    os.environ.setdefault('PACBENCH_OPENROUTER_PROFILE','luna-bedrock-global')
    from .workflow_memcal import MemCalSite
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--deployment', required=True, help='operator-installed synthetic upstream release and observation module')
    parser.add_argument('--billing-confirmation', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--canary', action='store_true')
    mode.add_argument('--after-canary', type=Path)
    args = parser.parse_args(argv)
    site = MemCalSite(**json.loads(args.site.read_text()))

    def connect(**kwargs):
        from .workflow_memcal_account import open_account
        return open_account(upstream=import_module(args.deployment), **kwargs)

    result = run_live(args.campaign, site, connect, args.billing_confirmation,
                      canary=args.canary, after_canary=args.after_canary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
