"""Observe ReMe's stock watched-resource and conversation capture workflow."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from provider_adapters.reme_resource import inside

from ..gateway.reuse_runtime import PROTOCOL
from ..live_dispatch import process_identity
from pacbench.workflows.tracks import HERMES_TOOLS, validate_native_deployment


class ReMeWitness:
    def __init__(self, root, owner, workspace, receipts, server, plugin, docs, all_docs, ledger):
        self.root,self.owner,self.workspace,self.receipts = root,owner,workspace,receipts
        self.server,self.plugin,self.ledger = server,plugin,ledger
        self.documents = tuple(docs)
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in docs))
        self.expected_names = {doc.filename for doc in all_docs}
        self.sources = {}
        self.conversations = {}
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        if self.owner.provider!='reme' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('ReMe native account or listener identity changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.workspace),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='0.4.2.2',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['reme']),native_processes={'memory':self.server,
                  'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def control_readback(self):
        resources = list((self.workspace/'resource').rglob('*.txt'))
        conversations = list((self.workspace/'session/dialog').glob('*.jsonl'))
        if resources or conversations or any(self.receipts.glob('*.json')):
            raise ValueError('ReMe native store already contains documents or conversation history')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_resource_count':0,'native_capture_count':0})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('ReMe nightly source releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        path = self.workspace/'resource'/datetime.fromisoformat(doc.cutoff).date().isoformat()/doc.filename
        if self.cutoff!=doc.cutoff or raw.get('resource')!=str(path) or path.is_symlink() or not path.is_file():
            raise ValueError('ReMe did not receive its current native resource file')
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'native auto_resource watcher','resource':str(path.relative_to(self.workspace))})

    def document_readback(self, doc, acceptance):
        if self.documents[len(self.sources)]!=doc:
            raise ValueError('ReMe document order differs from the frozen schedule')
        relative = acceptance['request']['resource']
        source = inside(self.workspace,relative)
        deadline = time.monotonic()+600
        while time.monotonic()<deadline:
            for receipt_file in self.receipts.glob('*.json'):
                record = json.loads(receipt_file.read_bytes())
                result = record.get('response') or {}
                matches = [item for item in (result.get('metadata') or {}).get('results',[]) if item.get('path')==relative]
                if not matches:
                    continue
                if len(matches)!=1 or record.get('processing_forced') is not False or matches[0].get('success') is not True:
                    raise ValueError('ReMe scoped resource action did not complete normally')
                item, = matches
                hook = record.get('hook_metadata') or {}
                meta = item['metadata']
                card = inside(self.workspace,meta['path'])
                link = f'[[{relative}]]'
                nodes = [node for node in hook.get('observed_index_nodes',[]) if node.get('path')==meta['path']]
                source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                if (source_hash!=doc.sha256 or not hook.get('index_observation_complete') or len(nodes)!=1
                        or not nodes[0].get('chunk_ids') or nodes[0].get('front_matter',{}).get('source_resource')!=link
                        or datetime.fromisoformat(doc.cutoff).date().isoformat() not in card.relative_to(self.workspace).parts
                        or meta.get('source_resource')!=link or not card.is_file() or link not in card.read_text()):
                    raise ValueError('ReMe native resource, card or searchable index binding differs')
                self.sources[doc.filename] = source_hash
                return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=source_hash,
                    readback={'resource':relative,'card':str(card.relative_to(self.workspace)),
                              'card_sha256':hashlib.sha256(card.read_bytes()).hexdigest(),
                              'native_observation_sha256':hashlib.sha256(receipt_file.read_bytes()).hexdigest(),
                              'indexed_chunk_count':len(nodes[0]['chunk_ids'])})
            time.sleep(.3)
        raise TimeoutError('ReMe native watcher did not expose a completed card and index')

    def source_inventory(self):
        paths = [path for path in (self.workspace/'resource').rglob('*.txt') if path.name in self.expected_names]
        raw = {path.name:path for path in paths}
        if (len(paths)!=len(raw) or set(raw)!=set(self.sources) or any(path.is_symlink() or
                hashlib.sha256(path.read_bytes()).hexdigest()!=self.sources[name] for name,path in raw.items())):
            raise ValueError('ReMe native resource inventory contains future, altered or missing source')
        return list(self.sources)

    def idle(self):
        now = time.monotonic_ns()
        deadline = time.monotonic()+90
        expected = self.owner.witness
        ident = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('ReMe native Hermes observation failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for r in sample['sessions'] for p in r['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=ident or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p['name']!='reme' for p in providers)):
                    raise RuntimeError('ReMe Hermes lifecycle evidence differs from its run-owned gateway')
                active = (sample['native_release_threads'] or any(r['pending_futures'] for r in sample['sessions'])
                          or any(p['unfinished_writes'] for p in providers))
                if (not active and sample['observed_monotonic_ns']>=now
                        and not any(not row.get('finished') and row['accounting_role']!='judge' for row in self.ledger.snapshot())):
                    return dict(gateway_identity=ident,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                readback={'gateway':sample})
            time.sleep(.1)
        raise TimeoutError('ReMe native Hermes work did not drain')

    def native_write_barrier(self, session, answer):
        self.idle()
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (session,)).fetchone()
        capture = self.workspace/'session/dialog'/(self.plugin._scoped_session_id('default',session)+'.jsonl')
        if not capture.is_file() or capture.is_symlink():
            raise ValueError('ReMe persisted capture is missing for the current Hermes session')
        content = capture.read_bytes()
        rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        observed = []
        for row in rows[-2:]:
            parts = row.get('content')
            text = parts if isinstance(parts,str) else ''.join(p.get('text','') for p in parts if p.get('type')=='text')
            observed.append((row.get('role'),text))
        if observed!=[('user',prompt.strip()),('assistant',answer.strip())]:
            raise ValueError('ReMe native conversation capture differs from this turn')
        self.conversations[session] = capture
        return dict(session_id=session,pending_writes=0,
                    readback={'native_dialog':str(capture.relative_to(self.workspace)),
                              'sha256':hashlib.sha256(content).hexdigest(),'message_count':len(rows)})

    def after_native_close(self, sid):
        sample = self.idle()['readback']['gateway']
        managers = [row for row in sample['sessions'] if row['session_id']==sid]
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            ended = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(sid,)).fetchone()
        if (not ended or ended[0] is None or ended[1]!='cli_close' or not managers
                or not all(r['shutdown_returned'] and r['clean'] for r in managers)):
            raise ValueError('ReMe selected session did not close natively')
        return dict(session_id=sid,pending_writes=0,readback={'native_dialog':str(self.conversations[sid].relative_to(self.workspace))})

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
    from contextlib import ExitStack
    from signal import SIGTERM
    from tempfile import TemporaryDirectory
    from urllib.parse import urlsplit
    from pacbench import harness
    from pacbench.workflows import capability as cap
    from ..checkpoint_clock import CheckpointClock
    from ..gateway_reuse import PersistentGateway, atomic
    from ..native_pilot import probe, provision, stop_arm_model_work
    from ..openrouter_route import ENDPOINT, MODEL
    from ..providers import provider_module
    from ..reme_memos_setup import PINS
    from . import run_environment
    from pacbench.workflows.native import NativeAccount, WarmHermes
    from pacbench.workflows.runner import Condition

    server = None

    def stop():
        stop_arm_model_work(root)
        if server is not None:
            receipt = json.loads((root/'native-service-stop.json').read_bytes())
            if (receipt['pid']!=server['pid'] or receipt['forced_kill']
                    or receipt['returncode'] not in (0,-SIGTERM) or not receipt['native_shutdown_complete']):
                raise RuntimeError('ReMe native shutdown could not be confirmed')

    with ExitStack() as stack:
        stack.callback(stop)
        env = provision(root,'reme',model=MODEL,local_timezone='America/New_York',inference_route='openrouter')
        home = root/'hermes-home'
        setup = json.loads((root/'native-server-setup.json').read_bytes())
        native = json.loads((home/'reme/config.json').read_bytes())
        paths = json.loads((root/'reme-resource-config.json').read_bytes())
        endpoint = urlsplit(setup['endpoint'])
        workspace = root/'reme-workspace'
        receipts = root/'reme-native-resource-results'
        if (endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port
                or native!={'mode':'http','endpoint':setup['endpoint'],'workspace_dir':str(workspace)}
                or paths!={'workspace':str(workspace),'receipts':str(receipts)}
                or setup['provider_revision']!=PINS['reme'][1] or not setup['native_resource_watcher']
                or not setup['native_defaults'] or not setup['health']):
            raise ValueError('ReMe native deployment differs from the pinned localhost resource watcher')
        server = process_identity(setup['pid'])
        ready = probe(root,'reme',env)
        atomic(root/'provider-probe.json',ready)
        if not ready['available']:
            raise RuntimeError('ReMe native plugin preflight failed')
        with run_environment({'HERMES_HOME':str(home)}):
            plugin = provider_module('reme')
        docs, _, _ = cap.schedule(scope['spec_sha256'],scope['canary'])
        all_docs, _, _ = cap.schedule(scope['spec_sha256'],False)
        tools = HERMES_TOOLS['reme']
        env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'))
        child = harness.turn_env(docs[0].cutoff,home,','.join(tools),env)
        clock = CheckpointClock(home,docs[0].cutoff,scope['spec_sha256'])
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-reme-chat-')))
        owner = stack.enter_context(PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
            checkpoint=scope['spec_sha256'],observation_clock=clock,provider='reme'))
        condition = Condition('reme','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
            MODEL,ENDPOINT,'Stock ReMe native historical resource watcher and BM25 index','0.4.2.2',
            owner.pins['hermes_isolation_sha256'],'Physical ReMe service and simulated Hermes question time',tools)
        witness = ReMeWitness(root,owner,workspace,receipts,server,plugin,docs,all_docs,ledger)
        yield condition,NativeAccount(str(root),'reme',str(workspace),None,WarmHermes(owner,clock,witness.idle),witness)


def check_canary(root):
    """Re-audit the pinned native source, capture, shutdown, and receipts."""
    from ..canary_capability import binding, read
    from ..reme_memos_setup import PINS
    from pacbench.workflows.document_canary import audit_document_canary, verify_listener_stopped

    audit = audit_document_canary(root, 'reme')
    root = audit.root
    setup = read(binding(root/'native-server-setup.json'))
    shutdown = read(binding(root/'native-service-stop.json'))
    proof = shutdown['log_slice']
    log = Path(proof['path'])
    if (setup['provider_revision'] != PINS['reme'][1]
            or setup['pid'] != audit.deployment['native_processes']['memory']['pid']
            or setup['pid'] != shutdown['pid'] or shutdown['forced_kill'] or not shutdown['native_shutdown_complete']
            or shutdown['returncode'] not in (0, -15) or log != root/'reme-native-service.private.log'
            or hashlib.sha256(log.read_bytes()[proof['offset']:proof['offset']+proof['length']]).hexdigest()
                != proof['sha256']):
        raise ValueError('ReMe canary lacks its native shutdown proof')
    verify_listener_stopped(setup['endpoint'], 'ReMe')
    return audit.bindings


def run(root, confirmation, *, canary, after_canary=None):
    from pacbench.workflows.lifecycle import run_document_week

    return run_document_week(
        root, confirmation, provider='reme', canary=canary, after_canary=after_canary,
        check_canary=check_canary, open_account=open_account, fresh_account_error='expected a fresh dedicated ReMe Luna account')
