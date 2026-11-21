"""Native Mem0 OSS readback for the continuing-account document workflow."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity
from .workflow_tracks import HERMES_TOOLS, validate_native_deployment


# Verified against the installed 2.2.1 SQLiteManager.save_messages.
HISTORY_WINDOW = 10


def append_history(previous, current, expected):
    """Match newly persisted native IDs and the remainder of the rolling suffix."""
    count = len(expected)
    new = current[-count:]
    if ([(role, text) for _,role,text in new] != expected
            or len({row[0] for row in (*previous, *new)}) != len(previous)+count
            or current != [*previous, *new][-HISTORY_WINDOW:]):
        raise RuntimeError('Mem0 native history differs from the expected next write')
    return new


class Mem0Documents:
    """Use the plugin's shipped SDK configuration, recording native extraction."""

    def __init__(self, config):
        self.config = config
        self.backend = None
        self.calls = []

    def open(self):
        from copy import deepcopy
        from .provider_loader import provider_module

        config = deepcopy(self.config)
        config['llm']['config']['response_callback'] = self.record_call
        self.backend = provider_module('mem0', '_backend').OSSBackend(config)
        return self

    def record_call(self, llm, response, request):
        self.calls.append((request, response.model_dump(mode='json')))

    def add(self, text, **kwargs):
        from provider_adapters.document_connector import ExtractionLog

        self.calls.clear()
        errors = ExtractionLog()
        logger = logging.getLogger('mem0.memory.main')
        old_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(errors)
        try:
            results = self.backend._memory.add(text, **kwargs)['results']
        finally:
            logger.removeHandler(errors)
            logger.setLevel(old_level)
        if len(self.calls) != 1 or errors.errors:
            raise RuntimeError('Mem0 extraction did not complete cleanly')
        request, response = self.calls[0]
        if (not any(text in item['content'] for item in request['messages']
                    if item['role'] == 'user' and isinstance(item.get('content'), str))
                or not response['choices'] or any(c['finish_reason'] != 'stop' for c in response['choices'])):
            raise RuntimeError('Mem0 did not process the exact released document to completion')
        return dict(sha256=hashlib.sha256(text.encode()).hexdigest(), native_results=results,
                    response_id=response['id'])

    def close(self):
        self.backend.close()


