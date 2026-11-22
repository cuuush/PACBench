"""Qualify the Hindsight continuing-account multipart document lane."""
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity
from .workflow_hindsight_documents import HindsightDocumentReadback
from .workflow_native import NativeAccount
from .workflow_tracks import HERMES_TOOLS, validate_native_deployment

OBSERVATION_CONDITION = 'Physical Hindsight service clock; authored Hermes observation capped at 60 seconds between admissions'

class NativeFileClient:
    def __init__(self, client, runner):
        from types import SimpleNamespace

        def sync(method):
            return lambda *args, **kwargs: runner.run(method(*args, **kwargs))

        self.retain_files = sync(client.aretain_files)
        self.documents = SimpleNamespace(
            list_documents=sync(client.documents.list_documents), get_document=sync(client.documents.get_document))
        self.operations = SimpleNamespace(
            get_operation_status=sync(client.operations.get_operation_status), list_operations=sync(client.operations.list_operations))


class HindsightAccount(NativeAccount):
    def ask(self, prompt, *, at, session_id, on_return=None, on_trace=None):
        from .gateway_reuse import atomic

        folder = self.witness.root/'native-cli-returns'
        folder.mkdir(mode=0o700,exist_ok=True)

        def save_return(snapshot):
            path = folder/(snapshot['turn_nonce']+'.json')
            if path.exists():
                raise ValueError('Hindsight native CLI return already retained for this question')
            atomic(path,snapshot)
            if on_return is not None:
                on_return(snapshot)

        trace = self.capture_answer(prompt,at=at,session_id=session_id,on_return=save_return)
        if on_trace is not None:
            on_trace(trace)
        return self.observe_answer(trace)


