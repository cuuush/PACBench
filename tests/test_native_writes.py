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
    pages=[{'documents':[{'id':'unrelated','metadata':{'session_id':'another'}}],
            'pagination':{'current_page':1,'total_pages':2}},
           {'documents':[{'id':'captured','metadata':{'session_id':'required'}}],
            'pagination':{'current_page':2,'total_pages':2}}]
    listing=Mock(side_effect=[model(v) for v in pages])
    get=Mock(side_effect=lambda namespace,id:model({'id':id,'system':{'status':'done','dreamingStatus':'done'}}))
    c=SimpleNamespace(list=listing,documents=SimpleNamespace(get=get))
    states=supermemory_status(c,'native-tag',[],['required'])
    assert [s['id'] for s in states]==['captured']
    assert get.call_count==1 and listing.call_count==2
    assert all(call.args == ('native-tag','documents') for call in listing.call_args_list)
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
    listing=Mock(return_value=model({'documents':[{'id':'doc','metadata':metadata}],
        'pagination':{'current_page':1,'total_pages':1}}))
    get=Mock(return_value=model({'id':'doc','metadata':metadata,'content':content,
        'system':{'status':'done','dreaming_status':'dreaming' if mode=='pending' else 'done'}}))
    client=SimpleNamespace(list=listing,documents=SimpleNamespace(get=get))
    states=supermemory_status(client,'native-tag',[],['required'],
        [{'session':'required','user':capture['prompt'],'binding':digest(capture)}])
    assert complete('supermemory',states)==(mode=='done',False)
    assert all(call.args == ('native-tag','documents') for call in listing.call_args_list)


def test_supermemory_all_required_instructions_must_be_captured():
    def model(value):return SimpleNamespace(model_dump=lambda **kwargs:value)
    metadata={'type':'conversation','session_id':'required'}
    client=SimpleNamespace(list=Mock(return_value=model({'documents':[{'id':'doc','metadata':metadata}],
            'pagination':{'current_page':1,'total_pages':1}})),documents=SimpleNamespace(
        get=Mock(return_value=model({'id':'doc','metadata':metadata,
            'content':'[role: user]\nFirst update\n[user:end]',
            'system':{'status':'done','dreaming_status':'done'}}))))
    states=supermemory_status(client,'native-tag',[],['required'],[
        {'session':'required','user':'First update','binding':'first'},
        {'session':'required','user':'Second update','binding':'second'}])
    assert states[-1]['missing_inputs']==['second']
    assert complete('supermemory',states)==(False,False)


def test_supermemory_v2_audit_rejects_status_only_capture_proof():
    from pacbench.native_writes import write_inputs,ready_proof
    from pacbench.followups import digest
    turn={'session_id':'required','prompt':'Move it to Sunday','tool_results':[]}
    version='accepted-native-writes-v2'
    bound={'dependencies':['edit'],'inputs':write_inputs([turn],'supermemory',version),'turn_hashes':[digest(turn)]}
    state={'id':'native-doc','status':'done','dreaming_status':'done'}
    record={'version':version,'binding':digest(bound),**bound,'state':'ready',
            'observed':{'ready':True,'processing_overrides':False,'snapshots':[[state]]}}
    assert not ready_proof(record,bound)
    state['verified_inputs']=[digest(bound['inputs']['captures'][0])]
    assert ready_proof(record,bound)


@pytest.mark.parametrize('mode',['accepted','failed','truncated','wrong-result','delete'])
def test_honcho_explicit_readiness_uses_actual_accepted_tool_input(mode):
    from pacbench.native_writes import write_inputs
    turn={'session_id':'sid','prompt':'Move it to Sunday','tool_calls':[
        {'name':'honcho_conclude','input':{'conclusion':'Sunday at 11',**({'delete_id':'x'} if mode=='delete' else {})}}],
        'tool_results':[{'tool':'honcho_conclude','success':mode!='failed','truncated':mode=='truncated',
            'text':json.dumps({'result':'Other result' if mode=='wrong-result' else 'Conclusion saved for user: Sunday at 11'})}]}
    inputs=write_inputs([turn],'honcho')
    assert bool(inputs['conclusions']) is (mode=='accepted')
    assert 'conclusions' not in write_inputs([turn],'honcho','accepted-native-writes-v1')