class Mem0Witness:
    def __init__(self, root, user, agent, documents, owner, transport, ledger):
        from mem0.memory.main import _build_session_scope

        self.root, self.user, self.owner = root, user, owner
        self.transport, self.ledger = transport, ledger
        self.import_scope = _build_session_scope({'user_id':user})
        self.chat_scope = _build_session_scope({'user_id':user, 'agent_id':agent})
        self.history = root/'mem0-state'/'history.db'
        actual_history = self.transport.backend._memory.db.connection.execute('PRAGMA database_list').fetchone()[2]
        if Path(actual_history).resolve() != self.history:
            raise RuntimeError('Mem0 SQLite history differs from the shared run-owned native path')
        self.cutoff = None
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in documents))
        self.imported = []
        self.source_history = []
        self.chat_history = []

    def rows(self, scope):
        with closing(sqlite3.connect(self.history.as_uri()+'?mode=ro', uri=True)) as connection:
            return list(connection.execute(
                'SELECT id,role,content FROM messages WHERE session_scope=? ORDER BY rowid', (scope,)))

    def verify_deployment(self, condition, identity):
        from importlib.metadata import version
        from .catalog_plugins import verify_retained_catalog_provider

        verify_retained_catalog_provider('mem0', self.owner.home)
        if self.owner.provider != 'mem0' or identity != str(self.root) or version('mem0ai') != '2.2.1':
            raise ValueError('unverified stock Mem0 OSS account')
        result = dict(account=identity, condition=vars(condition), store_identity=str(self.history.parent),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='OSS 2.2.1', frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'], effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['mem0']), native_processes={
                'driver':process_identity(self.owner.witness['pid']), 'memory':process_identity()})
        validate_native_deployment(condition, result)
        return result

    def control_readback(self):
        native = self.transport.backend._memory.get_all(filters={'user_id':self.user})['results']
        chat = self.rows(self.chat_scope)
        if native or chat or self.rows(self.import_scope):
            raise RuntimeError('Mem0 account or collection is not empty')
        return dict(account=str(self.root), chat_count=0, conversation_writes=0, source_files=[],
                    readback={'native_vector_records':len(native), 'native_history_rows':len(chat)})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index >= len(self.cutoffs) or cutoff != self.cutoffs[index]:
            raise ValueError('Mem0 releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff, as_of=cutoff, service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        if doc.cutoff != self.cutoff or raw['sha256'] != doc.sha256:
            raise ValueError('Mem0 accepted a different source or release')
        return dict(native_id=doc.filename, accepted=True, at=doc.cutoff,
                    request={'interface':'Memory.add(infer=True)', **raw})

    def document_readback(self, doc, acceptance):
        generation = acceptance['request']['response_id']
        candidates = [row for row in self.ledger.snapshot() if row['accounting_role']=='memory_provider'
                      and row['dispatch_context']['phase']=='ingest' and row['workflow_slot']['batch_id']==doc.filename
                      and row.get('http_status')==200]
        if len(candidates)!=1 or candidates[0].get('upstream_request_id') not in (None,generation):
            raise RuntimeError('Mem0 extraction response does not belong to the released document attempt')
        attempt, = candidates
        rows = self.rows(self.import_scope)
        results = acceptance['request']['native_results']
        stored = []
        for result in results:
            memory = self.transport.backend._memory.get(result['id'])
            if memory is None or memory.get('memory') != result.get('memory'):
                raise RuntimeError('Mem0 extracted record was not visible in the native vector store')
            stored.append(result['id'])
        new, = append_history(self.source_history, rows, [('user', doc.text)])
        self.source_history = rows
        self.imported.append(doc.filename)
        return dict(native_id=doc.filename, ready=True, at=doc.cutoff, submitted_sha256=doc.sha256,
                    readback={'native_input_id':new[0], 'native_vector_ids':stored,
                              'native_source_history_window':len(rows),
                              'extraction_attempt_id':attempt['id'],
                              'billing_state':attempt['state'],
                              'service_at':datetime.now(timezone.utc).isoformat()})

    def source_inventory(self):
        if self.rows(self.import_scope) != self.source_history:
            raise RuntimeError('Mem0 native source history changed outside its verified release')
        return list(self.imported)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic() + 60
        expected = self.owner.witness
        identity = [expected['pid'], expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic() < deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('Mem0 gateway observer failed')
            if observer.exists():
                sample = json.loads(observer.read_bytes())
                if ([sample['pid'], sample['start_time']] != identity or sample['protocol'] != PROTOCOL
                        or sample['observer_sha256'] != self.owner.pins['reuse_observer_sha256']
                        or sample['native_failures']):
                    raise RuntimeError('Mem0 native lifecycle observation failed')
                providers = [p for row in sample['sessions'] for p in row['providers']]
                if any('error_type' in p or p['name'] not in ('mem0','builtin')
                       or (p['name'] == 'mem0' and type(p.get('workers_alive')) is not int) for p in providers):
                    raise RuntimeError('incomplete Mem0 native provider observation')
                pending = (sample['native_release_threads'] or any(row['pending_futures'] for row in sample['sessions'])
                           or any(p['workers_alive'] for p in providers if p['name'] == 'mem0'))
                if not pending and sample['observed_monotonic_ns'] >= since:
                    active = any(not row.get('finished') and row['accounting_role'] != 'judge'
                                 for row in self.ledger.snapshot())
                    if not active:
                        return dict(gateway_identity=identity, inflight_requests=0, pending_native_writes=0,
                                    native_errors=[], readback=sample)
            time.sleep(.05)
        raise RuntimeError('Mem0 native open sessions did not become idle')

    def native_write_barrier(self, session, answer):
        from .provider_loader import provider_module

        sample = self.idle()['readback']
        managers = [r for r in sample['sessions'] if r['session_id'] == session and not r['shutdown_returned']]
        if not any(p['name'] == 'mem0' and p['backend_attached'] for r in managers for p in r['providers']):
            raise RuntimeError('Mem0 did not observe the live Hermes session')
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
            prompt, = connection.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (session,)).fetchone()
        truncate = provider_module('mem0')._truncate_for_sync
        limit = json.loads((self.owner.home/'mem0.json').read_text())['sync_max_chars']
        expected = [('user', truncate(prompt, limit)), ('assistant', truncate(answer, limit))]
        rows = self.rows(self.chat_scope)
        new = append_history(self.chat_history, rows, expected)
        self.chat_history = rows
        return dict(session_id=session, pending_writes=0, readback={'native_history_ids':[r[0] for r in new]})

    def after_native_close(self, session):
        deadline = time.monotonic()+60
        path = self.owner.home/'state.db'
        while time.monotonic() < deadline:
            sample = self.idle()['readback']
            managers = [r for r in sample['sessions'] if r['session_id'] == session]
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
                end = connection.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(session,)).fetchone()
            if end and end[0] is not None and end[1]=='cli_close' and managers and all(
                    row['shutdown_returned'] and row['clean'] for row in managers):
                if self.rows(self.chat_scope) != self.chat_history:
                    raise RuntimeError('Mem0 history has an unwitnessed write during native close')
                return dict(session_id=session, pending_writes=0,
                            readback={'closed_managers':len(managers),'observer_sample_ns':sample['observed_monotonic_ns']})
            time.sleep(.05)
        raise RuntimeError('Mem0 did not close the selected native session')

    def tool_boundaries(self, results):
        # Mem0 does not expose source-document reads. Native tool output is
        # retained but SDK-to-wire equivalence is not inferred.
        return {}

    def request_lineage(self, requests):
        selected = {request['request_id'] for request in requests}
        rows = self.ledger.snapshot()
        for row in reversed(rows):
            if row['id'] in selected and row.get('retry_of'):
                selected.add(row['retry_of'])
        return [dict(id=row['id'], parent=row.get('retry_of'), operation=row['dispatch_context']['operation_id'],
                     origin='harness' if row.get('retry_of') else 'unknown') for row in rows if row['id'] in selected]

    def close(self):
        pass