class HindsightWitness:
    def __init__(self, root, owner, endpoint, bank, server, source_readback, ledger):
        self.root, self.owner, self.endpoint, self.bank = root, owner, endpoint, bank
        self.server, self.reader, self.ledger = server, source_readback, ledger
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in self.reader.documents))
        self.cutoff = None
        self.conversation_documents = {}

    def verify_deployment(self, condition, identity):
        from .catalog_plugins import verify_retained_catalog_provider

        verify_retained_catalog_provider('hindsight', self.owner.home)
        if self.owner.provider!='hindsight' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('Hindsight gateway or run-owned server identity changed')
        result = dict(account=identity, condition=vars(condition), store_identity=str(self.root/'fallback-home'),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='1.2.1', frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'], effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['hindsight']),
            native_processes={'memory':self.server, 'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def control_readback(self):
        prefix, rows = self.reader.inventory()
        if rows or prefix:
            raise RuntimeError('Hindsight bank is not empty')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_documents':len(rows),'native_bank':self.bank})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('Hindsight source releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff, as_of=cutoff, service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        if doc.cutoff!=self.cutoff or not raw.operation_ids:
            raise RuntimeError('Hindsight did not identify the accepted file import')
        return dict(native_id=doc.filename, accepted=True, at=doc.cutoff,
                    request={'interface':'retain_files','operation_ids':raw.operation_ids})

    def document_readback(self, doc, acceptance):
        observed = self.reader.document(doc,acceptance['request']['operation_ids'])
        return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=doc.sha256,readback=observed)

    def source_inventory(self):
        return self.reader.inventory()[0]

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic()+180
        expected = self.owner.witness
        identity = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        bank_ns = receipt_ns = 0
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('Hindsight lifecycle observer failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for row in sample['sessions'] for p in row['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=identity or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p.get('name')!='hindsight' or p.get('bank')!=self.bank
                               or p.get('endpoint')!=self.endpoint for p in providers)):
                    raise RuntimeError('Hindsight lifecycle observation differs from the run-owned bank')
                active = (sample['native_release_threads'] or any(row['pending_futures'] for row in sample['sessions'])
                          or any(p['local_pending_writes'] or p['local_unfinished_writes']
                                 or any('prefetch' in t for t in p['active_workers'])
                                 for p in providers))
                if not active and sample['observed_monotonic_ns']>=since:
                    drain_started = time.monotonic_ns()
                    jobs = self.reader.drain(deadline=deadline)
                    receipt_started = time.monotonic_ns()
                    bank_ns += receipt_started - drain_started
                    local_calls = any(not r.get('finished') and r['accounting_role']!='judge' for r in self.ledger.snapshot())
                    checked = time.monotonic_ns()
                    receipt_ns += checked - receipt_started
                    if not local_calls:
                        timing = {'gateway_observer_wait_s':(checked - since - bank_ns - receipt_ns) / 1e9,
                                  'bank_drain_s':bank_ns / 1e9, 'receipt_check_s':receipt_ns / 1e9}
                        return dict(gateway_identity=identity,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                    readback={'gateway':sample,'bank_jobs':jobs,'timing':timing})
            time.sleep(.1)
        raise RuntimeError('Hindsight open-session write and bank readiness were not observed')

    def native_write_barrier(self, session, answer):
        sample = self.idle()['readback']
        read_started = time.monotonic()
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
            prompt, = connection.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (session,)).fetchone()
        _, rows = self.reader.inventory()
        same_session = [k for k in rows if k==session or k.startswith(session+'-')]
        for key in same_session:
            row = self.reader.client.documents.get_document(self.bank,key,_request_timeout=15.0)
            if row.bank_id!=self.bank or row.id!=key or not row.original_text:
                continue
            try:
                text = json.loads(row.original_text)
            except json.JSONDecodeError:
                continue
            if not isinstance(text,list) or len(text)<2:
                continue
            last = text[-2:]
            if ([item.get('content') for item in last]==['User: '+prompt,'Assistant: '+answer]
                    and key in rows and rows[key].content_hash==row.content_hash):
                timing = {**sample['timing'], 'conversation_readback_s':time.monotonic()-read_started}
                self.conversation_documents[session] = key
                return dict(session_id=session,pending_writes=0,
                            readback={'native_document':key,'content_hash':row.content_hash,'bank_jobs':sample['bank_jobs'],
                                      'timing':timing})
        raise RuntimeError('Hindsight did not expose the completed conversation write')

    def after_native_close(self, session):
        deadline = time.monotonic()+180
        path = self.owner.home/'state.db'
        while time.monotonic()<deadline:
            sample = self.idle()['readback']['gateway']
            managers = [r for r in sample['sessions'] if r['session_id']==session]
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
                end = connection.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(session,)).fetchone()
            if end and end[0] is not None and end[1]=='cli_close' and managers and all(
                    r['shutdown_returned'] and r['clean'] for r in managers):
                return dict(session_id=session,pending_writes=0,
                            readback={'native_document':self.conversation_documents[session]})
            time.sleep(.05)
        raise RuntimeError('Hindsight did not close the selected native chat')

    tool_boundaries = staticmethod(lambda _: {})

    def request_lineage(self, requests):
        selected = {r['request_id'] for r in requests}
        rows = self.ledger.snapshot()
        for row in reversed(rows):
            if row['id'] in selected and row.get('retry_of'):
                selected.add(row['retry_of'])
        return [dict(id=row['id'],parent=row.get('retry_of'),operation=row['dispatch_context']['operation_id'],
                     origin='harness' if row.get('retry_of') else 'unknown') for row in rows if row['id'] in selected]

    def close(self):
        pass


