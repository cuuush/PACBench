"""A complete-looking verdict is insufficient without a complete metered stream."""
import io
import json
from email.message import Message
from pathlib import Path
from unittest.mock import Mock
import pytest
from pacbench.chat_stream import parse_chat_stream,IncompleteChatStream,VERSION
from pacbench.model_calls import ModelClient,read_direct_response
from pacbench.direct_accounting import DirectBudget
from pacbench.direct_http import UrllibResponse
from pacbench.openrouter_route import MODEL


def frame(value):
    return ('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode()


def wire(*,done=True,usage=True,finish='stop',content='{"verdicts":[]}'):
    data=b': native keepalive\n\n\n'+frame({'id':'gen-native','model':MODEL,'choices':[{'index':0,'delta':{'role':'assistant','content':content[:5]}}]})
    data+=frame({'id':'gen-native','model':MODEL,'choices':[{'index':0,'delta':{'content':content[5:]},'finish_reason':finish}]})
    if usage:data+=frame({'id':'gen-native','model':MODEL,'choices':[], 'usage':{'prompt_tokens':7,'completion_tokens':11,'cost':0.000012}})
    if done:data+=b'data: [DONE]\n\n'
    return data


def test_complete_stream_keeps_answer_identity_usage_and_terminal_proof():
    body=wire(content='{"text":"café 🍕"}')
    result,proof=parse_chat_stream(body,MODEL)
    assert result['choices'][0]['message']['content']=='{"text":"café 🍕"}'
    assert result['id']=='gen-native' and result['usage']['cost']==0.000012
    assert proof['protocol']==VERSION and proof['done'] and proof['usage_complete']
    assert proof['finish_reason']=='stop' and proof['body_bytes']==len(body)


@pytest.mark.parametrize('body',[
    b'\n         \n',wire(done=False),wire(usage=False),wire(finish='length'),wire(finish=None),wire(content=''),
    frame({'error':{'code':502,'message':'Private upstream detail'}})+b'data: [DONE]\n\n',
    frame({'id':'other','model':MODEL,'choices':[]})+wire(),
    frame({'id':'gen-native','model':'different','choices':[]})+wire(),
    b'data: {invalid}\n\n'+wire(),wire()+frame({'choices':[]}),
    frame({'choices':[{'index':1,'delta':{'content':'bad'}}]})+wire(),
    frame({'choices':[{'delta':{'tool_calls':[{'function':'unexpected'}]}}]})+wire(),
])
def test_partial_failed_or_unmetered_streams_never_return_a_judgment(body):
    with pytest.raises(IncompleteChatStream):parse_chat_stream(body,MODEL)


def test_bool_tokens_and_negative_or_nonfinite_cost_are_rejected():
    for usage in [{'prompt_tokens':True,'completion_tokens':2,'cost':0},
                  {'prompt_tokens':1,'completion_tokens':2,'cost':-1},
                  {'prompt_tokens':1,'completion_tokens':2,'cost':'NaN'}]:
        body=wire(done=False,usage=False)+frame({'usage':usage,'choices':[]})+b'data: [DONE]\n\n'
        with pytest.raises(IncompleteChatStream,match='usage'):parse_chat_stream(body,MODEL)


def test_logical_done_does_not_wait_for_a_connection_to_close():
    response=Mock()
    response.fp=None
    response.read1.side_effect=[wire()[:-8],wire()[-8:],AssertionError('read after logical completion')]
    timing={}
    result=read_direct_response(response,__import__('time').monotonic(),timing,stop_at_done=True)
    assert result==wire() and response.read1.call_count==2


@pytest.fixture
def direct(tmp_path,monkeypatch):
    path=tmp_path/'fixture-ledger.json'
    path.write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':1024,
        'prompt_per_million':'0.1','completion_per_million':'0.2','max_completion_tokens':256,
        'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    budget=DirectBudget(path)
    monkeypatch.setenv('PACBENCH_DIRECT','1');monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter')
    monkeypatch.setattr('pacbench.gateway_client.validate_route',lambda *a:None)
    return budget


class Raw:
    status=200
    def __init__(self,body):
        self.parts=[body[i:i+3] for i in range(0,len(body),3)]
        self.headers=Message();self.headers['Content-Type']='text/event-stream';self.fp=None
    def read1(self,*args):return self.parts.pop(0) if self.parts else b''
    def close(self):pass


def test_actual_accounting_wrapper_settles_before_acceptance_and_saves_private_wire(tmp_path,monkeypatch,direct):
    requests=[]
    def opened(request,timeout):
        body=json.loads(request.data);requests.append(body)
        receipt=direct.reserve('offline-judge',body)
        return UrllibResponse(Raw(wire()),{'id':receipt['id'],'budget':direct})
    monkeypatch.setattr('pacbench.model_calls.urllib.request.urlopen',opened)
    client=ModelClient(key='inert',model=MODEL,record_dir=tmp_path/'calls',json_mode=True)
    output=client('Judge this frozen answer')
    assert requests[0]['stream'] is True and requests[0]['stream_options']=={'include_usage':True}
    assert requests[0]['response_format']=={'type':'json_object'}
    record=json.loads(Path(output.record_path).read_text())
    assert record['response_transport']==VERSION and record['stream_completion']['done']
    assert record['accounting_receipt']['state']=='settled'
    body=Path(output.record_path).parent/record['stream_wire_response']['relative_path']
    assert body.read_bytes()==wire() and body.stat().st_mode & 0o077==0
    assert direct.snapshot()[0]['accounted']=='0.000012'
    client('Judge this frozen answer')
    assert len(requests)==1


@pytest.mark.parametrize('body',[wire(done=False),wire(usage=False),b'\n         \n'])
def test_stream_failure_is_preserved_without_paid_retry_or_grade(tmp_path,monkeypatch,direct,body):
    sent=[]
    def opened(request,timeout):
        receipt=direct.reserve('offline-judge',json.loads(request.data));sent.append(receipt['id'])
        return UrllibResponse(Raw(body),{'id':receipt['id'],'budget':direct})
    monkeypatch.setattr('pacbench.model_calls.urllib.request.urlopen',opened)
    client=ModelClient(key='inert',model=MODEL,record_dir=tmp_path/'calls')
    with pytest.raises(RuntimeError,match='audit record'):client('Judge this frozen answer')
    assert len(sent)==1
    record=json.loads(next((tmp_path/'calls').glob('*.json')).read_text())
    assert record['status']=='failed'
    assert (tmp_path/'calls'/record['failed_response_body']['relative_path']).read_bytes()==body
    if body!=wire(done=False):
        hold=direct.snapshot()[0]['accounted']
        with pytest.raises(RuntimeError,match='Uncertain request outcome'):client('Judge this frozen answer')
        assert len(sent)==1 and direct.snapshot()[0]['state']=='uncertain'
        assert direct.snapshot()[0]['accounted']==hold


def test_disconnect_preserves_received_bytes_and_unknown_charge(tmp_path,monkeypatch,direct):
    partial=wire(done=False,usage=False)
    class Disconnected(Raw):
        def read1(self,*args):
            if self.parts:return super().read1(*args)
            raise ConnectionResetError('upstream disconnected')
    sent=[]
    def opened(request,timeout):
        receipt=direct.reserve('offline-judge',json.loads(request.data));sent.append(receipt['id'])
        return UrllibResponse(Disconnected(partial),{'id':receipt['id'],'budget':direct})
    monkeypatch.setattr('pacbench.model_calls.urllib.request.urlopen',opened)
    client=ModelClient(key='inert',model=MODEL,record_dir=tmp_path/'calls')
    with pytest.raises(RuntimeError,match='ConnectionResetError'):client('Frozen answer')
    record=json.loads(next((tmp_path/'calls').glob('*.json')).read_text())
    assert (tmp_path/'calls'/record['failed_response_body']['relative_path']).read_bytes()==partial
    row=direct.snapshot()[0]
    assert row['state']=='uncertain' and row['accounted']==row['reserved']
    assert len(sent)==1


def test_direct_stream_without_accounting_cannot_be_accepted(tmp_path,monkeypatch,direct):
    class Unaccounted(Raw):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    monkeypatch.setattr('pacbench.model_calls.urllib.request.urlopen',lambda *a,**k:Unaccounted(wire()))
    client=ModelClient(key='inert',model=MODEL,record_dir=tmp_path/'calls')
    with pytest.raises(RuntimeError,match='no accounting receipt'):client('Frozen answer')