@pytest.mark.parametrize('mode',['done','wrong-observer','wrong-target','wrong-session','wrong-content','missing-write','missing-instruction'])
def test_honcho_persisted_explicit_writes_do_not_wait_on_unrelated_capture(monkeypatch,mode):
    import io
    from pacbench.native_writes import honcho_status
    conclusion={'id':'native-conclusion','content':'Sunday at 11','observer_id':'hermes','observed_id':'user','session_id':'native-session'}
    key={'wrong-observer':'observer_id','wrong-target':'observed_id','wrong-session':'session_id','wrong-content':'content'}.get(mode)
    if key:conclusion[key]='other'
    calls=[]
    def read(request,**kwargs):
        calls.append(request)
        if '/conclusions/list?' in request.full_url:
            result={'items':[conclusion],'page':1,'pages':1}
        else:result={'items':[],'page':1,'pages':0}
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',read)
    write={'content':'Sunday at 11','observer_id':'hermes','observed_id':'user','input_binding':'actual-input'}
    entry={'hermes_session':'hermes-id','hashes':['actual-input-hash'],'input_bindings':['actual-input'],'conclusions':[write]}
    if mode=='missing-write':entry['conclusions'].append({**write,'content':'Another actual accepted write'})
    if mode=='missing-instruction':entry['input_bindings'].append('second-input')
    errors=Mock(return_value=[])
    states=honcho_status('http://127.0.0.1:1234','workspace','user',{'native-session':entry},errors)
    assert complete('honcho',states)==(mode=='done',False)
    if mode=='done':
        assert states[0]['write_path']=='explicit_conclusions'
        assert states[0]['conclusion_ids']==['native-conclusion']
        assert len(calls)==1
        errors.assert_not_called()


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


@pytest.mark.parametrize('mode',['done','stale-assistant','duplicate-pair','wrong-document'])
def test_v3_supermemory_explicit_write_never_suppresses_final_capture(mode):
    def model(value):return SimpleNamespace(model_dump=lambda **kwargs:value)
    metadata={'type':'conversation','session_id':'required'}
    listing=Mock(return_value=model({'documents':[{'id':'conversation','metadata':metadata}],
        'pagination':{'current_page':1,'total_pages':1}}))
    content='[role: user]\nMove it\n[user:end]\n[role: assistant]\n'+('Old answer' if mode=='stale-assistant' else 'Moved.')+'\n[assistant:end]'
    def get(namespace,id):
        return model({'id':id,'metadata':metadata if id=='conversation' else {},'content':content,
            'system':{'status':'done','dreamingStatus':'done'}})
    client=SimpleNamespace(list=listing,documents=SimpleNamespace(get=get))
    capture={'session':'required','user':'Move it','assistant':'Moved.','binding':'actual-turn',
             'document_id':'other' if mode=='wrong-document' else 'conversation'}
    captures=[capture,capture] if mode=='duplicate-pair' else [capture]
    states=supermemory_status(client,'native-tag',['explicit'],['required'],captures)
    listing.assert_called_once()
    assert any(s['id']=='explicit' for s in states)
    assert complete('supermemory',states)==(mode=='done',False)


