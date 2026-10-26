"""Native transport readiness, not simulated provider accuracy."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pacbench.followups import save
from pacbench.native_writes import (accepted,complete,dependencies,ensure_ready,
                                   audit_ready,supermemory_status,openviking_status)


def test_dependencies_wait_only_when_a_public_instruction_crosses_sessions():
    first={'id':'edit','session':'old'};later={'id':'later','session':'fresh','requires':['edit']}
    checks=[(first,None,None),(later,None,None)]
    assert dependencies(later,checks,'shared')==[first]
    assert dependencies({**later,'session':'old'},checks,'shared')==[]
    assert dependencies({**later,'requires':[]},checks,'shared')==[]


def test_only_successful_native_write_receipts_supply_ids():
    t={'session_id':'real-session','tool_results':[
        {'tool':'supermemory_store','success':True,'text':'{"id":"accepted-document"}'},
        {'tool':'supermemory_store','success':False,'text':'{"id":"failed-call"}'},
        {'tool':'supermemory_search','success':True,'text':'{"id":"search-result"}'},
        {'tool':'supermemory_store','success':True,'text':'not JSON'}]}
    assert accepted([t],'supermemory')==(['accepted-document'],['real-session'])


@pytest.mark.parametrize('provider,states,expected',[
    ('supermemory',[{'status':'done','dreaming_status':'dreaming'}],(False,False)),
    ('supermemory',[{'status':'done','dreaming_status':'done'}],(True,False)),
    ('supermemory',[{'status':'done','dreaming_status':'error'}],(False,True)),
    ('openviking',[{'status':'running','native_error':False}],(False,False)),
    ('openviking',[{'status':'completed','native_error':True}],(False,True)),
    ('openviking',[{'status':'completed','native_error':False}],(True,False)),
])
def test_queue_acceptance_and_indexing_do_not_prove_completed_memory(provider,states,expected):
    assert complete(provider,states)==expected


def test_supermemory_discovery_paginates_and_watches_only_the_required_session_and_direct_writes():
    def model(value):return SimpleNamespace(model_dump=lambda **kwargs:value)
    pages=[{'memories':[{'id':'unrelated','metadata':{'session_id':'another'}}],
            'pagination':{'current_page':1,'total_pages':2}},
           {'memories':[{'id':'captured','metadata':{'session_id':'required'}}],
            'pagination':{'current_page':2,'total_pages':2}}]
    listing=Mock(side_effect=[model(v) for v in pages])
    get=Mock(side_effect=lambda id:model({'id':id,'status':'done','dreamingStatus':'done'}))
    c=SimpleNamespace(documents=SimpleNamespace(list=listing,get=get))
    states=supermemory_status(c,'native-tag',[],['required'])
    assert [s['id'] for s in states]==['captured']
    assert get.call_count==1 and listing.call_count==2
    assert all(call.kwargs['include_content'] is False for call in listing.call_args_list)
    assert complete('supermemory',states)==(True,False)
    listing.reset_mock(side_effect=True)
    listing.side_effect=AssertionError('Do not wait on unrelated capture when an explicit write exists')
    assert supermemory_status(c,'native-tag',['explicit'],['required'])==[
        {'id':'explicit','status':'done','dreaming_status':'done'}]
    listing.assert_not_called()


def test_openviking_observer_reads_actual_task_completion_and_keeps_native_errors(monkeypatch):
    import io
    from urllib.parse import urlsplit,parse_qs
    calls=[]
    def get(request,**kwargs):
        calls.append(request)
        query=parse_qs(urlsplit(request.full_url).query)
        if '/tasks?' in request.full_url:
            assert query['resource_id']==['real-session'] and query['task_type']==['session_commit']
            if 'cursor' not in query:result={'items':[{'task_id':'capture'}],'has_more':True,'next_cursor':'next'}
            else:result={'items':[],'has_more':False}
        else:
            id=request.full_url.rsplit('/',1)[1]
            result={'task_id':id,'status':'completed','error':'native failure' if id=='explicit' else None}
        return io.BytesIO(json.dumps({'status':'ok','result':result}).encode())
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',get)
    states=openviking_status('http://127.0.0.1:1234',{},[],['real-session'])
    assert all(r.get_method()=='GET' for r in calls)
    assert [s['id'] for s in states]==['capture']
    assert complete('openviking',states)==(True,False)
    calls.clear()
    states=openviking_status('http://127.0.0.1:1234',{},['explicit'],['real-session'])
    assert len(calls)==1 and '/tasks/explicit' in calls[0].full_url
    assert complete('openviking',states)==(False,True)


@pytest.mark.parametrize('ready',[True,False])
@pytest.mark.parametrize('version',['accepted-native-writes-v1','accepted-native-writes-v2'])
def test_cross_session_readiness_is_cached_bound_to_actual_turns_and_auditable(tmp_path,monkeypatch,ready,version):
    root=tmp_path/'repeat-1/supermemory';first={'id':'edit','session':'old'}
    row={'id':'later','session':'fresh','at':'2026-10-22T11:00:00-04:00','requires':['edit']}
    last={**row,'id':'last','session':'another'}
    checks=[(first,None,None),(row,None,None),(last,None,None)]
    save(root.parent.parent/'summary.json',{'write_readiness_protocol':version})
    turn={'session_id':'actual-session','prompt':'Change the plan','tool_results':[{'tool':'supermemory_store','success':True,
            'text':'{"saved":true,"id":"doc-1"}'}]}
    save(root/'turns/edit/turn.json',turn)
    observation={'ready':ready,'processing_overrides':False,'snapshots':[
        [{'id':'doc-1','status':'done','dreaming_status':'done' if ready else 'dreaming'}]]}
    run=Mock(return_value=SimpleNamespace(returncode=0,stdout=json.dumps(observation)))
    monkeypatch.setattr('pacbench.native_writes.subprocess.run',run)
    monkeypatch.setattr('pacbench.harness.turn_env',lambda *args:{})
    for r in [row,last]:
        if ready:ensure_ready('supermemory',r,checks,'shared',root,{},root/'turns'/r['id'])
        else:
            with pytest.raises(RuntimeError,match='not ready'):
                ensure_ready('supermemory',r,checks,'shared',root,{},root/'turns'/r['id'])
    assert run.call_count==1  # Also do not repeat a failed 15-minute wait.
    save(root/'turns/later/turn.json',{'answer':'Synthetic transport fixture'})
    assert json.loads((root/'turns/later/write-readiness.json').read_text())['version']==version
    assert bool(audit_ready(checks,'shared',root,version)) is not ready
    if ready:
        turn['tool_results'][0]['text']='{"id":"other-document"}'
        save(root/'turns/edit/turn.json',turn)
        assert audit_ready(checks,'shared',root,version)


def test_empty_native_write_discovery_never_proves_completion():
    for provider in ('honcho','supermemory','openviking'):
        assert complete(provider,[])==(False,False)


@pytest.mark.parametrize('mode',['old-capture','assistant-echo','pending','done','wrong-session'])
def test_supermemory_capture_must_contain_the_actual_user_instruction(mode):
    from pacbench.followups import digest
    def model(value):return SimpleNamespace(model_dump=lambda **kwargs:value)
    capture={'session':'required','prompt':'Move it to Sunday'}
    users='Move it to Sunday' if mode not in {'old-capture','assistant-echo'} else 'Earlier question'
    content=f'[role: user]\n{users}\n[user:end]\n[role: assistant]\n'
    content+='[role: user]\nMove it to Sunday\n[user:end]' if mode=='assistant-echo' else 'Okay.'
    content+='\n[assistant:end]'
    metadata={'type':'conversation','session_id':'other' if mode=='wrong-session' else 'required'}
    listing=Mock(return_value=model({'memories':[{'id':'doc','metadata':metadata}],
        'pagination':{'current_page':1,'total_pages':1}}))
    get=Mock(return_value=model({'id':'doc','metadata':metadata,'content':content,
        'status':'done','dreaming_status':'dreaming' if mode=='pending' else 'done'}))
    client=SimpleNamespace(documents=SimpleNamespace(list=listing,get=get))
    states=supermemory_status(client,'native-tag',[],['required'],
        [{'session':'required','user':capture['prompt'],'binding':digest(capture)}])
    assert complete('supermemory',states)==(mode=='done',False)
    assert all(call.kwargs['include_content'] is False for call in listing.call_args_list)


def test_supermemory_all_required_instructions_must_be_captured():
    def model(value):return SimpleNamespace(model_dump=lambda **kwargs:value)
    metadata={'type':'conversation','session_id':'required'}
    client=SimpleNamespace(documents=SimpleNamespace(
        list=Mock(return_value=model({'memories':[{'id':'doc','metadata':metadata}],
            'pagination':{'current_page':1,'total_pages':1}})),
        get=Mock(return_value=model({'id':'doc','metadata':metadata,
            'content':'[role: user]\nFirst update\n[user:end]',
            'status':'done','dreaming_status':'done'}))))
    states=supermemory_status(client,'native-tag',[],['required'],[
        {'session':'required','user':'First update','binding':'first'},
        {'session':'required','user':'Second update','binding':'second'}])
    assert states[-1]['missing_inputs']==['second']
    assert complete('supermemory',states)==(False,False)


def test_supermemory_v2_audit_rejects_status_only_capture_proof():
    from pacbench.native_writes import VERSION,write_inputs,ready_proof
    from pacbench.followups import digest
    turn={'session_id':'required','prompt':'Move it to Sunday','tool_results':[]}
    bound={'dependencies':['edit'],'inputs':write_inputs([turn],'supermemory'),'turn_hashes':[digest(turn)]}
    state={'id':'native-doc','status':'done','dreaming_status':'done'}
    record={'version':VERSION,'binding':digest(bound),**bound,'state':'ready',
            'observed':{'ready':True,'processing_overrides':False,'snapshots':[[state]]}}
    assert not ready_proof(record,bound)
    state['verified_inputs']=[digest(bound['inputs']['captures'][0])]
    assert ready_proof(record,bound)


@pytest.mark.parametrize('mode',['pending','done','failed','missing','unenrolled','wrong-owner','wrong-body'])
def test_honcho_requires_actual_captured_user_input_enrollment_and_success(monkeypatch,mode):
    import hashlib,io,urllib.error
    from pacbench.native_writes import honcho_status
    calls=[]
    expected=hashlib.sha256(b'Change the plan').hexdigest()
    def get(request,**kwargs):
        calls.append(request)
        if '/messages/list?' in request.full_url:
            if mode=='missing':raise urllib.error.HTTPError(request.full_url,404,'absent',{},None)
            result={'items':[{'id':'native-1','peer_id':'other' if mode=='wrong-owner' else 'user',
                'content':'Other text' if mode=='wrong-body' else 'Change the plan'},
                {'id':'assistant-1','peer_id':'hermes','content':'Okay.'}], 'page':1,'pages':1}
        else:result={'total_work_units':0 if mode=='unenrolled' else 1,
            'pending_work_units':1 if mode=='pending' else 0,'in_progress_work_units':0}
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',get)
    captures={'native-session':{'hermes_session':'hermes-id','hashes':[expected]}}
    errors=lambda:[{'session_name':'native-session','error_class':'StructuredOutputError'}] if mode=='failed' else []
    result=honcho_status('http://127.0.0.1:1234','workspace','user',captures,errors)
    assert complete('honcho',result)==(mode=='done',mode=='failed')
    assert all(r.get_method() in {'GET','POST'} for r in calls)
    assert all(r.get_method()=='GET' or '/messages/list?' in r.full_url for r in calls)
    assert result[0]['hermes_session']=='hermes-id'
