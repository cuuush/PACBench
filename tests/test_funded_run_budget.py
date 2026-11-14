import json
from decimal import Decimal
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench.funded_run_budget import enroll,check_admission


def test_new_prepaid_budget_preserves_history_and_reserves_atomically(tmp_path,monkeypatch):
    p=tmp_path/'ledger.json';old={'id':'historical','project':'history','state':'uncertain','accounted':'19.9'}
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    p.write_text(json.dumps(dict(version=1,config={'cap':'20'},route_configs={'luna-bedrock-global':cfg},calls=[old])))
    b=DirectBudget(p,profile='luna-bedrock-global');snapshot={'remaining_usd':'.5','request_started':'2026-11-14T01:00:00+00:00','observed_at':'2026-11-14T01:00:01+00:00'}
    entry=enroll(b,'scored-remainder','Explicit fresh prepaid authorization',snapshot)
    assert entry['limit_usd']=='0.5'
    monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID',entry['id']);monkeypatch.setenv('PACBENCH_RESERVATION_POLICY','openai-cache-total-shared-v1')
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:snapshot)
    body={'model':b.model,'messages':[]};r=b.reserve('one-driver',body)
    with pytest.raises(RuntimeError,match='cap reached'):b.reserve('two-driver',body)
    b.settle(r['id'],200,json.dumps({'usage':{'cost':'.001','prompt_tokens':1,'completion_tokens':2}}).encode())
    b.reserve('two-driver',body)
    state=json.loads(p.read_text());assert state['calls'][0]==old
    assert all(r['funded_run_id']==entry['id'] for r in state['calls'][1:])
    with pytest.raises(ValueError,match='already enrolled'):enroll(b,entry['id'],'same',snapshot)


def test_credit_covers_inflight_unknown_and_post_read_settlements():
    entry={'id':'x','baseline_ledger_rows':1,'limit_usd':'20'}
    state={'calls':[{'state':'uncertain','accounted':'19.9'},
        {'state':'reserved','accounted':'.2','funded_run_id':'x'},
        {'state':'settled','accounted':'.1','funded_run_id':'x','finished':'2026-11-14T01:00:02+00:00'}]}
    snap={'request_started':'2026-11-14T01:00:01+00:00','remaining_usd':'.5'}
    check_admission(state,entry,Decimal('.2'),snap)
    with pytest.raises(RuntimeError,match='prepaid'):check_admission(state,entry,Decimal('.20001'),snap)
