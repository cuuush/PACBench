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

HONCHO_MESSAGE_LIMIT = 100  # Native MessageBatchCreateParams limit, including SDK 2.2.0.


def native_error_receipt(error):
    """Native exception bodies may echo complete sources or credentials."""
    import hashlib
    import re
    name=type(error).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}',name):name='NativeError'
    value={'error_class':name,'error_sha256':hashlib.sha256(str(error).encode()).hexdigest()}
    status=getattr(error,'status_code',getattr(error,'status',None))
    if type(status) is int and 100<=status<=599:value['http_status']=status
    if status in (401,403):value['authentication_blocked']=True
    body=getattr(error,'body',None)
    details=body.get('detail',[]) if isinstance(body,dict) else []
    for detail in details if isinstance(details,list) else []:
        if (isinstance(detail,dict) and detail.get('type')=='too_long' and
                detail.get('loc')==['body','messages'] and isinstance(detail.get('ctx'),dict)):
            value['validation_code']='too_long'
            for field in ('max_length','actual_length'):
                count=detail['ctx'].get(field)
                if type(count) is int and 0<=count<=10_000_000:value[field]=count
            break
    return value


def add_honcho_messages(session, messages, checkpoint):
    """Ordered native requests, without retrying a possibly committed chunk."""
    accepted=[]
    for start in range(0,len(messages),HONCHO_MESSAGE_LIMIT):
        chunk=messages[start:start+HONCHO_MESSAGE_LIMIT]
        index=start//HONCHO_MESSAGE_LIMIT
        checkpoint(accepted,index,chunk,'sending',None)
        try:
            response=session.add_messages(chunk)
        except Exception as error:
            failure={**native_error_receipt(error),'chunk_index':index,
                     'request_messages':len(chunk),'acknowledged_messages':len(accepted)}
            failure['commit_state']=('rejected' if failure.get('http_status') in (400,401,403,404,413,422)
                                     else 'unknown')
            checkpoint(accepted,index,chunk,'failed',failure)
            return accepted,failure
        try:
            valid=isinstance(response,list) and len(response)==len(chunk) and all(
                actual.session_id==session.id and actual.peer_id==sent.peer_id and
                actual.content==sent.content and actual.created_at==sent.created_at and
                all(actual.metadata.get(k)==v for k,v in sent.metadata.items())
                for actual,sent in zip(response,chunk))
            if valid:
                identifiers=[m.id for m in response]
                valid=(all(isinstance(i,str) and i for i in identifiers) and
                    len(set(identifiers))==len(identifiers) and
                    not {m.id for m in accepted}.intersection(identifiers))
        except (AttributeError,TypeError):valid=False
        if not valid:
            import hashlib
            failure={'error_class':'AcknowledgmentMismatch','chunk_index':index,
                'request_messages':len(chunk),'acknowledged_messages':len(accepted),'commit_state':'unknown',
                'received_messages':len(response) if isinstance(response,list) else None,
                'unverified_response_sha256':hashlib.sha256(repr(response).encode()).hexdigest()}
            checkpoint(accepted,index,chunk,'failed',failure)
            return accepted,failure
        accepted.extend(response)
        checkpoint(accepted,index,chunk,'acknowledged',None)
    return accepted,None


