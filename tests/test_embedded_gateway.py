import io
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.message import Message

import pytest

from pacbench.gateway.ledger import BudgetRefused, CONTEXT_TOKENS, DAILY_LIMIT, Ledger, MODEL, RESERVATION
from pacbench.gateway.server import Server


@pytest.mark.parametrize('payload,expected', [
    ({'error':{'code':'unsupported_parameter','param':'temperature','message':'private prompt and credential'}},
     {'code':'unsupported_parameter','param':'temperature'}),
    ({'error':{'code':'credential-secret','param':'private-user-text','message':'private-user-text'}}, {}),
    ({'error':{'message':'Unsupported parameter: private-user-text'}}, {'message_class':'unsupported_parameter'}),
    ({'error':{'code':[], 'param':{}}}, {}),
    ({'error':None}, {}), ({'error':[]}, {}), ([], {}),
])
def test_upstream_diagnostic_returns_only_fixed_labels(payload,expected):
    from pacbench.gateway.server import upstream_error_diagnostic
    assert upstream_error_diagnostic(io.BytesIO(json.dumps(payload).encode())) == expected


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


def test_oversized_request_is_rejected_before_body_upload_or_reservation(tmp_path):
    from http.client import HTTPConnection
    calls=[]
    with gateway(tmp_path,lambda *a,**k:calls.append(a)) as (server,base):
        connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            connection.putrequest('POST','/p/test/v1/responses')
            connection.putheader('Authorization','Bearer local-secret')
            connection.putheader('Content-Length',str(CONTEXT_TOKENS))
            connection.endheaders()
            # An oversized upload is refused from its headers. Sending the
            # full body races the early close and can mask the 400 with EPIPE.
            response=connection.getresponse()
            assert response.status==400
            assert json.load(response)['error']['type']=='request_refused'
        finally:connection.close()
        assert not calls
        assert server.ledger.health()['accounted_today_usd']==0


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


@pytest.mark.parametrize('stream',[False,True])
def test_truncation_metadata_survives_without_response_text_or_cost_changes(tmp_path,stream):
    choice={'choices':[{'finish_reason':'length','message':{'content':'private'}}]}
    usage={'usage':{'prompt_tokens':940,'completion_tokens':2500,
                   'completion_tokens_details':{'reasoning_tokens':2498}}}
    body=(b'data: '+json.dumps(choice).encode()+b'\n\ndata: '+json.dumps(usage).encode()+b'\n\n'
          if stream else json.dumps({**choice,**usage}).encode())
    with gateway(tmp_path,lambda *a,**k:Response(body,'text/event-stream' if stream else 'application/json')) as (server,base):
        with post(base,{'model':MODEL,'stream':stream}) as response:assert response.read()==body
        with server.ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
        assert row['finish_reason']=='length' and row['reasoning_tokens']==2498
        assert row['accounted']==594000 and row['state']=='settled'
        assert 'private' not in json.dumps(row)


