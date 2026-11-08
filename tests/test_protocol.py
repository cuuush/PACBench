import io
import json
import urllib.error

import pytest

from muse_gateway.ledger import Ledger, MODEL, RESERVATION
from muse_gateway.protocol import observe_usage, request_shape, safe_protocol
from test_gateway import gateway, post, Response


@pytest.mark.parametrize('choice,kind', [
    ('auto','auto'),('none','none'),('required','required'),
    ({'type':'function','function':{'name':'PRIVATE_TOOL'}},'function_object'),
    ('PRIVATE_CHOICE','other_string'),({'private':'PRIVATE_ARGUMENT'},'other_object'),
    (None,'invalid'),
])
def test_request_shape_never_rewrites_the_native_tool_choice(tmp_path,choice,kind):
    captured=[]
    def upstream(request,**kwargs):
        captured.append(json.loads(request.data))
        raise urllib.error.HTTPError('upstream',400,'private response',{},io.BytesIO(
            b'{"error":{"code":"invalid_value","param":"tool_choice","message":"PRIVATE_ERROR"}}'))
    value={'model':MODEL,'tool_choice':choice,'tools':[{'type':'function','function':{'name':'PRIVATE_TOOL'}}],
           'messages':[{'role':'user','content':'PRIVATE_PROMPT'}]}
    with gateway(tmp_path,upstream) as (server,base):
        with pytest.raises(urllib.error.HTTPError) as error:post(base,value)
        assert 'PRIVATE' not in error.value.read().decode()
        assert captured==[value]
        with server.ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
        shape=json.loads(row['protocol_diagnostic'])
        assert shape['tool_choice']==kind and shape['tool_count']==1
        assert row['error_label']=='upstream_http_400:invalid_value:tool_choice'
        assert row['state']=='unknown' and row['accounted']==RESERVATION
        assert 'PRIVATE' not in json.dumps(row)


@pytest.mark.parametrize('usage', [None,{}, {'prompt_tokens':'PRIVATE_NUMBER','completion_tokens':True}])
def test_missing_sse_usage_records_shape_without_releasing_reservation(tmp_path,usage):
    event={'choices':[{'delta':{'content':'PRIVATE_ANSWER'}}]}
    if usage is not None:event['usage']=usage
    body=b'data: '+json.dumps(event).encode()+b'\n\ndata: [DONE]\n\n'
    with gateway(tmp_path,lambda *a,**k:Response(body,'text/event-stream')) as (server,base):
        with post(base,{'model':MODEL,'stream':True}) as response:assert response.read()==body
        with server.ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
        shape=json.loads(row['protocol_diagnostic'])
        assert shape['wire']=='sse' and shape['json_values']==1
        assert shape.get('usage_objects',0)==(0 if usage is None else 1)
        if usage:
            assert shape['usage_fields']=={'prompt_tokens':'string','completion_tokens':'boolean'}
        assert row['state']=='unknown' and row['accounted']==RESERVATION
        assert row['error_label']=='missing_usage_sse'
        assert 'PRIVATE' not in json.dumps(row)


@pytest.mark.parametrize('content_type',['text/event-stream','Text/Event-Stream; charset=UTF-8'])
def test_nested_response_usage_settles_exactly_and_preserves_wire_bytes(tmp_path,content_type):
    value={'type':'response.completed','response':{'usage':{'input_tokens':20,'output_tokens':30},
           'output':[{'private':'PRIVATE_ANSWER'}]}}
    body=b'data: '+json.dumps(value).encode()
    with gateway(tmp_path,lambda *a,**k:Response(body,content_type)) as (server,base):
        with post(base,{'model':MODEL,'stream':True},'/p/test/v1/responses') as response:assert response.read()==body
        with server.ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
        assert row['state']=='settled' and row['accounted']==20*100+30*200
        shape=json.loads(row['protocol_diagnostic'])
        assert shape['nested_usage'] is True and shape['usage_objects']==1
        assert shape['usage_fields']=={'input_tokens':'integer','output_tokens':'integer'}
        assert 'PRIVATE' not in json.dumps(row)


def test_ledger_revalidates_untrusted_protocol_metadata(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db');id=ledger.reserve('test','responses')
    ledger.finish(id,diagnostic={'protocol':{
        'tool_choice':'PRIVATE_CHOICE','tools':{'PRIVATE':'PRIVATE'},'tool_count':True,
        'wire':'PRIVATE_HEADER','json_values':-1,'usage_objects':2_000_000,
        'stream':'PRIVATE_STREAM','top_usage':['PRIVATE'],
        'usage_fields':{'input_tokens':'PRIVATE_TYPE','PRIVATE_KEY':'integer'},
        'PRIVATE_FIELD':'PRIVATE_VALUE'}})
    with ledger.connect() as db:row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert 'PRIVATE' not in json.dumps(row)
    assert row['state']=='unknown' and row['accounted']==RESERVATION


def test_shape_helpers_ignore_arbitrary_schema_and_token_field_names():
    shape=request_shape({'response_format':{'type':'json_schema','json_schema':{'private':'PRIVATE_SCHEMA'}}})
    observe_usage(shape,{'usage':{'PRIVATE_FIELD':'PRIVATE_VALUE','total_tokens':None}})
    assert safe_protocol(shape)['usage_fields']=={'total_tokens':'null'}
    assert 'PRIVATE' not in json.dumps(safe_protocol(shape))
