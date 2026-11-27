"""Offline receipts use synthetic data and temporary ledgers only."""

import json

from pacbench.openrouter_route import MODEL

from pacbench.receipt_costs import parse_response as parse, sanitized

def event(cost='0.00001',**extra):
    return {'id':'gen-offline','model':MODEL,'provider':'DeepInfra',
        'choices':[{'finish_reason':'stop','message':{'content':'PRIVATE-CONTENT-SENTINEL'}}],
        'usage':{'cost':cost,'prompt_tokens':3,'completion_tokens':2},**extra}

def wire(e):return ('data: '+json.dumps(e)+'\n\n').encode()

def test_parser_preserves_multiline_sse_and_malformed_tail():
    events,errors=parse(b'data: {"id":"gen-offline",\ndata: "usage":{"cost":0,"prompt_tokens":0,"completion_tokens":0}}\n\ndata: broken')
    assert len(events)==1 and errors==1
    assert sanitized(events[0])['usage']['cost']=='0'

def test_blank_sse_prefix_and_empty_keepalive_do_not_hide_usage():
    events,errors=parse(b'\n\n: keepalive\n\ndata:\n\n'+wire(event()))
    assert len(events)==1 and errors==0
