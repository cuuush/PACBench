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
        expected=len(json.loads((source/'messages.json').read_text()))
        if report.read!=expected:
            raise RuntimeError(f'Native reader consumed {report.read}/{expected} new messages')
        os.environ['MEMCAL_TODAY']=processing_at
        result=dream(conn,cfg)  # product defaults, ordinary nightly processing
        brief.write(conn,cfg)
        return {'ready':not bool(result.errors),'interface':'native iMessage reader + native nightly dream',
                'contacts':{'linked':linked,'report':contact_report},'ingest':asdict(report),
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
    result=json.loads(p.stdout)
    return {'ready':p.returncode==0 and result.get('success') is True and not result.get('data', {}).get('pendingReview'),
            'interface':'brv curate --files (no custom prompt)',
            'result':result,'stderr':p.stderr,'readiness':'Native curation completion; no pending review'}


def main(args=None):
    provider,src,arm,at,processing_at=args or sys.argv[1:]
    dispatch={'mem0':mem0,'memcal':memcal,'hindsight':hindsight,'openviking':openviking,'byterover':byterover}
    result=dispatch[provider](Path(src),Path(arm),at,processing_at)
    print(json.dumps(result,ensure_ascii=False,default=lambda value:sorted(value) if isinstance(value,set) else str(value)))
    return 0 if result['ready'] else 1


if __name__=='__main__':raise SystemExit(main())
