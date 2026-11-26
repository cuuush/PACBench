"""Qualify the Hindsight continuing-account multipart document lane."""
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time

from ...gateway.reuse_runtime import PROTOCOL
from ...live_dispatch import process_identity
from .evidence import HindsightDocumentReadback
from pacbench.workflows.native import NativeAccount, native_chat_closed, receipt_lineage
from pacbench.workflows.tracks import HERMES_TOOLS, validate_native_deployment

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
        from ...gateway_reuse import atomic

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
        from ...catalog_plugins import verify_retained_catalog_provider

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
        while time.monotonic()<deadline:
            sample = self.idle()['readback']['gateway']
            if native_chat_closed(self.owner.home, session, sample['sessions']):
                return dict(session_id=session,pending_writes=0,
                            readback={'native_document':self.conversation_documents[session]})
            time.sleep(.05)
        raise RuntimeError('Hindsight did not close the selected native chat')

    tool_boundaries = staticmethod(lambda _: {})

    def request_lineage(self, requests):
        return receipt_lineage(self.ledger.snapshot(), requests)

    def close(self):
        pass


@contextmanager
def open_account(root, scope, ledger):
    from asyncio import Runner
    from tempfile import TemporaryDirectory
    from urllib.parse import urlsplit
    import psutil
    from hindsight_client import Hindsight

    from pacbench import harness
    from pacbench.workflows import capability as cap
    from ...checkpoint_clock import OBSERVATION_LIMIT_S, CheckpointClock
    from ...gateway_reuse import PersistentGateway, atomic
    from ...native_pilot import probe, provision, stop_arm_model_work
    from ...openrouter_route import ENDPOINT, MODEL
    from pacbench.workflows.native import WarmHermes
    from pacbench.workflows.runner import Condition

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
    from ...canary_capability import binding

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
    from ...canary_capability import binding, read
    from ...catalog_plugins import verify_retained_catalog_provider
    from ...checkpoint_clock import CEILING_MODE, audit_question
    from pacbench.workflows.document_canary import audit_document_canary

    audit = audit_document_canary(root, 'hindsight')
    root = audit.root
    clean = read(binding(root/'service-shutdown.json'))['active'] is False
    cleanup_binding = None
    if not clean:
        cleanup_binding = binding(root/'service-shutdown-reconciliation.json')
        cleanup = read(cleanup_binding)
        clean = {key: value for key, value in cleanup.items() if key != 'recorded_at'} == shutdown_recovery_evidence(root)
    verify_retained_catalog_provider('hindsight', root/'hermes-home')
    setup = json.loads((root/'native-bank-setup.json').read_text())
    runtime = json.loads(setup['stdout'].splitlines()[-1])['runtime_versions']
    condition = audit.condition
    expected_outcome = ('live-canary-complete', 'complete') if cleanup_binding is None else ('incomplete', 'unverified')
    if (condition['version'] != '1.2.1' or tuple(condition['tool_names']) != HERMES_TOOLS['hindsight']
            or condition['service_clock'] != OBSERVATION_CONDITION
            or runtime.get('hindsight-api-slim') != '1.2.1'
            or not clean or audit.outcome != expected_outcome):
        raise ValueError('Hindsight canary lacks its exact native runtime or shutdown proof')
    visible = [event for event in audit.events if event['phase'] == 'answer-visible']
    for turn, event in zip(audit.turns, visible, strict=True):
        surface = event['surface']
        evidence = surface['clock_evidence']
        desc = evidence['descriptor']
        adoption = evidence['gateway_process_adoption']
        owner = {'pid': adoption['pid'], 'start_time': adoption['process_start_ticks'], 'process_adoption': adoption}
        if (event['question'] != turn.id or desc['clock_ceiling'] != CEILING_MODE
                or desc['at'] != turn.at or desc['checkpoint'] != audit.spec_sha256
                or [owner['pid'], owner['start_time']] != surface['gateway_identity']
                or evidence != audit_question(root/'reuse-clock', desc,
                    {'prompt': turn.prompt, 'session_id': surface['session_id']}, owner)):
            raise ValueError('Hindsight canary lacks its bounded native admission chain')
    if cleanup_binding is not None:
        if cleanup['server'] != audit.deployment['native_processes']['memory']:
            raise ValueError('Hindsight shutdown proof belongs to another process')
        return {**audit.bindings, 'cleanup': cleanup_binding}
    return audit.bindings


def run(root, confirmation, *, canary, after_canary=None):
    from pacbench.workflows.lifecycle import run_document_week

    return run_document_week(
        root, confirmation, provider='hindsight', canary=canary, after_canary=after_canary,
        check_canary=check_canary, open_account=open_account, fresh_account_error='expected a fresh dedicated Hindsight Luna account')