@contextmanager
def open_account(root, endpoint, scope, ledger):
    from tempfile import TemporaryDirectory
    from urllib.parse import urlsplit

    from . import harness, workflow_capability as cap
    from .checkpoint_clock import CheckpointClock
    from .gateway_reuse import PersistentGateway, atomic
    from .native_pilot import probe, provision
    from .openrouter_route import ENDPOINT, MODEL
    from .workflow_memcal_live import run_environment
    from .workflow_native import NativeAccount, WarmHermes
    from .workflow_runner import Condition
    from provider_adapters.embedding_health import check
    from qdrant_client import QdrantClient

    uri = urlsplit(endpoint)
    if uri.scheme != 'http' or uri.hostname != '127.0.0.1' or not uri.port or endpoint != f'http://127.0.0.1:{uri.port}':
        raise ValueError('Mem0 requires a dedicated local Qdrant service')
    with closing(QdrantClient(url=endpoint)) as vector:
        qdrant = vector.info()
        if qdrant.version != '1.20.0' or vector.get_collections().collections:
            raise ValueError('expected a fresh pinned local Qdrant service')
        atomic(root/'vector-service.json', dict(version=qdrant.version, commit=qdrant.commit, driver='qdrant-client 1.20.0'))
    atomic(root/'embedding-health.json', check('http://127.0.0.1:11435/v1'))
    env = provision(root, 'mem0', model=MODEL, local_timezone='America/New_York', inference_route='openrouter')
    home = root/'hermes-home'
    config_path = home/'mem0.json'
    config = json.loads(config_path.read_text())
    config['oss']['vector_store']['config'].update(host=uri.hostname, port=uri.port,
        collection_name='pacbench_'+hashlib.sha256(str(root).encode()).hexdigest()[:20])
    config['oss']['vector_store']['config'].pop('path', None)
    atomic(config_path, config)
    ready = probe(root, 'mem0', env)
    atomic(root/'provider-probe.json', ready)
    if not ready['available']:
        raise RuntimeError('Mem0 native plugin preflight did not pass')
    store = root/'mem0-state'
    store.mkdir(mode=0o700, exist_ok=True)
    (root/'mem0-observation').symlink_to(store, target_is_directory=True)
    documents, _, _ = cap.schedule(scope['spec_sha256'], scope['canary'])
    tools = HERMES_TOOLS['mem0']
    env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'], PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'))
    child = harness.turn_env(documents[0].cutoff, home, ','.join(tools), env)
    clock = CheckpointClock(home, documents[0].cutoff, scope['spec_sha256'])
    importer_env = {key:child[key] for key in ('HERMES_HOME','HOME','MEM0_DIR','MEM0_TELEMETRY')}
    with run_environment(importer_env), TemporaryDirectory(prefix='pacbench-mem0-chat-') as cwd:
        with closing(Mem0Documents(config['oss']).open()) as transport, PersistentGateway(
                home, harness.ISOLATED_BIN.parent/'python', child, cwd=Path(cwd),
                checkpoint=scope['spec_sha256'], observation_clock=clock, provider='mem0') as owner:
            condition = Condition('mem0','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
                MODEL, ENDPOINT, 'Mem0 OSS with isolated loopback Qdrant', 'OSS 2.2.1',
                owner.pins['hermes_isolation_sha256'], 'Physical Mem0 persistence time and simulated Hermes question time', tools)
            witness = Mem0Witness(root, config['user_id'], config.get('agent_id','hermes'),
                                  documents, owner, transport, ledger)
            frontend = WarmHermes(owner, clock, witness.idle)
            yield condition, NativeAccount(str(root),'mem0',config['user_id'],transport,frontend,witness)


