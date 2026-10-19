import io
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.message import Message

import pytest

from muse_gateway.ledger import BudgetRefused, CONTEXT_TOKENS, DAILY_LIMIT, Ledger, MODEL, RESERVATION
from muse_gateway.server import Server


def test_reservation_is_atomic_across_simultaneous_clients(tmp_path):
    ledger = Ledger(tmp_path/'ledger.db')
    def reserve(_):
        try:return ledger.reserve('test','responses')
        except BudgetRefused:return None
    with ThreadPoolExecutor(max_workers=12) as executor:
        requests = list(executor.map(reserve,range(50)))
    assert sum(x is not None for x in requests) == DAILY_LIMIT//RESERVATION
    assert ledger.health()['accounted_today_usd'] <= 10
    assert ledger.health()['budget_blocked']


@pytest.mark.parametrize('settled',[False,True])
def test_completed_costs_stay_on_original_date_and_active_holds_span_midnight(tmp_path,settled):
    stamp = ['2026-10-19T23:59:50+00:00']
    ledger = Ledger(tmp_path/'ledger.db',now=lambda:stamp[0])
    first = ledger.reserve('test','responses')
    stamp[0] = '2026-10-20T00:00:01+00:00'
    assert ledger.health()['accounted_today_usd'] == RESERVATION/1e9
    ledger.finish(first,usage=(100,200) if settled else None)
    assert ledger.health()['accounted_today_usd'] == 0
    with ledger.connect() as db:
        row = db.execute('SELECT * FROM calls').fetchone()
    assert row['accounted'] == 50_000 if settled else row['accounted'] == RESERVATION
    assert row['started'].startswith('2026-10-19')