@pytest.mark.parametrize('mode',['done','assistant-pending','not-enrolled','user-only','explicit-missing','duplicate-turn'])
def test_v3_honcho_binds_both_sides_and_causal_processing_without_requiring_new_facts(monkeypatch,mode):
    import io
    from pacbench.native_writes import honcho_status
    messages=[{'id':'u','peer_id':'user','content':'Move it'}, {'id':'a','peer_id':'hermes','content':'Moved.'}]
    if mode=='user-only':messages.pop()
    conclusion={'id':'explicit','content':'Moved','session_id':'native','observer_id':'hermes','observed_id':'user'}
    def read(request,**kwargs):
        value={'items':([] if mode=='explicit-missing' else [conclusion]) if '/conclusions/list?' in request.full_url else messages,'page':1,'pages':1}
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',read)
    chunks=[{'peer':'user','content':'Move it'},{'peer':'hermes','content':'Moved.'}]
    captures=[{'binding':'input','chunks':chunks}]
    bindings=['input']
    if mode=='duplicate-turn':captures.append(captures[0]);bindings.append('input')
    expected={'hermes_session':'real','hashes':[],'input_bindings':bindings,'capture_chunks':captures,
        'conclusions':[{'content':'Moved','observer_id':'hermes','observed_id':'user','input_binding':'input'}]}
    work=[{'id':i,'task_type':'representation','session_name':'native','message_id':mid,
        'processed':not(mode=='assistant-pending' and mid=='a'),'native_error':False} for i,mid in enumerate(['u','a'])]
    if mode=='not-enrolled':work=[]
    processing={'messages':[{'public_id':mid,'session_name':'native','peer_name':peer,'seq_in_session':i}
        for i,(mid,peer) in enumerate([('u','user'),('a','hermes')])], 'queue':work}
    states=honcho_status('http://127.0.0.1:1234','workspace','user',{'native':expected},lambda:[],
        read_processing=lambda:processing,witnesses={})
    assert complete('honcho',states)==(mode=='done',False)
    if mode=='done':
        assert states[0]['captured_ids']==['u','a'] and states[0]['conclusion_ids']==['explicit']
        assert states[0]['explicit_writes_verified']


def test_v3_honcho_retains_observed_success_when_native_cleanup_removes_queue_row(monkeypatch):
    import io
    from pacbench.native_writes import honcho_status
    messages=[{'id':'u','content':'Update','peer_id':'user'}]
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',lambda *a,**k:io.BytesIO(json.dumps({'items':messages,'page':1,'pages':1}).encode()))
    expected={'hermes_session':'real','input_bindings':['input'],'conclusions':[],
        'capture_chunks':[{'binding':'input','chunks':[{'peer':'user','content':'Update'}]}]}
    processing={'messages':[{'public_id':'u','session_name':'native','peer_name':'user','seq_in_session':0}],
        'queue':[{'id':7,'task_type':'representation','session_name':'native','message_id':'u','processed':True,'native_error':False}]}
    retained={}
    first=honcho_status('http://127.0.0.1:1234','workspace','user',{'native':expected},lambda:[],read_processing=lambda:processing,witnesses=retained)
    assert complete('honcho',first)==(True,False)
    processing['queue']=[]
    later=honcho_status('http://127.0.0.1:1234','workspace','user',{'native':expected},lambda:[],read_processing=lambda:processing,witnesses=retained)
    assert complete('honcho',later)==(True,False)


def test_v3_supermemory_audit_replays_exact_final_pair_and_rejects_deleted_assistant():
    from copy import deepcopy
    from pacbench.native_writes import VERSION,write_inputs,ready_proof
    from pacbench.followups import digest
    turn={'session_id':'session','prompt':'Move it','answer':'Moved.'}
    inputs=write_inputs([turn],'supermemory')
    capture=inputs['captures'][0]
    inputs['capture_description']={'provider':'supermemory','captures':[{'binding':digest(capture),'session':'session',
        'document_id':'conversation','user':'Move it','assistant':'Moved.'}]}
    bound={'dependencies':['edit'],'inputs':inputs,'turn_hashes':[digest(turn)]}
    state={'id':'conversation','status':'done','dreaming_status':'done','verified_inputs':[digest(capture)],
        'capture_document':{'id':'conversation','metadata':{'session_id':'session','type':'conversation'},
            'content':'[role: user]\nMove it\n[user:end]\n[role: assistant]\nMoved.\n[assistant:end]'}}
    record={'version':VERSION,'binding':digest(bound),**bound,'state':'ready',
        'observed':{'ready':True,'processing_overrides':False,'snapshots':[[state]]}}
    assert ready_proof(record,bound)
    state['capture_document']['content']='[role: user]\nMove it\n[user:end]'
    assert not ready_proof(record,bound)


