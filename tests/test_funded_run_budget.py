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
    body={'model':b.model,'messages':[]}
    with pytest.raises(RuntimeError,match='authorization'):b.reserve('one-driver',body)
    with b.locked() as state:
        state['funded_runs'][entry['id']].update(status='active',prepaid_safety={'auto_recharge':'off','evidence':'Synthetic read-only test fixture'})
    r=b.reserve('one-driver',body)
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
    entry['prepaid_safety']={'local_stop_floor_usd':'.01'}
    with pytest.raises(RuntimeError,match='floor'):check_admission(state,entry,Decimal('.2'),snap)


def test_read_only_budget_checks_do_not_rewrite_financial_history(tmp_path):
    path=tmp_path/'ledger.json';cfg=dict(cap='20',model='deepseek/deepseek-v4.1-flash-1017',endpoint='deepinfra/fp8/flex',context_tokens=100,max_completion_tokens=10,prompt_per_million='1',completion_per_million='2')
    path.write_text(json.dumps(dict(version=1,config=cfg,calls=[]),indent=2));before=path.read_bytes();stamp=path.stat().st_mtime_ns
    b=DirectBudget(path,profile='deepseek-flex');b.snapshot()
    with b.locked(write=False) as state:assert state['calls']==[]
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stamp
