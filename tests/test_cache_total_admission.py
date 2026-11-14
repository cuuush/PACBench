import json
from decimal import Decimal
import pytest
from pacbench.direct_accounting import DirectBudget


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
