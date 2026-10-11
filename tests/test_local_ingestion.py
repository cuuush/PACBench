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


@pytest.mark.parametrize('status,pending,expected', [('completed',None,True),
    ('completed',{'pendingCount':1},False),('error',None,False),('queued',None,False)])
def test_byterover_native_progress_is_not_readiness(tmp_path,monkeypatch,status,pending,expected):
    config=tmp_path/'config/brv';config.mkdir(parents=True)
    (config/'providers.json').write_text(json.dumps({'activeProvider':'openai-compatible',
        'providers':{'openai-compatible':{'activeModel':'muse-spark-1.3-contributor','baseUrl':'http://127.0.0.1:1/v1'}}}))
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


def test_supermemory_ingestion_uses_native_namespace_resolver(tmp_path,monkeypatch):
    from provider_adapters.document_connector import supermemory
    source=tmp_path/'day';source.mkdir();(source/'messages.txt').write_text('Raw daily text\n')
    home=tmp_path/'arm/hermes-home';home.mkdir(parents=True)
    resolve=Mock(return_value='native_canonical_tag')
    monkeypatch.setitem(sys.modules,'plugins.memory.supermemory',SimpleNamespace(
        _load_supermemory_config=lambda path:{'base_url':'http://127.0.0.1:1234','container_tag':'raw-tag'},
        _resolve_container_tag=resolve))
    add=Mock(return_value=SimpleNamespace(
        id='doc-1',model_dump=lambda **kw:{'id':'doc-1'}))
    documents=SimpleNamespace(add=add,get=lambda id:SimpleNamespace(model_dump=lambda **kw:{'status':'done'}))
    monkeypatch.setitem(sys.modules,'supermemory',SimpleNamespace(Supermemory=lambda **kw:SimpleNamespace(documents=documents)))
    monkeypatch.setenv('SUPERMEMORY_API_KEY','local-test-key')
    result=supermemory(source,home.parent,'now','now')
    resolve.assert_called_once_with('raw-tag','default')
    assert add.call_args.kwargs['container_tags']==['native_canonical_tag']
    assert add.call_args.kwargs['content']=='Raw daily text\n'
    assert result['ready'] and result['container_tag']=='native_canonical_tag'


def test_honcho_daily_batch_preserves_real_speakers_text_and_times(tmp_path,monkeypatch):
    from provider_adapters.document_connector import honcho
    from datetime import datetime
    source=tmp_path/'day';source.mkdir()
    messages=[{'source_id':f'm-{i}','channel':'imessage','thread':'group-1',
               'sender':sender,'from_owner':i==0,'text':text,
               'written_at':f'2026-09-10T18:0{i}:00-04:00','collected_at':'2026-09-10T18:10:00-04:00'}
              for i,(sender,text) in enumerate([('Alex','Where should we meet?'),('Devon','At the park.')])]
    (source/'messages.json').write_text(json.dumps(messages))
    home=tmp_path/'arm/hermes-home';home.mkdir(parents=True)
    (home/'honcho.json').write_text(json.dumps({'baseUrl':'http://127.0.0.1:1234',
        'hosts':{'hermes':{'workspace':'local-test','peerName':'user','aiPeer':'hermes'}}}))
    pending=[];participants=[]
    class Peer:
        def __init__(self,id):self.id=id
        def message(self,content,created_at,metadata):
            return SimpleNamespace(id=metadata['source_id'],session_id='session-1',peer_id=self.id,content=content,
                created_at=datetime.fromisoformat(created_at),metadata=metadata)
    session=SimpleNamespace(id='session-1',get_peer_configuration=lambda peer:SimpleNamespace(observe_me=False,observe_others=True),add_peers=lambda peers:participants.extend(p[0].id if isinstance(p,tuple) else p.id for p in peers),
        add_messages=lambda batch:pending.extend(batch) or batch,
        queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':0,'in_progress_work_units':0}))
    client=SimpleNamespace(session=lambda id:session,peer=Peer)
    monkeypatch.setitem(sys.modules,'honcho',SimpleNamespace(Honcho=lambda **kw:client))
    monkeypatch.setitem(sys.modules,'honcho.api_types',SimpleNamespace(SessionPeerConfig=lambda **kw:SimpleNamespace(**kw)))
    monkeypatch.setitem(sys.modules,'plugins.memory.honcho.client',SimpleNamespace(
        HonchoClientConfig=SimpleNamespace(from_global_config=lambda:SimpleNamespace(ai_observe_me=False,ai_observe_others=True))))
    result=honcho(source,home.parent,'now','now')
    assert result['ready'] and len(pending)==len(messages)
    assert list(result['processing_status'][0]['sessions'])==['session-1']
    assert participants==['user','Devon','hermes']
    assert result['result'][0]['observer']=={'peer_id':'hermes','observe_me':False,'observe_others':True}
    for native,original in zip(pending,messages):
        assert native.content==original['text']
        assert native.created_at==datetime.fromisoformat(original['written_at'])
        assert native.metadata=={k:v for k,v in original.items() if k!='text'}


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
    assert not ready and 'no observable progress' in reason and clock[0]==300 and len(records)==1


def test_honcho_bad_model_requests_stop_processing_wait(monkeypatch):
    from provider_adapters.document_connector import wait_honcho_sessions
    monkeypatch.setattr('pacbench.usage.snapshot',lambda roles:{})
    monkeypatch.setattr('pacbench.usage.provider_model_health',lambda capture:{'state':'stop','reason':'Repeated provider-side HTTP 400'})
    session=SimpleNamespace(id='submitted',queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'pending_work_units':1,'in_progress_work_units':0}))
    records,ready,reason,health=wait_honcho_sessions([session],{'provider-project':'memory_provider'})
    assert not ready and 'HTTP 400' in reason and health['state']=='stop' and len(records)==1