def verify_honcho_delivery(path, *, complete=False):
    """Bind the small manifest to every partial native acknowledgment receipt."""
    import hashlib
    import re
    path=Path(path);manifest=json.loads(path.read_text())
    if manifest.get('version')!='honcho-native-delivery-v1':raise ValueError('Unknown native delivery receipt')
    if complete and manifest.get('state')!='submitted':raise ValueError('Native delivery is incomplete')
    if complete and (not manifest['sessions'] or
            sum(s['source_messages'] for s in manifest['sessions'])!=manifest['source_messages']):
        raise ValueError('Native delivery omits sessions')
    for session in manifest['sessions']:
        name=session['session_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',name):raise ValueError('Unsafe native session receipt')
        count=0
        if complete and list(session['chunks'])!=[f'{i:06d}.json' for i in range(len(session['chunks']))]:
            raise ValueError('Native chunk order is incomplete')
        for filename,digest in session['chunks'].items():
            chunk=path.parent/name/filename
            if not re.fullmatch(r'[0-9]{6}\.json',filename) or chunk.is_symlink():
                raise ValueError('Unsafe native chunk receipt')
            raw=chunk.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Native chunk receipt changed')
            value=json.loads(raw)
            if value['session_id']!=name:raise ValueError('Native chunk session differs')
            if complete and (value['state']!='acknowledged' or
                    [m['source_id'] for m in value['acknowledged']]!=value['source_ids']):
                raise ValueError('Native chunk acknowledgment is incomplete')
            count+=len(value['acknowledged'])
        if complete and count!=session['source_messages']:raise ValueError('Native delivery omits messages')
    return manifest


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
    from pacbench.openrouter_direct import active
    if active():
        # The bare SDK prioritizes this environment key over config.api_key.
        # Hermes' shipped DirectOpenAILLM instead uses the explicit config key.
        os.environ['OPENROUTER_API_KEY']=cfg['oss']['llm']['config']['api_key']
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
    from memcal import archive,brief,config,db,identity,sources
    from memcal.dream.run import dream
    from pacbench.gateway_client import validate_route
    if not (source/'chat.db').exists():
        raise ValueError('Native source fixture unavailable for this channel; no archive-write fallback')
    messages=json.loads((source/'messages.json').read_text())
    if any(m['channel'] not in {'imessage','email'} for m in messages):
        raise ValueError('No native fixture connector for this source channel')
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
        os.environ['MEMCAL_TODAY']=processing_at
        linked,contact_report=identity.import_contacts(conn)
        collection=archive.open_collection(conn,mode='cli')
        mail_report=None
        mail_transport=None
        collection_error=None
        try:
            report=sources.catch_up(sources.get('imessage',cfg),conn,cfg,collection_id=collection)
            if report.more and not report.error:
                collection_error='native collection incomplete'
                raise RuntimeError('iMessage collection incomplete')
            if report.error:raise RuntimeError(report.error)
            expected=sum(m['channel']=='imessage' for m in messages)
            if report.read!=expected:
                raise RuntimeError(f'Native reader consumed {report.read}/{expected} new messages')
            if any(m['channel']=='email' for m in messages):
                from pacbench.email_fixture import MailboxServer
                with MailboxServer(source/'mailbox.json') as mailbox:
                    cfg.env.update(PROTON_BRIDGE_HOST='127.0.0.1',PROTON_BRIDGE_PORT=str(mailbox.port),
                                   PROTON_BRIDGE_USER=mailbox.user,PROTON_BRIDGE_PASSWORD=mailbox.password)
                    mail_report=sources.catch_up(sources.get('email',cfg),conn,cfg,collection_id=collection)
                    mail_transport={'interface':'TLS IMAP4rev1 Inbox/Sent fixture',
                                    'commands':mailbox.commands,'account':mailbox.user}
                if mail_report.more and not mail_report.error:
                    collection_error='native collection incomplete'
                    raise RuntimeError('email collection incomplete')
                if mail_report.error:raise RuntimeError(mail_report.error)
                expected_mail=sum(m['channel']=='email' for m in messages)
                if mail_report.read!=expected_mail:
                    raise RuntimeError(f'Native IMAP reader consumed {mail_report.read}/{expected_mail} new emails')
            expected_channels={'imessage'} | ({'email'} if mail_report is not None else set())
            persisted=conn.execute('SELECT collection_id,channel,status FROM collection_sources WHERE collection_id=?',
                                   (collection,)).fetchall()
            if (len(persisted)!=len(expected_channels) or
                {row['channel'] for row in persisted}!=expected_channels or
                any(row['collection_id']!=collection or row['status']!='complete' for row in persisted)):
                raise RuntimeError('Native collection outcomes missing or incomplete')
        except Exception:
            collection_error=collection_error or 'native collection failed'
            raise
        finally:
            archive.close_collection(conn,collection,error=collection_error)
        collection_record=archive.collections(conn,limit=1)[0]
        result=dream(conn,cfg)  # product defaults, ordinary nightly processing
        brief.write(conn,cfg)
        event_state=[dict(row) for row in conn.execute(
            'SELECT key,date,time,title,location,status,participants,subject FROM events ORDER BY key')]
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
        return {'ready':not bool(result.errors),'interface':'native source readers + native nightly dream',
                'collector_protocol':'native-cli-collection-v1',
                'collection':collection_record,
                'contacts':{'linked':linked,'report':contact_report},'ingest':asdict(report),
                **({'email_ingest':asdict(mail_report),'email_transport':mail_transport} if mail_report else {}),
                'bundles':native_bundles,'model_call_record':str(diagnostic),
                'native_event_state':event_state,
                'processing':{'run_id':result.run_id,'errors':result.errors,'report':result.report()},
                'readiness':'Native dream completion without errors'}
    finally:conn.close()


