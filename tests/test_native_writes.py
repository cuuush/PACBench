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
    states=supermemory_status(c,'native-tag',['explicit'],['required'])
    assert [s['id'] for s in states]==['captured','explicit']
    assert get.call_count==2 and listing.call_count==2
    assert all(call.kwargs['include_content'] is False for call in listing.call_args_list)
    assert complete('supermemory',states)==(True,False)


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
    states=openviking_status('http://127.0.0.1:1234',{},['explicit'],['real-session'])
    assert all(r.get_method()=='GET' for r in calls)
    assert [s['id'] for s in states]==['capture','explicit']
    assert complete('openviking',states)==(False,True)


@pytest.mark.parametrize('ready',[True,False])
def test_cross_session_readiness_is_cached_bound_to_actual_turns_and_auditable(tmp_path,monkeypatch,ready):
    root=tmp_path/'supermemory';first={'id':'edit','session':'old'}
    row={'id':'later','session':'fresh','at':'2026-10-22T11:00:00-04:00','requires':['edit']}
    last={**row,'id':'last','session':'another'}
    checks=[(first,None,None),(row,None,None),(last,None,None)]
    turn={'session_id':'actual-session','tool_results':[{'tool':'supermemory_store','success':True,
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
    from pacbench.native_writes import VERSION
    assert bool(audit_ready(checks,'shared',root,VERSION)) is not ready
    if ready:
        turn['tool_results'][0]['text']='{"id":"other-document"}'
        save(root/'turns/edit/turn.json',turn)
        assert audit_ready(checks,'shared',root,VERSION)
