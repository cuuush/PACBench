"""Continuing-account Pizza Week through the stock MemoryOS OSS REST service."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import time

from provider_adapters.memos_oss_service import backend

from .checkpoint_clock import CheckpointClock
from .gateway_reuse import PersistentGateway, atomic
from .live_dispatch import process_identity
from .workflow_native import NativeAccount, WarmHermes
from .workflow_tracks import MEMOS_OSS_LUNA_COMPAT, MEMOS_OSS_TOOLS, MEMOS_OSS_VERSION, validate_native_deployment


def native_transport_drained(rows):
    requests=[row for row in rows if row['accounting_role']=='memory_provider']
    if any(not row.get('finished') for row in requests):
        return False
    superseded={row['retry_of'] for row in requests if row.get('retry_of')}
    for row in requests:
        if row['id'] in superseded:
            continue
        result=row.get('response_validation') or {}
        if (row.get('http_status')!=200 or result.get('body_complete') is not True
                or result.get('envelope_errors') or result.get('wire_parse_errors')
                or not (result.get('assistant_nonwhitespace_characters') or result.get('tool_call_fragments'))):
            raise RuntimeError('MemOS OSS native inference ended without usable model output')
    return True


class MemosAccount(NativeAccount):
    def submit(self, doc):
        return self.witness.submit(doc)

    def _post_answer_inventory(self, barrier):
        return barrier['readback']['source_files']

    def observe_answer(self, trace):
        sid = trace['session_id']
        prompt = next(row['content'] for row in reversed(trace['history']) if row['role']=='user')
        nonce,={row['turn_nonce'] for row in trace['model_requests']}
        self.witness.current_capture = sid,prompt,trace['answer'],trace['question_at'],nonce
        surface = super().observe_answer(trace)
        results = {result['tool_call_id']:result for result in surface['tool_results']}
        for call in surface['tool_provenance']:
            if call['tool'] in ('memos_get','memos_search'):
                result = results[call['tool_call_id']]
                if result.get('success') and self.witness.opened_file_source(call['tool'],result):
                    call['category'] = 'source_evidence'
        return surface


class MemosWitness:
    def __init__(self, root, owner, client, service, publish, pulls, docs, ledger):
        self.root, self.owner, self.client = root, owner, client
        self.service, self.publish, self.pulls = service, publish, pulls
        self.docs = tuple(docs)
        self.cutoffs = tuple(dict.fromkeys(d.cutoff for d in docs))
        self.ledger = ledger
        self.cutoff = None
        self.observed = {}
        self.pending = None
        self.captures = {}
        self.current_capture = None

    def verify_deployment(self, condition, identity):
        if (identity!=str(self.root) or self.owner.provider!='memos' or
                process_identity(self.service['process']['pid'])!=self.service['process']):
            raise ValueError('MemOS OSS native process changed')
        proof = dict(account=identity,condition=vars(condition),store_identity=str(self.root/'memos-oss-service'),
            synthetic_source_provenance='Committed Pizza Week v2 mixed nightly files',
            observed_version=MEMOS_OSS_VERSION,frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(MEMOS_OSS_TOOLS),native_processes={'memory':self.service['process'],
                'driver':process_identity(self.owner.witness['pid'])},
            native_rest_revision=self.service['revision'],
            hermes_rest_binding=self.owner.pins['memos_oss_hermes_files'])
        validate_native_deployment(condition,proof)
        return proof

    def control_readback(self):
        rows=self.client.inventory()
        if rows:
            raise ValueError('MemOS OSS native user contains prior memory')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_user':self.client.user,'memory_nodes':0})

    def idle(self):
        path=self.owner.home/'reuse-observer.json'
        started_ns=time.monotonic_ns()
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if path.is_file():
                gateway=json.loads(path.read_text())
                if (gateway['pid']!=self.owner.witness['pid'] or gateway['start_time']!=self.owner.witness['start_time']
                        or gateway['observer_sha256']!=self.owner.pins['reuse_observer_sha256']):
                    raise ValueError('MemOS OSS gateway observer identity differs')
                native=self.client.scheduler()
                if (gateway['native_failures'] or any(p.get('write_error') for s in gateway['sessions'] for p in s['providers'])
                        or native['failed'] or native['cancelled']):
                    raise RuntimeError('MemOS OSS native scheduler or capture reported failure')
                if (gateway['observed_monotonic_ns']>=started_ns
                        and all(s['pending_futures']==0 for s in gateway['sessions'])
                        and not any(native[k] for k in ('waiting','pending','in_progress'))
                        and native_transport_drained(self.ledger.snapshot())):
                    return {'gateway_identity':[gateway['pid'],gateway['start_time']],
                            'pending_native_writes':0,'inflight_requests':0,'native_errors':[],
                            'readback':{'gateway':gateway,'native_scheduler':native}}
            time.sleep(.15)
        raise TimeoutError('MemOS OSS native work did not drain before the next action')

    def collection_clock(self, cutoff):
        if self.cutoffs[len({r['at'] for r in self.observed.values()})]!=cutoff:
            raise ValueError('MemOS OSS cutoff is not the next frozen nightly release')
        self.idle()
        self.cutoff=cutoff
        return {'cutoff':cutoff,'as_of':cutoff,'service_at':datetime.now(timezone.utc).isoformat()}

    def submit(self, doc):
        if self.pending is not None or self.docs[len(self.observed)]!=doc or doc.cutoff!=self.cutoff:
            raise ValueError('MemOS OSS submission is outside the frozen current release')
        url=self.publish(doc)
        rows=self.client.post('add',writable_cube_ids=[self.client.user],session_id='file-'+doc.sha256[:24],
                              async_mode='sync',messages=[{'type':'file','file':{
                                  'filename':doc.filename,'file_data':url}}],
                              info={'source_doc_id':doc.sha256,'pizza_release_at':doc.cutoff})
        if not rows or any(r['cube_id']!=self.client.user or not r.get('memory_id') for r in rows):
            raise RuntimeError('MemOS OSS native file acceptance lacks scoped stored memory IDs')
        self.pending=doc,url,tuple(r['memory_id'] for r in rows)
        return {'accepted':True,'native_id':doc.filename,'at':doc.cutoff,
                'request':{'interface':'REST /product/add type file','source_url':url,
                           'source_sha256':doc.sha256,'async_mode':'sync','authored_release':doc.cutoff}}

    def _files(self, inventory):
        locations={}
        for row in inventory:
            meta=row['metadata']
            for source in meta.get('sources',[]):
                if source.get('type')=='file':
                    locations.setdefault(source.get('doc_path'),[]).append((row['id'],meta,source))
        return locations

    def document_readback(self, doc, acceptance):
        active,url,ids=self.pending
        if active!=doc or acceptance['native_id']!=doc.filename or self.pulls.count((doc.filename,doc.sha256))!=1:
            raise ValueError('MemOS OSS current file lacks an exact physical loopback fetch')
        idle=self.idle()
        entries=self._files(self.client.inventory()).get(doc.filename,[])
        pieces={}
        for native_id,meta,source in entries:
            if (native_id not in ids or meta.get('source_doc_id')!=doc.sha256 or meta.get('pizza_release_at')!=doc.cutoff
                    or meta['user_id']!=self.client.user or source['file_info']['file_data']!=url):
                raise ValueError('MemOS OSS graph file provenance differs from the accepted document')
            index=source.get('chunk_index',0)
            previous=pieces.setdefault(index,source['content'])
            if previous!=source['content']:
                raise ValueError('MemOS OSS graph delivered conflicting source chunks')
        if (not pieces or sorted(pieces)!=list(range(len(pieces))) or
                ' '.join(pieces[index] for index in sorted(pieces)).split()!=doc.text.split()):
            raise ValueError('MemOS OSS native file text did not match the frozen source')
        proof={'native_id':doc.filename,'ready':True,'at':doc.cutoff,'submitted_sha256':doc.sha256,
               'readback':{'cube_id':self.client.user,'memory_ids':list(ids),'native_url':url,
                           'native_chunk_sha256':[sha256(pieces[i].encode()).hexdigest() for i in sorted(pieces)],
                           'source_payload_physically_verified':True,'native_whitespace_normalized':True,
                           'scheduler':idle['readback']['native_scheduler']}}
        self.observed[doc.filename]=proof
        self.pending=None
        return proof

    def source_inventory(self):
        return self._verified_source_files(self.client.inventory())

    def _verified_source_files(self, inventory):
        actual=self._files(inventory)
        if set(actual)!=set(self.observed):
            raise ValueError('MemOS OSS cube contains missing or unreleased native source files')
        return list(self.observed)

    def native_write_barrier(self, session, answer):
        sid,prompt,saved,at,nonce=self.current_capture
        if sid!=session or answer!=saved:
            raise ValueError('MemOS OSS expected session or native answer changed')
        question_hash=sha256(prompt.encode()).hexdigest()
        answer_hash=sha256(answer.encode()).hexdigest()
        retained=self.root/'memos-native-captures'/(nonce+'.json')
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            idle=self.idle()
            if not retained.is_file():
                time.sleep(.15)
                continue
            acceptance=json.loads(retained.read_text())
            if (acceptance['session_id'],acceptance['at'],acceptance['cube_id'],
                    acceptance['question_sha256'],acceptance['answer_sha256'])!=(
                    session,at,self.client.user,question_hash,answer_hash):
                raise ValueError('MemOS OSS native working memory receipt belongs to another observation')
            matches={}
            inventory = self.client.inventory()
            for row in inventory:
                meta=row['metadata']
                if (meta.get('session_id'),meta.get('user_id'))!=(session,self.client.user):
                    continue
                for source in meta.get('sources',[]):
                    matches.setdefault(source.get('role'),set()).add(source.get('content'))
            materialized=prompt in matches.get('user',set()) and answer in matches.get('assistant',set())
            extraction=[] if materialized else self.working_memory_processed(acceptance,prompt,answer)
            if materialized or extraction:
                proof={'session_id':session,'pending_writes':0,'readback':{
                    'native_exact_user_sha256':question_hash,'native_exact_assistant_sha256':answer_hash,
                    'native_working_memory_ids':acceptance['memory_ids'],
                    'active_exact_qa_provenance':materialized,
                    'native_extraction_attempts':extraction,
                    'source_files':self._verified_source_files(inventory),
                    'queue':idle['readback']['native_scheduler']}}
                self.captures[session]=proof
                return proof
            time.sleep(.15)
        raise TimeoutError('MemOS OSS native user and assistant capture lacks exact readback')

    def working_memory_processed(self, acceptance, prompt, answer):
        # Stock MEM_READ consumes and deletes its raw working nodes even if it
        # extracts no durable item. Witness the accepted raw write and native read.
        if (not acceptance['memory_ids'] or not set(acceptance['memory_types']) <= {'WorkingMemory','LongTermMemory'}
                or not acceptance['exact_user_in_ack'] or not acceptance['exact_assistant_in_ack']):
            return []
        path=self.root/'workflow/workflow.jsonl'
        dispatch=json.loads(path.read_text().splitlines()[-1])
        if dispatch['phase']!='dispatch' or dispatch['prompt']!=prompt:
            return []
        traces=[]
        for row in self.ledger.snapshot():
            slot=row.get('workflow_slot') or {}
            if (row.get('accounting_role')!='memory_provider' or row.get('http_status')!=200
                    or slot.get('kind')!='answer' or slot.get('question_id')!=dispatch['question']):
                continue
            native=self.root/'memory-api-inputs'/(row['id']+'.json')
            sent=json.loads(native.read_text())
            if sent['request_id']!=row['id'] or sent['project']!=row['project']:
                raise ValueError('MemOS OSS extraction trace belongs to another request')
            if any(isinstance(message.get('content'),str) and prompt in message['content']
                   and answer in message['content'] for message in sent['request']['messages']):
                traces.append(row['id'])
        return traces

    def after_native_close(self, session):
        state=self.idle()['readback']['gateway']
        managers=[r for r in state['sessions'] if r['session_id']==session]
        if not managers or not all(row['shutdown_returned'] and row['clean'] for row in managers):
            raise RuntimeError('MemOS OSS exact logical chat did not close cleanly')
        return self.captures[session]

    def opened_file_source(self, tool, result):
        try:
            data=json.loads(result['text'])
            rows=[data['memory']] if tool=='memos_get' else data['memories']
        except (ValueError,KeyError,TypeError):
            return False
        for row in rows:
            if not row or row['metadata'].get('user_id')!=self.client.user:
                continue
            for item in row['metadata'].get('sources',[]):
                witness=self.observed.get(item.get('doc_path'))
                if witness and item.get('type')=='file' and item.get('content'):
                    digest=sha256(item['content'].encode()).hexdigest()
                    if digest in witness['readback']['native_chunk_sha256']:
                        return True
        return False

    tool_boundaries = staticmethod(lambda _: {})

    def request_lineage(self, requests):
        ids={request['request_id'] for request in requests}
        rows=self.ledger.snapshot()
        for row in reversed(rows):
            if row['id'] in ids and row.get('retry_of'):
                ids.add(row['retry_of'])
        return [{'id':r['id'],'parent':r.get('retry_of'),'operation':r['dispatch_context']['operation_id'],
                 'origin':'harness' if r.get('retry_of') else 'unknown'} for r in rows if r['id'] in ids]

    def close(self):
        self.idle()


@contextmanager
def open_account(root, scope, ledger, source, qualification):
    import os
    from . import harness, workflow_capability as capability
    from .gateway_client import client_settings
    from .openrouter_route import ENDPOINT, MODEL
    from .workflow_runner import Condition

    checkout=Path(__file__).resolve().parents[1]
    roles=scope['projects']['memos']
    driver_base,driver_key=client_settings(roles['driver'])
    memory_base,memory_key=client_settings(roles['memory'])
    docs,_,_=capability.schedule(scope['spec_sha256'],scope['canary'])
    home=root/'hermes-home'
    home.mkdir(mode=0o700)
    plugins=home/'plugins'
    plugins.mkdir(mode=0o700)
    fallback=root/'fallback-home'
    fallback.mkdir(mode=0o700)
    work=root/'empty-project'
    work.mkdir(mode=0o700)
    shutil.copytree(checkout/'provider_adapters/memos_oss_hermes',plugins/'memososs',
                    ignore=shutil.ignore_patterns('__pycache__'))
    with backend(root,source,qualification,MODEL,memory_base,memory_key) as (service,publish,pulls):
        config=home/'memos-oss.json'
        atomic(config,{key:service[key] for key in ('endpoint','user','local_api_token')})
        atomic(home/'config.yaml',{'model':{'provider':'custom','default':MODEL,'base_url':driver_base,
              'api_mode':'chat_completions'},'memory':{'provider':'memososs','memory_enabled':False,
              'user_profile_enabled':False},'plugins':{'enabled':['memososs']},
              'agent':{'disabled_toolsets':['terminal','file','web','browser','session_search',
                 'delegation','code_execution','skills','todo','clarify']}})
        env={**os.environ,'PACBENCH_PROVIDER_ONLY':'1','OPENAI_BASE_URL':driver_base,'OPENAI_API_KEY':driver_key,
             'HERMES_INFERENCE_PROVIDER':'custom','PACBENCH_MEMOS_OSS_CONFIG':str(config),
             'PACBENCH_MEMOS_OSS_SOURCE':str(source),'PACBENCH_WORKFLOW_CAPTURE':scope['protocol'],
             'PACBENCH_API_INPUT_TRACE':str(root/'api-inputs.jsonl'),'HOME':str(fallback)}
        child=harness.turn_env(docs[0].cutoff,home,','.join(MEMOS_OSS_TOOLS),env)
        clock=CheckpointClock(home,docs[0].cutoff,scope['spec_sha256'])
        with PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=work,
                               checkpoint=scope['spec_sha256'],observation_clock=clock,provider='memos') as owner:
            condition=Condition('memos','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
                MODEL,ENDPOINT,'Stock MemoryOS OSS local REST graph; PACBench Hermes lifecycle binding',
                MEMOS_OSS_VERSION,owner.pins['hermes_isolation_sha256'],
                'Physical MemOS and scheduler; authored Hermes observation clock',MEMOS_OSS_TOOLS)
            from provider_adapters.memos_oss_hermes import ScopedCube
            client=ScopedCube(service)
            witness=MemosWitness(root,owner,client,service,publish,pulls,docs,ledger)
            yield condition,MemosAccount(str(root),'memos',client.user,client,WarmHermes(owner,clock,witness.idle),witness)


def check_canary(root):
    """Recheck the retained, separately receipted MemOS OSS qualification."""
    from provider_adapters.memos_oss_service import REVISION

    from .workflow_document_canary import audit_document_canary, verify_listener_stopped

    audit = audit_document_canary(root, 'memos')
    root = audit.root
    setup = json.loads((root/'native-server-setup.json').read_text())
    shutdown = json.loads((root/'native-service-stop.json').read_text())
    if (audit.condition['version'] != MEMOS_OSS_VERSION
            or setup['revision'] != REVISION or setup['process'] != audit.deployment['native_processes']['memory']
            or shutdown != {'state': 'verified-after-exit', 'process': setup['process']}
            or any(row['accounting_role'] == 'memory_provider' and (
                row.get('native_request_compatibility') != MEMOS_OSS_LUNA_COMPAT or row['http_status'] != 200)
                for row in audit.receipts)
            or any(row['evidence']['readback']['source_payload_physically_verified'] != True
                   for row in audit.events if row['phase'] == 'readback')):
        raise ValueError('MemOS OSS canary lacks its native payload or shutdown proof')
    verify_listener_stopped(setup['endpoint'], 'MemOS OSS')
    return audit.bindings


def run(root, confirmation, source, qualification, *, canary, after_canary=None):
    from .workflow_document_lifecycle import run_document_week

    def open_native_account(root, scope, ledger):
        return open_account(root, scope, ledger, source, qualification)

    return run_document_week(
        root, confirmation, provider='memos', canary=canary, after_canary=after_canary,
        check_canary=check_canary, open_account=open_native_account, fresh_account_error='Expected a fresh MemoryOS OSS Luna account',
        extra_environment={'PACBENCH_MEMOS_OSS_LUNA_COMPAT': MEMOS_OSS_LUNA_COMPAT}, cleanup_after_direct=True)
