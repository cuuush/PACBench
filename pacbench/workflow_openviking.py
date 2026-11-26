"""Continuing-account OpenViking native file and conversation witnesses."""
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import time

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity
from .workflow_tracks import HERMES_TOOLS, validate_native_deployment


def uri_rows(client, root):
    """Native recursive listing, excluding generated hidden summaries."""
    page = client.get('/api/v1/fs/ls', params={'uri':root,'recursive':True,'output':'original',
                                              'include_abstract':False,'node_limit':1000},timeout=20)
    if page.get('status')!='ok' or page.get('has_more') or not isinstance(page.get('result'),list):
        raise RuntimeError('OpenViking native resource tree is incomplete')
    rows = {r['uri']:r for r in page['result']}
    if len(rows)!=len(page['result']):
        raise RuntimeError('OpenViking reported repeated resource identities')
    return rows


def observe_queues(client, *, deadline):
    from .document_audit import openviking_observer_state

    while time.monotonic()<deadline:
        value = client.get('/api/v1/observer/queue?format=json',timeout=15)
        state = openviking_observer_state(value)
        if state=='ready':
            return value
        if state!='busy':
            raise RuntimeError('OpenViking native queues reported errors')
        time.sleep(.25)
    raise TimeoutError('OpenViking native queues did not become processing-ready')