def hindsight_operations(provider,client,bank_id,deadline):
    """Read all native operation pages; completed history is not a failure."""
    pages=[];operations=[];total=None
    while total is None or len(operations)<total:
        if time.monotonic()>=deadline:break
        status=provider._run_sync(client.operations.list_operations(
            bank_id=bank_id,type='consolidation',limit=100,offset=len(operations)))
        page=status.model_dump(mode='json');pages.append(page)
        if total is None:total=page['total']
        if page['total']!=total:break  # A shifting listing cannot prove readiness.
        rows=page['operations']
        if not rows:break
        operations.extend(rows)
    complete=(total is not None and len(operations)==total and
        len({o['id'] for o in operations})==len(operations))
    return {'pages':pages,'total':total,'operations':operations,'pagination_complete':complete}


def hindsight(source,root,at,processing_at):
    from pacbench.provider_loader import provider_module
    HindsightMemoryProvider = provider_module("hindsight").HindsightMemoryProvider
    from pacbench.gateway_client import validate_route
    from pacbench.document_ingestion import PROCESSING_TIMEOUT_S
    deadline=time.monotonic()+PROCESSING_TIMEOUT_S
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
        statuses=[]
        while True:
            data=hindsight_operations(provider,client,cfg['bank_id'],deadline)
            if not statuses or data!=statuses[-1]:statuses.append(data)
            operations=data['operations']
            pending=any(o['status'] in {'pending','processing'} for o in operations)
            failed=not data['pagination_complete'] or any(
                o['status'] not in {'pending','processing','completed'} for o in operations)
            if failed or not pending or time.monotonic()>=deadline:break
            time.sleep(1)
        return {'ready':not failed and not pending,'interface':'Hindsight.retain(content,document_id,retain_async=False)',
                'result':result.model_dump(mode='json') if hasattr(result,'model_dump') else str(result),
                'processing_status':statuses,
                'readiness':'Native retain and observation consolidation completion via Operations API'}
    finally:provider.shutdown()


