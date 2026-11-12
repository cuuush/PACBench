import json
from pathlib import Path
from types import SimpleNamespace

from pacbench.api_input_trace import record,rows
from pacbench.clock_context import VERSION,LEGACY,delivery,expected_prompt,instruction,judge_question

AT='2026-09-11T09:00:00-04:00'
QUESTION='Can you recap our pizza plan?'


def witness(tmp_path,monkeypatch):
    home=tmp_path/'hermes-home';home.mkdir()
    monkeypatch.setenv('HERMES_HOME',str(home))
    monkeypatch.setenv('PACBENCH_API_INPUT_TRACE',str(tmp_path/'api-inputs.jsonl'))
    body={'model':'selected-model','messages':[
        {'role':'system','content':'Native provider steering\n\n'+instruction(AT)},
        {'role':'user','content':QUESTION+'\n\n<memory-context>retained memory</memory-context>'},
        {'role':'assistant','content':'not stored reasoning','reasoning':'private'}]}
    record('pacbench-test-driver',{'id':'request1','request':body})
    hook={'session_id':'s1','query':QUESTION,'prefetch':'retained memory'}
    (tmp_path/'context.jsonl').write_text(json.dumps(hook)+'\n')
    turn=SimpleNamespace(at=AT,prompt=QUESTION,session_id='s1',prefetch_context='retained memory')
    return turn


def test_clock_is_system_context_and_actual_native_query_is_unchanged(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    result=delivery(tmp_path,turn)
    assert result['state']=='pass'
    assert result==delivery(tmp_path,turn,identifiers=result['api_input_ids'],hook_index=result['native_query_index'])
    text=(tmp_path/'api-inputs.jsonl').read_text()
    assert 'private' not in text and 'not stored reasoning' not in text
    assert expected_prompt(AT,QUESTION,VERSION)==QUESTION
    assert expected_prompt(AT,QUESTION,LEGACY)==instruction(AT)+'\n\n'+QUESTION


def test_missing_clock_at_actual_http_boundary_fails(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    data=rows(tmp_path/'api-inputs.jsonl');data[0]['messages'][0]['content']='Native prompt only'
    (tmp_path/'api-inputs.jsonl').write_text(json.dumps(data[0])+'\n')
    assert delivery(tmp_path,turn)['state']=='fail'


def test_clock_metadata_in_provider_query_fails(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    hook={'session_id':'s1','query':instruction(AT)+'\n\n'+QUESTION,'prefetch':'retained memory'}
    (tmp_path/'context.jsonl').write_text(json.dumps(hook)+'\n')
    assert delivery(tmp_path,turn)['state']=='fail'


def test_repeated_natural_question_uses_current_hook_and_request_not_old_turn(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    hook=json.loads((tmp_path/'context.jsonl').read_text())
    with (tmp_path/'context.jsonl').open('a') as f:f.write(json.dumps(hook)+'\n')
    body={'model':'selected-model','messages':[
        {'role':'system','content':instruction(AT)},
        {'role':'user','content':QUESTION+'\n\n<memory-context>retained memory</memory-context>'}]}
    record('pacbench-test-driver',{'id':'request2','request':body})
    result=delivery(tmp_path,turn,before=['request1'],hook_before=1)
    assert result['state']=='pass' and result['api_input_ids']==['request2']
    assert result['native_query_index']==1
    assert delivery(tmp_path,turn,identifiers=['missing'],hook_index=1)['state']=='fail'


def test_non_driver_requests_are_never_saved_as_hermes_inputs(tmp_path,monkeypatch):
    monkeypatch.setenv('PACBENCH_API_INPUT_TRACE',str(tmp_path/'api-inputs.jsonl'))
    record('pacbench-judge',{'id':'no-save','request':{}})
    assert not (tmp_path/'api-inputs.jsonl').exists()


def test_known_native_title_call_is_distinct_but_retained_in_the_evidence(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    title=Path(__file__).parent/'fixtures/native-hermes-title-prompt.txt'
    body={'model':'selected-model','messages':[{'role':'system','content':title.read_text()},
        {'role':'user','content':QUESTION}]}
    record('pacbench-test-driver',{'id':'native-title','request':body})
    result=delivery(tmp_path,turn)
    assert result['state']=='pass'
    assert result['actor_api_input_ids']==['request1']
    assert result['auxiliary_title_api_input_ids']==['native-title']
    assert len(result['api_input_ids'])==2
    assert delivery(tmp_path,turn,identifiers=['native-title'])['state']=='fail'


def test_arbitrary_non_clock_request_cannot_be_hidden_as_auxiliary(tmp_path,monkeypatch):
    turn=witness(tmp_path,monkeypatch)
    body={'model':'selected-model','messages':[{'role':'system','content':'Some other helper'},
        {'role':'user','content':QUESTION}]}
    record('pacbench-test-driver',{'id':'unclassified','request':body})
    assert delivery(tmp_path,turn)['state']=='fail'


def test_judge_retains_explicit_date_while_native_query_stays_natural():
    t=SimpleNamespace(at=AT,prompt=QUESTION,clock_context={'protocol':VERSION})
    assert judge_question(t)==instruction(AT)+'\n\n'+QUESTION
    assert t.prompt==QUESTION
    legacy=SimpleNamespace(at=AT,prompt=instruction(AT)+'\n\n'+QUESTION)
    assert judge_question(legacy)==legacy.prompt


def test_native_condition_binds_actual_system_to_native_saved_prompt(tmp_path,monkeypatch):
    import hashlib
    from pacbench.clock_context import NATIVE
    turn=witness(tmp_path,monkeypatch)
    path=tmp_path/'api-inputs.jsonl';data=rows(path)
    native='Stock Hermes prompt and native provider instructions'
    data[0]['messages'][0]['content']=native
    path.write_text(json.dumps(data[0])+'\n')
    turn.context_isolation={'state':'pass','system_prompt_sha256':hashlib.sha256(native.encode()).hexdigest()}
    result=delivery(tmp_path,turn,protocol=NATIVE)
    assert result['state']=='pass' and result['protocol']==NATIVE
    assert result==delivery(tmp_path,turn,protocol=NATIVE,identifiers=result['api_input_ids'],hook_index=result['native_query_index'])
    turn.clock_context=result
    assert judge_question(turn)==instruction(AT)+'\n\n'+QUESTION
    # Any appended instruction fails, even if it is absent from our known list.
    data[0]['messages'][0]['content']+='\nUse memory tools before answering.'
    path.write_text(json.dumps(data[0])+'\n')
    assert delivery(tmp_path,turn,protocol=NATIVE)['state']=='fail'
    data[0]['messages'][0]['content']=native+'\n\n'+instruction(AT)
    path.write_text(json.dumps(data[0])+'\n')
    assert delivery(tmp_path,turn,protocol=NATIVE)['state']=='fail'


def test_native_condition_requires_independent_saved_prompt_witness(tmp_path,monkeypatch):
    from pacbench.clock_context import NATIVE
    turn=witness(tmp_path,monkeypatch)
    assert delivery(tmp_path,turn,protocol=NATIVE)['state']=='fail'