def test_metadata_is_optional_and_cannot_release_unknown_costs_or_store_arbitrary_text(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');id=ledger.reserve('test','responses')
    ledger.finish(id,status=200,diagnostic={'finish_reason':'private answer','reasoning_tokens':True,'content':'private'})
    with ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['finish_reason'] is None and row['reasoning_tokens'] is None
    assert row['accounted']==RESERVATION and row['state']=='unknown'
    assert 'private' not in json.dumps(row)


def test_metadata_migration_preserves_existing_costs_and_auth_circuit(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');id=ledger.reserve('test','responses');ledger.finish(id,status=401);ledger.block_auth()
    with ledger.connect() as db:
        db.execute('ALTER TABLE calls DROP COLUMN finish_reason');db.execute('ALTER TABLE calls DROP COLUMN reasoning_tokens')
        db.execute('ALTER TABLE calls DROP COLUMN protocol_diagnostic')
    before=ledger.health();updated=Ledger(tmp_path/'ledger.db')
    assert updated.health()==before
    with updated.connect() as db:
        row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['state']=='unknown' and row['accounted']==RESERVATION
    assert row['finish_reason'] is None and row['reasoning_tokens'] is None
    assert row['protocol_diagnostic'] is None


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


def test_safe_metering_fields_and_unknown_actual_charge(tmp_path):
    body=json.dumps({'id':'chatcmpl-abc123','private':'secret', 'usage':{
        'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
        'prompt_tokens_details':{'cached_tokens':25,'private':'secret'},
        'completion_tokens_details':{'reasoning_tokens':10}}}).encode()
    def open_(*args,**kwargs):
        response=Response(body)
        response.headers['X-Request-ID']='req-123-abc'
        return response
    with gateway(tmp_path,open_) as (server,base):
        with post(base,{'model':MODEL}) as response: response.read()
        with server.ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['upstream_request_id']=='req-123-abc'
    assert row['generation_id']=='chatcmpl-abc123'
    assert json.loads(row['usage_detail']) == {'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
        'prompt_tokens_details.cached_tokens':25,'completion_tokens_details.reasoning_tokens':10}
    assert row['actual_cost_nano'] is None and row['actual_cost_source'] is None
    assert 'secret' not in json.dumps(row)


def test_partial_stream_preserves_id_and_bound_on_timeout(tmp_path):
    class TimedOut(Response):
        def readline(self,*args):
            line=super().readline(*args)
            if not line: raise TimeoutError('private upstream error')
            return line
    body=b'data: {"id":"chatcmpl-abc123","choices":[]}\n\n'
    with gateway(tmp_path,lambda *a,**k:TimedOut(body,'text/event-stream')) as (server,base):
        with post(base,{'model':MODEL,'stream':True}) as response: response.read()
        with server.ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['generation_id']=='chatcmpl-abc123'
    assert row['state']=='unknown' and row['accounted']==RESERVATION
    assert row['actual_cost_nano'] is None and 'private' not in json.dumps(row)


def test_concurrent_upstream_streams_do_not_hold_budget_lock(tmp_path):
    entered=threading.Barrier(2,timeout=3)
    def open_(*args,**kwargs):
        entered.wait()
        return Response(b'data: {"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n','text/event-stream')
    with gateway(tmp_path,open_) as (server,base):
        def call(_):
            with post(base,{'model':MODEL,'stream':True}) as response: return response.read()
        with ThreadPoolExecutor(max_workers=2) as pool: bodies=list(pool.map(call,range(2)))
        with server.ledger.connect() as db: rows=db.execute('SELECT * FROM calls').fetchall()
    assert len(bodies)==len(rows)==2 and all(row['state']=='settled' for row in rows)


def test_openrouter_reconciliation_retry_zero_and_idempotence(tmp_path):
    from pacbench.gateway.openrouter_reconcile import reconcile
    ledger=Ledger(tmp_path/'ledger.db')
    rid=ledger.reserve('provider','responses')
    ledger.finish(rid,status=504)
    with ledger.connect() as db:
        db.execute("UPDATE calls SET upstream_origin='openrouter',generation_id='gen-abc123' WHERE id=?",(rid,))
    def fail(_): raise TimeoutError('private')
    assert reconcile(ledger,rid,fail)=='lookup_failed'
    with ledger.connect() as db: assert db.execute('SELECT accounted FROM calls WHERE id=?',(rid,)).fetchone()[0]==RESERVATION
    assert reconcile(ledger,rid,lambda _: {'data':{'id':'gen-abc123','total_cost':0}})=='reconciled'
    assert reconcile(ledger,rid,lambda _: pytest.fail('must not refetch'))=='already_reconciled'
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls WHERE id=?',(rid,)).fetchone())
    assert row['actual_cost_nano']==0 and row['accounted']==0 and row['reconciled_at']


def test_openrouter_reconciliation_scoped_and_concurrent(tmp_path):
    from pacbench.gateway.openrouter_reconcile import reconcile
    ledger=Ledger(tmp_path/'ledger.db')
    direct=ledger.reserve('provider','responses');ledger.finish(direct)
    assert reconcile(ledger,direct,lambda _: pytest.fail('wrong origin'))=='ineligible'
    rid=ledger.reserve('provider','responses');ledger.finish(rid)
    with ledger.connect() as db: db.execute("UPDATE calls SET upstream_origin='openrouter' WHERE id=?",(rid,))
    assert reconcile(ledger,rid,lambda _: pytest.fail('no id'))=='missing_generation_id'
    with ledger.connect() as db: db.execute("UPDATE calls SET generation_id='gen-abc123' WHERE id=?",(rid,))
    assert reconcile(ledger,rid,lambda _: {'data':{'id':'gen-other','total_cost':0}})=='unresolved'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:reconcile(ledger,rid,lambda __:{'data':{'id':'gen-abc123','total_cost':0.002}}),range(8)))
    assert results.count('reconciled')==1
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls WHERE id=?',(rid,)).fetchone())
    assert row['accounted']==row['actual_cost_nano']==2_000_000


def test_openrouter_pending_retries_after_restart_and_duplicate_id(tmp_path):
    from pacbench.gateway.openrouter_reconcile import reconcile_pending
    ledger=Ledger(tmp_path/'ledger.db')
    first=ledger.reserve('p','responses');second=ledger.reserve('p','responses')
    for rid in (first,second):ledger.finish(rid)
    with ledger.connect() as db:
        db.execute("UPDATE calls SET upstream_origin='openrouter', generation_id='gen-abc123' WHERE id IN (?,?)",(first,second))
    assert set(reconcile_pending(ledger,lambda _: (_ for _ in ()).throw(TimeoutError())).values()) == {'lookup_failed'}
    restarted=Ledger(tmp_path/'ledger.db')
    result=reconcile_pending(restarted,lambda _: {'data':{'id':'gen-abc123','total_cost':0.001}})
    assert sorted(result.values())==['duplicate_generation_id','reconciled']
    with restarted.connect() as db:
        rows=db.execute('SELECT accounted,actual_cost_nano FROM calls').fetchall()
    assert sorted(row['accounted'] for row in rows)==[1_000_000,RESERVATION]


