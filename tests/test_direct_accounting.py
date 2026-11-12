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