def openviking(source,root,at,processing_at):
    from pacbench.provider_loader import provider_module
    OpenVikingMemoryProvider = provider_module("openviking").OpenVikingMemoryProvider
    from pacbench.gateway_client import validate_route
    cfg=json.loads((root/'ov.conf').read_text())
    validate_route(cfg['vlm']['api_base'],cfg['vlm']['api_key'])
    p=OpenVikingMemoryProvider()
    p.initialize('document-import',hermes_home=str(root/'hermes-home'),platform='cli')
    try:
        # The native plugin's real upload/client preserves file bytes. Keep the
        # full API response (its tool wrapper discards processing evidence).
        upload=p._client.upload_temp_file(source/'messages.txt')
        from pacbench.document_ingestion import PROCESSING_TIMEOUT_S
        result=p._client.post('/api/v1/resources',{'temp_file_id':upload,
            'source_name':source.name+'.txt','wait':True,'timeout':PROCESSING_TIMEOUT_S-60},
            timeout=PROCESSING_TIMEOUT_S-30)
        native=result.get('result',{})
        queues=native.get('queue_status')
        ready=(native.get('status')=='success' and not native.get('errors') and
            bool(native.get('root_uri')) and isinstance(queues,dict) and bool(queues) and not any(
                not isinstance(q,dict) or q.get('errors') or q.get('error_count',0)
                for q in queues.values()))
        return {'ready':ready,'interface':'OpenViking temp_upload + resources(wait=True)',
                'result':result,'readiness':'Native completed semantic/vector processing with queue status and no errors'}
    finally:p.shutdown()


def byterover(source,root,at,processing_at):
    from pacbench.gateway_client import validate_route
    cfg=json.loads((Path(os.environ['XDG_CONFIG_HOME'])/'brv/providers.json').read_text())
    active=cfg['providers'].get('openai-compatible',{})
    expected_model = 'muse-spark-1.3-contributor'
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        from pacbench.openrouter_route import MODEL
        expected_model = MODEL
    if cfg['activeProvider']!='openai-compatible' or active.get('activeModel')!=expected_model:
        raise ValueError('ByteRover inference must use the configured route model')
    validate_route(active['baseUrl'],os.environ['OPENAI_COMPATIBLE_API_KEY'])
    project=root/'hermes-home/byterover'
    target=project/'imports'/source.name/'messages.txt';target.parent.mkdir(parents=True)
    shutil.copyfile(source/'messages.txt',target)
    # No curate prompt: use the CLI's documented raw-file input and its own
    # ordinary curation. Native review is retrospective (reject restores files
    # from backup), not a barrier to reading already-applied changes. Leave the
    # review log unchanged; do not approve reviews or perform cloud sync.
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
            result.get('data',{}).get('status')=='completed',
            'interface':'brv curate --files (no custom prompt)',
            'result':result,'events':events,'stdout':p.stdout,'stderr':p.stderr,
            'readiness':'Native curation completed; retrospective review log preserved without approval'}