def check_canary(root):
    """Re-audit the saved canary without opening the retired provider account."""
    from .canary_capability import binding, read, sha
    from .observational_receipts import RunReceiptJournal
    from .observation_adapters import MEM0_PLUGIN_SHA256
    from .openrouter_route import PROFILES
    from .workflow_capability import schedule
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary
    from .workflow_spec import SPEC

    root = Path(root).resolve(strict=True)
    result_binding = binding(root/'result.json')
    scope_binding = binding(root/'workflow-scope.json')
    result = read(result_binding)
    scope = read(scope_binding)
    documents, turns, frozen = schedule(sha(SPEC), True)
    journal = root/'workflow/workflow.jsonl'
    summary = summarize(journal)
    with journal.open() as stream:
        events = [json.loads(line) for line in stream]
    receipts = RunReceiptJournal(root/'receipts.jsonl').snapshot()
    matched = receipt_summary(root,receipts,scope['run_id'])
    actual_sources = [e['evidence'] for e in events if e['phase']=='readback']
    actual_turns = [e['question'] for e in events if e['phase']=='verified-turn']
    expected_sources = [(doc.filename,doc.sha256) for doc in documents]
    roles = {row['accounting_role'] for row in receipts if row.get('upstream_request_id')}
    coverage = summary['coverage']
    condition = events[1]['condition']
    deployment = events[1]['deployment_witness']
    profile = PROFILES['luna-bedrock-global']
    installed = root/'hermes-home/plugins/mem0'
    plugin_files = {p.name:sha(p) for p in sorted(installed.glob('*.py'))}
    installed_sha = hashlib.sha256(json.dumps(plugin_files,sort_keys=True).encode()).hexdigest()
    harness_root = Path(__file__).resolve().parents[1]
    hermes_revision = next(line.split('=',1)[1] for line in (harness_root/'vendor/HERMES_PIN').read_text().splitlines()
                           if line.startswith('HERMES_PIN='))
    if (condition['provider']!='mem0' or condition['version']!='OSS 2.2.1'
            or condition['track']!='nightly-document' or condition['state']!='continuing-account'
            or condition['frontend']!='Hermes '+hermes_revision
            or condition['gateway_patch']!=sha(harness_root/'patches/hermes-isolated.diff')
            or condition['model']!=profile['model'] or condition['model_route']!=profile['endpoint']
            or condition['tool_names']!=list(HERMES_TOOLS['mem0']) or deployment['account']!=str(root)
            or installed_sha!=MEM0_PLUGIN_SHA256):
        raise ValueError('Mem0 canary static runtime and route differ from the Week condition')
    if (root.name!='mem0' or scope['root']!=str(root) or scope['provider']!='mem0' or scope['canary'] is not True
            or scope.get('admission')!='mem0-native-oss-document-week-v1'
            or scope['spec_sha256']!=sha(SPEC) or scope['frozen_inputs']!=frozen
            or result['state']!='live-canary-complete' or result['cleanup']!='complete'
            or result['canary'] is not True or summary!=result['workflow'] or matched!=result['billing']
            or events[-1]['phase']!='completion'
            or coverage['planned_turns']!=4 or coverage['verified_turns']!=4
            or coverage['planned_criteria']!=12 or coverage['graded_criteria']!=12
            or roles != {'hermes','judge','memory_provider'} or matched['complete_cost_usd'] is None
            or actual_turns!=[t.id for t in turns] or [(a['native_id'],a['submitted_sha256']) for a in actual_sources]!=expected_sources
            or any(not row['ready'] for row in actual_sources)
            or any(t['driver_attempt_coverage']['coverage']!='complete' for t in summary['turns'])):
        raise ValueError('Mem0 canary does not establish a complete native and receipted qualification')
    return {'result':result_binding,'scope':scope_binding}


