"""Run MemCal Week and prepare its native account."""
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import os
import time

from .. import run_environment
from ... import harness
from ...canary_capability import digest
from ...checkpoint_clock import CheckpointClock
from ...context_isolation import validate_recall_environment
from pacbench.corpus.sources import load_bundle
from ...gateway_client import client_settings
from ...gateway_reuse import PersistentGateway
from pacbench.corpus.prepare import read_spec
from ...provider_versions import memcal_week_pin, verify_memcal_source
from pacbench.workflows.delivery import MemCalReaders
from pacbench.workflows.native import NativeAccount, WarmHermes
from pacbench.workflows.runner import Condition
from pacbench.campaigns.pizza_week import ROOT, SPEC
from pacbench.workflows.tracks import memcal_week_tools
from .chat import MemCalChatWitness


def _source_messages():
    spec = read_spec(SPEC)
    dataset = ROOT / spec['dataset']
    manifest = json.loads((dataset/'manifest.json').read_bytes())
    bundle = load_bundle(dataset/manifest['source_bundle'], manifest['source_bundle_hash'])
    return [r for life in bundle['lives'].values() for r in life['messages']]


def _model_config(store, model, base, key):
    from memcal import config
    cfg = config.load(home=store)
    if (cfg.llm_provider != 'openai-compatible' or cfg.fallback_provider or cfg.openai_base_url != base
            or cfg.openai_api_key != key or (cfg.propose_model,cfg.sweep_model,cfg.match_model) != (model,model,model)
            or cfg.publish_calendar or cfg.publish_reminders):
        raise ValueError('Existing MemCal configuration conflicts with the scoped Luna route or enables outward writes')
    return cfg


@contextmanager
def open_account(*, root, site, scope, ledger, upstream):
    """Yield (condition, account). The site supplies release(cutoff), observe(cutoff),
    deployment(), bindings_path, bindings_sha256. Its actual Messages/IMAP consent,
    mapping, and as-of supply must already exist. The site .env is preserved. Omit
    model overrides there or set MEMCAL_LLM_PROVIDER=openai-compatible, the OpenRouter
    /api/v1 base, and the selected Luna model for propose/sweep/match. Leave the
    compatibility API key to the scoped accounting environment, with fallback and
    calendar/reminder publishing disabled. Existing Hermes homes require their original
    warm owner; this factory never recreates a controller or adopts an orphan.
    """
    from pacbench.workflows import capability as cap
    from ...native_pilot import memcal_model_settings
    from ...openrouter_route import PROFILES
    from .site import inspect_memcal_site
    from .source import MemCalSourceWitness

    root = Path(root)
    if (root.resolve() != root or root.name != 'memcal' or Path(site.memcal_home) != root/'memcal-home'
            or os.environ.get('PACBENCH_DIRECT') != '1' or json.loads(os.environ[cap.ENV]) != scope):
        raise ValueError('MemCal factory requires its run-owned existing store and scoped direct transport')
    verified = cap.load()
    if (verified['root'] != str(root) or verified['provider'] != 'memcal' or verified['account'] != site.account
            or verified['store'] != site.memcal_home or verified['site_sha256'] != digest(asdict(site))):
        raise ValueError('workflow capability differs from site account')
    for name in ('release','observe','deployment'):
        if not callable(getattr(upstream,name,None)):
            raise ValueError(f'provisioned synthetic site requires {name}')
    home = root/'hermes-home'
    if home.exists() or home.is_symlink() or (root/'reuse-clock').exists():
        raise ValueError('existing Hermes home or clock requires its original warm owner')
    validate_recall_environment()
    release = verify_memcal_source(harness.REPO)
    label, revision, version = memcal_week_pin()
    if release['revision'] != revision or verified['access']['memcal_revision'] != revision:
        raise ValueError('MemCal Week source differs from the qualified site')
    # Checks pinned import, dedicated OS home and both real readers, without fetching bodies.
    inspect_memcal_site(site)
    from memcal import db
    documents, _, frozen = cap.schedule(verified['spec_sha256'], verified['canary'])
    messages = _source_messages()
    if frozen != verified['frozen_inputs']:
        raise ValueError('scoped dataset differs from the verified v2 inputs')
    tools = memcal_week_tools(revision)
    profile = PROFILES['luna-bedrock-global']
    model = profile['model']
    roles = verified['projects']['memcal']
    driver_url, driver_key = client_settings(roles['driver'])
    memory_url, memory_key = client_settings(roles['memory'])
    # Never feed a site mailbox credential to the driver. MemCal resolves source
    # settings from the explicitly scoped local store.
    model_env = memcal_model_settings(model, memory_url, memory_key)
    stack = ExitStack()
    try:
        stack.enter_context(run_environment(model_env))
        cfg = _model_config(root/'memcal-home', model, memory_url, memory_key)
        conn = stack.enter_context(closing(db.connect(cfg.db_path)))
        reader = MemCalReaders(conn, cfg, site=site)
        source = MemCalSourceWitness(conn, site, messages, documents,
            bindings_path=upstream.bindings_path, bindings_sha256=upstream.bindings_sha256,
            release=upstream.release, observe=upstream.observe)
        fallback = root/'fallback-home'
        fallback.mkdir(mode=0o700)
        harness.provision_home(home, provider_plugin_src=Path(release['source'])/'integrations/hermes/memcal',
                               provider_name='memcal',model=model,model_provider='openrouter',model_base_url=driver_url)
        env = harness.turn_env(documents[0].cutoff,home,','.join(tools),dict(
            HOME=str(fallback), MEMCAL_HOME=str(cfg.home), MEMCAL_SRC=release['source'],
            PACBENCH_MEMCAL_CONDITION=label, PACBENCH_PROVIDER_ONLY='1', HERMES_ENABLE_PROJECT_PLUGINS='0',
            PACBENCH_WORKFLOW_CAPTURE='pizza-week-conversation-v2', PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'),
            OPENROUTER_API_KEY=driver_key, OPENROUTER_BASE_URL=driver_url, OPENROUTER_API_BASE=driver_url,
            PACBENCH_PREFETCH_PROVENANCE=json.dumps({'protocol':'native-prefetch-provenance-v1','provider':'memcal',
                'state':'unverified','reason':'Stock brief/wiki injection lacks a typed per-block source witness.'}),
            TZ='America/New_York'))
        clock = CheckpointClock(home, documents[0].cutoff, verified['spec_sha256'])
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-memcal-chat-')))
        owner = stack.enter_context(PersistentGateway(home, harness.ISOLATED_BIN.parent/'python', env, cwd=cwd,
                                    checkpoint=verified['spec_sha256'], observation_clock=clock,provider='memcal'))
        witness = MemCalChatWitness(owner,ledger,deployment=upstream.deployment)
        account = NativeAccount(site.account,'memcal',cfg.home,reader,WarmHermes(owner,clock,witness.idle),
                                witness, source_witness=source)
        deployment = 'synthetic chat.db and isolated IMAP' if site.synthetic_chatdb else f'synthetic {site.imessage_backend} and IMAP'
        service_clock = ('simulated MemCal and Hermes clocks; physical IMAP and execution clocks' if site.synthetic_chatdb
                         else 'physical connector service clock; scheduled source visibility and simulated Hermes question clock')
        condition = Condition('memcal','first-party-connector','continuing-account','Hermes '+owner.pins['hermes'],
            model,profile['endpoint'],deployment,version,owner.pins['hermes_isolation_sha256'],service_clock,tools)
        yield condition, account
    except BaseException as primary:
        try:
            stack.close()
        except BaseException as cleanup:
            primary.add_note(f'MemCal account cleanup also failed ({type(cleanup).__name__})')
        raise
    else:
        stack.close()


