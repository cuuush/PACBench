import json
from decimal import Decimal
import pytest
from pacbench.direct_accounting import DirectBudget


def test_native_memos_filter_limit_never_waives_answer_or_harness_truncation():
    from pacbench.direct_accounting import native_memos_filter_limit
    row={'project':'pacbench-synthetic-memos-memory','model':'openai/gpt-6-luna',
        'native_request_parameters':{'reasoning':{'effort':'none'},'max_tokens':160},
        'effective_output_maximum':160,'effective_output_ceiling_condition':32768}
    assert native_memos_filter_limit(row)
    assert not native_memos_filter_limit({**row,'project':'pacbench-synthetic-memos-driver'})
    assert not native_memos_filter_limit({**row,'effective_output_maximum':32768})
    assert not native_memos_filter_limit({**row,'native_request_parameters':{'max_tokens':160,'reasoning':{'effort':'medium'}}})


def test_cache_total_future_admission_retains_old_holds_and_rejects_ttl(tmp_path,monkeypatch):
    path=tmp_path/'ledger.json';old={'id':'old','project':'old','state':'uncertain','accounted':'19.503585917232'}
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    path.write_text(json.dumps(dict(version=1,config={'cap':'20'},route_configs={'luna-bedrock-global':cfg},calls=[old])))
    monkeypatch.setenv('PACBENCH_RESERVATION_POLICY','openai-cache-total-shared-v1')
    b=DirectBudget(path,profile='luna-bedrock-global');body={'model':b.model,'messages':[{'role':'user','content':'synthetic'}]}
    for addition in [{'prompt_cache_options':{'ttl':'12h'}},{'messages':[{'role':'user','content':[{'type':'text','text':'x','prompt_cache_breakpoint':{'ttl':'12h'}}]}]}]:
        with pytest.raises(ValueError,match='TTL'):b.reserve('unit-memory',{**body,**addition})
    r=b.reserve('unit-memory',body);rows=json.loads(path.read_text())['calls']
    assert rows[0]==old and Decimal(rows[1]['reserved'])==Decimal('.3265')
    assert 'max_tokens' not in r['request'] and 'max_completion_tokens' not in r['request']
    with pytest.raises(RuntimeError,match='spending cap'):b.reserve('other-memory',body)
    b.settle(r['id'],200,json.dumps({'model':b.model,'usage':{'cost':'.001','prompt_tokens':2,'completion_tokens':3},'choices':[]}).encode())
    b.reserve('other-memory',body);assert json.loads(path.read_text())['calls'][0]==old


def test_predispatch_release_is_evidence_bound_idempotent_and_not_settled_zero(tmp_path):
    import hashlib
    path=tmp_path/'ledger.json';cfg=dict(cap='20',model='deepseek/deepseek-v4.1-flash-1017',endpoint='deepinfra/fp8/flex',context_tokens=100,max_completion_tokens=50,prompt_per_million='1',completion_per_million='2')
    path.write_text(json.dumps(dict(version=1,config=cfg,calls=[])));b=DirectBudget(path)
    r=b.reserve('unit',{'model':b.model,'messages':[]})
    error={'error':{'code':404,'message':'No endpoints found that can handle the requested parameters. To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection','metadata':{'failed_routing_step':'Filter by Parameters','routing_funnel':[{'step':'Filter by Allowed Providers','endpoint_count':1}]}}}
    raw=json.dumps(error).encode();b.settle(r['id'],404,raw,body_complete=True)
    before=json.loads(path.read_text())['calls'][0]['accounted'];sha=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):b.release_routing_rejection(r['id'],evidence_sha256='0'*64)
    event=b.release_routing_rejection(r['id'],evidence_sha256=sha)
    assert event['released_bound_usd']==before and event['provider_reported_cost_usd'] is None
    assert b.release_routing_rejection(r['id'],evidence_sha256=sha)==event
    state=json.loads(path.read_text());assert len(state['reservation_releases'])==1
    assert state['calls'][0]['state']=='rejected_no_dispatch' and state['calls'][0]['accounted']=='0'


def test_explicit_temperature_omission_preserves_native_parameter_evidence(tmp_path,monkeypatch):
    path=tmp_path/'ledger.json';cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    path.write_text(json.dumps(dict(version=1,config={'cap':'20'},route_configs={'luna-bedrock-global':cfg},calls=[])))
    monkeypatch.setenv('PACBENCH_LUNA_COMPATIBILITY','temperature-omitted-default-v1')
    b=DirectBudget(path,profile='luna-bedrock-global');r=b.reserve('unit-memory',{'model':b.model,'temperature':0,'messages':[]})
    assert 'temperature' not in r['request']
    row=json.loads(path.read_text())['calls'][0]
    assert row['native_request_parameters']['temperature']==0 and row['omitted_unsupported_parameters']==['temperature']


def test_explicit_output_ceiling_is_transmitted_preserves_smaller_native_and_stops_truncation(tmp_path,monkeypatch):
    path=tmp_path/'ledger.json';cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    path.write_text(json.dumps(dict(version=1,config={'cap':'20'},route_configs={'luna-bedrock-global':cfg},calls=[])))
    monkeypatch.setenv('PACBENCH_EFFECTIVE_OUTPUT_TOKENS','32768');monkeypatch.setenv('PACBENCH_RESERVATION_POLICY','openai-cache-total-shared-v1')
    stop=tmp_path/'STOP';monkeypatch.setenv('PACBENCH_STOP_FILE',str(stop))
    b=DirectBudget(path,profile='luna-bedrock-global');body={'model':b.model,'messages':[]}
    first=b.reserve('health-memory',{**body,'max_tokens':160});assert first['request']['max_tokens']==160
    b.settle(first['id'],200,json.dumps({'model':b.model,'usage':{'cost':'.001','prompt_tokens':2,'completion_tokens':1},'choices':[]}).encode())
    r=b.reserve('unit-driver',body);assert r['request']['max_completion_tokens']==32768
    row=json.loads(path.read_text())['calls'][-1];assert Decimal(row['reserved'])==Decimal('.278884') and row['native_request_parameters']=={}
    b.settle(r['id'],200,json.dumps({'model':b.model,'usage':{'cost':'.002','prompt_tokens':2,'completion_tokens':32768},'choices':[{'finish_reason':'length'}]}).encode())
    assert json.loads(stop.read_text())['reason']=='output_truncated'
    with pytest.raises(RuntimeError,match='truncated|admission stopped'):b.reserve('unit-driver',body)