@contextmanager
def terminal_result(root, ledger, run_id, canary, started):
    """Reconcile the closed account once and persist even a partial report."""
    from .gateway_reuse import atomic
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary

    result = {'provider':'mem0', 'canary':canary, 'state':'incomplete', 'cleanup':'unverified',
              'workflow':None, 'billing':None}
    failure = None
    try:
        yield result
    except BaseException as error:
        failure = error
        result['error'] = type(error).__name__
        raise
    finally:
        try:
            report = summarize(root/'workflow/workflow.jsonl')
            result['workflow'] = report
            rows = ledger.snapshot()
            billing = receipt_summary(root, rows, run_id)
            result['billing'] = billing
            if failure is None:
                coverage = report['coverage']
                roles = {row['accounting_role'] for row in rows if row.get('upstream_request_id')}
                if (coverage['verified_turns'] == coverage['planned_turns']
                        and coverage['graded_criteria'] == coverage['planned_criteria']
                        and all(t['driver_attempt_coverage']['coverage'] == 'complete' for t in report['turns'])
                        and roles == {'hermes','judge','memory_provider'}
                        and (not canary or billing['complete_cost_usd'] is not None)):
                    result['state'] = 'live-canary-complete' if canary else 'live-week-complete'
        except Exception as error:
            result['report_error'] = type(error).__name__
            if failure is None:
                result['error'] = type(error).__name__
                failure = error
                raise
            failure.add_note(f'Additional reporting failure: {type(error).__name__}')
        finally:
            result['elapsed_s'] = time.monotonic() - started
            try:
                atomic(root/'result.json', result)
                # atomic() syncs the file before rename. Persist the directory entry too.
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except Exception as error:
                if failure is None:
                    raise
                failure.add_note(f'Could not write result.json: {type(error).__name__}')


def run(root, endpoint, confirmation, *, canary, after_canary=None):
    from . import workflow_capability as cap
    from .canary_capability import binding, read
    from .context_isolation import validate_recall_environment
    from .gateway_client import client_settings
    from .live_dispatch import CONDITION
    from .model_calls import ModelClient
    from .observational_receipts import check_cohort_stop, credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .workflow_memcal_contract import verify_contract
    from .workflow_memcal_live import run_environment
    from .workflow_runner import run_current_week
    from .workflow_spec import compile_week

    root = Path(root).absolute()
    if (root.name != 'mem0' or root != root.resolve() or PROFILE_NAME != cap.PROFILE or os.environ.get(cap.ENV)
            or canary == (after_canary is not None)):
        raise ValueError('fresh dedicated Mem0 Luna campaign required')
    qualified = check_canary(after_canary) if after_canary is not None else None
    auth = binding(confirmation)
    cap.confirm_setting(read(auth))
    validate_recall_environment()
    _, documents, turns, frozen = compile_week('continuing-account')
    verify_contract(documents, turns, frozen)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'), PACBENCH_DISPATCH_TRACKING=CONDITION,
                    PACBENCH_ROLE_ROUTES='luna-all-roles-v1', PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',
                    PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768', PACBENCH_STOP_FILE=str(root/'STOP'),
                    PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent.parent/'ACCOUNT_STOP'),
                    PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'), PACBENCH_IMPORT_ROOT=str(root))
    start = time.monotonic()
    with run_environment(settings):
        check_cohort_stop()
        fetch_catalog(profile=cap.PROFILE)
        bound = cap.create(root, 'mem0', canary=canary, prepaid=credit_snapshot(),
                           auto_recharge_evidence=auth, qualification=qualified)
        with run_environment({cap.ENV:json.dumps(bound)}):
            scope = cap.load()
            ledger = cap.budget()
            with terminal_result(root, ledger, scope['run_id'], canary, start) as result:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root, endpoint, scope, ledger) as (condition, account):
                        _, key = client_settings(scope['projects']['mem0']['judge'])
                        judge = ModelClient(key=key,model=MODEL,record_dir=root/'workflow/model-calls',
                                            url=CHAT_COMPLETIONS_URL,json_mode=True,max_attempts=1)
                        run_current_week(condition,account,root/'workflow',judge=judge,canary=canary)
                    result['cleanup'] = 'complete'
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--qdrant',required=True)
    parser.add_argument('--billing-confirmation',type=Path,required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--canary',action='store_true')
    action.add_argument('--after-canary',type=Path)
    args = parser.parse_args()
    run(args.root, args.qdrant, args.billing_confirmation, canary=args.canary, after_canary=args.after_canary)


if __name__ == '__main__':
    main()