def check_canary(root):
    from ...canary_capability import binding, read, sha
    from ...observational_receipts import RunReceiptJournal
    from pacbench.workflows.capability import MEMCAL_SCOPE, schedule
    from pacbench.workflows.grading import retained_grade
    from pacbench.workflows.recording import receipt_summary
    from pacbench.workflows.progress import WorkflowProgress
    from pacbench.campaigns.pizza_week import SPEC

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
    from .site import inspect_memcal_site
    from .contract import verify_contract
    from pacbench.campaigns.pizza_week import compile_pizza_week

    if canary == (after_canary is not None):
        raise ValueError('choose either a fresh live canary or a verified completed canary')
    full_plan = compile_pizza_week()
    verify_contract(list(full_plan.documents), list(full_plan.turns), full_plan.source_basis)
    plan = compile_pizza_week(canary=canary) if canary else full_plan
    inspect_memcal_site(site)
    with run_environment({'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global'}):
        if after_canary is not None:
            if Path(check_canary(after_canary)).resolve() == Path(site.memcal_home).resolve():
                raise ValueError('full week requires a fresh store distinct from the canary')
            prior = json.loads((Path(after_canary)/'workflow-scope.json').read_text())
            if (prior['access'].get('source_condition') == 'synthetic-chatdb-imap-v1') != site.synthetic_chatdb:
                raise ValueError('canary and full week require the same source condition')
        return _run(Path(campaign).absolute(), site, open_account, Path(billing_confirmation), plan)


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


def _run(campaign, site, open_account, billing_confirmation, plan):
    from pacbench.workflows import capability as cap
    from ...canary_capability import binding, read
    from ...context_isolation import validate_recall_environment
    from ...gateway_client import client_settings
    from ...gateway_reuse import atomic
    from ...live_dispatch import CONDITION
    from ...model_calls import ModelClient
    from ...observational_receipts import credit_snapshot
    from ...openrouter_direct import direct_environment
    from ...openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from ...openrouter_run import fetch_catalog
    from pacbench.workflows.recording import receipt_summary
    from pacbench.workflows.recording import coverage, export_verified_metrics
    from pacbench.workflows.native import NativeAccount
    from pacbench.workflows.progress import WorkflowProgress
    from pacbench.workflows.runner import run_compiled_workflow

    canary = plan.canary

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
                        run_compiled_workflow(plan, condition, account, root/'workflow', judge=judge)
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
    from .site import MemCalSite
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
        return open_account(upstream=import_module(args.deployment), **kwargs)

    result = run_live(args.campaign, site, connect, args.billing_confirmation,
                      canary=args.canary, after_canary=args.after_canary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
