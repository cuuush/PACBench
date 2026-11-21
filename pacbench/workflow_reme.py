"""Observe ReMe's stock watched-resource and conversation capture workflow."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from provider_adapters.reme_resource import inside

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity
from .workflow_tracks import HERMES_TOOLS, validate_native_deployment


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
    from . import harness, workflow_capability as cap
    from .checkpoint_clock import CheckpointClock
    from .gateway_reuse import PersistentGateway, atomic
    from .native_pilot import probe, provision, stop_arm_model_work
    from .openrouter_route import ENDPOINT, MODEL
    from .provider_loader import provider_module
    from .reme_memos_setup import PINS
    from .workflow_memcal_live import run_environment
    from .workflow_native import NativeAccount, WarmHermes
    from .workflow_runner import Condition

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
    from socket import create_connection
    from urllib.parse import urlsplit
    from .canary_capability import binding, read, sha
    from .observational_receipts import RunReceiptJournal
    from .openrouter_route import PROFILES
    from .reme_memos_setup import PINS
    from .workflow_capability import schedule
    from .workflow_metrics import summarize
    from .workflow_receipts import receipt_summary
    from .workflow_runner import Condition
    from .workflow_spec import SPEC

    root = Path(root).resolve(strict=True)
    bindings = {'result':binding(root/'result.json'),'scope':binding(root/'workflow-scope.json')}
    result, scope = read(bindings['result']), read(bindings['scope'])
    journal = root/'workflow/workflow.jsonl'
    report = summarize(journal)
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    condition = Condition(**events[1]['condition'])
    deployment = events[1]['deployment_witness']
    validate_native_deployment(condition,deployment)
    docs, turns, frozen = schedule(sha(SPEC),True)
    rows = RunReceiptJournal(root/'receipts.jsonl').snapshot()
    receipts = receipt_summary(root,rows,scope['run_id'])
    roles = {r['accounting_role'] for r in rows if r.get('upstream_request_id')}
    readbacks = [r['evidence'] for r in events if r['phase']=='readback']
    visible = [row for row in events if row['phase']=='answer-visible']
    closed = [row for row in events if row['phase']=='chat-closed']
    setup = read(binding(root/'native-server-setup.json'))
    shutdown = read(binding(root/'native-service-stop.json'))
    proof = shutdown['log_slice']
    log = Path(proof['path'])
    endpoint = urlsplit(setup['endpoint'])
    profile = PROFILES['luna-bedrock-global']
    cov = report['coverage']
    checkout = Path(__file__).resolve().parents[1]
    revision = next(line.split('=',1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                    if line.startswith('HERMES_PIN='))
    if (root.name!='reme' or scope['root']!=str(root) or scope['provider']!='reme'
            or scope['admission']!='reme-native-watched-resource-week-v1' or scope['canary'] is not True
            or scope['spec_sha256']!=sha(SPEC) or scope['frozen_inputs']!=frozen
            or condition.provider!='reme' or condition.track!='nightly-document' or condition.state!='continuing-account'
            or condition.model_route!=profile['endpoint'] or condition.model!=profile['model']
            or condition.frontend!='Hermes '+revision or condition.gateway_patch!=sha(checkout/'patches/hermes-isolated.diff')
            or deployment['account']!=str(root) or setup['provider_revision']!=PINS['reme'][1]
            or setup['pid']!=deployment['native_processes']['memory']['pid']
            or setup['pid']!=shutdown['pid'] or shutdown['forced_kill'] or not shutdown['native_shutdown_complete']
            or shutdown['returncode'] not in (0,-15) or log!=root/'reme-native-service.private.log'
            or hashlib.sha256(log.read_bytes()[proof['offset']:proof['offset']+proof['length']]).hexdigest()!=proof['sha256']
            or endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port
            or result['provider']!='reme' or result['canary'] is not True
            or (result['state'],result['cleanup'])!=('live-canary-complete','complete')
            or result['workflow']!=report or result['billing']!=receipts or receipts['complete_cost_usd'] is None
            or roles!={'hermes','judge','memory_provider'} or events[-1]['phase']!='completion'
            or (cov['verified_turns'],cov['planned_turns'],cov['graded_criteria'],cov['planned_criteria'])!=(4,4,12,12)
            or [row['question'] for row in events if row['phase']=='verified-turn']!=[t.id for t in turns]
            or len(visible)!=4 or len(closed)!=3 or visible[0]['surface']['session_id']!=visible[3]['surface']['session_id']
            or [(r['native_id'],r['submitted_sha256'],r['ready']) for r in readbacks]!=[
                (d.filename,d.sha256,True) for d in docs]
            or any(t['driver_attempt_coverage']['coverage']!='complete' for t in report['turns'])):
        raise ValueError('ReMe requires its exact completed native and receipted canary')
    try:
        connection = create_connection((endpoint.hostname,endpoint.port),timeout=2)
    except ConnectionRefusedError:
        pass
    else:
        connection.close()
        raise ValueError('ReMe canary listener remains active')
    return bindings


def run(root, confirmation, *, canary, after_canary=None):
    import os
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
    if (root.name!='reme' or root!=root.resolve() or PROFILE_NAME!=cap.PROFILE or os.environ.get(cap.ENV)
            or canary==(after_canary is not None)):
        raise ValueError('expected a fresh dedicated ReMe Luna account')
    qualification = check_canary(after_canary) if after_canary is not None else None
    auth = binding(confirmation)
    cap.confirm_setting(read(auth))
    validate_recall_environment()
    _, docs, turns, frozen = compile_week('continuing-account')
    verify_contract(docs,turns,frozen)
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    settings = dict(PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),PACBENCH_DISPATCH_TRACKING=CONDITION,
                    PACBENCH_ROLE_ROUTES='luna-all-roles-v1',PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',
                    PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',PACBENCH_STOP_FILE=str(root/'STOP'),
                    PACBENCH_OBSERVATIONAL_COHORT_STOP=str(root.parent.parent/'ACCOUNT_STOP'),
                    PACBENCH_MEMORY_API_INPUT_TRACE_ROOT=str(root/'memory-api-inputs'),PACBENCH_IMPORT_ROOT=str(root))
    began = time.monotonic()
    with run_environment(settings):
        check_cohort_stop()
        fetch_catalog(profile=PROFILE_NAME)
        bound = cap.create(root,'reme',canary=canary,prepaid=credit_snapshot(),auto_recharge_evidence=auth,
                           qualification=qualification)
        with run_environment({cap.ENV:json.dumps(bound)}):
            scope = cap.load()
            ledger = cap.budget()
            result = {'provider':'reme','canary':canary,'state':'incomplete','cleanup':'unverified'}
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root,scope,ledger) as (condition,account):
                        _, key = client_settings(scope['projects']['reme']['judge'])
                        judge = ModelClient(key=key,model=MODEL,record_dir=root/'workflow/model-calls',
                                            url=CHAT_COMPLETIONS_URL,json_mode=True,max_attempts=1)
                        run_current_week(condition,account,root/'workflow',judge=judge,canary=canary)
                    result['cleanup'] = 'complete'
                report = summarize(root/'workflow/workflow.jsonl')
                rows = ledger.snapshot()
                matched = receipt_summary(root,rows,scope['run_id'])
                coverage = report['coverage']
                roles = {r['accounting_role'] for r in rows if r.get('upstream_request_id')}
                if (coverage['verified_turns']==coverage['planned_turns']
                        and coverage['graded_criteria']==coverage['planned_criteria']
                        and all(t['driver_attempt_coverage']['coverage']=='complete' for t in report['turns'])
                        and roles=={'hermes','judge','memory_provider'} and
                        (not canary or matched['complete_cost_usd'] is not None)):
                    result['state'] = 'live-canary-complete' if canary else 'live-week-complete'
            except BaseException as error:
                result['error'] = type(error).__name__
                raise
            finally:
                result.update(elapsed_s=time.monotonic()-began,billing=receipt_summary(root,ledger.snapshot(),scope['run_id']),
                              workflow=summarize(root/'workflow/workflow.jsonl'))
                atomic(root/'result.json',result)
    return result
