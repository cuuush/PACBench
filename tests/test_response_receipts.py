"""Offline receipts use synthetic data and temporary ledgers only."""
import json
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import pytest

from tests.test_direct_accounting import budget, MODEL, generation_receipt
from pacbench.direct_http import Capture
from pacbench.response_receipts import parse, sanitized, financials


def event(cost='0.00001',**extra):
    return {'id':'gen-offline','model':MODEL,'provider':'DeepInfra',
        'choices':[{'finish_reason':'stop','message':{'content':'PRIVATE-CONTENT-SENTINEL'}}],
        'usage':{'cost':cost,'prompt_tokens':3,'completion_tokens':2},**extra}


def wire(e):return ('data: '+json.dumps(e)+'\n\n').encode()


def test_generation_is_durable_before_stream_close(budget):
    r=budget.reserve('stream',{'model':MODEL,'stream':True})
    c=Capture({'id':r['id'],'budget':budget},200)
    c.add(wire({'id':'gen-offline','model':MODEL,'choices':[{'delta':{'content':'PRIVATE-CONTENT-SENTINEL'}}]}))
    saved=budget.snapshot()[0]
    assert saved['upstream_request_id']=='gen-offline'
    assert saved['state']=='reserved' and saved['accounted']==saved['reserved']
    assert 'PRIVATE-CONTENT-SENTINEL' not in budget.path.read_text()
    c.finish()
    assert budget.snapshot()[0]['state']=='uncertain'


def test_malformed_stream_tail_does_not_erase_valid_cost(budget):
    r=budget.reserve('stream',{'model':MODEL,'stream':True})
    body=wire(event())+b'data: {"choices":'
    budget.settle(r['id'],200,body,body_complete=False)
    saved=budget.snapshot()[0]
    assert saved['state']=='settled' and saved['accounted']=='0.00001'
    assert saved['response_validation']['wire_parse_errors']==1
    assert saved['response_body_complete'] is False
    receipt=json.loads((budget.path.parent/saved['billing_receipt']['relative_path']).read_text())
    assert receipt['usage']['cost']=='0.00001' and receipt['attempt_id']==r['id']
    assert 'PRIVATE-CONTENT-SENTINEL' not in json.dumps(receipt)


def test_response_amount_is_exact_and_duplicate_settlement_is_one_charge(budget):
    r=budget.reserve('exact',{'model':MODEL});amount='0.00001000000000000000000000003'
    raw=json.dumps(event()).replace('"0.00001"',amount).encode()
    budget.settle(r['id'],200,raw,body_complete=True)
    budget.settle(r['id'],200,raw,body_complete=True)
    saved=budget.snapshot();assert len(saved)==1
    assert Decimal(saved[0]['accounted'])==Decimal(amount)
    assert saved[0]['settled_charge_usd']==amount


def test_reconciliation_is_idempotent_and_over_bound_remains_actual_charge(budget):
    r=budget.reserve('exact',{'model':MODEL});budget.settle(r['id'],200,b'{"id":"gen-offline"}')
    args=(r['id'],generation_receipt('.0003'),{'id':MODEL,'canonical_slug':'canonical-model'})
    first=budget.reconcile_generation(*args,receipt_sha256='a'*64)
    assert first['state']=='settled' and first['accounted']=='0.0003'
    second=budget.reconcile_generation(*args,receipt_sha256='a'*64)
    assert second==first
    assert financials(first)['settled_charge_usd']=='0.0003'
    assert first['reported_liability_exceeds_bound'] is True
    with pytest.raises(RuntimeError,match='liability'):budget.reserve('another',{'model':MODEL})


def test_token_bound_violation_does_not_hide_a_reported_charge(budget):
    r=budget.reserve('exact',{'model':MODEL});budget.settle(r['id'],200,json.dumps(event(usage={'cost':'.00001','prompt_tokens':101,'completion_tokens':2})).encode())
    saved=budget.snapshot()[0]
    assert saved['state']=='settled' and saved['accounted']=='0.00001'
    assert saved['reported_token_bounds_exceeded'] is True


def test_known_charge_is_separate_from_invalid_token_usage_and_response_shape(budget):
    for i in range(3):
        r=budget.reserve('native',{'model':MODEL,'messages':[{'role':'user','content':str(i)}]})
        budget.settle(r['id'],200,json.dumps({'id':'gen-offline-'+str(i),'choices':'malformed','usage':{'cost':'.00001'}}).encode())
        saved=budget.snapshot()[-1]
        assert saved['state']=='settled' and saved['settled_charge_usd']=='0.00001'
        assert saved['error_label']=='response_token_usage_unavailable'
    with pytest.raises(RuntimeError,match='lack usable token usage'):budget.reserve('native',{'model':MODEL})


def test_parser_preserves_multiline_sse_and_malformed_tail():
    events,errors=parse(b'data: {"id":"gen-offline",\ndata: "usage":{"cost":0,"prompt_tokens":0,"completion_tokens":0}}\n\ndata: broken')
    assert len(events)==1 and errors==1
    assert sanitized(events[0])['usage']['cost']=='0'