class OpenVikingWitness:
    def __init__(self, root, owner, client, server, documents, all_documents, ledger):
        self.root,self.owner,self.client,self.server,self.ledger = root,owner,client,server,ledger
        self.documents = tuple(documents)
        self.cutoffs = list(dict.fromkeys(d.cutoff for d in documents))
        self.planned_names = {PurePosixPath(doc.filename).stem for doc in all_documents}
        self.initial_uris = set()
        self.sources = {}
        self.captures = {}
        self.explicit_writes = []
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        from .catalog_plugins import verify_retained_catalog_provider

        verify_retained_catalog_provider('openviking', self.owner.home)
        if self.owner.provider!='openviking' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('OpenViking gateway or run-owned server identity changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.root/'openviking-data'),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='0.5.4.1',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['openviking']),
            native_processes={'memory':self.server,'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def control_readback(self):
        native = uri_rows(self.client,'viking://resources')
        sessions = self.client.get('/api/v1/sessions').get('result')
        if native or sessions!=[]:
            raise RuntimeError('OpenViking native resources or sessions already contain records')
        self.initial_uris = set(native)
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_resource_count':len(native),'native_session_count':len(sessions)})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('OpenViking source releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        if doc.cutoff!=self.cutoff or raw.get('status')!='ok' or not isinstance(raw.get('result'),dict):
            raise RuntimeError('OpenViking native upload acceptance is missing')
        result = raw['result']
        queues = result.get('queue_status')
        uri = result.get('root_uri','')
        if (result.get('status')!='success' or result.get('errors') or not uri.startswith('viking://resources/')
                or not queues or any(q.get('errors') or q.get('error_count') for q in queues.values())):
            raise RuntimeError('OpenViking file processing did not return a valid native resource')
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'temp_upload and resources(wait=true)','root_uri':uri})

    def document_readback(self, doc, acceptance):
        from .document_audit import openviking_failure_log, openviking_generated_artifacts, openviking_response_evidence

        if self.documents[len(self.sources)]!=doc:
            raise ValueError('OpenViking file readback differs from frozen source order')
        deadline = time.monotonic()+180
        waited = self.client.post('/api/v1/system/wait',{},timeout=150)
        queues = waited.get('result')
        if (waited.get('status')!='ok' or not isinstance(queues,dict) or not queues
                or any(q.get('error_count')!=0 or q.get('errors')!=[] for q in queues.values())):
            raise RuntimeError('OpenViking global processing wait failed')
        queue = observe_queues(self.client,deadline=deadline)
        uri = acceptance['request']['root_uri']
        tree = uri_rows(self.client,'viking://resources')
        leaves = [key for key,val in tree.items() if (key==uri or key.startswith(uri+'/'))
                  and not val.get('isDir',val.get('is_dir',False))]
        proof = []
        for key in leaves:
            raw = self.client.get('/api/v1/content/read',params={'uri':key,'raw':True},timeout=15).get('result')
            if isinstance(raw,str) and hashlib.sha256(raw.encode()).hexdigest()==doc.sha256:
                proof.append(key)
        responses = [r for r in self.ledger.snapshot() if r.get('workflow_slot',{}).get('batch_id')==doc.filename]
        generated = openviking_generated_artifacts(self.root/'openviking-data')
        failures = openviking_failure_log(self.root/'openviking-server.log')
        native_directory = PurePosixPath('viking')/self.client._account/uri.removeprefix('viking://')
        scoped = [row for row in generated['files'] if PurePosixPath(row['path']).is_relative_to(native_directory)]
        if (len(proof)!=1 or generated['state']!='complete' or failures['state']!='complete'
                or not scoped or any(item['failed_placeholder'] for item in scoped)
                or openviking_response_evidence(responses)['state']!='complete'):
            raise RuntimeError('OpenViking raw source, generated content or processed output is unproven')
        self.sources[doc.filename] = (uri,proof[0])
        return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=doc.sha256,
                    readback=dict(uri=proof[0],root_uri=uri,queue=queue,model_attempts=[r['id'] for r in responses],
                                  generated_source_artifacts=scoped))

    def source_inventory(self):
        rows = uri_rows(self.client,'viking://resources')
        visible = {uri for pair in self.sources.values() for uri in pair}
        if not visible.issubset(rows):
            raise RuntimeError('OpenViking released native resource is missing')
        # A future benchmark filename cannot hide under a different parent.
        issued = {PurePosixPath(name).stem for name in self.sources}
        for uri in rows:
            identities = {PurePosixPath(part).stem for part in uri.split('/')}
            if (identities & self.planned_names)-issued:
                raise RuntimeError('OpenViking inventory contains an unreleased benchmark source')
        return list(self.sources)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic()+90
        expected = self.owner.witness
        identity = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('OpenViking native lifecycle observer failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for row in sample['sessions'] for p in row['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=identity or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256']
                        or sample['native_failures'] or any(p['name']!='openviking'
                           or p.get('endpoint')!=self.client._endpoint for p in providers)):
                    raise RuntimeError('OpenViking lifecycle observation does not bind the native service')
                active = (sample['native_release_threads'] or any(row['pending_futures'] for row in sample['sessions'])
                          or any(p['active_writers'] for p in providers))
                if not active and sample['observed_monotonic_ns']>=since:
                    queue = observe_queues(self.client,deadline=deadline)
                    pending = any(not row.get('finished') and row['accounting_role']!='judge' for row in self.ledger.snapshot())
                    if not pending:
                        return dict(gateway_identity=identity,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                    readback={'gateway':sample,'queue':queue})
            time.sleep(.05)
        raise RuntimeError('OpenViking native open-account work did not finish')

    def observe_capture(self, captures):
        from urllib.parse import quote
        from .terminal_processing import capture_snapshot

        return capture_snapshot(self.root,dict(turns=captures,explicit_writes=self.explicit_writes),
                                lambda id:self.client.get('/api/v1/tasks/'+quote(id,safe=''),timeout=15)['result'],
                                account=self.client._account,user=self.client._user)

    def native_write_barrier(self, session, answer):
        from .canary_capability import digest

        self.idle()
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (session,)).fetchone()
        scoped = self.captures.setdefault(session,[])
        capture = dict(session=session,prompt=prompt,answer=answer,turn=len(scoped))
        capture['binding'] = digest(capture)
        scoped.append(capture)
        result = self.observe_capture(scoped)
        if result['state']=='native_failed' or not all(row['both_sides_captured'] for row in result['captures']):
            raise RuntimeError('OpenViking native session lacks its completed conversation capture')
        return dict(session_id=session,pending_writes=0,readback=result['captures'][-1])

    def after_native_close(self, session):
        deadline = time.monotonic()+120
        path = self.owner.home/'state.db'
        while time.monotonic()<deadline:
            sample = self.idle()['readback']['gateway']
            managers = [r for r in sample['sessions'] if r['session_id']==session]
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                end = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(session,)).fetchone()
            if end and end[0] is not None and end[1]=='cli_close' and managers and all(
                    r['shutdown_returned'] and r['clean'] for r in managers):
                committed = self.observe_capture(self.captures[session])
                if committed['state']=='complete':
                    return dict(session_id=session,pending_writes=0,readback=committed)
                if committed['state']=='native_failed':
                    raise RuntimeError('OpenViking native session commit failed')
            time.sleep(.25)
        raise RuntimeError('OpenViking session closure or native commit readiness was not proven')

    def tool_boundaries(self, results):
        from urllib.parse import quote

        boundaries = {}
        for row in results:
            if row['tool']!='viking_remember' or not row['success']:
                continue
            value = json.loads(row['text'])
            task_id = value.get('task_id')
            if not isinstance(task_id,str) or not task_id or any(w['task_id']==task_id for w in self.explicit_writes):
                raise RuntimeError('OpenViking explicit native write lacks a distinct task ID')
            witness = {'task_id':task_id,'session':value.get('session_id')}
            deadline = time.monotonic()+120
            while time.monotonic()<deadline:
                proof = self.client.get('/api/v1/tasks/'+quote(task_id,safe=''),timeout=15)['result']
                if (proof.get('task_id')!=task_id or proof.get('task_type')!='session_commit'
                        or witness['session'] and proof.get('resource_id')!=witness['session']
                        or proof.get('error') or proof.get('status') in ('failed','cancelled')):
                    raise RuntimeError('OpenViking explicit write native completion failed')
                if proof['status']=='completed':
                    returned = proof.get('result') or {}
                    if returned.get('errors') or returned.get('status') in ('failed','error'):
                        raise RuntimeError('OpenViking explicit write contains native errors')
                    self.explicit_writes.append(witness)
                    boundaries[row['tool_call_id']] = {'native_write_task':task_id,'native_status':'completed'}
                    break
                time.sleep(.25)
            else:
                raise TimeoutError('OpenViking explicit native write did not complete')
        return boundaries

    def request_lineage(self, requests):
        selected = {request['request_id'] for request in requests}
        attempts = self.ledger.snapshot()
        for attempt in reversed(attempts):
            if attempt['id'] in selected and attempt.get('retry_of'):
                selected.add(attempt['retry_of'])
        return [dict(id=row['id'],parent=row.get('retry_of'),operation=row['dispatch_context']['operation_id'],
                     origin='harness' if row.get('retry_of') else 'unknown') for row in attempts if row['id'] in selected]

    def close(self):
        pass