@contextmanager
def open_account(root, scope, ledger):
    from asyncio import Runner
    from tempfile import TemporaryDirectory
    from urllib.parse import urlsplit
    import psutil
    from hindsight_client import Hindsight

    from . import harness, workflow_capability as cap
    from .checkpoint_clock import OBSERVATION_LIMIT_S, CheckpointClock
    from .gateway_reuse import PersistentGateway, atomic
    from .native_pilot import probe, provision, stop_arm_model_work
    from .openrouter_route import ENDPOINT, MODEL
    from .workflow_native import WarmHermes
    from .workflow_runner import Condition

    stack = ExitStack()
    server = None

    def stop():
        stop_arm_model_work(root)
        if server is not None:
            pid = server['pid']
            deadline = time.monotonic()+45
            while True:
                try:
                    active = psutil.Process(pid).status()!=psutil.STATUS_ZOMBIE and process_identity(pid)==server
                except (OSError,psutil.NoSuchProcess):
                    active = False
                if not active or time.monotonic()>=deadline:
                    break
                time.sleep(.25)
            atomic(root/'service-shutdown.json',{'server_pid':pid,'active':active})
            if active:
                raise RuntimeError('Hindsight scoped listener remained alive after native shutdown')

    stack.callback(stop)
    with stack:
        env = provision(root,'hindsight',model=MODEL,local_timezone='America/New_York',inference_route='openrouter')
        home = root/'hermes-home'
        config = json.loads((home/'hindsight/config.json').read_text())
        endpoint = urlsplit(config['api_url'])
        if endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port:
            raise ValueError('expected a run-owned local Hindsight bank')
        pid, = {c.pid for c in psutil.net_connections(kind='tcp') if c.status=='LISTEN' and c.pid
                and c.laddr.port==endpoint.port and c.laddr.ip in ('127.0.0.1','0.0.0.0')}
        server_env = psutil.Process(pid).environ()
        if (server_env.get('HOME')!=str(root/'fallback-home') or server_env.get(cap.ENV)!=os.environ[cap.ENV]
                or server_env.get('PACBENCH_RECEIPT_JOURNAL')!=str(root/'receipts.jsonl')):
            raise ValueError('Hindsight listener belongs to another account or execution scope')
        server = process_identity(pid)
        ready = probe(root,'hindsight',env)
        atomic(root/'provider-probe.json',ready)
        if not ready['available']:
            raise RuntimeError('Hindsight native plugin preflight failed')
        runner = stack.enter_context(Runner())
        client = Hindsight(config['api_url'],timeout=30.0,max_attempts=1)
        stack.callback(lambda:runner.run(client.aclose()))
        setup = json.loads((root/'native-bank-setup.json').read_text())
        versioned_server = json.loads(setup['stdout'].splitlines()[-1])
        advertised = runner.run(client.aget_version()).api_version
        if (versioned_server['endpoint']!=config['api_url'] or not advertised
                or versioned_server['runtime_versions'].get('hindsight-api-slim')!='1.2.1'):
            raise ValueError('Hindsight native server version differs')
        raw = NativeFileClient(client,runner)
        documents, _, _ = cap.schedule(scope['spec_sha256'],scope['canary'])
        all_documents, all_turns, _ = cap.schedule(scope['spec_sha256'],False)
        if any((datetime.fromisoformat(b.at) - datetime.fromisoformat(a.at)).total_seconds() <= OBSERVATION_LIMIT_S
               for a, b in zip(all_turns, all_turns[1:])):
            raise ValueError('Hindsight authored checkpoint spacing is too short for the observation clock')
        bank = config['bank_id']
        reader = HindsightDocumentReadback(raw,bank,documents,all_documents=all_documents)
        tools = HERMES_TOOLS['hindsight']
        env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'))
        child = harness.turn_env(documents[0].cutoff,home,','.join(tools),env)
        clock = CheckpointClock(home,documents[0].cutoff,scope['spec_sha256'],bounded=True)
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-hindsight-chat-')))
        owner = stack.enter_context(PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
            checkpoint=scope['spec_sha256'],observation_clock=clock,provider='hindsight'))
        condition = Condition('hindsight','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
            MODEL,ENDPOINT,'Hindsight 1.2.1 local_external native bank','1.2.1',owner.pins['hermes_isolation_sha256'],
            OBSERVATION_CONDITION,tools)
        witness = HindsightWitness(root,owner,config['api_url'],bank,server,reader,ledger)
        yield condition,HindsightAccount(str(root),'hindsight',bank,raw,WarmHermes(owner,clock,witness.idle),witness)


