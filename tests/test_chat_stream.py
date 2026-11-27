"""A complete-looking verdict is insufficient without a complete metered stream."""

import json

from unittest.mock import Mock

import pytest

from pacbench.model_calls import parse_chat_stream, IncompleteChatStream, CHAT_STREAM_PROTOCOL as VERSION

from pacbench.model_calls import read_bounded_response

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
    result=read_bounded_response(response,__import__('time').monotonic(),timing,stop_at_done=True)
    assert result==wire() and response.read1.call_count==2
