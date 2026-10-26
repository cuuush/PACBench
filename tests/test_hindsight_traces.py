"""Native reflection can return original chunks inside an otherwise normal recall."""
from types import SimpleNamespace

import pytest

from pacbench.hindsight_traces import convert,category,capture
from pacbench.followups import tool_profile
from pacbench.usage import attribute_provider_paths


def recorded_rows(chunks):
    user={'role':'user','content':'Public query'}
    call={'id':'native-call','function':{'name':'recall','arguments':'{"query":"Public query"}'}}
    rows=[]
    for index,names,messages in [(0,['recall'],[user]),(1,['done'],[user,
            {'role':'assistant','tool_calls':[call]},
            {'role':'tool','tool_call_id':'native-call','content':{'memories':[],'chunks':chunks}}])]:
        rows.append({'id':str(index),'trace_id':'operation','scope':'reflect','status':'success',
            'started_at':f'2026-10-26T10:00:0{index}+00:00','ended_at':f'2026-10-26T10:00:0{index+1}+00:00',
            'input':messages,'llm_info':{'tool_calls':names}})
    return rows


def make_turn():
    return SimpleNamespace(tool_calls=[{'name':'hindsight_reflect','input':{'query':'Public query'}}],
        tool_results=[{'success':True,'text':'Synthesized answer'}],provider_traces=[])


@pytest.mark.parametrize('chunks,source',[
    ({'chunk-1':{'chunk_text':'Original message text'}},True),({},False)])
def test_internal_recall_source_is_detected_without_leaking_model_prompts(chunks,source):
    turn=make_turn();turn.provider_traces=convert(recorded_rows(chunks),turn)
    profile=tool_profile(turn)
    assert profile['source_fallback_attempted'] is source
    assert profile['provider_internal_tool_calls']==2
    assert not profile['unclassified_operations']
    calls=turn.provider_traces[0]['record']['toolCalls']
    assert calls[0]['args']=={'query':'Public query'}
    assert calls[0]['result']['chunks']==chunks
    assert calls[1]['capture']=='native-name-only'
    assert 'input' not in turn.provider_traces[0]['record']


def test_native_traces_deduplicate_growing_message_history():
    turn=make_turn();rows=recorded_rows({})
    rows.append({**rows[-1],'id':'third','started_at':'2026-10-26T10:00:03+00:00',
        'ended_at':'2026-10-26T10:00:04+00:00','llm_info':{'tool_calls':[]}})
    assert len(convert(rows,turn)[0]['record']['toolCalls'])==2


def test_incomplete_native_reflection_traces_never_assert_clean_retention(monkeypatch):
    monkeypatch.setattr('pacbench.hindsight_traces.read',lambda env:[])
    monkeypatch.setattr('pacbench.hindsight_traces.time.monotonic',iter([0,7]).__next__)
    traces=capture({}, {'ids':[]}, make_turn())
    assert traces[0]['error']=='Native reflection trace coverage incomplete'
    turn=make_turn();turn.provider_traces=traces
    assert tool_profile(turn)['unclassified_operations']==1


def test_late_reflection_cannot_supply_an_earlier_hermes_request():
    turn=make_turn();turn.provider_traces=convert(recorded_rows({'c':{'chunk_text':'source'}}),turn)
    attribute_provider_paths(turn,{'model_calls':[{'role':'hermes','started':'2026-10-26T10:00:00+00:00'}]})
    profile=tool_profile(turn)
    assert not profile['source_fallback_attempted']
    assert profile['provider_internal_tool_calls']==2


def test_unobserved_recall_arguments_are_unknown_and_expand_is_source():
    assert category({'toolName':'recall','result':None})=='unclassified'
    assert category({'toolName':'expand','args':{'depth':'document'}})=='source_evidence'
