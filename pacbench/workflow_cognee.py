"""Stock Cognee native files and warm conversation provenance for Pizza Week v2."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID

from provider_adapters.cognee_checkpoint import verify_turn
from provider_adapters.cognee_completion import completed, pipeline_rows, require_idle
from provider_adapters.cognee_local import QualificationError, qualify_incremental_document

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity
from .workflow_native import NativeAccount
from .workflow_tracks import HERMES_TOOLS, validate_native_deployment


class CogneeAccount(NativeAccount):
    def submit(self, doc):
        return self.witness.submit(doc)

    def ask(self, prompt, *, at, session_id):
        surface = super().ask(prompt,at=at,session_id=session_id)
        self.witness.observe_explicit_captures(surface)
        return surface

    def end_chat(self, sid):
        self.witness.prior_close[sid] = [row['id'] for row in pipeline_rows(self.witness.root)]
        return super().end_chat(sid)


class CogneeWitness:
    def __init__(self, root, owner, backend, endpoint, dataset, server, docs, all_docs, ledger):
        self.root,self.owner,self.backend,self.endpoint,self.dataset = root,owner,backend,endpoint,dataset
        self.server,self.ledger = server,ledger
        self.documents = tuple(docs)
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in docs))
        self.known_sources = {doc.sha256:doc.filename for doc in all_docs}
        self.sources = {}
        self.turns = {}
        self.explicit_captures = {}
        self.prior_close = {}
        self.pending = {}
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        if self.owner.provider!='cognee' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('Cognee native owner or listener changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.root/'cognee-data'),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='1.6.8 / Hermes 1.4.3',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['cognee']),native_processes={'memory':self.server,
                'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def native_documents(self):
        root = self.root/'cognee-data'
        path = self.root/'cognee-system/databases/cognee_db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            rows = db.execute('SELECT id,external_metadata,raw_data_location FROM data').fetchall()
        observed = {}
        for identifier,attribute,location in rows:
            target = Path(urlparse(location).path).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError('Cognee document original lies outside the run-owned store')
            native = str(UUID(identifier))
            if native in observed:
                raise ValueError('Cognee native duplicate document identity')
            observed[native] = (hashlib.sha256(target.read_bytes()).hexdigest(),json.loads(attribute) if attribute else {})
        return observed

    def control_readback(self):
        if self.native_documents():
            raise ValueError('Cognee native store has pre-existing documents or captured conversation')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_dataset':self.dataset,'native_document_count':0})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('Cognee nightly release is out of chronological order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def prior_native_captures(self):
        by_hash = {}
        for sid, rows in self.turns.items():
            native_session, = {row['native_session'] for row in rows}
            header = f'Session ID: {native_session}\n\n'
            entries = [f"Question: {row['prompt']}\n\nAnswer: {row['answer']}\n\n" for row in rows]
            # Stock session extraction writes the contiguous Q&A after its persist
            # watermark. A single native promotion can contain multiple verified turns.
            for start in range(len(entries)):
                body = header
                for index in range(start, len(entries)):
                    body += entries[index]
                    digest = hashlib.sha256(body.encode()).hexdigest()
                    candidates = by_hash.setdefault(digest, [])
                    candidates.append({'session_id':sid,'native_session':native_session,
                                       'qa_ids':[r['qa_id'] for r in rows[start:index+1]]})
        source_ids = {rec['data_id'] for rec in self.sources.values()}
        native = self.native_documents()
        output = []
        for native_id,(source_hash,meta) in native.items():
            if native_id in source_ids:
                continue
            explicit = self.explicit_captures.get(native_id)
            if explicit and not meta and explicit['source_sha256']==source_hash:
                output.append(explicit)
                continue
            matches = by_hash.get(source_hash, [])
            if meta.get('node_set')!=['user_sessions_from_cache'] or len(matches)!=1:
                raise ValueError('Cognee prior dataset contains a document without attributable native capture')
            output.append({'data_id':native_id,'source_sha256':source_hash,'native_capture_kind':'promoted_qas',**matches[0]})
        return output

    def observe_explicit_captures(self, surface):
        """Bind stock tool-originated documents to their verified stored bytes."""
        for call,result in zip(surface['tool_calls'],surface['tool_results'],strict=True):
            if call['name']!='cognee_remember' or result.get('success') is not True:
                continue
            body = json.loads(result['text'])
            args = call['input']
            text = str(args.get('content') or '').strip()
            if (body.get('result')!='Content stored in Cognee.' or body.get('status') not in {'running','completed'}
                    or (args.get('dataset') or self.dataset)!=self.dataset or not text
                    or call['tool_call_id']!=result['tool_call_id']):
                raise ValueError('Cognee explicit permanent write lacks an exact native tool receipt')
            digest = hashlib.sha256(text.encode()).hexdigest()
            native_ids = [identifier for identifier,(stored,meta) in self.native_documents().items()
                          if stored==digest and not meta and digest not in self.known_sources]
            if len(native_ids)!=1:
                raise ValueError('Cognee explicit tool write lacks a unique native stored document')
            identifier, = native_ids
            path = self.root/'cognee-system/databases/cognee_db'
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                membership = db.execute('SELECT datasets.name FROM data JOIN datasets ON datasets.id=data.dataset_id '
                                        'WHERE data.id=?',(UUID(identifier).hex,)).fetchall()
            if membership!=[(self.dataset,)]:
                raise ValueError('Cognee explicit tool write is outside the bound native dataset')
            self.explicit_captures.setdefault(identifier,{'data_id':identifier,'source_sha256':digest,
                'native_capture_kind':'explicit_remember','session_id':surface['session_id'],
                'tool_call_id':call['tool_call_id']})

    def submit(self, doc):
        if self.cutoff!=doc.cutoff or self.pending or self.documents[len(self.sources)]!=doc:
            raise ValueError('Cognee native file submission is outside the frozen release order')
        before = {r['id'] for r in pipeline_rows(self.root)}
        require_idle(pipeline_rows(self.root))

        def status(*,dataset_id,timeout):
            identifier = str(UUID(dataset_id))
            return self.backend._request('GET',f'/api/v1/datasets/{identifier}/processing-status?pipeline=cognify_pipeline',timeout=timeout)

        source = qualify_incremental_document(self.backend,service_url=self.endpoint,dataset=self.dataset,
            text=doc.text,prior=list(self.sources.values()),processing_status=status,timeout=1800,
            native_captures=self.prior_native_captures())
        started = time.monotonic()
        deadline = started + 1800
        while True:
            operations = pipeline_rows(self.root)
            if completed(operations,before):
                break
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('Cognee native automatic improve did not finish')
            # After per-item qualification, catch an imminent local terminal row
            # without increasing the sustained query rate on a long-running improve.
            time.sleep(.2 if now - started < 1 else 1)
        self.pending[doc.filename] = {**source,'native_operation_completion':[r for r in operations if r['id'] not in before]}
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'stock remember_permanent','dataset':self.dataset,'data_id':source['data_id']})

    def document_readback(self, doc, acceptance):
        observed = self.pending.pop(doc.filename)
        if (observed['data_id']!=acceptance['request']['data_id'] or observed['source_sha256']!=doc.sha256
                or not observed['stored_source_verified'] or not observed['processing_complete']):
            raise ValueError('Cognee native completion differs from the frozen source')
        self.sources[doc.filename] = {'data_id':observed['data_id'],'source_sha256':doc.sha256}
        return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=doc.sha256,readback=observed)

    def source_inventory(self):
        native = self.native_documents()
        originals = {ident:hashed for ident,(hashed,_) in native.items() if hashed in self.known_sources}
        if originals!={r['data_id']:r['source_sha256'] for r in self.sources.values()}:
            raise ValueError('Cognee native inventory includes a missing, changed or unreleased source')
        return list(self.sources)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic()+180
        expected = self.owner.witness
        ident = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('Cognee native Hermes observation failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for r in sample['sessions'] for p in r['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=ident or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p['name']!='cognee' or p['endpoint']!=self.endpoint or p['dataset']!=self.dataset
                               for p in providers)):
                    raise RuntimeError('Cognee native lifecycle observation differs from its run-owned dataset')
                active = (sample['native_release_threads'] or any(r['pending_futures'] for r in sample['sessions'])
                          or any(not p['clean'] for p in providers))
                pending = any(not r.get('finished') and r['accounting_role']!='judge' for r in self.ledger.snapshot())
                if not active and not pending and sample['observed_monotonic_ns']>=since:
                    rows = pipeline_rows(self.root)
                    try:require_idle(rows)
                    except QualificationError:pass
                    else:
                        if any(r.get('outcome') not in (None,'succeeded') for r in rows):
                            raise RuntimeError('Cognee native pipeline failed')
                        return dict(gateway_identity=ident,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                    readback={'gateway':sample})
            time.sleep(.2)
        raise TimeoutError('Cognee native lifecycle did not drain')

    def native_write_barrier(self, sid, answer):
        from cognee_integration_hermes import memory_credit, recall_notice

        sample = self.idle()['readback']['gateway']
        identities = {p['native_session'] for r in sample['sessions'] if r['session_id']==sid
                      for p in r['providers'] if p['name']=='cognee' and p.get('native_session')}
        if len(identities)!=1:
            raise RuntimeError('Cognee native session is not uniquely bound to the Hermes chat')
        native_session, = identities
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE role='user' AND session_id=? ORDER BY id DESC LIMIT 1",
                                 (sid,)).fetchone()
        text = memory_credit.strip_credit_lines(recall_notice.strip_notice(answer))
        cached = self.backend.get_session(session_id=native_session,timeout=20) or {}
        entries = [q['qa_id'] for q in cached.get('qas',[]) if q.get('question')==prompt and q.get('answer')==text]
        if len(entries)!=1:
            raise RuntimeError('Cognee did not expose the exact persisted native turn')
        self.turns.setdefault(sid,[]).append({'prompt':prompt,'answer':text,'native_session':native_session,'qa_id':entries[0]})
        return dict(session_id=sid,pending_writes=0,readback={'native_session':native_session,'qa_id':entries[0]})

    def after_native_close(self, sid):
        sample = self.idle()['readback']['gateway']
        managers = [row for row in sample['sessions'] if row['session_id']==sid]
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            ended = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(sid,)).fetchone()
        if (not ended or ended[0] is None or ended[1]!='cli_close' or not managers
                or not all(r['shutdown_returned'] and r['clean'] for r in managers)):
            raise RuntimeError('Cognee selected chat did not close through the native provider')
        outcome = verify_turn({'native_root':str(self.root),'endpoint':self.endpoint,'dataset':self.dataset,
            'verified_qa_history':self.turns[sid],'prior_pipeline_ids':self.prior_close[sid],'source_documents':{rec['data_id']:rec['source_sha256'] for rec in self.sources.values()}},
            SimpleNamespace(session_id=sid, **self.turns[sid][-1]),sample)
        return dict(session_id=sid,pending_writes=0,readback=outcome)

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
    from tempfile import TemporaryDirectory
    from urllib.parse import urlsplit
    from . import harness, workflow_capability as cap
    from .checkpoint_clock import CheckpointClock
    from .cognee_memobase_setup import PINS
    from .gateway_reuse import PersistentGateway, atomic
    from .live_dispatch import OwnershipConflict
    from .native_pilot import probe, provision, stop_arm_model_work
    from .openrouter_route import ENDPOINT, MODEL
    from .workflow_native import WarmHermes
    from .workflow_runner import Condition
    from provider_adapters.cognee_checkpoint import native

    server = None
    endpoint = None

    def stop():
        from socket import create_connection

        stop_arm_model_work(root)
        if server is None:
            return
        try:
            active = process_identity(server['pid']) == server
        except (FileNotFoundError,OwnershipConflict):
            active = False
        if active:
            raise RuntimeError('Cognee native server remained active after shutdown')
        try:
            connection = create_connection((endpoint.hostname,endpoint.port),timeout=2)
        except ConnectionRefusedError:
            pass
        else:
            connection.close()
            raise RuntimeError('Cognee native listener remained active after shutdown')
        atomic(root/'native-service-stop.json',{'server':server,'state':'verified-after-exit'})

    with ExitStack() as stack:
        stack.callback(stop)
        env = provision(root,'cognee',model=MODEL,local_timezone='America/New_York',inference_route='openrouter')
        home = root/'hermes-home'
        setup = json.loads((root/'native-server-setup.json').read_bytes())
        config = json.loads((root/'cognee-document-config.json').read_bytes())
        endpoint = urlsplit(setup['endpoint'])
        if (endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port
                or setup['endpoint']!=config['service_url'] or setup['dataset']!=config['dataset']
                or setup['provider_revision']!=PINS['cognee']['commit'] or setup['health']['version']!='1.6.8'
                or setup['hosted_memory_service'] is not False or env['COGNEE_TRANSPORT']!='http'):
            raise ValueError('Cognee native deployment differs from the pinned localhost service')
        server = process_identity(setup['pid'])
        report = probe(root,'cognee',env)
        atomic(root/'provider-probe.json',report)
        if not report['available']:
            raise RuntimeError('Cognee native plugin preflight failed')
        dataset = setup['dataset']
        import sys
        sys.path.insert(0,str(home/'plugins/cognee'))
        from cognee_integration_hermes.http_backend import HttpBackend
        initial = HttpBackend(agent_session_name='pacbench-native-enrollment')
        initial.connect(url=setup['endpoint'],api_key='',timeout=25)
        try:
            initial.ensure_dataset(dataset=dataset,timeout=60)
        finally:
            initial.close(timeout=5)
        backend, _ = native(setup['endpoint'],dataset,home)
        stack.callback(lambda:backend.close(timeout=5))
        docs, _, _ = cap.schedule(scope['spec_sha256'],scope['canary'])
        all_docs, _, _ = cap.schedule(scope['spec_sha256'],False)
        tools = HERMES_TOOLS['cognee']
        env.update(PACBENCH_WORKFLOW_CAPTURE=scope['protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'),
                   PACBENCH_COGNEE_PHYSICAL_EXIT_WATCHER='1',COGNEE_IMPROVE_BACKGROUND='false')
        child = harness.turn_env(docs[0].cutoff,home,','.join(tools),env)
        clock = CheckpointClock(home,docs[0].cutoff,scope['spec_sha256'])
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-cognee-chat-')))
        owner = stack.enter_context(PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
            checkpoint=scope['spec_sha256'],observation_clock=clock,provider='cognee'))
        condition = Condition('cognee','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
            MODEL,ENDPOINT,'Stock Cognee native permanent-document extraction, HTTP Hermes and inline session promotion',
            '1.6.8 / Hermes 1.4.3',owner.pins['hermes_isolation_sha256'],
            'Physical Cognee service and exit watcher; simulated Hermes question time',tools)
        witness = CogneeWitness(root,owner,backend,setup['endpoint'],dataset,server,docs,all_docs,ledger)
        yield condition,CogneeAccount(str(root),'cognee',dataset,backend,
            WarmHermes(owner,clock,witness.idle,chat_close_timeout_s=360),witness)


def check_canary(root):
    """Re-audit the retained native source, session and receipt bindings."""
    from socket import create_connection
    from urllib.parse import urlsplit
    from .canary_capability import binding, read, sha
    from .cognee_memobase_setup import PINS
    from .observational_receipts import RunReceiptJournal
    from .openrouter_route import PROFILES
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
    readbacks = [row['evidence'] for row in events if row['phase']=='readback']
    visible = [row for row in events if row['phase']=='answer-visible']
    closed = [row for row in events if row['phase']=='chat-closed']
    setup = read(binding(root/'native-server-setup.json'))
    shutdown = read(binding(root/'native-service-stop.json'))
    server = deployment['native_processes']['memory']
    endpoint = urlsplit(setup['endpoint'])
    profile = PROFILES['luna-bedrock-global']
    cov = report['coverage']
    checkout = Path(__file__).resolve().parents[1]
    revision = next(line.split('=',1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                    if line.startswith('HERMES_PIN='))
    plugin = root/'hermes-home/plugins/cognee/cognee_integration_hermes/provider.py'
    if (root.name!='cognee' or scope['root']!=str(root) or scope['provider']!='cognee'
            or scope['admission']!='cognee-native-permanent-document-week-v1' or scope['canary'] is not True
            or scope['spec_sha256']!=sha(SPEC) or scope['frozen_inputs']!=frozen
            or condition.provider!='cognee' or condition.track!='nightly-document' or condition.state!='continuing-account'
            or 'inline session promotion' not in condition.deployment
            or condition.model_route!=profile['endpoint'] or condition.model!=profile['model']
            or condition.frontend!='Hermes '+revision or condition.gateway_patch!=sha(checkout/'patches/hermes-isolated.diff')
            or deployment['account']!=str(root) or setup['provider_revision']!=PINS['cognee']['commit']
            or setup['health']['version']!='1.6.8' or setup['pid']!=server['pid']
            or setup['plugin_source_sha256']!=sha(plugin)
            or shutdown!={'server':server,'state':'verified-after-exit'}
            or endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port
            or result['provider']!='cognee' or result['canary'] is not True
            or (result['state'],result['cleanup'])!=('live-canary-complete','complete')
            or result['workflow']!=report or result['billing']!=receipts or receipts['complete_cost_usd'] is None
            or roles!={'hermes','judge','memory_provider'} or events[-1]['phase']!='completion'
            or (cov['verified_turns'],cov['planned_turns'],cov['graded_criteria'],cov['planned_criteria'])!=(4,4,12,12)
            or [row['question'] for row in events if row['phase']=='verified-turn']!=[t.id for t in turns]
            or len(visible)!=4 or len(closed)!=3 or visible[0]['surface']['session_id']!=visible[3]['surface']['session_id']
            or [(r['native_id'],r['submitted_sha256'],r['ready']) for r in readbacks]!=[
                (d.filename,d.sha256,True) for d in docs]
            or any(t['driver_attempt_coverage']['coverage']!='complete' for t in report['turns'])):
        raise ValueError('Cognee requires its exact complete native and receipted canary')
    try:
        connection = create_connection((endpoint.hostname,endpoint.port),timeout=2)
    except ConnectionRefusedError:
        pass
    else:
        connection.close()
        raise ValueError('Cognee canary listener remains active')
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
    if (root.name!='cognee' or root!=root.resolve() or PROFILE_NAME!=cap.PROFILE or os.environ.get(cap.ENV)
            or canary==(after_canary is not None)):
        raise ValueError('expected a fresh dedicated Cognee Luna account')
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
        bound = cap.create(root,'cognee',canary=canary,prepaid=credit_snapshot(),auto_recharge_evidence=auth,
                           qualification=qualification)
        with run_environment({cap.ENV:json.dumps(bound)}):
            scope = cap.load()
            ledger = cap.budget()
            result = {'provider':'cognee','canary':canary,'state':'incomplete','cleanup':'unverified'}
            try:
                with direct_environment(os.environ['OPENROUTER_API_KEY']):
                    with open_account(root,scope,ledger) as (condition,account):
                        _, key = client_settings(scope['projects']['cognee']['judge'])
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