def test_v3_honcho_cannot_pair_user_with_later_turn_assistant(monkeypatch):
    import io
    from pacbench.native_writes import honcho_status
    messages=[{'id':mid,'peer_id':peer,'content':text} for mid,peer,text in [
        ('u1','user','First'),('u2','user','Second'),('a2','hermes','Done')]]
    monkeypatch.setattr('pacbench.native_writes.urllib.request.urlopen',lambda *a,**k:io.BytesIO(json.dumps({'items':messages,'page':1,'pages':1}).encode()))
    expected={'hermes_session':'real','input_bindings':['first'],'conclusions':[],
        'capture_chunks':[{'binding':'first','chunks':[{'peer':'user','content':'First'},{'peer':'hermes','content':'Done'}]}]}
    processing={'messages':[{'public_id':m['id'],'peer_name':m['peer_id'],'session_name':'native','seq_in_session':i} for i,m in enumerate(messages)],
        'queue':[{'id':i,'task_type':'representation','session_name':'native','message_id':m['id'],'processed':True,'native_error':False} for i,m in enumerate(messages)]}
    states=honcho_status('http://127.0.0.1:1234','workspace','user',{'native':expected},lambda:[],read_processing=lambda:processing)
    assert not states[0]['captures_verified'] and complete('honcho',states)==(False,False)


@pytest.mark.parametrize('mode',['done','drop-assistant','wrong-queue-session','reordered-turn'])
def test_v3_honcho_audit_checks_every_chunk_and_cross_turn_sequence(mode):
    import hashlib
    from pacbench.native_writes import VERSION,write_inputs,ready_proof
    from pacbench.followups import digest
    turns=[{'session_id':'real','prompt':f'Update {i}','answer':f'Done {i}'} for i in range(2)]
    inputs=write_inputs(turns,'honcho');descriptions=[];witnesses=[];work=[];ids=[]
    for i,capture in enumerate(inputs['captures']):
        chunks=[{'peer':peer,'content':capture[key]} for peer,key in [('user','prompt'),('hermes','answer')]]
        descriptions.append({'binding':digest(capture),'session':'native','chunks':chunks})
        observed=[]
        for j,chunk in enumerate(chunks):
            mid=f'm{i}-{j}';ids.append(mid)
            observed.append({'id':mid,'peer':chunk['peer'],'sequence':i*2+j,
                'text_sha256':hashlib.sha256(chunk['content'].encode()).hexdigest()})
            work.append({'id':i*2+j,'session_name':'native','task_type':'representation','message_id':mid,'processed':True,'native_error':False})
        witnesses.append({'binding':digest(capture),'session':'native','chunks':observed})
    inputs['capture_description']={'provider':'honcho','owner':'user','assistant':'hermes','captures':descriptions}
    state={'id':'native','hermes_session':'real','status':'completed','native_error':False,
        'verified_inputs':[digest(c) for c in inputs['captures']],'captures_verified':True,'captured_ids':ids,
        'capture_witnesses':witnesses,'processing_witnesses':work,'explicit_writes_verified':True,'conclusion_ids':[]}
    if mode=='drop-assistant':state['captured_ids'].pop()
    if mode=='wrong-queue-session':work[-1]['session_name']='other'
    if mode=='reordered-turn':
        for j,chunk in enumerate(witnesses[1]['chunks']):chunk['sequence']=j
    bound={'dependencies':['first','second'],'inputs':inputs,'turn_hashes':[digest(t) for t in turns]}
    record={'version':VERSION,'binding':digest(bound),**bound,'state':'ready',
        'observed':{'ready':True,'processing_overrides':False,'snapshots':[[state]]}}
    assert ready_proof(record,bound) is (mode=='done')