def test_retry_driver_persists_backoff_and_later_reconciles(tmp_path):
    from pacbench.gateway.openrouter_reconcile import retry_due
    stamp=['2026-10-19T23:00:00+00:00']
    ledger=Ledger(tmp_path/'ledger.db',now=lambda:stamp[0])
    rid=ledger.reserve('p','responses');ledger.finish(rid)
    no_id=ledger.reserve('p','responses');ledger.finish(no_id)
    with ledger.connect() as db:
        db.execute("UPDATE calls SET upstream_origin='openrouter',generation_id='gen-abc123' WHERE id=?",(rid,))
        db.execute("UPDATE calls SET upstream_origin='openrouter' WHERE id=?",(no_id,))
    def not_ready(_): raise urllib.error.HTTPError('https://example',404,'private',{},None)
    assert retry_due(ledger,not_ready)=={rid:'not_ready',no_id:'missing_generation_id'}
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls WHERE id=?',(rid,)).fetchone())
    assert row['accounted']==RESERVATION and row['reconcile_status']=='not_ready'
    assert row['reconcile_next_attempt']=='2026-10-19T23:01:00+00:00'
    restarted=Ledger(tmp_path/'ledger.db',now=lambda:stamp[0],existing_only=True)
    assert retry_due(restarted,lambda _:pytest.fail('backoff')) == {}
    stamp[0]='2026-10-19T23:01:00+00:00'
    assert retry_due(restarted,lambda _: {'data':{'id':'gen-abc123','total_cost':0.003}})=={rid:'reconciled'}
    with restarted.connect() as db:
        row=dict(db.execute('SELECT * FROM calls WHERE id=?',(rid,)).fetchone())
        missing=dict(db.execute('SELECT * FROM calls WHERE id=?',(no_id,)).fetchone())
    assert row['accounted']==row['actual_cost_nano']==3_000_000 and row['reconcile_status']=='reconciled'
    assert missing['accounted']==RESERVATION and missing['actual_cost_nano'] is None
    assert missing['reconcile_status']=='missing_generation_id'


def test_reconcile_cli_missing_state_never_creates_ledger(tmp_path):
    import os, subprocess, sys
    state=tmp_path/'missing'
    result=subprocess.run([sys.executable,'-m','pacbench.gateway.reconcile_cli','--state',str(state)],
                          env={**os.environ,'OPENROUTER_API_KEY':'fixture'},capture_output=True,text=True)
    assert result.returncode != 0 and not state.exists()
    assert 'fixture' not in result.stderr


def test_finish_preserves_previously_observed_partial_metadata(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');rid=ledger.reserve('p','responses')
    ledger.observe_metadata(rid,{'generation_id':'gen-abc','usage':{'input_tokens':2}})
    ledger.finish(rid)
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['generation_id']=='gen-abc' and json.loads(row['usage_detail'])=={'input_tokens':2}
    assert row['accounted']==RESERVATION


def test_generation_usage_remains_separate_from_native_response_usage(tmp_path):
    from pacbench.gateway.openrouter_reconcile import reconcile
    ledger=Ledger(tmp_path/'ledger.db');rid=ledger.reserve('p','responses')
    ledger.finish(rid,usage=(2,3),metadata={'usage':{'input_tokens':2}})
    with ledger.connect() as db:db.execute("UPDATE calls SET upstream_origin='openrouter',generation_id='gen-abc' WHERE id=?",(rid,))
    assert reconcile(ledger,rid,lambda _: {'data':{'id':'gen-abc','total_cost':0.01,'native_tokens_prompt':4,'native_tokens_reasoning':2,'secret':'private'}})=='reconciled'
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls WHERE id=?',(rid,)).fetchone())
    assert json.loads(row['usage_detail'])=={'input_tokens':2}
    assert json.loads(row['generation_usage_detail'])=={'native_tokens_prompt':4,'native_tokens_reasoning':2}
    assert 'private' not in json.dumps(row)


def test_reservation_clock_is_read_after_transaction_lock(tmp_path):
    stamp=['2026-10-19T23:59:59+00:00']
    ledger=Ledger(tmp_path/'ledger.db',now=lambda:stamp[0])
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ledger.reserve,'p','responses')
            # Move the clock while the writer is blocked on SQLite's lock.
            stamp[0]='2026-10-20T00:00:01+00:00'
            db.commit()
            rid=future.result(timeout=5)
    with ledger.connect() as db: row=db.execute('SELECT started FROM calls WHERE id=?',(rid,)).fetchone()
    assert row['started'].startswith('2026-10-20')
