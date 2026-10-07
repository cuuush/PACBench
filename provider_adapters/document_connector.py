"""Transport raw daily documents into real provider APIs, never an answer key."""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


class ExtractionLog(logging.Handler):
    """Observe native errors; never turn swallowed parser failures into readiness."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.errors=[]

    def emit(self,record):
        self.errors.append({'logger':record.name,'message':record.getMessage()})


def mem0(source, root, at, processing_at):
    from mem0 import Memory
    from pacbench.gateway_client import validate_route
    cfg=json.loads((root/'hermes-home/mem0.json').read_text())
    validate_route(cfg['oss']['llm']['config']['openai_base_url'],os.environ['OPENAI_API_KEY'])
    # The SDK explicitly documents string input and infer=True. No plugin sync
    # truncation, fake user/assistant pair, custom extraction prompt or infer=False.
    model_calls=[]
    def observe(llm,response,request):
        model_calls.append({'request':request,'response':response.model_dump(mode='json')})
    cfg['oss']['llm']['config']['response_callback']=observe
    memory=Memory.from_config(cfg['oss'])
    validate_route(str(memory.llm.client.base_url).rstrip('/'),memory.llm.client.api_key)
    errors=ExtractionLog()
    logging.getLogger().addHandler(errors)
    try:
        result=memory.add((source/'messages.txt').read_text(),user_id=cfg['user_id'],
                          infer=True,metadata={'source':'daily-transcript','document_id':source.name,
                                               'collected_at':at})
    finally:
        logging.getLogger().removeHandler(errors)
        folder=root/'ingestion-model-calls'
        folder.mkdir(exist_ok=True)
        (folder/(source.name+'.json')).write_text(json.dumps(model_calls,ensure_ascii=False,indent=2)+'\n')
    incomplete=any(c.get('finish_reason')=='length' for call in model_calls for c in call['response'].get('choices',[]))
    return {'ready':not errors.errors and not incomplete,'interface':'mem0.Memory.add(str,infer=True)',
            'result':result,'native_errors':errors.errors,'model_output_truncated':incomplete,
            'model_call_record':str(folder/(source.name+'.json')),
            'readiness':'Synchronous OSS add with no native error or truncated model response'}


def memcal(source, root, at, processing_at):
    sys.path.insert(0,os.environ['MEMCAL_SRC'])
    from memcal import archive,brief,config,db,identity
    from memcal.sources import imessage
    from memcal.dream.run import dream
    from pacbench.gateway_client import validate_route
    if not (source/'chat.db').exists():
        raise ValueError('Native source fixture unavailable for this channel; no archive-write fallback')
    messages=json.loads((source/'messages.json').read_text())
    if any(m['channel']!='imessage' for m in messages):
        raise ValueError('This Memcal connector supports iMessage capture only; mixed channels need a native source connector')
    # Normal filesystem locations under the isolated HOME; native contacts loader.
    target=Path.home()/'Library/Application Support/AddressBook/Sources/00000000-0000-0000-0000-000000000001/AddressBook-v22.abcddb'
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source/'chat.contacts.abcddb',target)
    chat=Path.home()/'Library/Messages/chat.db'
    chat.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source/'chat.db',chat)
    cfg=config.load()
    if cfg.llm_provider!='openai-compatible':raise ValueError('Memcal inference must use gateway')
    validate_route(cfg.openai_base_url,cfg.openai_api_key)
    conn=db.open_db(cfg.db_path)
    try:
        os.environ['MEMCAL_TODAY']=at
        linked,contact_report=identity.import_contacts(conn)
        collection=archive.open_collection(conn,mode='imessage')
        report=imessage.ingest(conn,collection_id=collection)  # actual native default path
        if report.error:raise RuntimeError(report.error)
        expected=len(messages)
        if report.read!=expected:
            raise RuntimeError(f'Native reader consumed {report.read}/{expected} new messages')
        os.environ['MEMCAL_TODAY']=processing_at
        result=dream(conn,cfg)  # product defaults, ordinary nightly processing
        brief.write(conn,cfg)
        # Read the product's own bundle/call records after processing. No
        # monkeypatching the packer, extra extraction or inferred debug facts.
        native_bundles=[dict(row) for row in conn.execute('SELECT * FROM run_bundles WHERE run_id=?',(result.run_id,))]
        native_calls=[]
        for path in sorted((cfg.home/'calls'/f'run-{result.run_id:04d}').glob('*.json')):
            data=json.loads(path.read_text())
            native_calls.append({k:v for k,v in data.items() if k not in ('reasoning','encrypted_content')})
        folder=root/'ingestion-model-calls';folder.mkdir(exist_ok=True)
        diagnostic=folder/(source.name+'.json')
        diagnostic.write_text(json.dumps({'bundles':native_bundles,'calls':native_calls},ensure_ascii=False,indent=2)+'\n')
        return {'ready':not bool(result.errors),'interface':'native iMessage reader + native nightly dream',
                'contacts':{'linked':linked,'report':contact_report},'ingest':asdict(report),
                'bundles':native_bundles,'model_call_record':str(diagnostic),
                'processing':{'run_id':result.run_id,'errors':result.errors,'report':result.report()},
                'readiness':'Native dream completion without errors'}
    finally:conn.close()


def hindsight(source,root,at,processing_at):
    from plugins.memory.hindsight import HindsightMemoryProvider
    from pacbench.gateway_client import validate_route
    cfg=json.loads((root/'hermes-home/hindsight/config.json').read_text())
    validate_route(cfg['llm_base_url'],os.environ['HINDSIGHT_LLM_API_KEY'])
    provider=HindsightMemoryProvider()
    provider.initialize('document-import',hermes_home=str(root/'hermes-home'),platform='cli')
    try:
        # Use the native plugin's configured recommended embedded client and bank,
        # but its documented retain API directly; no conversation hooks or turn.
        client=provider._get_client()
        result=provider._run_sync(client.aretain(bank_id=cfg['bank_id'],content=(source/'messages.txt').read_text(),
            timestamp=min(datetime.fromisoformat(m['written_at']) for m in json.loads((source/'messages.json').read_text())),
            document_id=source.name,metadata={'source':'daily-transcript','collected_at':at},retain_async=False))
        # Retain completes fact extraction; normal observation consolidation is
        # a separate queued operation. Observe its real status before recall.
        # No retry of the source, extra Hermes turn, recall-type change or delay
        # chosen from an answer. The shared ingestion deadline still applies.
        deadline=time.monotonic()+180
        statuses=[]
        while True:
            status=provider._run_sync(client.operations.list_operations(bank_id=cfg['bank_id'],type='consolidation',limit=100))
            data=status.model_dump(mode='json');statuses.append(data)
            operations=data['operations']
            pending=any(o['status'] in {'pending','processing'} for o in operations)
            failed=data['total']>len(operations) or any(
                o['status'] not in {'pending','processing','completed'} for o in operations)
            if failed or not pending or time.monotonic()>=deadline:break
            time.sleep(1)
        return {'ready':not failed and not pending,'interface':'Hindsight.retain(content,document_id,retain_async=False)',
                'result':result.model_dump(mode='json') if hasattr(result,'model_dump') else str(result),
                'processing_status':statuses,
                'readiness':'Native retain and observation consolidation completion via Operations API'}
    finally:provider.shutdown()


def openviking(source,root,at,processing_at):
    from plugins.memory.openviking import OpenVikingMemoryProvider
    from pacbench.gateway_client import validate_route
    cfg=json.loads((root/'ov.conf').read_text())
    validate_route(cfg['vlm']['api_base'],cfg['vlm']['api_key'])
    p=OpenVikingMemoryProvider()
    p.initialize('document-import',hermes_home=str(root/'hermes-home'),platform='cli')
    try:
        # The native plugin's real upload/client preserves file bytes. Keep the
        # full API response (its tool wrapper discards processing evidence).
        upload=p._client.upload_temp_file(source/'messages.txt')
        result=p._client.post('/api/v1/resources',{'temp_file_id':upload,
            'source_name':source.name+'.txt','wait':True,'timeout':210},timeout=230)
        native=result.get('result',{})
        queues=native.get('queue_status')
        ready=bool(native.get('root_uri')) and queues is not None and not any(
            q.get('errors') for q in queues.values() if isinstance(q,dict))
        return {'ready':ready,'interface':'OpenViking temp_upload + resources(wait=True)',
                'result':result,'readiness':'Native completed semantic/vector processing with queue status and no errors'}
    finally:p.shutdown()


def byterover(source,root,at,processing_at):
    from pacbench.gateway_client import validate_route
    cfg=json.loads((Path(os.environ['XDG_CONFIG_HOME'])/'brv/providers.json').read_text())
    active=cfg['providers'].get('openai-compatible',{})
    if cfg['activeProvider']!='openai-compatible' or active.get('activeModel')!='muse-spark-1.3-contributor':
        raise ValueError('ByteRover inference must use configured Contributor')
    validate_route(active['baseUrl'],os.environ['OPENAI_COMPATIBLE_API_KEY'])
    project=root/'hermes-home/byterover'
    target=project/'imports'/source.name/'messages.txt';target.parent.mkdir(parents=True)
    shutil.copyfile(source/'messages.txt',target)
    # No curate prompt: use the CLI's documented raw-file input and its own
    # ordinary curation. Do not auto-approve pending native reviews or cloud sync.
    p=subprocess.run(['brv','curate','--files',str(target.relative_to(project)),'--format','json'],
        cwd=project,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=220)
    # The real CLI emits progress plus a final JSON record, not one JSON value.
    decoder=json.JSONDecoder();remaining=p.stdout.strip();events=[]
    while remaining:
        record,end=decoder.raw_decode(remaining);events.append(record)
        remaining=remaining[end:].lstrip()
    finals=[e for e in events if e.get('command')=='curate' and
            e.get('data',{}).get('status') in {'completed','error','cancelled'}]
    result=finals[-1] if finals else {}
    return {'ready':p.returncode==0 and result.get('success') is True and
            result.get('data',{}).get('status')=='completed' and not result.get('data', {}).get('pendingReview'),
            'interface':'brv curate --files (no custom prompt)',
            'result':result,'events':events,'stdout':p.stdout,'stderr':p.stderr,'readiness':'Native curation completion; no pending review'}


def honcho(source,root,at,processing_at):
    from honcho import Honcho
    import hashlib
    cfg=json.loads((root/'hermes-home/honcho.json').read_text())
    if not cfg['baseUrl'].startswith('http://127.0.0.1:'):
        raise ValueError('Honcho ingestion requires the run-owned local server')
    host=cfg['hosts']['hermes']
    client=Honcho(base_url=cfg['baseUrl'],workspace_id=host['workspace'],environment='local',max_retries=0,timeout=30)
    messages=json.loads((source/'messages.json').read_text())
    groups={}
    for message in messages:groups.setdefault((message['channel'],message['thread']),[]).append(message)
    acknowledgments=[]
    for identity,batch in groups.items():
        session=client.session('source-'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24])
        peers={}
        for m in batch:
            peer_id=host['peerName'] if m['from_owner'] else m['sender']
            peers[peer_id]=client.peer(peer_id)
        # Real source participants, not a fabricated user/assistant exchange.
        session.add_peers(list(peers.values()))
        pending=[]
        for m in batch:
            peer_id=host['peerName'] if m['from_owner'] else m['sender']
            pending.append(peers[peer_id].message(m['text'],created_at=m['written_at'],
                metadata={k:v for k,v in m.items() if k!='text'}))
        response=session.add_messages(pending)
        if len(response)!=len(batch):raise RuntimeError('Honcho did not acknowledge the full batch')
        for actual,sent in zip(response,pending):
            if (actual.peer_id!=sent.peer_id or actual.content!=sent.content or
                actual.created_at!=sent.created_at or
                any(actual.metadata.get(k)!=v for k,v in sent.metadata.items())):
                raise RuntimeError('Honcho source acknowledgment differs from the submitted message')
        acknowledgments.append({'session_id':session.id,'messages':[m.model_dump(mode='json') if hasattr(m,'model_dump') else {
            'id':m.id,'session_id':m.session_id,'peer_id':m.peer_id,'content':m.content,
            'created_at':m.created_at.isoformat(),'metadata':m.metadata} for m in response]})
    statuses=[];deadline=time.monotonic()+180
    while True:
        status=client.queue_status().model_dump(mode='json');statuses.append(status)
        done=status['pending_work_units']==0 and status['in_progress_work_units']==0
        if done or time.monotonic()>=deadline:break
        time.sleep(1)
    return {'ready':done,'interface':'Native Honcho session.add_messages(real source peers and timestamps)',
        'result':acknowledgments,'processing_status':statuses,
        'readiness':('Native message acknowledgments and processing queue drained' if done
                     else 'Native messages acknowledged; processing queue did not drain before deadline')}


def supermemory(source,root,at,processing_at):
    from supermemory import Supermemory
    from plugins.memory.supermemory import _load_supermemory_config, _resolve_container_tag
    cfg=_load_supermemory_config(str(root/'hermes-home'))
    tag=_resolve_container_tag(cfg['container_tag'],'default')
    if not cfg['base_url'].startswith('http://127.0.0.1:'):
        raise ValueError('Supermemory document ingest requires the run-owned local server')
    client=Supermemory(api_key=os.environ['SUPERMEMORY_API_KEY'],base_url=cfg['base_url'],max_retries=0,timeout=30)
    result=client.documents.add(content=(source/'messages.txt').read_text(),
        container_tags=[tag],custom_id=source.name,
        metadata={'source':'daily-transcript','collected_at':at})
    statuses=[];deadline=time.monotonic()+180
    while True:
        status=client.documents.get(result.id).model_dump(mode='json');statuses.append(status)
        state=status.get('status')
        if state in {'done','failed','error'} or time.monotonic()>=deadline:break
        time.sleep(1)
    return {'ready':state=='done','interface':'Supermemory SDK documents.add(raw content) + documents.get',
            'result':result.model_dump(mode='json'),'container_tag':tag,'processing_status':statuses,
            'readiness':'Native document processing status done'}


def main(args=None):
    provider,src,arm,at,processing_at=args or sys.argv[1:]
    dispatch={'mem0':mem0,'memcal':memcal,'hindsight':hindsight,'openviking':openviking,'byterover':byterover,'supermemory':supermemory,'honcho':honcho}
    result=dispatch[provider](Path(src),Path(arm),at,processing_at)
    print(json.dumps(result,ensure_ascii=False,default=lambda value:sorted(value) if isinstance(value,set) else str(value)))
    return 0 if result['ready'] else 1


if __name__=='__main__':raise SystemExit(main())
