"""Native reflection can return original chunks inside an otherwise normal recall."""
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_legacy_native_tools_with_final_json_schema_remain_supported():
    rows=recorded_rows({})
    rows[-1]['llm_info']['request']={'response_schema':'ReflectResponse'}
    assert len(convert(rows,make_turn())[0]['record']['toolCalls'])==2


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


@pytest.mark.parametrize('memories,chunks,expected', [
    ([], {'c1': {'chunk_text': 'raw'}}, 'source_messages'),
    ([{'text': 'retained fact', 'chunk_id': 'c1'}], {'c1': {'chunk_text': 'citation'}}, 'memory_search'),
    ([{'text': 'retained fact'}], {'c1': {'chunk_text': 'raw'}}, 'unclassified'),
    ([{'text': 'retained fact', 'chunk_id': 'c1'}], {'c1': {'chunk_text': 'citation'}, 'c2': {'chunk_text': 'raw'}}, 'unclassified'),
    ([{'text': 'retained fact'}], {}, 'memory_search'),
    (None, {'c1': {'chunk_text': 'raw'}}, 'unclassified'),
    ([], {'c1': {}}, 'unclassified'),
])
def test_uniform_provenance_requires_linked_citations(memories, chunks, expected):
    call={'toolName':'recall','result':{'memories':memories,'chunks':chunks}}
    assert category(call, provenance_version='source-provenance-v2') == expected


def test_uniform_mixed_path_is_review_not_confirmed_source():
    turn=make_turn()
    turn.provider_traces=convert(recorded_rows({'c':{'chunk_text':'source'}}),turn)
    call=turn.provider_traces[0]['record']['toolCalls'][0]
    call['result']['memories']=[{'text':'retained'}]
    legacy=tool_profile(turn)
    uniform=tool_profile(turn, provenance_version='source-provenance-v2')
    assert legacy['source_fallback_attempted']
    assert not uniform['source_fallback_attempted'] and uniform['unclassified_operations']==1


def test_complete_traces_stop_after_native_flush_without_waiting_for_deadline(monkeypatch):
    read=Mock(return_value=recorded_rows({}))
    monkeypatch.setattr('pacbench.hindsight_traces.read',read)
    monkeypatch.setattr('pacbench.hindsight_traces.time.monotonic',iter([0,1]).__next__)
    traces=capture({}, {'ids':[]}, make_turn())
    assert read.call_count==1
    assert len(traces)==1 and traces[0]['record']['complete']


def schema_rows(repeated=False):
    """1.2's native tools_via_schema role/content and request metadata format."""
    import json
    tools=[{'name':n,'description':d,'parameters':{'type':'object'}} for n,d in
           [('search_observations','Search observations.'),('recall','Search raw memories.'),('done','Your final answer.')]]
    system={'role':'system','content':'Native instructions\n\n## Functions\n\nYou act by calling these functions:\n\n'+json.dumps(tools)}
    history=[system,{'role':'user','content':'Public query'}];rows=[]
    calls=[('recall',{'query':'Public query'},{'memories':[],'chunks':{'c':{'chunk_text':'original source'}}}),
           ('recall',{'query':'Public query'},{'memories':[],'chunks':{}})] if repeated else [
           ('search_observations',{'query':'Public query'},{'observations':[]}),
           ('recall',{'query':'Public query'},{'memories':[],'chunks':{'c':{'chunk_text':'original source'}}})]
    for index,(name,args,result) in enumerate(calls+[('done',{'answer':'Answer'},None)]):
        instruction=f"Call `{name}` now ({next(t['description'] for t in tools if t['name']==name).rstrip('.')}). Reply with only its arguments, as one JSON object."
        messages=[dict(m) for m in history];messages[-1]['content']+='\n\n'+instruction
        rows.append({'id':f'native-{index}','trace_id':'schema-operation','scope':'reflect' if name=='done' else 'reflect_tool_call',
            'status':'success','started_at':f'2026-10-26T10:00:0{index}+00:00',
            'ended_at':f'2026-10-26T10:00:0{index+1}+00:00','input':messages,'output':json.dumps(args),
            'llm_info':{'request':{'response_schema':name}}})
        if result is not None:
            history.extend([{'role':'assistant','content':json.dumps({'call':{'name':name,'arguments':args}})},
                            {'role':'user','content':f'Result of `{name}`:\n'+json.dumps(result)}])
    return rows


def test_schema_tools_pair_native_outputs_and_replayed_results():
    trace=convert(schema_rows(),make_turn())[0]
    assert trace['attribution']=='explicit-query-match'
    assert trace['record']['query']=='Public query'
    assert trace['record']['complete']
    operations=trace['record']['toolCalls']
    assert [c['toolName'] for c in operations]==['search_observations','recall','done']
    assert operations[1]['result']['chunks']['c']['chunk_text']=='original source'
    turn=make_turn();turn.provider_traces=[trace]
    assert tool_profile(turn)['source_fallback_attempted']
    assert not tool_profile(turn)['unclassified_operations']
    assert 'input' not in trace['record'] and 'output' not in trace['record']


def test_schema_repeated_identical_calls_keep_separate_result_occurrences():
    calls=convert(schema_rows(repeated=True),make_turn())[0]['record']['toolCalls']
    assert calls[0]['id']!=calls[1]['id']
    assert calls[0]['args']==calls[1]['args']
    assert calls[0]['result']['chunks'] and not calls[1]['result']['chunks']


def test_schema_free_choice_output_is_recognized():
    import json
    rows=schema_rows();row=rows[1]
    original='Call `recall` now (Search raw memories). Reply with only its arguments, as one JSON object.'
    free=('Decide your next step: call exactly ONE of the declared functions. Reply with one JSON object, '
          '{"call": {"name": <function name>, "arguments": {...}}}.')
    row['input'][-1]['content']=row['input'][-1]['content'].removesuffix(original)+free
    row['llm_info']['request']['response_schema']='next_call'
    row['output']=json.dumps({'call':{'name':'recall','arguments':json.loads(row['output'])}})
    assert convert(rows,make_turn())[0]['record']['toolCalls'][1]['result']['chunks']


@pytest.mark.parametrize('mutation',['replay','result','query','declaration','output','duplicate-id'])
def test_schema_ambiguous_or_changed_native_evidence_fails_closed(mutation):
    import json
    rows=schema_rows()
    if mutation=='replay':rows[2]['input'][2]['content']=json.dumps({'call':{'name':'recall','arguments':{}}})
    elif mutation=='result':rows[2]['input'][3]['content']='Result of `search_observations`:\n{"changed":true}'
    elif mutation=='query':rows[2]['input'][1]['content']='Different query'
    elif mutation=='declaration':rows[1]['llm_info']['request']['response_schema']='unknown'
    elif mutation=='output':rows[1]['output']='not JSON'
    else:rows[1]['id']=rows[0]['id']
    with pytest.raises(ValueError):convert(rows,make_turn())


def test_raw_native_snapshot_survives_conversion_failure_privately(tmp_path,monkeypatch):
    import json,hashlib,stat
    rows=schema_rows();rows[1]['output']='incomplete JSON'
    monkeypatch.setattr('pacbench.hindsight_traces.read',lambda env:rows)
    traces=capture({'PACBENCH_HINDSIGHT_TRACE_DIR':str(tmp_path)}, {'ids':[]},make_turn())
    assert traces[0]['error'].startswith('Native trace observation failed:')
    witness=traces[0]['native_snapshot'];path=tmp_path/(witness['sha256']+'.json')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==witness['sha256']
    assert json.loads(path.read_text())==rows
    assert stat.S_IMODE(path.stat().st_mode)==0o600
