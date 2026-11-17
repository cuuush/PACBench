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


def test_describe_admission_distinguishes_old_run_guard_from_credit_without_releasing_unknowns():
    from copy import deepcopy
    from pacbench.funded_run_budget import describe_admission
    entry={'id':'legacy-run','baseline_ledger_rows':1,'limit_usd':'20','prepaid_safety':{'local_stop_floor_usd':'0'}}
    snapshot={'remaining_usd':'18.295731653','request_started':'2026-11-15T22:30:47+00:00','observed_at':'2026-11-15T22:30:48+00:00'}
    state={'calls':[{'state':'settled','accounted':'5','finished':'2026-10-01'},
        {'state':'settled','funded_run_id':'legacy-run','accounted':'2.808023860','finished':'2026-11-15T21:00:00+00:00'},
        {'state':'uncertain','funded_run_id':'legacy-run','accounted':'16.69743600'}]}
    original=deepcopy((state,entry,snapshot));d=describe_admission(state,entry,snapshot)
    assert d['named_run_guard_headroom_usd']=='0.494540140'
    assert d['prepaid_room_after_conservative_exposure_usd']=='1.598295653'
    assert d['observed_available_prepaid_usd']=='18.295731653'
    assert d['post_baseline_settled_before_credit_read_usd']=='2.808023860'
    assert d['proven_still_unreflected_actual_usd'] is None
    assert d['currently_enforced_admission_room_usd']=='0.494540140'
    assert (state,entry,snapshot)==original
    check_admission(deepcopy(state),deepcopy(entry),Decimal('.3'),snapshot)
    with pytest.raises(RuntimeError,match='run spending cap'):
        check_admission(deepcopy(state),deepcopy(entry),Decimal('.5'),snapshot)


def test_explicit_prepaid_alignment_keeps_unknowns_and_posted_costs_once(tmp_path,monkeypatch):
    from pacbench.funded_run_budget import align_existing_prepaid_limit,selected
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    calls=[{'state':'settled','accounted':'3','funded_run_id':'same','finished':'2026-11-15'},
           {'state':'uncertain','accounted':'16.7','funded_run_id':'same'}]
    entry={'id':'same','status':'active','baseline_ledger_rows':0,'limit_usd':'20',
           'prepaid_safety':{'auto_recharge':'off','evidence':'Synthetic confirmation'}}
    path=tmp_path/'ledger.json';path.write_text(json.dumps({'version':1,'config':cfg,'route_configs':{'luna-bedrock-global':cfg},'calls':calls,'funded_runs':{'same':entry}}))
    b=DirectBudget(path,profile='luna-bedrock-global');monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','same')
    snap={'remaining_usd':'18','request_started':'2026-11-16','observed_at':'2026-11-16'}
    event=align_existing_prepaid_limit(b,'same','Explicit remaining prepaid authorization',snap)
    assert event['new_limit_usd']=='21.0' and event['payable_room_usd']=='1.3'
    with b.locked(write=False) as state:
        assert state['calls']==calls
        e=selected(state);check_admission(state,e,Decimal('1.3'),snap)
        with pytest.raises(RuntimeError):check_admission(state,e,Decimal('1.300001'),snap)
        # Fresh wallet depletion still blocks, despite the new named envelope.
        with pytest.raises(RuntimeError,match='prepaid'):check_admission(state,e,Decimal('.3'),{**snap,'remaining_usd':'16.9'})
        e['limit_usd']='99'
        with pytest.raises(RuntimeError,match='authorization'):selected(state)
    with pytest.raises(ValueError):align_existing_prepaid_limit(b,'different','not a replacement run',snap)


@pytest.mark.parametrize('owner',[
    None,
    {'state':'live','pid':123,'start_ticks':10},
    {'state':'unknown'},
    {'state':'ended','pid':123,'start_ticks':10,'observed_start_ticks':11},
    {'state':'ended','reservation_id':'a-different-request'},
])
def test_unproven_reserved_owner_never_releases_hold_or_aligns(tmp_path,monkeypatch,owner):
    from pacbench.funded_run_budget import align_existing_prepaid_limit
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
    row={'id':'retained','state':'reserved','accounted':'1.23456789','reserved':'1.23456789','owner':owner}
    entry={'id':'existing','status':'active','baseline_ledger_rows':0,'limit_usd':'20',
           'prepaid_safety':{'auto_recharge':'off','evidence':'Synthetic confirmation'}}
    path=tmp_path/'ledger.json';path.write_text(json.dumps({'version':1,'config':cfg,'route_configs':{'luna-bedrock-global':cfg},'calls':[row],'funded_runs':{'existing':entry}}))
    b=DirectBudget(path,profile='luna-bedrock-global');monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','existing')
    before=path.read_bytes()
    snapshot={'remaining_usd':'18','request_started':'2026-11-16','observed_at':'2026-11-16'}
    with pytest.raises(RuntimeError,match='In-flight'):
        align_existing_prepaid_limit(b,'existing','Synthetic explicit continuation',snapshot)
    assert path.read_bytes()==before


def test_second_resume_cannot_take_existing_owner_lease(tmp_path):
    import fcntl
    path=tmp_path/'native-diagnostic-owner.lock';path.touch()
    with path.open('r') as first,path.open('r') as second:
        fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(first,fcntl.LOCK_UN)
        fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)
