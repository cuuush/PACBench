"""Transport/telemetry regressions; no mocked provider-accuracy claims."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from pacbench import harness
from provider_adapters.document_connector import byterover


def test_hindsight_does_not_fail_at_one_hundred_operations_or_miss_later_pending_work():
    import time
    from provider_adapters.document_connector import hindsight_operations
    history=[{'id':str(i),'status':'completed'} for i in range(100)]
    history.append({'id':'pending-later-page','status':'pending'})
    requests=[]
    def listing(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(model_dump=lambda **kw:{'total':len(history),
            'operations':history[kwargs['offset']:kwargs['offset']+kwargs['limit']]})
    client=SimpleNamespace(operations=SimpleNamespace(list_operations=listing))
    provider=SimpleNamespace(_run_sync=lambda value:value)
    result=hindsight_operations(provider,client,'bank',time.monotonic()+10)
    assert result['pagination_complete'] and len(result['operations'])==101
    assert [r['offset'] for r in requests]==[0,100]
    assert result['operations'][-1]['status']=='pending'
    history[-1]['status']='completed'
    assert all(o['status']=='completed' for o in hindsight_operations(
        provider,client,'bank',time.monotonic()+10)['operations'])
    # Truncated native pagination cannot silently become "ready".
    client.operations.list_operations=lambda **kw:SimpleNamespace(
        model_dump=lambda **unused:{'total':101,'operations':[]})
    assert not hindsight_operations(provider,client,'bank',time.monotonic()+10)['pagination_complete']


@pytest.mark.parametrize('status,pending,expected', [('completed',None,True),
    ('completed',{'count':1},True),('completed',{'count':0},True),
    ('error',None,False),('queued',None,False)])
@pytest.mark.parametrize('transport,model', [('muse-gateway','muse-spark-1.3-contributor'),
                                          ('openrouter','deepseek/deepseek-v4.1-flash-1017')])
def test_byterover_requires_completion_without_approving_retrospective_review(tmp_path,monkeypatch,status,pending,expected,transport,model):
    monkeypatch.setenv('PACBENCH_TRANSPORT',transport)
    config=tmp_path/'config/brv';config.mkdir(parents=True)
    (config/'providers.json').write_text(json.dumps({'activeProvider':'openai-compatible',
        'providers':{'openai-compatible':{'activeModel':model,'baseUrl':'http://127.0.0.1:1/v1'}}}))
    monkeypatch.setenv('XDG_CONFIG_HOME',str(config.parent))
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY','unit-test-token')
    source=tmp_path/'source';source.mkdir();(source/'messages.txt').write_text('Unmodified source text\n')
    arm=tmp_path/'arm';(arm/'hermes-home/byterover').mkdir(parents=True)
    final={'command':'curate','success':status!='error','data':{'status':status}}
    if pending:final['data']['pendingReview']=pending
    stdout=json.dumps({'command':'curate','success':True,'data':{'status':'queued'}})+'\n'+json.dumps(final)+'\n'
    with patch('pacbench.gateway_client.validate_route'),patch('provider_adapters.document_connector.subprocess.run') as run:
        run.return_value=subprocess.CompletedProcess([],0,stdout,'')
        result=byterover(source,arm,'now','now')
    assert result['ready']==expected
    assert len(result['events'])==2 and result['result']==(final if status!='queued' else {})
    assert (arm/'hermes-home/byterover/imports/source/messages.txt').read_bytes()==(source/'messages.txt').read_bytes()
    assert run.call_args.args[0]==['brv','curate','--files','imports/source/messages.txt','--format','json']


def test_native_task_telemetry_only_captures_changed_records(tmp_path):
    folder=tmp_path/'projects/real-project/task-history/data';folder.mkdir(parents=True)
    old=folder/'tsk-1.json';old.write_text(json.dumps({'taskId':'old','toolCalls':[]}))
    env={'PACBENCH_PROVIDER_TRACE_ROOT':str(tmp_path),'OPENAI_API_KEY':'unit-test-token'}
    before=harness.provider_trace_snapshot(env)
    record={'taskId':'new','toolCalls':[{'toolName':'read_file','args':{'path':'context-tree/plan.md'},'result':'unit-test-token'}]}
    (folder/'tsk-2.json').write_text(json.dumps(record))
    turn=harness.TurnResult(prompt='',at='',answer='',tool_calls=[],tool_results=[],policy_blocks=0,
                            latency_s=0,returncode=0,stderr_tail='',raw_events=0)
    harness.capture_provider_traces(turn,env,before)
    assert len(turn.provider_traces)==1
    captured=turn.provider_traces[0]['record']
    assert captured['taskId']=='new'
    assert captured['toolCalls'][0]['result']=='<redacted>'
    assert 'read_file' in json.dumps(captured)


def test_stream_error_remains_failed_when_loop_warning_follows_json():
    event={'name':'brv_query','is_error':True,
           'output':'{"error":"query timeout"}\n[Tool loop warning]'}
    assert harness._summarize_tool_result(event)['success'] is False


def test_completed_run_cleanup_stops_only_its_owned_child(tmp_path):
    from pacbench.process_cleanup import cleanup_run_processes
    if not Path('/proc').exists():pytest.skip('Linux process receipt')
    owned=tmp_path/'completed-run';owned.mkdir()
    other=tmp_path/'unrelated';other.mkdir()
    children=[subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],cwd=p)
              for p in (owned,other)]
    try:
        records=cleanup_run_processes(owned,grace_s=.2)
        children[0].wait(timeout=2)
        assert [r['pid'] for r in records]==[children[0].pid]
        assert children[1].poll() is None
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
            child.wait(timeout=2)


@pytest.mark.parametrize('dreaming_field',['dreaming_status','dreamingStatus',None])
def test_supermemory_ingestion_waits_for_memory_and_uses_native_namespace(tmp_path,monkeypatch,dreaming_field):
    from provider_adapters.document_connector import supermemory
    source=tmp_path/'day';source.mkdir();(source/'messages.txt').write_text('Raw daily text\n')
    home=tmp_path/'arm/hermes-home';home.mkdir(parents=True)
    (home.parent/'document-ingestion-config.json').write_text(json.dumps({'dreaming':'instant'}))
    resolve=Mock(return_value='native_canonical_tag')
    plugin=SimpleNamespace(
        _load_supermemory_config=lambda path:{'base_url':'http://127.0.0.1:1234','container_tag':'raw-tag'},
        _resolve_container_tag=resolve)
    resolver=Mock(return_value=plugin)
    monkeypatch.setattr('pacbench.provider_loader.provider_module',resolver)
    add=Mock(return_value=SimpleNamespace(
        id='doc-1',model_dump=lambda **kw:{'id':'doc-1'}))
    statuses=iter([{'status':'done'}] if dreaming_field is None else [
                  {'status':'done',dreaming_field:'dreaming'},
                   {'status':'done',dreaming_field:'dreaming'},
                   {'status':'done',dreaming_field:'done'}])
    get=Mock(side_effect=lambda id:SimpleNamespace(model_dump=lambda **kw:next(statuses)))
    documents=SimpleNamespace(add=add,get=get)
    monkeypatch.setitem(sys.modules,'supermemory',SimpleNamespace(Supermemory=lambda **kw:SimpleNamespace(documents=documents)))
    monkeypatch.setenv('SUPERMEMORY_API_KEY','local-test-key')
    monkeypatch.delenv('PACBENCH_USAGE_ROLES',raising=False)
    sleep=Mock();monkeypatch.setattr('provider_adapters.document_connector.time.sleep',sleep)
    result=supermemory(source,home.parent,'now','now')
    resolver.assert_called_once_with('supermemory')
    resolve.assert_called_once_with('raw-tag','default')
    assert add.call_args.kwargs['container_tags']==['native_canonical_tag']
    assert add.call_args.kwargs['content']=='Raw daily text\n'
    assert add.call_args.kwargs['dreaming']=='instant'
    assert result['delivery_options']=={'dreaming':'instant'}
    assert result['container_tag']=='native_canonical_tag'
    if dreaming_field is None:
        assert not result['ready'] and 'unavailable' in result['reason']
        assert get.call_count==1 and sleep.call_count==0
    else:
        assert result['ready']
        assert get.call_count==3 and sleep.call_count==2
        assert len(result['processing_status'])==2  # No duplicate polling records.


def test_honcho_daily_batch_preserves_real_speakers_text_and_times(tmp_path,monkeypatch):
    from provider_adapters.document_connector import honcho
    from datetime import datetime
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    source=tmp_path/'day';source.mkdir()
    messages=[{'source_id':f'm-{i}','channel':'imessage','thread':'group-1',
               'sender':sender,'from_owner':i==0,'text':text,
               'written_at':f'2026-09-10T18:0{i}:00-04:00','collected_at':'2026-09-10T18:10:00-04:00'}
              for i,(sender,text) in enumerate([('Alex','Where should we meet?'),('Devon','At the park.')])]
    (source/'messages.json').write_text(json.dumps(messages))
    home=tmp_path/'arm/hermes-home';home.mkdir(parents=True)
    (home/'honcho.json').write_text(json.dumps({'baseUrl':'http://127.0.0.1:1234',
        'hosts':{'hermes':{'workspace':'local-test','peerName':'user','aiPeer':'hermes'}}}))
    pending=[];participants=[];configurations={}
    (home.parent/'native-processing-settings.json').write_text(json.dumps(
        {'deriver':{'REPRESENTATION_BATCH_MAX_AGE_SECONDS':1800}}))
    class Peer:
        def __init__(self,id):self.id=id
        def message(self,content,created_at,metadata):
            return SimpleNamespace(id=metadata['source_id'],session_id='session-1',peer_id=self.id,content=content,
                created_at=datetime.fromisoformat(created_at),metadata=metadata)
    def add_peers(peers):
        participants.extend(p[0].id if isinstance(p,tuple) else p.id for p in peers)
        configurations.update({p[0].id:p[1] for p in peers if isinstance(p,tuple)})
    session=SimpleNamespace(id='session-1',get_peer_configuration=lambda peer:configurations[peer],add_peers=add_peers,
        add_messages=lambda batch:pending.extend(batch) or batch,
        queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':0,'in_progress_work_units':0,'total_work_units':len(pending)}))
    client=SimpleNamespace(session=lambda id:session,peer=Peer)
    monkeypatch.setitem(sys.modules,'honcho',SimpleNamespace(Honcho=lambda **kw:client))
    monkeypatch.setitem(sys.modules,'honcho.api_types',SimpleNamespace(SessionPeerConfig=lambda **kw:SimpleNamespace(**kw)))
    plugin=SimpleNamespace(HonchoClientConfig=SimpleNamespace(
        from_global_config=lambda:SimpleNamespace(ai_observe_me=True,ai_observe_others=True,
            user_observe_me=True,user_observe_others=True)))
    resolver=Mock(return_value=plugin)
    monkeypatch.setattr('pacbench.provider_loader.provider_module',resolver)
    monkeypatch.setattr('provider_adapters.honcho_local.queue_errors',lambda root:[])
    result=honcho(source,home.parent,'now','now')
    resolver.assert_called_once_with('honcho','client')
    assert result['ready'] and len(pending)==len(messages)
    assert list(result['processing_status'][0]['sessions'])==['session-1']
    assert participants==['user','Devon','hermes']
    assert result['result'][0]['observer']=={'peer_id':'hermes','observe_me':True,'observe_others':True}
    assert result['result'][0]['owner']=={'peer_id':'user','observe_me':True,'observe_others':True}
    for native,original in zip(pending,messages):
        assert native.content==original['text']
        assert native.created_at==datetime.fromisoformat(original['written_at'])
        assert native.metadata=={k:v for k,v in original.items() if k!='text'}


@pytest.mark.parametrize('session_name',['source','unrelated',None,''])
def test_honcho_public_completed_counter_does_not_hide_failed_source_work(session_name):
    from provider_adapters.document_connector import wait_honcho_sessions
    session=SimpleNamespace(id='source',queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{
        'total_work_units':1,'completed_work_units':1,'pending_work_units':0,'in_progress_work_units':0}))
    failure={'session_name':session_name,'error_class':'StructuredOutputError','error_sha256':'error-hash'}
    records,ready,reason,_=wait_honcho_sessions([session],{}, {'source':1},read_errors=lambda:[failure])
    assert ready==(session_name=='unrelated')
    if session_name!='unrelated':
        assert 'work failed' in reason and records[-1]['native_queue_errors']==[failure]
    else:
        assert records[-1]['unrelated_queue_errors']==[failure]
        assert not records[-1]['native_queue_errors']


def test_honcho_empty_session_discovery_cannot_prove_source_processing():
    from provider_adapters.document_connector import wait_honcho_sessions
    records,ready,reason,_=wait_honcho_sessions([],{})
    assert not ready and not records and 'unproven' in reason


def test_honcho_native_queue_telemetry_never_exports_raw_error_text(tmp_path,monkeypatch):
    from provider_adapters.honcho_local import queue_errors
    raw=[{'id':1,'error':'StructuredOutputError: secret-token in upstream diagnostic'},
         {'id':2,'error':'secret-token'}]
    monkeypatch.setattr('provider_adapters.honcho_local.subprocess.run',lambda *a,**kw:
        SimpleNamespace(returncode=0,stdout=json.dumps(raw)))
    rows=queue_errors(tmp_path)
    assert [r['error_class'] for r in rows]==['StructuredOutputError','NativeError']
    assert all(len(r['error_sha256'])==64 for r in rows)
    assert 'secret-token' not in json.dumps(rows) and all('error' not in r for r in rows)


def test_honcho_scoped_barrier_allows_progress_past_old_deadline(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    clock=[0];polls=[0]
    monkeypatch.setattr('provider_adapters.document_connector.time.monotonic',lambda:clock[0])
    def sleep(seconds):clock[0]+=seconds
    monkeypatch.setattr('provider_adapters.document_connector.time.sleep',sleep)
    def status():
        polls[0]+=1
        done=min(int(clock[0]//100),4)
        return SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':4-done,'in_progress_work_units':0,'completed_work_units':done})
    records,ready,reason,_=wait_honcho_sessions([SimpleNamespace(id='submitted',queue_status=status)],{})
    assert ready and not reason and clock[0]==400
    assert polls[0]>180 and len(records)==5  # Changed status only, no repetitive polling bloat.


def test_honcho_stalled_processing_remains_unready(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    clock=[0]
    monkeypatch.setattr('provider_adapters.document_connector.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('provider_adapters.document_connector.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    session=SimpleNamespace(id='submitted',queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':1,'in_progress_work_units':0}))
    records,ready,reason,_=wait_honcho_sessions([session],{})
    assert not ready and 'stalled' in reason and clock[0]==300 and len(records)==1


def test_honcho_native_accumulation_delay_is_not_mistaken_for_a_stall(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    clock=[0]
    monkeypatch.setattr('provider_adapters.document_connector.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('provider_adapters.document_connector.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def status():
        return SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':int(clock[0]<1840),
            'in_progress_work_units':0,'total_work_units':1})
    session=SimpleNamespace(id='source',queue_status=status)
    records,ready,reason,_=wait_honcho_sessions([session],{}, {'source':1},batch_grace_s=1800)
    assert ready and not reason and clock[0]==1840 and len(records)==2


def test_honcho_bad_model_requests_stop_processing_wait(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    monkeypatch.setattr('pacbench.usage.snapshot',lambda roles:{})
    monkeypatch.setattr('pacbench.usage.provider_model_health',lambda capture:{'state':'stop','reason':'Repeated provider-side HTTP 400'})
    session=SimpleNamespace(id='submitted',queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':1,'in_progress_work_units':0}))
    records,ready,reason,health=wait_honcho_sessions([session],{'provider-project':'memory_provider'})
    assert not ready and 'HTTP 400' in reason and health['state']=='stop' and len(records)==1


def test_honcho_native_tool_capture_is_sanitized_and_affects_retention(tmp_path):
    from pacbench.followups import tool_profile
    journal=tmp_path/'native-private.jsonl'
    env={'PACBENCH_HONCHO_TRACE_FILE':str(journal),'OPENAI_API_KEY':'test-gateway-secret'}
    previous=json.dumps({'task_type':'dialectic_chat','timestamp':0,'input':{'messages':[]},
        'output':{'tool_calls':[],'thinking_content':'test-gateway-secret'}})+'\n'
    journal.write_text(previous)
    before=harness.provider_trace_snapshot(env)
    record={'timestamp':1,'task_type':'dialectic_chat','model':'spark',
        'input':{'messages':[{'role':'user','content':'Query: Pizza Friday?'}]},
        'output':{'thinking_content':'private reasoning','encrypted_content':'private ciphertext','tool_calls':[
            {'tool_name':'search_memory','tool_input':{'query':'pizza'},'tool_result':'retained event'},
            {'tool_name':'search_messages','tool_input':{'query':'Friday'},'tool_result':'source test-gateway-secret'}]}}
    journal.write_text(previous+json.dumps(record)+'\n')
    turn=harness.TurnResult(prompt='',at='',answer='',tool_calls=[{'name':'honcho_reasoning','input':{'query':'Pizza Friday?'}}],
        tool_results=[],policy_blocks=0,latency_s=0,returncode=0,stderr_tail='',raw_events=0)
    harness.capture_provider_traces(turn,env,before)
    assert 'private reasoning' not in json.dumps(turn.provider_traces)
    assert 'private ciphertext' not in json.dumps(turn.provider_traces)
    assert 'test-gateway-secret' not in json.dumps(turn.provider_traces)
    metrics=tool_profile(turn)
    assert metrics['provider_internal_tool_calls']==2
    assert metrics['provider_internal_source_fallback_confirmed'] and metrics['source_fallback_attempted']
    assert metrics['provider_internal_operations'][0]['category']=='memory_search'
    from pacbench.semantic_grade import Grade,GradedClaim
    from pacbench.followups import recovery_score
    grade=Grade('check',slots=[GradedClaim('fact','correct','Answer is correct')],correct=1)
    score=recovery_score(grade,metrics,[])
    assert score['points']==0 and score['claims'][0]['retention_failed']
    assert grade.correct==1  # Semantic judgment remains unchanged.


def test_unattributed_native_source_work_needs_review_not_false_causal_failure():
    from pacbench.followups import tool_profile
    turn=SimpleNamespace(tool_calls=[],tool_results=[],provider_traces=[{'provider':'honcho',
        'attribution':'background-or-prefetch','record':{'toolCalls':[{'toolName':'grep_messages','args':{}}]}}])
    metrics=tool_profile(turn)
    assert not metrics['source_fallback_attempted']
    assert metrics['provider_internal_path_needs_review'] and metrics['unclassified_operations']==1


def test_honcho_empty_old_queue_is_not_readiness_for_a_new_daily_batch(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    clock=[0]
    monkeypatch.setattr('provider_adapters.document_connector.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('provider_adapters.document_connector.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def status():
        values={'total_work_units':6,'pending_work_units':0,'in_progress_work_units':0}
        if clock[0]>=2:values.update(total_work_units=12,pending_work_units=6)
        if clock[0]>=4:values['pending_work_units']=0
        return SimpleNamespace(model_dump=lambda **kw:values)
    records,ready,reason,_=wait_honcho_sessions([SimpleNamespace(id='source',queue_status=status)],{}, {'source':12})
    assert ready and not reason and clock[0]==4 and len(records)==3