def honcho(source,root,at,processing_at):
    from honcho import Honcho
    from honcho.api_types import SessionPeerConfig
    from pacbench.provider_loader import provider_module
    HonchoClientConfig = provider_module("honcho", "client").HonchoClientConfig
    import hashlib
    cfg=json.loads((root/'hermes-home/honcho.json').read_text())
    if not cfg['baseUrl'].startswith('http://127.0.0.1:'):
        raise ValueError('Honcho ingestion requires the run-owned local server')
    host=cfg['hosts']['hermes']
    native_config=HonchoClientConfig.from_global_config()
    observer=host['aiPeer']
    client=Honcho(base_url=cfg['baseUrl'],workspace_id=host['workspace'],environment='local',max_retries=0,timeout=30)
    messages=json.loads((source/'messages.json').read_text())
    from provider_adapters.honcho_enrollment import ENVIRONMENT, PROTOCOL, LEGACY_PROTOCOL, source_peer_id, source_plans, verify_before_submission
    membership = None
    membership_plans = {}
    peer_protocol=PROTOCOL if os.environ.get(ENVIRONMENT)==PROTOCOL else LEGACY_PROTOCOL
    if os.environ.get(ENVIRONMENT):
        if os.environ[ENVIRONMENT] != PROTOCOL:
            raise ValueError('Unsupported Honcho membership protocol')
        membership = verify_before_submission(root, source, messages, host)
        membership_plans = {p['session_id']:p for p in source_plans(messages, host)}
    groups={}
    for message in messages:groups.setdefault((message['channel'],message['thread']),[]).append(message)
    acknowledgments=[];sessions=[];minimum_work={}
    from pacbench.followups import save
    journal_path=root/'ingestion-delivery'/source.name/'index.json'
    if journal_path.exists():raise RuntimeError('Existing native delivery receipt; refusing blind resubmission')
    journal={'version':'honcho-native-delivery-v1','request_limit':HONCHO_MESSAGE_LIMIT,'source_messages':len(messages),
             'state':'uploading','sessions':[]}
    save(journal_path,journal)
    for identity,batch in groups.items():
        session=client.session('source-'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24])
        sessions.append(session)
        peers={}
        for m in batch:
            peer_id=source_peer_id('Me' if m['from_owner'] else m['sender'],host,protocol=peer_protocol)
            peers[peer_id]=client.peer(peer_id)
        if membership is not None:
            # Include silent source-declared participants, especially the owner
            # receiving a single incoming message, without inventing utterances.
            for peer_id in membership_plans[session.id]['source_peers']:
                peers.setdefault(peer_id, client.peer(peer_id))
        # Real source participants, not a fabricated user/assistant exchange.
        # Hermes recalls from its AI observer's perspective. Honcho requires
        # this observer to witness source messages as they arrive; adding it
        # afterward does not backfill memory. This is a non-speaking reader,
        # not a fabricated participant/message. Use the native Hermes flags.
        members=[(peer,SessionPeerConfig(observe_me=native_config.user_observe_me,
            observe_others=native_config.user_observe_others)) if name==host['peerName'] else peer
            for name,peer in peers.items()]
        session.add_peers([*members, (client.peer(observer), SessionPeerConfig(
            observe_me=native_config.ai_observe_me,
            observe_others=native_config.ai_observe_others))])
        observed=session.get_peer_configuration(observer)
        if (observed.observe_me!=native_config.ai_observe_me or
                observed.observe_others!=native_config.ai_observe_others):
            raise RuntimeError('Honcho did not apply the configured recall observer')
        owner=None
        if host['peerName'] in peers:
            owner=session.get_peer_configuration(host['peerName'])
            if (owner.observe_me!=native_config.user_observe_me or
                    owner.observe_others!=native_config.user_observe_others):
                raise RuntimeError('Honcho did not apply the configured owner membership')
        if membership is not None:
            # Re-adding active peers must not move joined_at. Verify after the
            # SDK call, immediately before any original messages are submitted.
            membership = verify_before_submission(root, source, messages, host)
        baseline=session.queue_status().model_dump(mode='json')
        pending=[]
        for m in batch:
            peer_id=source_peer_id('Me' if m['from_owner'] else m['sender'],host,protocol=peer_protocol)
            pending.append(peers[peer_id].message(m['text'],created_at=m['written_at'],
                metadata={k:v for k,v in m.items() if k!='text'}))
        entry={'session_id':session.id,'source_messages':len(pending),
               'chunks':{},
               'observer':{'peer_id':observer,'observe_me':observed.observe_me,'observe_others':observed.observe_others}}
        journal['sessions'].append(entry)
        def checkpoint(accepted,index,chunk,state,error):
            if state=='sending':
                from pacbench.stop import check_stop
                from pacbench.gateway_client import require_live_cost_boundary
                check_stop();require_live_cost_boundary()
            # Each chunk has its own atomic receipt: never rewrite the entire
            # accepted corpus as a large daily import progresses.
            chunk_receipt={'session_id':session.id,'state':state,'chunk_index':index,
                'source_ids':[m.metadata.get('source_id') for m in chunk],
                'acknowledged':[{'native_message_id':m.id,'source_id':m.metadata.get('source_id'),
                    'binding_sha256':hashlib.sha256(json.dumps({
                        'session_id':m.session_id,'peer_id':m.peer_id,'content':m.content,
                        'created_at':m.created_at.isoformat(),'metadata':m.metadata},
                        sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
                    for m in accepted[index*HONCHO_MESSAGE_LIMIT:]]}
            if error is not None:chunk_receipt['error']=error;journal['state']='failed'
            chunk_path=journal_path.parent/session.id/(f'{index:06d}.json')
            save(chunk_path,chunk_receipt)
            entry['chunks'][chunk_path.name]=hashlib.sha256(chunk_path.read_bytes()).hexdigest()
            entry.update(state=state,last_chunk=index,acknowledged_messages=len(accepted))
            save(journal_path,journal)
        response,failure=add_honcho_messages(session,pending,checkpoint)
        minimum_work[session.id]=baseline['total_work_units']+len(response)
        acknowledgments.append({'session_id':session.id,'processing_baseline':baseline,'minimum_work_units':minimum_work[session.id],'observer':{'peer_id':observer,
            'observe_me':observed.observe_me,'observe_others':observed.observe_others},
            'owner':({'peer_id':host['peerName'],'observe_me':owner.observe_me,'observe_others':owner.observe_others} if owner else None),
            'messages':[m.model_dump(mode='json') if hasattr(m,'model_dump') else {
            'id':m.id,'session_id':m.session_id,'peer_id':m.peer_id,'content':m.content,
            'created_at':m.created_at.isoformat(),'metadata':m.metadata} for m in response]})
        if failure:
            return {'ready':False,'interface':'Native Honcho session.add_messages, ordered <=100-message requests',
                'result':acknowledgments,'native_error':failure,'delivery_journal':str(journal_path.relative_to(root)),
                'readiness':'Native message delivery incomplete; no blind retry; processing not verified'}
    journal['state']='submitted';save(journal_path,journal)
    defaults=json.loads((root/'native-processing-settings.json').read_text())
    from provider_adapters.honcho_clock import enabled, wait_for_batch
    membership_fields = ({'membership_protocol':PROTOCOL,'membership_witness':membership}
                         if membership is not None else {})
    if enabled(root):
        return {**wait_for_batch(root, acknowledgments,
            json.loads(os.environ.get('PACBENCH_USAGE_ROLES','{}'))), **membership_fields}
    grace=defaults['deriver']['REPRESENTATION_BATCH_MAX_AGE_SECONDS']
    from provider_adapters.honcho_local import queue_errors
    statuses,done,reason,health=wait_honcho_sessions(sessions,json.loads(os.environ.get('PACBENCH_USAGE_ROLES','{}')),minimum_work,
        batch_grace_s=grace,read_errors=lambda:queue_errors(root))
    return {**membership_fields, 'ready':done,'interface':'Native Honcho session.add_messages(real source peers and timestamps)',
        'result':acknowledgments,'processing_status':statuses,'model_health':health,
        'source_error_scope':'submitted-source-sessions-v1',
        'readiness':('Native acknowledgments, new work enrollment and submitted-session processing complete' if done else reason),
        'readiness_scope':'Finite isolated batch, submitted source sessions only; unrelated workspace work is not a barrier'}


def wait_honcho_sessions(sessions,roles,minimum_work=None,*,batch_grace_s=0,read_errors=None):
    if not sessions:
        return [],False,'No submitted Honcho sessions; source processing is unproven',{}
    minimum_work=minimum_work or {}
    # A finite, isolated import has no concurrent producers. This is a scoped
    # benchmark readiness barrier, not a production requirement that the entire
    # workspace queue empty. Keep native processing unchanged.
    statuses=[];started=time.monotonic();last_progress=started
    done=False;reason='';health={}
    from pacbench.usage import provider_model_health, snapshot
    from pacbench.stop import check_stop
    previous=None;last_error_check=None;previous_errors=[]
    while True:
        check_stop()
        status={session.id:session.queue_status().model_dump(mode='json') for session in sessions}
        elapsed=time.monotonic()-started
        if status!=previous:
            statuses.append({'elapsed_s':round(elapsed,2),'sessions':status})
            last_progress=time.monotonic();previous=status
        done=all(s['pending_work_units']==0 and s['in_progress_work_units']==0 and
            s.get('total_work_units',0)>=minimum_work.get(id,0) for id,s in status.items())
        if read_errors and (done or last_error_check is None or elapsed-last_error_check>=10):
            observed_errors=read_errors();last_error_check=elapsed
            # An absent session cannot prove an error belongs to unrelated
            # capture work. Match the simulated-clock barrier's fail-closed scope.
            failures=[r for r in observed_errors if not r.get('session_name') or r['session_name'] in status]
            unrelated=[r for r in observed_errors if r.get('session_name') and r['session_name'] not in status]
            if observed_errors!=previous_errors:
                statuses.append({'elapsed_s':round(elapsed,2),'sessions':status,
                    'native_queue_errors':failures,'unrelated_queue_errors':unrelated})
                previous_errors=observed_errors
            if failures:
                done=False;reason='Native Honcho submitted-source work failed; public completion counts include errors';break
        if roles:
            health=provider_model_health(snapshot(roles))
            if health['state']=='stop':
                done=False;reason=health['reason'];break
            # A refusal is terminal: never wait for retries to find a reset.
            from pacbench.gateway_client import require_live_cost_boundary
            try:require_live_cost_boundary()
            except RuntimeError as exc:
                done=False;reason=str(exc);break
            if any(c.get('http_status')==429 for c in snapshot(roles).get('calls',[])):
                done=False;reason='Gateway or upstream refused a request (HTTP 429); import stopped';break
        if done:break
        if elapsed>=batch_grace_s+900:
            reason='Native submitted-session processing exceeded its batch delay plus 15 minutes';break
        if elapsed>=batch_grace_s+300 and time.monotonic()-last_progress>=300:
            reason='Native submitted-session processing stalled for five minutes after its batch delay';break
        busy=any(s['in_progress_work_units'] for s in status.values())
        time.sleep(2 if busy or elapsed>=batch_grace_s else 10)
    return statuses,done,reason,health


def supermemory(source,root,at,processing_at):
    from supermemory import Supermemory
    from pacbench.provider_loader import provider_module
    plugin = provider_module("supermemory")
    _load_supermemory_config, _resolve_container_tag = plugin._load_supermemory_config, plugin._resolve_container_tag
    cfg=_load_supermemory_config(str(root/'hermes-home'))
    tag=_resolve_container_tag(cfg['container_tag'],'default')
    if not cfg['base_url'].startswith('http://127.0.0.1:'):
        raise ValueError('Supermemory document ingest requires the run-owned local server')
    client=Supermemory(api_key=os.environ['SUPERMEMORY_API_KEY'],base_url=cfg['base_url'],max_retries=0,timeout=30)
    delivery=json.loads((root/'document-ingestion-config.json').read_text())
    if delivery.get('dreaming')!='instant':raise ValueError('Undeclared Supermemory ingestion mode')
    content=(source/'messages.txt').read_text()
    try:
        result=client.documents.add(content=content,
            container_tags=[tag],custom_id=source.name,dreaming=delivery['dreaming'],
            metadata={'source':'daily-transcript','collected_at':at})
    except Exception as error:
        failure={**native_error_receipt(error),'phase':'document-add'}
        failure['commit_state']=('rejected' if failure.get('http_status') in (400,401,403,404,413,422)
                                 else 'unknown')
        return {'ready':False,'reason':'Native document delivery failed; no blind retry; raw error withheld',
                'interface':'Supermemory SDK documents.add(raw content) + documents.get',
                'native_error':failure,'container_tag':tag,'processing_status':[],
                'delivery_options':{'dreaming':delivery['dreaming']}}
    from pacbench.document_ingestion import PROCESSING_TIMEOUT_S
    from pacbench.gateway_client import require_live_cost_boundary
    from pacbench.usage import snapshot,provider_model_health
    roles=json.loads(os.environ.get('PACBENCH_USAGE_ROLES','{}'))
    statuses=[];deadline=time.monotonic()+PROCESSING_TIMEOUT_S-30
    ready=False;reason='Native memory processing deadline exceeded';failure=None
    while True:
        try:
            status=client.documents.get(result.id).model_dump(mode='json')
        except Exception as error:
            # Delivery already succeeded. Preserve its ID and every observed
            # state, rather than losing them to a traceback or resubmitting.
            failure={**native_error_receipt(error),'phase':'document-status','commit_state':'accepted'}
            reason='Native document status unavailable; accepted delivery retained; raw error withheld'
            break
        # SDK responses duplicate camel/snake aliases and may append complete
        # memories on every poll. Keep the source witness and state changes,
        # not repeated native memory dumps or duplicate spelling aliases.
        observed={k:status.get(k) for k in ('id','custom_id','content','container_tags','status')}
        observed['dreaming_status']=status.get('dreaming_status',status.get('dreamingStatus'))
        if not statuses or observed!=statuses[-1]:statuses.append(observed)
        state=status.get('status')
        dreaming=status.get('dreaming_status',status.get('dreamingStatus'))
        if roles:
            health=provider_model_health(snapshot(roles))
            if health['state']=='stop':reason=health['reason'];break
            try:require_live_cost_boundary()
            except RuntimeError as exc:reason=str(exc);break
        if state=='done' and dreaming=='done':
            ready=True;reason='';break
        if state in {'failed','error'} or dreaming in {'failed','error'}:
            reason='Native document processing or memory dreaming failed';break
        if state=='done' and dreaming is None:
            reason='Native memory consolidation status unavailable';break
        if time.monotonic()>=deadline:break
        time.sleep(1)
    return {'ready':ready,'reason':reason,'interface':'Supermemory SDK documents.add(raw content) + documents.get',
            'result':result.model_dump(mode='json'),'container_tag':tag,'processing_status':statuses,
            **({'native_error':failure} if failure else {}),
            'delivery_options':{'dreaming':delivery['dreaming']},
            'readiness_requirement':'Native document processing and memory dreaming both done'}


def main(args=None):
    from pacbench.stop import OperatorStop, check_stop
    try:
        check_stop()
    except OperatorStop:
        print(json.dumps({'operator_stop': True}))
        return 75
    provider,src,arm,at,processing_at=args or sys.argv[1:]
    from pacbench.provider_lineup import require_not_retired
    require_not_retired(provider)
    dispatch={'mem0':mem0,'memcal':memcal,'hindsight':hindsight,'openviking':openviking,'supermemory':supermemory,'honcho':honcho}
    if provider=='reme':
        from provider_adapters.reme_resource import ingest
        dispatch['reme']=lambda source,root,at,processing_at:ingest(source,root)
    if provider=='cognee':
        from provider_adapters.cognee_documents import ingest
        dispatch['cognee']=lambda source,root,at,processing_at:ingest(source,root)
    if provider=='memos':
        from provider_adapters.reme_memos_documents import ingest
        def ingest_memos(source,root,at,processing_at):
            endpoint=json.loads((root/'memos-document-config.json').read_text())['endpoint']
            return ingest('memos',source,endpoint)
        dispatch['memos']=ingest_memos
    try:
        result=dispatch[provider](Path(src),Path(arm),at,processing_at)
    except OperatorStop:
        print(json.dumps({'operator_stop':True}))
        return 75
    except Exception as error:
        if provider not in ('honcho','supermemory'):raise
        result={'ready':False,'interface':f'Native {provider.title()} connector',
                'native_error':native_error_receipt(error),
                'readiness':'Native connector failed; raw exception body withheld'}
        journal=Path(arm)/'ingestion-delivery'/Path(src).name/'index.json'
        if provider=='honcho' and journal.exists():result['delivery_journal']=str(journal.relative_to(Path(arm)))
    print(json.dumps(result,ensure_ascii=False,default=lambda value:sorted(value) if isinstance(value,set) else str(value)))
    return 0 if result['ready'] else 1


if __name__=='__main__':raise SystemExit(main())