def test_refusal_latches_for_day_without_lifetime_cap(tmp_path):
    stamp = ['2026-10-19T23:00:00+00:00']
    ledger = Ledger(tmp_path/'ledger.db',now=lambda:stamp[0])
    requests = [ledger.reserve('test','responses') for _ in range(DAILY_LIMIT//RESERVATION)]
    with pytest.raises(BudgetRefused):ledger.reserve('test','responses')
    for request in requests:ledger.finish(request,usage=(1,1))
    with pytest.raises(BudgetRefused):ledger.reserve('test','responses')
    stamp[0] = '2026-10-20T00:00:01+00:00'
    assert not ledger.health()['budget_blocked']
    assert ledger.reserve('test','responses')


def test_restart_preserves_unknown_bounds_and_completion_is_once(tmp_path):
    ledger = Ledger(tmp_path/'ledger.db')
    request = ledger.reserve('test','responses')
    ledger.recover_abandoned()
    assert ledger.health()['active_reservations'] == 0
    assert ledger.health()['accounted_today_usd'] == RESERVATION/1e9
    with pytest.raises(ValueError):ledger.finish(request,usage=(0,0))


@pytest.mark.parametrize('usage',[None,(-1,10),(True,1),('3',1),(CONTEXT_TOKENS+1,1)])
def test_bad_usage_never_releases_an_unknown_cost(tmp_path,usage):
    ledger = Ledger(tmp_path/'ledger.db')
    request = ledger.reserve('test','responses')
    ledger.finish(request,usage=usage,status=200)
    assert ledger.health()['accounted_today_usd'] == RESERVATION/1e9


class Response(io.BytesIO):
    status = 200
    def __init__(self,body,content_type='application/json'):
        super().__init__(body)
        self.headers = Message()
        self.headers['Content-Type'] = content_type


@contextmanager
def gateway(tmp_path,opener):
    server = Server(('127.0.0.1',0),Ledger(tmp_path/'ledger.db'),'local-secret','upstream-secret',opener=opener)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield server,f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base,value,path='/p/test/v1/chat/completions',key='local-secret'):
    req = urllib.request.Request(base+path,data=json.dumps(value).encode(),headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    return urllib.request.urlopen(req,timeout=5)


@pytest.mark.parametrize('path,value,key,status',[
 ('/p/test/v1/chat/completions',{'model':MODEL},'wrong',401),
 ('/p/test/v1/other',{'model':MODEL},'local-secret',404),
 ('/p/test/v1/chat/completions',{'model':'unpriced'},'local-secret',400),
 ('/p/test/v1/responses',{'model':MODEL,'max_output_tokens':-1},'local-secret',400),
 ('/p/test/v1/responses',{'model':MODEL,'max_output_tokens':True},'local-secret',400),
 ('/p/test/v1/responses',{'model':MODEL,'input':'x'*CONTEXT_TOKENS},'local-secret',400),
 ('/p/test/v1/chat/completions',{'model':MODEL,'stream':'yes'},'local-secret',400),
 ('/p/test/v1/chat/completions',{'model':MODEL,'stream':True,'stream_options':[]},'local-secret',400),
])
def test_rejected_requests_never_reach_upstream_or_reserve(tmp_path,path,value,key,status):
    calls=[]
    with gateway(tmp_path,lambda *a,**k:calls.append(a)) as (server,base):
        with pytest.raises(urllib.error.HTTPError) as error:post(base,value,path,key)
        assert error.value.code == status
        assert not calls
        assert server.ledger.health()['accounted_today_usd'] == 0


def test_json_passthrough_settles_tokens_without_logging_content(tmp_path):
    body = json.dumps({'choices':[{'message':{'content':'private answer'}}],
        'usage':{'prompt_tokens':100,'completion_tokens':20}}).encode()
    calls=[]
    def open_(request,**kwargs):
        calls.append(request)
        return Response(body)
    with gateway(tmp_path,open_) as (server,base):
        with post(base,{'model':MODEL,'messages':[{'role':'user','content':'private prompt'}]}) as response:
            assert response.read() == body
        with server.ledger.connect() as db:
            row = dict(db.execute('SELECT * FROM calls').fetchone())
        assert row['state'] == 'settled' and row['input_tokens'] == 100
        assert row['output_tokens'] == 20 and row['accounted'] == 14000
        assert 'private' not in json.dumps(row) and 'secret' not in json.dumps(row)
        assert json.loads(calls[0].data)['messages'][0]['content'] == 'private prompt'


@pytest.mark.parametrize('endpoint', ['chat/completions','responses'])
@pytest.mark.parametrize('ending', [b'\n\ndata: [DONE]\n\n', b'\n', b''])
def test_native_sse_passthrough_and_final_usage(tmp_path,endpoint,ending):
    value = ({'usage':{'prompt_tokens':123,'completion_tokens':45}} if endpoint.startswith('chat')
             else {'type':'response.completed','response':{'usage':{'input_tokens':123,'output_tokens':45}}})
    body = b'data: {"choices": []}\n\n' + b'data: '+json.dumps(value).encode()+ending
    calls=[]
    def open_(request,**kwargs):
        calls.append(json.loads(request.data))
        return Response(body,'text/event-stream')
    with gateway(tmp_path,open_) as (server,base):
        with post(base,{'model':MODEL,'stream':True},'/p/test/v1/'+endpoint) as response:
            assert response.read() == body
        with server.ledger.connect() as db:
            row = db.execute('SELECT * FROM calls').fetchone()
        assert row['state']=='settled' and row['input_tokens']==123 and row['output_tokens']==45
        if endpoint.startswith('chat'):assert calls[0]['stream_options']=={'include_usage':True}


@pytest.mark.parametrize('mode',['timeout','http','no-usage'])
def test_upstream_failures_and_missing_usage_keep_full_bound(tmp_path,mode):
    def open_(*a,**k):
        if mode=='timeout':raise TimeoutError('secret raw diagnostic')
        if mode=='http':raise urllib.error.HTTPError('upstream',401,'raw secret',{},io.BytesIO(b'private'))
        return Response(b'{"choices":[]}')
    with gateway(tmp_path,open_) as (server,base):
        try:
            with post(base,{'model':MODEL}) as response:response.read()
        except urllib.error.HTTPError as exc:
            assert b'secret' not in exc.read()
        with server.ledger.connect() as db:
            row = dict(db.execute('SELECT * FROM calls').fetchone())
        assert row['state']=='unknown' and row['accounted']==RESERVATION
        assert row['finished'] and 'secret' not in json.dumps(row)


def test_budget_refusal_blocks_before_upstream_and_has_terminal_label(tmp_path):
    calls=[]
    with gateway(tmp_path,lambda *a,**k:calls.append(a)) as (server,base):
        for _ in range(DAILY_LIMIT//RESERVATION):server.ledger.reserve('previous','responses')
        with pytest.raises(urllib.error.HTTPError) as error:post(base,{'model':MODEL})
        assert error.value.code==429
        assert json.load(error.value)['error']['type']=='budget_refused'
        assert not calls


def test_auth_circuit_blocks_paid_retries_until_free_model_inventory_succeeds(tmp_path):
    calls=[]
    def open_(request,**kwargs):
        calls.append(request.full_url)
        if request.get_method()=='GET':
            return Response(json.dumps({'data':[{'id':MODEL}]}).encode())
        raise urllib.error.HTTPError('upstream',401,'Unauthorized',{},io.BytesIO(b'private'))
    with gateway(tmp_path,open_) as (server,base):
        with pytest.raises(urllib.error.HTTPError) as first:post(base,{'model':MODEL})
        assert first.value.code==401
        before=server.ledger.health()['accounted_today_usd']
        assert server.ledger.health()['upstream_auth_blocked']
        for _ in range(3):
            with pytest.raises(urllib.error.HTTPError) as retry:post(base,{'model':MODEL})
            assert retry.value.code==503
            assert json.load(retry.value)['error']['type']=='upstream_auth_blocked'
        assert len(calls)==1 and server.ledger.health()['accounted_today_usd']==before
        req=urllib.request.Request(base+'/p/test/v1/models',headers={'Authorization':'Bearer local-secret'})
        with urllib.request.urlopen(req) as response:assert json.load(response)['data'][0]['id']==MODEL
        assert not server.ledger.health()['upstream_auth_blocked']
        assert server.ledger.health()['accounted_today_usd']==before


def test_auth_circuit_persists_across_ledger_restart(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');ledger.block_auth()
    assert Ledger(tmp_path/'ledger.db').health()['upstream_auth_blocked']
