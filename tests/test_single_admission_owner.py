"""Synthetic ledger only; no remote channel, credential, or paid request."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench.single_admission_owner import SingleAdmissionOwner,PROTOCOL

@pytest.fixture
def owner(tmp_path,monkeypatch):
    for k in ['PACBENCH_ROLE_ROUTES','PACBENCH_FUNDED_RUN','PACBENCH_COHORT_ID','PACBENCH_COHORT_LIMIT_USD']:
        monkeypatch.delenv(k,raising=False)
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',
        prompt_per_million='.45',completion_per_million='.75',context_tokens=100,max_completion_tokens=50,
        request_scope='scored-week-roles')
    path=tmp_path/'ledger.json';path.write_text(json.dumps(dict(version=1,offline_fixture=PROTOCOL,
        config={'cap':'20'},route_configs={'luna-bedrock-global':cfg},calls=[])))
    b=DirectBudget(path,profile='luna-bedrock-global')
    return SingleAdmissionOwner(b,diagnostic_id='correction-native-dac',executor_id='memcal-executor',
        project='pacbench-synthetic-memcal-memory',cohort_id='a'*32,ceiling_usd='1')

def body(text='synthetic'):return dict(model='openai/gpt-6-luna',messages=[{'role':'user','content':text}],max_tokens=10)
def response(cost='.00001'):
    return 200,{},json.dumps(dict(id='gen-offline',model='openai/gpt-6-luna',provider='Amazon Bedrock',
        choices=[{'finish_reason':'stop','message':{'content':'ok'}}],
        usage=dict(cost=cost,prompt_tokens=2,completion_tokens=1))).encode()
def dispatch(owner,sender,attempt='a',payload=None):
    return owner.dispatch_offline(attempt_id=attempt,body=payload or body(),sender=sender,authenticated_executor_id='memcal-executor')

def test_duplicate_attempt_dispatches_once_and_settles_exact_response_cost(owner):
    sent=[]
    def sender(request):sent.append(request);return response()
    one=dispatch(owner,sender);two=dispatch(owner,sender)
    assert one==two and one['state']=='settled' and Decimal(one['actual_cost_usd'])==Decimal('.00001')
    assert len(sent)==1 and len(owner.budget.snapshot())==1
    with pytest.raises(ValueError,match='conflict'):dispatch(owner,sender,payload=body('different'))
    assert len(sent)==1


def test_simultaneous_duplicates_cannot_race_sender_and_distinct_attempts_share_cap(owner):
    entered=threading.Event();release=threading.Event();sent=[]
    def sender(r):sent.append(r);entered.set();assert release.wait(3);return response()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(dispatch,owner,sender)
        assert entered.wait(3)
        second=dispatch(owner,lambda r:pytest.fail('duplicate sent'))
        assert second['state']=='dispatching'
        release.set();assert first.result()['state']=='settled'
    assert len(sent)==1
    # A different attempt is an explicit independently reserved request.
    assert dispatch(owner,lambda r:response(),attempt='b',payload=body('new'))['state']=='settled'
    assert len(owner.budget.snapshot())==2


def test_ambiguous_dispatch_retains_full_hold_and_never_resends(owner):
    def interrupted(r):raise TimeoutError('synthetic interruption')
    with pytest.raises(TimeoutError):dispatch(owner,interrupted)
    rows=owner.budget.snapshot();assert rows[0]['accounted']==rows[0]['reserved']
    assert dispatch(owner,lambda r:pytest.fail('ambiguous request replayed'))['state']=='unknown'
    assert len(owner.budget.snapshot())==1


def test_admission_refusal_does_not_dispatch_and_live_channel_is_disabled(owner):
    owner.scope['limit_usd']='.000001'
    with pytest.raises(RuntimeError,match='envelope reached'):dispatch(owner,lambda r:pytest.fail('over cap'))
    assert owner.budget.snapshot()==[]
    with pytest.raises(RuntimeError,match='authenticated private'):owner.dispatch()
    with owner.budget.locked() as state:state.pop('offline_fixture')
    with pytest.raises(RuntimeError,match='live task ledger'):dispatch(owner,lambda r:pytest.fail('live dispatch'),attempt='new')


def test_wrong_executor_or_project_cannot_admit(owner):
    with pytest.raises(ValueError,match='identity'):
        owner.dispatch_offline(attempt_id='x',body=body(),sender=lambda r:pytest.fail('wrong executor'),authenticated_executor_id='other')
    assert owner.budget.snapshot()==[]
    with pytest.raises(ValueError,match='capability'):
        SingleAdmissionOwner(owner.budget,diagnostic_id='d',executor_id='e',project='pacbench-judge',cohort_id='a'*32,ceiling_usd='1')


def test_simultaneous_distinct_attempts_cannot_overbook_global_cap(owner):
    with owner.budget.locked() as state:
        state['calls'].append(dict(id='old',project='old',state='uncertain',accounted='19.99994'))
    def one(n):
        try:return dispatch(owner,lambda r:response(),attempt=str(n),payload=body(str(n)))['state']
        except RuntimeError:return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,range(2)))
    assert sorted(results)==['blocked','settled']
    assert sum(Decimal(r['accounted']) for r in owner.budget.snapshot())<=20


def test_response_above_estimate_stays_actual_not_clamped(owner):
    result=dispatch(owner,lambda r:response('.01'))
    assert result['state']=='settled' and Decimal(result['actual_cost_usd'])==Decimal('.01')
    row=owner.budget.snapshot()[0]
    assert Decimal(row['accounted'])==Decimal('.01')>Decimal(row['reserved'])