def test_blank_sse_prefix_and_empty_keepalive_do_not_hide_usage():
    events,errors=parse(b'\n\n: keepalive\n\ndata:\n\n'+wire(event()))
    assert len(events)==1 and errors==0


def test_compressed_stream_persists_identity_before_eof(budget):
    import zlib
    r=budget.reserve('gzip',{'model':MODEL,'stream':True});compress=zlib.compressobj(wbits=31)
    capture=Capture({'id':r['id'],'budget':budget},200,encoding='gzip')
    capture.add(compress.compress(wire({'id':'gen-offline','model':MODEL}))+compress.flush(zlib.Z_SYNC_FLUSH))
    assert budget.snapshot()[0]['upstream_request_id']=='gen-offline'
    capture.add(compress.compress(wire(event()))+compress.flush());capture.finish(complete=True)
    assert budget.snapshot()[0]['state']=='settled'


@pytest.mark.parametrize('body',[b'{"usage":',b'data: {"usage":\n\n',b'data: [DONE]\n\n'])
def test_missing_authoritative_cost_never_means_free(budget,body):
    r=budget.reserve('native',{'model':MODEL});budget.settle(r['id'],200,body,body_complete=False)
    saved=budget.snapshot()[0]
    assert saved['state']=='uncertain' and saved['accounted']==saved['reserved']
    assert financials(saved)['settled_charge_usd'] is None


@pytest.mark.parametrize('zero',['0','0.0','0E-9',0.0])
def test_reported_zero_has_a_receipt_and_conflicting_duplicate_stops_admission(budget,tmp_path,monkeypatch,zero):
    stop=tmp_path/'STOP';monkeypatch.setenv('PACBENCH_STOP_FILE',str(stop))
    r=budget.reserve('zero',{'model':MODEL});budget.settle(r['id'],200,json.dumps(event(cost=zero)).encode(),body_complete=True)
    saved=budget.snapshot()[0];assert saved['state']=='settled' and Decimal(saved['settled_charge_usd'])==0
    proof=json.loads((budget.path.parent/saved['billing_receipt']['relative_path']).read_text())
    assert proof['reported_zero'] is True
    budget.settle(r['id'],200,wire(event(cost='.00001'))+wire({'id':'gen-offline','choices':[]}),body_complete=True)
    saved=budget.snapshot()[0];assert saved['state']=='uncertain' and saved['billing_conflict'] is True
    assert Decimal(saved['accounted'])>=Decimal(saved['reserved'])
    conflict=json.loads((budget.path.parent/saved['conflicting_billing_receipt']['relative_path']).read_text())
    assert conflict['usage']['cost']=='0.00001'
    with pytest.raises(RuntimeError):budget.reserve('new',{'model':MODEL})


def test_funded_week_cannot_fall_back_to_deepseek(budget,monkeypatch):
    from pacbench.funded_run_budget import ENV
    monkeypatch.setenv(ENV,'offline-week')
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:{'remaining_usd':'20','request_started':'2026-11-15T01:00:00+00:00'})
    with budget.locked() as state:
        state['funded_runs']={'offline-week':{'id':'offline-week','status':'active','limit_usd':'20','baseline_ledger_rows':0,
            'prepaid_safety':{'auto_recharge':'off','evidence':'synthetic fixture'}}}
    with pytest.raises(RuntimeError,match='requires Luna'):budget.reserve('answer-driver',{'model':MODEL})
    assert budget.snapshot()==[]


def test_simultaneous_attempts_have_distinct_receipts_and_no_duplicate_charge(budget):
    def one(i):
        r=budget.reserve('p'+str(i),{'model':MODEL});body=json.dumps(event(id='gen-offline-'+str(i))).encode()
        budget.settle(r['id'],200,body,body_complete=True);budget.settle(r['id'],200,body,body_complete=True)
        return r['id']
    ids=list(ThreadPoolExecutor(max_workers=4).map(one,range(12)))
    rows=budget.snapshot();assert len(set(ids))==len(rows)==12
    assert sum(Decimal(r['accounted']) for r in rows)==Decimal('.00012')
    assert len({r['billing_receipt']['relative_path'] for r in rows})==12


@pytest.mark.parametrize('asynchronous',[False,True])
def test_eager_httpx_read_cannot_lose_headers_or_first_stream_id(budget,monkeypatch,asynchronous):
    import asyncio,httpx
    from tests.test_direct_accounting import install
    install(monkeypatch,budget)
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield wire({'id':'gen-offline','model':MODEL,'choices':[]})
            raise httpx.ReadTimeout('synthetic body interruption')
    class AsyncBroken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire({'id':'gen-offline','model':MODEL,'choices':[]})
            raise httpx.ReadTimeout('synthetic body interruption')
    if asynchronous:
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=AsyncBroken()))) as c:
                with pytest.raises(httpx.ReadTimeout):await c.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer pbdirect-native'},json={'model':MODEL,'stream':True})
        asyncio.run(run())
    else:
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Broken()))) as c:
            with pytest.raises(httpx.ReadTimeout):c.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer pbdirect-native'},json={'model':MODEL,'stream':True})
    row=budget.snapshot()[0]
    assert row['upstream_request_id']=='gen-offline' and row['http_status']==200
    assert row['state']=='uncertain' and row['accounted']==row['reserved']