@contextmanager
def open_account(root, scope, ledger):
    from tempfile import TemporaryDirectory
    from . import harness, workflow_capability as cap
    from .checkpoint_clock import CheckpointClock
    from .gateway_reuse import PersistentGateway, atomic
    from .native_pilot import probe, provision, stop_arm_model_work
    from .openrouter_route import ENDPOINT, MODEL
    from .provider_loader import provider_module
    from .workflow_memcal_live import run_environment
    from .workflow_native import NativeAccount, WarmHermes
    from .workflow_runner import Condition

    with ExitStack() as stack:
        stack.callback(stop_arm_model_work,root)
        env = provision(root,'openviking',model=MODEL,local_timezone='America/New_York',inference_route='openrouter')
        home = root/'hermes-home'
        receipt = json.loads((root/'native-server-setup.json').read_text())
        if (receipt['version']!='0.5.4.1' or receipt['endpoint']!=env['OPENVIKING_ENDPOINT'] or not receipt['health']
                or not env['OPENVIKING_ENDPOINT'].startswith('http://127.0.0.1:') or env['OPENVIKING_API_KEY']):
            raise ValueError('OpenViking native deployment is not the fresh pinned localhost arm')
        server = process_identity(receipt['pid'])
        ready = probe(root,'openviking',env)
        atomic(root/'provider-probe.json',ready)
        if not ready['available']:
            raise RuntimeError('OpenViking native plugin preflight failed')
        with run_environment({'HERMES_HOME':str(home)}):
            client = provider_module('openviking')._VikingClient(env['OPENVIKING_ENDPOINT'],account=env['OPENVIKING_ACCOUNT'],
                user=env['OPENVIKING_USER'],agent='',extra_headers={})
        docs, _, _ = cap.schedule(scope['spec_sha256'],scope['canary'])
        all_docs, _, _ = cap.schedule(scope['spec_sha256'],False)
        tools = HERMES_TOOLS['openviking']
        env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'),
                   PACBENCH_SOURCE_ARGUMENT_RULES='{}')
        child = harness.turn_env(docs[0].cutoff,home,','.join(tools),env)
        clock = CheckpointClock(home,docs[0].cutoff,scope['spec_sha256'])
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-openviking-chat-')))
        owner = stack.enter_context(PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
            checkpoint=scope['spec_sha256'],observation_clock=clock,provider='openviking'))
        condition = Condition('openviking','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
            MODEL,ENDPOINT,'OpenViking 0.5.4.1 native local server','0.5.4.1',owner.pins['hermes_isolation_sha256'],
            'Physical OpenViking server and simulated Hermes question time',tools)
        witness = OpenVikingWitness(root,owner,client,server,docs,all_docs,ledger)
        yield condition,NativeAccount(str(root),'openviking',str(root),client,WarmHermes(owner,clock,witness.idle),witness)