def shutdown_recovery_evidence(root):
    """Corroborate that the originally observed listener and its store have exited."""
    from urllib.parse import urlsplit
    import socket
    import psutil
    from .canary_capability import binding

    root = Path(root).resolve(strict=True)
    result = json.loads((root/'result.json').read_text())
    shutdown = json.loads((root/'service-shutdown.json').read_text())
    journal = [json.loads(line) for line in (root/'workflow/workflow.jsonl').read_text().splitlines()]
    server = journal[1]['deployment_witness']['native_processes']['memory']
    config = json.loads((root/'hermes-home/hindsight/config.json').read_text())
    endpoint = urlsplit(config['api_url'])
    if (root.name!='hindsight' or result['state']!='incomplete' or result['cleanup']!='unverified'
            or result.get('error')!='RuntimeError' or result['canary'] is not True
            or shutdown!={'active':True,'server_pid':server['pid']} or journal[-1]['phase']!='completion'
            or endpoint.hostname!='127.0.0.1' or not endpoint.port
            or 'Hindsight scoped listener remained alive after native shutdown' not in (root.parent/'run.log').read_text()):
        raise ValueError('Hindsight discrepancy was not limited to the original shutdown observation')
    logs = list((root/'fallback-home/.hindsight/profiles').glob('*.log'))
    log, = logs
    content = log.read_text()
    for witness in ('Embedded PostgreSQL stopped','Application shutdown complete.',f"Finished server process [{server['pid']}]"):
        if witness not in content:
            raise ValueError('Hindsight native server or database did not attest shutdown')
    try:
        if psutil.Process(server['pid']).status()!=psutil.STATUS_ZOMBIE and process_identity(server['pid'])==server:
            raise ValueError('Hindsight native server process remains active')
    except (OSError,psutil.NoSuchProcess):
        pass
    with socket.socket() as probe:
        probe.settimeout(2)
        if probe.connect_ex((endpoint.hostname,endpoint.port))==0:
            raise ValueError('Hindsight native listener remains reachable')
    return dict(state='verified-after-exit',server=server,
                result=binding(root/'result.json'),initial=binding(root/'service-shutdown.json'),
                daemon_log=binding(log),controller_log=binding(root.parent/'run.log'))


def reconcile_canary_cleanup(root):
    evidence = shutdown_recovery_evidence(root)
    path = Path(root).resolve()/'service-shutdown-reconciliation.json'
    with path.open('x') as stream:
        json.dump({**evidence,'recorded_at':datetime.now(timezone.utc).isoformat()},stream,indent=2)
    return path


