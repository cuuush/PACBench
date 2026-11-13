"""No upstream calls: admission, concurrency, stream failures and real SDK transport."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from pacbench.direct_accounting import DirectBudget,MODEL,VERSION


@pytest.fixture
def budget(tmp_path):
    path=tmp_path/'ledger.json'
    path.write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':100,
       'prompt_per_million':'1','completion_per_million':'2','max_completion_tokens':50,
       'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    return DirectBudget(path)


def reply(cost='0.00001'):
    return json.dumps({'id':'gen-offline','model':MODEL,'provider':'DeepInfra',
        'usage':{'cost':cost,'prompt_tokens':3,'completion_tokens':2},'choices':[]}).encode()


def test_missing_history_is_not_recreated(tmp_path):
    with pytest.raises(FileNotFoundError):DirectBudget(tmp_path/'missing')
    assert not (tmp_path/'missing').exists()


def test_measured_import_429_stops_all_projects_without_releasing_hold(budget,tmp_path,monkeypatch):
    stop=tmp_path/'STOP';monkeypatch.setenv('PACBENCH_DIRECT_STOP_ON_429',str(stop))
    row=budget.reserve('first',{'model':MODEL,'messages':[]})
    before=budget.snapshot()[0]['accounted']
    budget.settle(row['id'],429,b'{"error":{"message":"rate limited"}}',headers={
        'Retry-After':'30','Authorization':'SECRET-SENTINEL','Set-Cookie':'SECRET-SENTINEL'})
    assert json.loads(stop.read_text())['reason']=='upstream_rate_limit'
    after=budget.snapshot();assert after[0]['accounted']==before and after[0]['state']=='uncertain'
    with pytest.raises(RuntimeError,match='admission stopped'):
        budget.reserve('different-project',{'model':MODEL,'messages':[]})
    assert budget.snapshot()==after
    error=after[0]['private_error_body'];path=budget.path.parent/error['relative_path']
    assert path.read_bytes()==b'{"error":{"message":"rate limited"}}'
    assert path.stat().st_mode&0o777==0o600
    assert after[0]['response_diagnostic_headers']=={'retry-after':'30'}
    assert 'SECRET-SENTINEL' not in json.dumps(after)


@pytest.mark.parametrize('complete',[True,False])
def test_private_rejection_capture_distinguishes_eof_from_interruption(budget,complete):
    from pacbench.direct_http import Capture
    row=budget.reserve('native',{'model':MODEL,'messages':[]})
    capture=Capture({'id':row['id'],'budget':budget},429,headers={'retry-after':'30'})
    capture.add(b'{"error":{"code":429,"message":"capacity"}}')
    capture.finish(complete=complete)
    saved=budget.snapshot()[0]
    assert saved['response_body_complete'] is complete
    assert saved['private_error_body']['body_complete'] is complete
    assert saved['state']=='uncertain' and saved['accounted']==saved['reserved']


def test_nonblocking_snapshot_never_waits_on_suspended_ledger_owner(budget, monkeypatch):
    import fcntl
    from pacbench import usage
    import pacbench.direct_accounting as accounting
    monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter')
    monkeypatch.setattr('pacbench.openrouter_direct.active',lambda:True)
    monkeypatch.setattr(accounting,'live_budget',
        lambda **kw:DirectBudget(budget.path,**kw))
    original=budget.path.read_bytes()
    with budget.path.open('r+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX)
        result=usage.snapshot({'native':'memory_provider'},nonblocking=True)
        assert result['state']=='unavailable'
        assert result['reason']=='BlockingIOError'
        assert budget.path.read_bytes()==original
    assert usage.snapshot({'native':'memory_provider'},nonblocking=True)['state']=='captured'


def test_three_completed_unmetered_responses_stop_background_admission(budget):
    for i in range(3):
        row=budget.reserve('provider',{'model':MODEL,'messages':[{'role':'user','content':str(i)}],'max_tokens':5})
        budget.settle(row['id'],200,b'{}')
    before=budget.snapshot()
    with pytest.raises(RuntimeError,match='lack usable token usage'):
        budget.reserve('provider',{'model':MODEL,'messages':[{'role':'user','content':'different fourth request'}],'max_tokens':5})
    assert budget.snapshot()==before


def test_reservations_bound_all_concurrent_work_and_merge_settlements(budget):
    # Each request costs at most .0002, so only two fit after a preserved hold.
    with budget.locked() as state:
        state['calls'].append({'id':'old-unknown','project':'historical','state':'uncertain','accounted':'19.9996'})
    barrier=threading.Barrier(3)
    def reserve(i):
        barrier.wait()
        try:return budget.reserve('native-'+str(i),{'model':MODEL,'messages':[]})['id']
        except RuntimeError:return None
    with ThreadPoolExecutor(max_workers=3) as pool:ids=list(pool.map(reserve,range(3)))
    assert len([i for i in ids if i])==2
    assert sum(Decimal(c['accounted']) for c in budget.snapshot())==Decimal('20')
    for i in filter(None,ids):budget.settle(i,200,reply())
    assert budget.snapshot()[0]['accounted']=='19.9996'
    assert all(c['state']=='settled' for c in budget.snapshot()[1:])


def test_uncertain_charge_is_retained_and_not_replayed(budget):
    r=budget.reserve('native',{'model':MODEL})
    budget.settle(r['id'],0)
    assert budget.snapshot()[0]['accounted']=='0.0002'
    with pytest.raises(RuntimeError,match='Uncertain'):budget.reserve('native',{'model':MODEL})


def test_different_question_can_proceed_while_full_unknown_charge_remains_held(budget):
    first={'model':MODEL,'messages':[{'role':'user','content':'First independent question'}]}
    second={'model':MODEL,'messages':[{'role':'user','content':'Different independent question'}]}
    r=budget.reserve('native',first);budget.settle(r['id'],0)
    other=budget.reserve('native',second)
    assert len(budget.snapshot())==2
    assert sum(Decimal(c['accounted']) for c in budget.snapshot())==Decimal('.0004')
    assert other['id']!=r['id']
    with pytest.raises(RuntimeError,match='Uncertain'):budget.reserve('native',first)
    with budget.locked() as state:state['calls'][0]['accounted']='19.9999'
    with pytest.raises(RuntimeError,match='spending cap'):
        budget.reserve('native',{'model':MODEL,'messages':[{'role':'user','content':'Third question'}]})


def test_auth_failure_stops_all_projects_and_exact_route_is_required(budget):
    with pytest.raises(ValueError):budget.reserve('native',{'model':MODEL,'provider':{'only':['other']}})
    r=budget.reserve('native',{'model':MODEL})
    budget.settle(r['id'],401)
    with pytest.raises(RuntimeError,match='authentication'):budget.reserve('other',{'model':MODEL})


def test_definitive_rejections_allow_native_fallback_until_three_errors(budget):
    for _ in range(3):
        r=budget.reserve('native',{'model':MODEL})
        budget.settle(r['id'],422)
    with pytest.raises(RuntimeError,match='repeated'):budget.reserve('native',{'model':MODEL})
    assert len(budget.snapshot())==3
    assert sum(Decimal(c['accounted']) for c in budget.snapshot())==Decimal('0.0006')


def install(monkeypatch,budget):
    import pacbench.direct_http as hook
    monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY','offline-upstream')
    monkeypatch.setenv('PACBENCH_DIRECT','1')
    monkeypatch.setattr(hook,'live_budget',lambda:budget)
    hook.install()
    return hook


def test_real_httpx_transports_stream_without_buffering_and_without_network_lock(monkeypatch,budget):
    import httpx
    install(monkeypatch,budget)
    proceed=threading.Event()
    class NativeStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[]}\n\n'
            assert proceed.wait(2)
            yield b'data: '+reply()+b'\n\ndata: [DONE]\n\n'
    def upstream(request):
        assert str(request.url)=='https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['authorization']=='Bearer offline-upstream'
        data=json.loads(request.content)
        assert data['provider']['only']==['deepinfra/fp8/flex']
        assert data['provider']['allow_fallbacks'] is False
        assert data['stream_options']['include_usage'] is True
        return httpx.Response(200,stream=NativeStream())
    with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
        with client.stream('POST','https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization':'Bearer pbdirect-native'},json={'model':MODEL,'stream':True}) as response:
            chunks=response.iter_bytes()
            assert next(chunks).startswith(b'data:')
            # A second request can reserve while the first network stream stalls.
            other=budget.reserve('concurrent',{'model':MODEL})
            assert budget.snapshot()[0]['state']=='reserved'
            proceed.set();list(chunks)
    assert budget.snapshot()[0]['state']=='settled'
    assert budget.snapshot()[1]['state']=='reserved'


def test_async_httpx_completion_records_usage(monkeypatch,budget):
    import httpx
    install(monkeypatch,budget)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=reply()))) as client:
            response=await client.post('https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization':'Bearer pbdirect-native'},json={'model':MODEL})
            assert response.json()['model']==MODEL
    asyncio.run(check())
    assert budget.snapshot()[0]['state']=='settled'


def test_incomplete_stream_keeps_full_bound(monkeypatch,budget):
    import httpx
    install(monkeypatch,budget)
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[]}\n\n'
            raise httpx.ReadTimeout('offline failure')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Broken()))) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post('https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization':'Bearer pbdirect-native'},json={'model':MODEL,'stream':True})
    assert budget.snapshot()[0]['state']=='uncertain'
    assert budget.snapshot()[0]['accounted']=='0.0002'


def test_native_public_inventory_needs_no_credential_but_inference_still_does(monkeypatch,budget):
    hook=install(monkeypatch,budget)
    value=hook.admitted('https://openrouter.ai/api/v1/models',{},b'')
    assert value=={'headers':{},'free':True}
    assert not budget.snapshot()
    for path,body in [('chat/completions',b'{"model":"anything"}'),('key',b'')]:
        with pytest.raises(RuntimeError,match='Unaccounted'):
            hook.admitted('https://openrouter.ai/api/v1/'+path,{},body)


def ended_unknown(budget):
    body={'model':MODEL,'stream':True,'messages':[{'role':'user','content':'Frozen request'}]}
    row=budget.reserve('native',body)
    budget.settle(row['id'],200,json.dumps({'id':'generation-ended','model':MODEL}).encode())
    return row,body


def test_explicit_retry_keeps_original_hold_and_consumes_exactly_once(budget):
    original,body=ended_unknown(budget)
    before=budget.snapshot()[0]
    grant=budget.authorize_retry(original['id'],authorization='User explicitly authorizes one bounded retry with unknown charge held')
    retry=budget.reserve('native',body)
    rows=budget.snapshot()
    assert rows[0]['accounted']==before['accounted'] and rows[0]['state']=='uncertain'
    assert rows[0]['authorized_retry']['used_by']==retry['id']
    assert rows[1]['explicit_retry_of']==original['id']
    assert rows[1]['retry_authorization_id']==grant['id']
    budget.settle(retry['id'],200,reply())
    with pytest.raises(RuntimeError,match='Uncertain'):budget.reserve('native',body)
    with pytest.raises(ValueError,match='already'):budget.authorize_retry(original['id'],authorization='Again')


def test_explicit_retry_does_not_bypass_cap_or_consume_grant_on_refusal(budget):
    original,body=ended_unknown(budget)
    budget.authorize_retry(original['id'],authorization='User authorization')
    with budget.locked() as state:state['calls'][0]['accounted']='20'
    with pytest.raises(RuntimeError,match='spending cap'):budget.reserve('native',body)
    assert budget.snapshot()[0]['authorized_retry']['used_by'] is None


def test_retry_authorization_requires_ended_identified_response(budget):
    row=budget.reserve('native',{'model':MODEL})
    with pytest.raises(ValueError):budget.authorize_retry(row['id'],authorization='User authorization')
    budget.settle(row['id'],200,b'{}')
    with pytest.raises(ValueError):budget.authorize_retry(row['id'],authorization='User authorization')
    with pytest.raises(ValueError):budget.authorize_retry(row['id'],authorization='')


def test_concurrent_explicit_retries_consume_one_durable_grant(budget):
    original,body=ended_unknown(budget)
    budget.authorize_retry(original['id'],authorization='One user-authorized retry')
    def reserve(_):
        try:return budget.reserve('native',body)['id']
        except RuntimeError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(reserve,range(2)))
    assert sum(i is not None for i in ids)==1
    assert len(budget.snapshot())==2


def generation_receipt(cost='0.00001'):
    return {'id':'gen-offline','model':'canonical-model','provider_name':'DeepInfra',
            'service_tier':'flex','is_byok':False,'cancelled':True,'total_cost':cost,
            'usage':cost,'native_tokens_prompt':3,'native_tokens_completion':2}


def test_generation_reconciliation_keeps_original_bound_and_authoritative_cost(budget):
    row=budget.reserve('judge',{'model':MODEL,'messages':[]})
    budget.settle(row['id'],200,b'{"id":"gen-offline"}')
    result=budget.reconcile_generation(row['id'],generation_receipt(),
        {'id':MODEL,'canonical_slug':'canonical-model'},receipt_sha256='a'*64)
    assert result['state']=='settled' and result['accounted']=='0.00001'
    assert result['reserved']=='0.0002'
    assert result['reconciliation']['original_accounting']['state']=='uncertain'
    from pacbench.usage import direct_call
    assert direct_call(result,'judge')['actual_cost_source']=='openrouter.generation.total_cost'


@pytest.mark.parametrize('field,value',[
    ('id','another-generation'),('model','another-model'),('provider_name','other'),
    ('service_tier','other'),('cancelled',False),('native_tokens_prompt',None),
    ('native_tokens_completion',True),('usage','0.00002')])
def test_invalid_generation_receipts_do_not_release_holds(budget,field,value):
    row=budget.reserve('judge',{'model':MODEL,'messages':[]})
    budget.settle(row['id'],200,b'{"id":"gen-offline"}')
    before=budget.snapshot();receipt=generation_receipt();receipt[field]=value
    with pytest.raises(ValueError):
        budget.reconcile_generation(row['id'],receipt,{'id':MODEL,'canonical_slug':'canonical-model'},
            receipt_sha256='a'*64)
    assert budget.snapshot()==before


def test_generation_liability_over_bound_stops_admission_without_understatement(budget):
    row=budget.reserve('judge',{'model':MODEL,'messages':[]})
    budget.settle(row['id'],200,b'{"id":"gen-offline"}')
    result=budget.reconcile_generation(row['id'],generation_receipt('0.0003'),
        {'id':MODEL,'canonical_slug':'canonical-model'},receipt_sha256='a'*64)
    assert result['state']=='uncertain' and result['accounted']=='0.0003'
    with pytest.raises(RuntimeError,match='liability exceeded'):
        budget.reserve('different-project',{'model':MODEL,'messages':[]})


def test_abandoned_worker_retains_hold_and_allows_one_distinct_retry(budget):
    import subprocess,sys,hashlib
    p=subprocess.Popen([sys.executable,'-c','pass']);p.wait()
    body={'model':MODEL,'messages':[{'role':'user','content':'native source'}]}
    first=budget.reserve('native',body)
    held=budget.snapshot()[0]['accounted']
    row=budget.abandon_worker_request(first['id'],worker_pids=[p.pid],evidence_sha256='a'*64,authorization='Explicit bounded recovery with old charge held')
    assert row['state']=='abandoned_unknown' and row['accounted']==held and row['finished'] is None
    second=budget.reserve('native',body)
    assert second['id']!=first['id']
    rows=budget.snapshot()
    assert rows[1]['explicit_retry_of']==first['id']
    assert sum(Decimal(r['accounted']) for r in rows)==2*Decimal(held)
    with pytest.raises(RuntimeError):budget.reserve('native',body)


def test_alive_worker_cannot_be_abandoned(budget):
    import os
    first=budget.reserve('native',{'model':MODEL})
    with pytest.raises(ValueError,match='alive'):budget.abandon_worker_request(first['id'],worker_pids=[os.getpid()],evidence_sha256='a'*64,authorization='recovery')
    assert budget.snapshot()[0]['state']=='reserved'


def test_abandoned_retry_cannot_exceed_aggregate_cap(budget):
    import subprocess,sys
    p=subprocess.Popen([sys.executable,'-c','pass']);p.wait()
    body={'model':MODEL};first=budget.reserve('native',body)
    budget.abandon_worker_request(first['id'],worker_pids=[p.pid],evidence_sha256='a'*64,authorization='recovery')
    with budget.locked() as state:state['calls'].append({'id':'external','project':'other','state':'allocated','accounted':'19.9997'})
    with pytest.raises(RuntimeError,match='cap'):budget.reserve('native',body)
    rows=budget.snapshot();assert rows[0]['authorized_retry']['used_by'] is None and rows[0]['accounted']=='0.0002'


def test_native_cached_and_reasoning_token_details_are_retained_without_payload(budget):
    row=budget.reserve('native',{'model':MODEL});data=json.loads(reply())
    data['usage'].update(prompt_tokens_details={'cached_tokens':2,'secret':'never'},completion_tokens_details={'reasoning_tokens':1},other='ignored')
    budget.settle(row['id'],200,json.dumps(data).encode())
    r=budget.snapshot()[0]
    assert r['usage_detail']['prompt_tokens_details.cached_tokens']==2
    assert r['usage_detail']['completion_tokens_details.reasoning_tokens']==1
    assert 'secret' not in str(r['usage_detail']) and 'other' not in r['usage_detail']


def test_native_request_parameters_are_observed_without_injecting_cap_or_thinking(budget):
    a=budget.reserve('default',{'model':MODEL});row=budget.snapshot()[0]
    assert row['native_request_parameters']=={}
    assert 'max_tokens' not in a['request'] and 'max_completion_tokens' not in a['request'] and 'reasoning_effort' not in a['request']
    assert row['completion_bound_tokens']==50
    b=budget.reserve('configured',{'model':MODEL,'reasoning_effort':'low','max_completion_tokens':20})
    assert budget.snapshot()[1]['native_request_parameters']=={'reasoning_effort':'low','max_completion_tokens':20}
    assert b['request']['reasoning_effort']=='low' and b['request']['max_completion_tokens']==20