def check_canary(root):
    """Verify the retained native source, session, process and receipt evidence."""
    from .canary_capability import binding, read
    from .catalog_plugins import verify_retained_catalog_provider
    from .live_dispatch import OwnershipConflict
    from .workflow_document_canary import audit_document_canary, verify_listener_stopped

    audit = audit_document_canary(root, 'openviking')
    root = audit.root
    verify_retained_catalog_provider('openviking', root/'hermes-home')
    setup = read(binding(root/'native-server-setup.json'))
    server = audit.deployment['native_processes']['memory']
    closes = [row for row in audit.events if row['phase'] == 'chat-closed']
    if (audit.condition['version'] != '0.5.4.1'
            or tuple(audit.condition['tool_names']) != HERMES_TOOLS['openviking']
            or setup['version'] != '0.5.4.1' or setup['pid'] != server['pid']
            or read(binding(root/'import-condition.json'))['condition'] != 'native-defaults'
            or any(row['evidence']['readback']['state'] != 'complete' for row in closes)):
        raise ValueError('OpenViking canary lacks its native ingestion or shutdown proof')
    try:
        observed = process_identity(server['pid'])
    except (FileNotFoundError, OwnershipConflict):
        observed = None
    if observed == server:
        raise ValueError('OpenViking canary server remains active')
    verify_listener_stopped(setup['endpoint'], 'OpenViking')
    return audit.bindings


def run(root, confirmation, *, canary, after_canary=None):
    from .workflow_document_lifecycle import run_document_week

    return run_document_week(
        root, confirmation, provider='openviking', canary=canary, after_canary=after_canary,
        check_canary=check_canary, open_account=open_account, fresh_account_error='expected a fresh dedicated OpenViking Luna account',
        extra_environment={'PACBENCH_OPENVIKING_IMPORT_CONDITION': 'native-defaults'})