def check_canary(root):
    """Re-audit the retained native document canary before starting a new account."""
    from .canary_capability import binding, read, sha
    from .catalog_plugins import verify_retained_catalog_provider
    from .checkpoint_clock import CEILING_MODE, audit_question
    from .observational_receipts import RunReceiptJournal
    from .openrouter_route import PROFILES
    from .workflow_capability import schedule
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary
    from .workflow_runner import Condition
    from .workflow_spec import SPEC

    root = Path(root).resolve(strict=True)
    result_binding = binding(root/'result.json')
    scope_binding = binding(root/'workflow-scope.json')
    result, scope = read(result_binding), read(scope_binding)
    clean = read(binding(root/'service-shutdown.json'))['active'] is False
    cleanup_binding = None
    if not clean:
        cleanup_binding = binding(root/'service-shutdown-reconciliation.json')
        cleanup = read(cleanup_binding)
        clean = {key:value for key,value in cleanup.items() if key!='recorded_at'} == shutdown_recovery_evidence(root)
    journal = root/'workflow/workflow.jsonl'
    report = summarize(journal)
    with journal.open() as stream:
        events = [json.loads(line) for line in stream]
    deployment = events[1]['deployment_witness']
    condition = Condition(**events[1]['condition'])
    validate_native_deployment(condition,deployment)
    verify_retained_catalog_provider('hindsight',root/'hermes-home')
    documents, turns, frozen = schedule(sha(SPEC),True)
    receipts = RunReceiptJournal(root/'receipts.jsonl').snapshot()
    matched = receipt_summary(root,receipts,scope['run_id'])
    roles = {row['accounting_role'] for row in receipts if row.get('upstream_request_id')}
    sources = [e['evidence'] for e in events if e['phase']=='readback']
    seen_turns = [e['question'] for e in events if e['phase']=='verified-turn']
    visible = [e for e in events if e['phase']=='answer-visible']
    setup = json.loads((root/'native-bank-setup.json').read_text())
    runtime = json.loads(setup['stdout'].splitlines()[-1])['runtime_versions']
    profile = PROFILES['luna-bedrock-global']
    checkout = Path(__file__).resolve().parents[1]
    hermes_revision = next(line.split('=',1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                           if line.startswith('HERMES_PIN='))
    cov = report['coverage']
    if (root.name!='hindsight' or scope['root']!=str(root) or scope['provider']!='hindsight'
            or scope.get('admission')!='hindsight-native-file-document-week-v1' or scope['canary'] is not True
            or scope['spec_sha256']!=sha(SPEC) or scope['frozen_inputs']!=frozen
            or condition.provider!='hindsight' or condition.version!='1.2.1' or condition.track!='nightly-document'
            or condition.state!='continuing-account' or tuple(condition.tool_names)!=HERMES_TOOLS['hindsight']
            or condition.service_clock!=OBSERVATION_CONDITION
            or deployment['account']!=str(root) or runtime.get('hindsight-api-slim')!='1.2.1'
            or condition.model_route!=profile['endpoint'] or condition.model!=profile['model']
            or condition.frontend!='Hermes '+hermes_revision or condition.gateway_patch!=sha(checkout/'patches/hermes-isolated.diff')
            or not clean or result['canary'] is not True
            or (cleanup_binding is None and (result['state'],result['cleanup'])!=('live-canary-complete','complete'))
            or (cleanup_binding is not None and (result['state'],result['cleanup'])!=('incomplete','unverified'))
            or result['workflow']!=report or result['billing']!=matched or matched['complete_cost_usd'] is None
            or roles!={'hermes','judge','memory_provider'} or events[-1]['phase']!='completion'
            or (cov['verified_turns'],cov['planned_turns'],cov['graded_criteria'],cov['planned_criteria'])!=(4,4,12,12)
            or seen_turns!=[turn.id for turn in turns]
            or [e['question'] for e in visible]!=seen_turns
            or len([e for e in events if e['phase']=='chat-closed'])!=3
            or visible[0]['surface']['session_id']!=visible[3]['surface']['session_id']
            or [(row['native_id'],row['submitted_sha256'],row['ready']) for row in sources]!=[
                (doc.filename,doc.sha256,True) for doc in documents]
            or any(row['driver_attempt_coverage']['coverage']!='complete' for row in report['turns'])):
        raise ValueError('Hindsight requires its exact completed native and receipted canary')
    for turn, event in zip(turns, visible, strict=True):
        surface = event['surface']
        evidence = surface['clock_evidence']
        desc = evidence['descriptor']
        adoption = evidence['gateway_process_adoption']
        owner = {'pid':adoption['pid'],'start_time':adoption['process_start_ticks'],'process_adoption':adoption}
        if (desc['clock_ceiling']!=CEILING_MODE or desc['at']!=turn.at or desc['checkpoint']!=scope['spec_sha256']
                or [owner['pid'],owner['start_time']]!=surface['gateway_identity']
                or evidence != audit_question(root/'reuse-clock',desc,
                    {'prompt':turn.prompt,'session_id':surface['session_id']},owner)):
            raise ValueError('Hindsight canary lacks its bounded native admission chain')
    qualified = {'result':result_binding,'scope':scope_binding}
    if cleanup_binding is not None:
        if cleanup['server'] != deployment['native_processes']['memory']:
            raise ValueError('Hindsight shutdown proof belongs to another process')
        qualified['cleanup'] = cleanup_binding
    return qualified


def run(root, confirmation, *, canary, after_canary=None):
    from . import workflow_capability as cap
    from .canary_capability import binding, read
    from .context_isolation import validate_recall_environment
    from .gateway_client import client_settings
    from .gateway_reuse import atomic
    from .live_dispatch import CONDITION
    from .model_calls import ModelClient
    from .observational_receipts import check_cohort_stop, credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .workflow_memcal_contract import verify_contract
    from .workflow_memcal_live import run_environment
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary
    from .workflow_runner import run_current_week
    from .workflow_spec import compile_week

    root = Path(root).absolute()
    if (root.name!='hindsight' or root!=root.resolve() or PROFILE_NAME!=cap.PROFILE or os.environ.get(cap.ENV)
            or canary==(after_canary is not None)):
        raise ValueError('expected a fresh dedicated Hindsight Luna account')
    qualification = check_canary(after_canary) if after_canary is not None else None
    auth = binding(confirmation)
    cap.confirm_setting(read(auth))
    validate_recall_environment()
    _, documents, turns, frozen = compile_week('continuing-account')
    verify_contract(documents,turns,frozen)
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_DISPATCH_TRACKING=CONDITION,
                    PACBENCH_ROLE_ROUTES='luna-all-roles-v1',PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',
                    PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',PACBENCH_STOP_FILE=str(root/'STOP'),
                    PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent.parent/'ACCOUNT_STOP'),
                    PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'),PACBENCH_IMPORT_ROOT=str(root))
    started = time.monotonic()
    with run_environment(settings):
        check_cohort_stop()
        fetch_catalog(profile=PROFILE_NAME)
        bound = cap.create(root,'hindsight',canary=canary,prepaid=credit_snapshot(),
                           auto_recharge_evidence=auth,qualification=qualification)
        with run_environment({cap.ENV:json.dumps(bound)}):
            scope = cap.load()
            ledger = cap.budget()
            result = {'provider':'hindsight','canary':canary,'state':'incomplete','cleanup':'unverified'}
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root,scope,ledger) as (condition,account):
                        _, key = client_settings(scope['projects']['hindsight']['judge'])
                        judge = ModelClient(key=key,model=MODEL,record_dir=root/'workflow/model-calls',
                                            url=CHAT_COMPLETIONS_URL,json_mode=True,max_attempts=1)
                        run_current_week(condition,account,root/'workflow',judge=judge,canary=canary)
                    result['cleanup'] = 'complete'
                report = summarize(root/'workflow/workflow.jsonl')
                rows = ledger.snapshot()
                matched = receipt_summary(root,rows,scope['run_id'])
                roles = {row['accounting_role'] for row in rows if row.get('upstream_request_id')}
                cov = report['coverage']
                if (cov['verified_turns']==cov['planned_turns'] and cov['graded_criteria']==cov['planned_criteria']
                        and all(t['driver_attempt_coverage']['coverage']=='complete' for t in report['turns'])
                        and roles=={'hermes','judge','memory_provider'}
                        and (not canary or matched['complete_cost_usd'] is not None)):
                    result['state'] = 'live-canary-complete' if canary else 'live-week-complete'
            except BaseException as error:
                result['error'] = type(error).__name__
                raise
            finally:
                result.update(elapsed_s=time.monotonic()-started, billing=receipt_summary(root,ledger.snapshot(),scope['run_id']),
                              workflow=summarize(root/'workflow/workflow.jsonl'))
                atomic(root/'result.json',result)
    return result
