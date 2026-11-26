import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from pacbench import honcho_daily_gate as gate, honcho_first_answer_admission as answer
from pacbench import honcho_first_answer_peer as peer, workflow_capability
from pacbench.canary_capability import digest, read
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.live_dispatch import operation_id, process_identity
from pacbench.openrouter_route import PROFILES, with_route

MODEL = PROFILES[answer.PROFILE]['model']


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    root = tmp_path/'honcho'
    (root/'peer-reads').mkdir(parents=True)
    scope = dict(root=str(root),first_question=answer.QUESTION)
    native = dict(host=dict(workspace='fixture-scope',peerName='user',aiPeer='hermes'),
                  api=process_identity(),api_url='http://127.0.0.1:7777')
    current = dict(session_id='native-hermes',native_request_id='native-submission',turn_nonce='nonce',ready={})
    monkeypatch.setattr(peer,'_native',lambda scope:native)
    monkeypatch.setattr(peer,'active',lambda scope,**kwargs:current)
    monkeypatch.setattr(workflow_capability,'load',lambda:scope)
    return scope,current


def test_stock_peer_sdk_keeps_explicit_and_automatic_requests_separate(scoped, monkeypatch):
    sdk = pytest.importorskip('honcho')
    from importlib.metadata import version
    assert version('honcho-ai') == '2.9.0'
    scope, current = scoped
    seen, requests = [], []
    selected = [dict(kind='honcho-prefetch-first',submission=current),
                dict(kind='explicit',submission=current,selected_by='settled',tool_call_id='tool-1')]

    async def stock_endpoint(asgi, receive, send):
        assert peer.HEADER.encode() not in dict(asgi['headers'])
        proof = read(peer._read.get()[0])
        seen.append(proof['origin']['kind'])
        request = await receive()
        assert sha256(request['body']).hexdigest() == proof['request']['sha256']
        from starlette.responses import JSONResponse
        await JSONResponse({'content':'scripted local answer'})(asgi,receive,send)

    app = peer.wrap_api(stock_endpoint)

    async def send_asgi(req):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            return await client.request(req.method,str(req.url),headers=req.headers,content=req.content)

    def wire(req):
        assert req.url.host == '127.0.0.1'
        if req.url.path == '/v3/workspaces':
            return httpx.Response(200,json={'id':'fixture-scope','created_at':'2026-01-01T00:00:00Z'})
        if req.url.path.endswith('/peers'):
            return httpx.Response(200,json={'id':'hermes','workspace_id':'fixture-scope',
                                           'created_at':'2026-01-01T00:00:00Z'})
        req.headers[peer.HEADER] = peer._issue(scope,selected[len(requests)],req)
        requests.append(req)
        response = asyncio.run(send_asgi(req))
        return httpx.Response(response.status_code,content=response.content)

    with httpx.Client(transport=httpx.MockTransport(wire)) as transport:
        client = sdk.Honcho(environment='local',workspace_id='fixture-scope',max_retries=0,
                            base_url='http://127.0.0.1:7777',http_client=transport)
        observer = client.peer('hermes')
        for _ in range(2):
            assert observer.chat('Same query',target='user',reasoning_level='low') == 'scripted local answer'

    assert requests[0].content == requests[1].content
    assert requests[0].headers[peer.HEADER] != requests[1].headers[peer.HEADER]
    assert seen == ['honcho-prefetch-first','explicit']
    assert asyncio.run(send_asgi(requests[0])).status_code == 403
    tampered = httpx.Request('POST',str(requests[1].url),headers={peer.HEADER:requests[1].headers[peer.HEADER]},
                            json={'query':'other','target':'user','reasoning_level':'low','stream':False})
    assert asyncio.run(send_asgi(tampered)).status_code == 403
    missing = httpx.Request('POST',str(requests[1].url),content=requests[1].content)
    assert asyncio.run(send_asgi(missing)).status_code == 403
    assert len(seen) == 2


def test_later_turn_prefetch_is_recorded_but_does_not_invoke_inference(scoped, monkeypatch):
    scope, current = scoped
    stops = []
    from pacbench import direct_accounting
    monkeypatch.setattr(direct_accounting,'stop_measured_admission',lambda *args:stops.append(args))

    async def should_not_run(*args):
        pytest.fail('A request to warm a subsequent turn reached the provider')

    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=peer.wrap_api(should_not_run))) as client:
            req = client.build_request('POST','http://127.0.0.1:7777/v3/workspaces/fixture-scope/peers/hermes/chat',
                                       json=dict(query='Synthetic recall',reasoning_level='low',stream=False))
            req.headers[peer.HEADER] = peer._issue(scope,dict(kind='post-answer-prefetch',submission=current),req)
            return await client.send(req)

    assert asyncio.run(send()).status_code == 403
    assert not stops
    assert len(list((Path(scope['root'])/'peer-reads').glob('*.claimed.json'))) == 1


def test_only_exact_stock_driver_prompt_and_context_are_admitted(scoped, monkeypatch):
    portal = pytest.importorskip('agent.portal_tags')
    from agent.memory_context import build_memory_context_block
    from providers import get_provider_profile
    from pacbench import provider_loader

    scope, current = scoped
    turn = answer.question(scope)
    tools = [dict(type='function',function=dict(name=name,parameters={})) for name in answer.TOOLS]
    monkeypatch.setattr(provider_loader,'provider_module',lambda name:SimpleNamespace(
        ALL_TOOL_SCHEMAS=[deepcopy(tool['function']) for tool in tools]))
    monkeypatch.setattr(answer,'active',lambda scope:current)
    monkeypatch.setattr(answer,'_history',lambda *_:('Native system', []))
    monkeypatch.setattr(portal,'get_conversation_context',lambda:current['session_id'])
    prefetch = 'Scripted local memory used only by the transport test'
    Path(scope['root'],'reuse-context.jsonl').write_text(json.dumps(dict(session_id=current['session_id'],
                                    query=turn.prompt,prefetch=prefetch)))
    body = with_route(dict(model=MODEL,stream=True,stream_options={'include_usage':True},tools=tools,
                 reasoning={'enabled':True,'effort':'medium'},max_completion_tokens=32768,
                 messages=[dict(role='system',content='Native system'),
                           dict(role='user',content=turn.prompt+'\n\n'+build_memory_context_block(prefetch))],
                 session_id=get_provider_profile('openrouter').build_extra_body(session_id=current['session_id'])['session_id']),
                 profile=answer.PROFILE)
    assert answer.driver_slot(scope,body)['honcho_first_answer'] == {**current,'tool_rounds':[]}
    for field in ('session_id','user','system','tools','route'):
        change = deepcopy(body)
        if field in ('user','system'):
            next(m for m in change['messages'] if m['role']==field)['content'] += ' added text'
        elif field == 'tools':
            change['tools'][0]['function']['parameters']['new'] = True
        elif field == 'route':
            change['provider']['allow_fallbacks'] = True
        else:
            change[field] = 'foreign'
        with pytest.raises((ValueError, OwnershipConflict)):
            answer.driver_slot(scope,change)


def test_exact_tool_result_and_settled_operation_sequence(scoped):
    _, current = scoped
    call = dict(type='function',id='tool-1',function=dict(name='honcho_reasoning',arguments='{"query":"Same query"}'))
    selected = answer.selected_tools([{'choices':[{'delta':{'tool_calls':[call]},'finish_reason':'tool_calls'}]}])
    ordered = [dict(role='assistant',content='',tool_calls=[call]),dict(role='tool',content='result',tool_call_id=call['id'])]
    stored = [dict(role='assistant',content=None,tool_calls=json.dumps([call])),
              dict(role='tool',content='result',tool_call_id=call['id'],tool_name='honcho_reasoning')]
    assert answer.tool_rounds(ordered,stored) == [selected]
    changed = deepcopy(ordered)
    changed[1]['content'] = 'foreign'
    with pytest.raises(OwnershipConflict):
        answer.tool_rounds(changed,stored)
    receipts = dict(state='settled',http_status=200,dispatch_execution='response_ended',response_body_complete=True,
                    served_model=MODEL,served_provider='Amazon Bedrock',billing_receipt={'proof':True},
                    upstream_request_id='generation-1',finished=True,response_validation={'finish_reasons':['tool_calls']})
    first = dict(workflow_capability={'sha256':'scope'},workflow_slot={'honcho_first_answer':{**current,'tool_rounds':[]}},
                 honcho_selected_tools=selected,**receipts)
    following = dict(first,workflow_slot={'honcho_first_answer':{**current,'tool_rounds':[selected]}})
    assert operation_id(first) != operation_id(following)
    assert operation_id(first) == operation_id(dict(first,request_sha256='different-wire'))
    answer.verify_receipts({'first':first})
    answer.check_prepared({'first':first},following)
    with pytest.raises(OwnershipConflict):
        answer.verify_receipts({'first':first,'reused-generation':following})
    with pytest.raises(OwnershipConflict):
        answer.verify_receipts({'first':dict(first,state='receipt_missing')})
    with pytest.raises(OwnershipConflict):
        answer.check_prepared({'first':first},first)
    with pytest.raises(OwnershipConflict):
        answer.check_prepared({'first':dict(first,honcho_selected_tools=[])},following)
    early = dict(first,dispatch_execution='ambiguous',response_body_complete=False)
    assert not answer.settled(early)
    first_frame = dict(early,response_validation={'finish_reasons':['tool_calls'],'sse_done_observed':True})
    assert answer.settled(first_frame)
    answer.check_prepared({'first':first_frame},following)


def test_stream_terminal_requires_a_complete_unique_done_frame():
    from pacbench.response_receipts import read_frames

    assert read_frames(b'data: {}\n\ndata: [DONE]\n\n').sse_done_observed
    assert not read_frames(b'data: {}\n\ndata: [DONE]\n').sse_done_observed
    assert not read_frames(b'data: [DONE]\n\ndata: {}\n\n').sse_done_observed


def test_peer_native_model_attempt_needs_sdk_and_owned_claim(scoped, monkeypatch):
    pytest.importorskip('openai')
    from pacbench import direct_http
    from pacbench.openrouter_route import CHAT_COMPLETIONS_URL

    scope, current = scoped
    request = httpx.Request('POST','http://127.0.0.1:7777/v3/workspaces/fixture-scope/peers/hermes/chat',
                            json=dict(query='Same query',target='user',reasoning_level='low',stream=False))
    token = peer._issue(scope,dict(kind='honcho-prefetch-first',submission=current),request)
    claim = peer.claim(scope,token,peer._peer_request(scope,request.method,request.url.path,request.content))
    with pytest.raises(OwnershipConflict):
        peer.native_slot(scope)
    permit = peer._read.set([claim])
    ctx = gate._executor.set([(SimpleNamespace(call_purpose='dialectic.answer',workspace_name='fixture-scope',
                        observer='hermes',observed='user',run_id='native-run',trace_id='native-run',
                        session_id=None,step_seq=1,iteration=1), SimpleNamespace(attempt=1,is_fallback=False))])
    try:
        slot = peer.native_slot(scope)
        assert slot['honcho_peer_read']['claim'] == claim
        outbound = httpx.Request('POST',CHAT_COMPLETIONS_URL,json={'model':MODEL})
        outbound.extensions[gate.EXTENSION] = gate._executor.get(),'synthetic-key',0,gate._request_binding(outbound)
        marker = direct_http._native_http_request.set(outbound)
        try:
            row = {'workflow_slot':slot}
            peer.bind_attempt({},row)
            with pytest.raises(OwnershipConflict):
                peer.bind_attempt({'prior':row},row)
            with pytest.raises(OwnershipConflict):
                peer.bind_attempt({}, {'workflow_slot':dict(slot,honcho_peer_read={**slot['honcho_peer_read'],'step':2})})
        finally:
            direct_http._native_http_request.reset(marker)
    finally:
        gate._executor.reset(ctx)
        peer._read.reset(permit)


@pytest.fixture
def honcho_wire_journal(tmp_path, monkeypatch):
    from pacbench.live_dispatch import CONDITION, sender_identity
    from pacbench.observational_receipts import RunReceiptJournal

    journal = RunReceiptJournal(tmp_path/'receipts.jsonl')
    stop = tmp_path/'STOP'
    monkeypatch.delenv('PACBENCH_DIRECT_STOP_ON_429', raising=False)
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(stop))

    def observe(identifier, wire, *, complete=True, status=200, slot=None):
        row = dict(id=identifier, project='offline-honcho-driver', state='reserved',
                   dispatch_execution='ambiguous', dispatch_events=[{'kind': 'possible'}],
                   dispatch_context={'operation_id': identifier}, dispatch_owner=sender_identity(),
                   dispatch_tracking_condition=CONDITION, accounted='0',
                   workflow_slot=slot or {'honcho_first_answer': {'tool_rounds': []}})
        with journal.journal() as (_, append):
            append('prepared', identifier, row)
        journal.settle(identifier, status, wire, body_complete=complete)
        return journal.get_attempt(identifier)

    return observe, stop


def test_honcho_terminal_tool_decision_does_not_require_cost(honcho_wire_journal, scoped):
    observe, stop = honcho_wire_journal
    _, current = scoped
    call = dict(type='function', id='tool-1', function=dict(name='honcho_reasoning', arguments='{"query":"Query"}'))
    first_frame = dict(id='gen-tool', model=MODEL, provider='Amazon Bedrock',
                       choices=[dict(index=0, delta=dict(tool_calls=[call]), finish_reason='tool_calls')])
    usage_frame = dict(id='gen-tool', choices=[], usage=dict(prompt_tokens=5, completion_tokens=2))
    wire = f'data: {json.dumps(first_frame)}\n\ndata: {json.dumps(usage_frame)}\n\ndata: [DONE]\n\n'.encode()
    proof = {'honcho_first_answer': {**current, 'tool_rounds': []}}
    row = observe('one', wire, complete=False, slot=proof)
    assert row['state'] == 'receipt_missing'
    assert not row.get('billing_receipt') and not row.get('settled_charge_usd')
    assert not answer.settled(row)
    assert answer.usable_response(row)
    answer.verify_receipts({'one': row})
    answer.check_prepared({'one': row}, {'workflow_slot': {
        'honcho_first_answer': {**current, 'tool_rounds': [row['honcho_selected_tools']]}}})
    assert not stop.exists()

    duplicate = observe('two', wire, complete=False)
    with pytest.raises(OwnershipConflict, match='unique usable'):
        answer.verify_receipts({'one': row, 'two': duplicate})
    assert json.loads(stop.read_text())['reason'] == 'honcho_unusable_or_duplicate_terminal_response'


@pytest.mark.parametrize('failure', ['wrong-route', 'parse', 'duplicate-id', 'missing-done',
                                    'length', 'empty', 'no-request-id', 'refused', 'non-stream-driver'])
def test_honcho_rejects_unusable_response_with_missing_cost(honcho_wire_journal, failure):
    observe, stop = honcho_wire_journal
    event = dict(id='gen-final', model=MODEL, provider='Amazon Bedrock',
                 choices=[dict(index=0, delta={'content': 'answer'}, finish_reason='stop')])
    if failure == 'wrong-route':
        event['provider'] = 'Other provider'
    if failure == 'length':
        event['choices'][0]['finish_reason'] = 'length'
    if failure == 'empty':
        event['choices'][0]['delta']['content'] = ' '
    if failure == 'no-request-id':
        del event['id']
    wire = f'data: {json.dumps(event)}\n\n'.encode()
    if failure == 'parse':
        wire += b'data: broken-json\n\n'
    if failure == 'duplicate-id':
        wire += b'data: {"id":"gen-foreign","choices":[]}\n\n'
    if failure != 'missing-done':
        wire += b'data: [DONE]\n\n'
    if failure == 'non-stream-driver':
        wire = json.dumps(event).encode()
    row = observe('one', wire, status=403 if failure == 'refused' else 200)
    assert not answer.usable_response(row)
    with pytest.raises(OwnershipConflict, match='unique usable'):
        answer.verify_receipts({'one': row})
    assert stop.exists()


def test_honcho_costless_json_completion_remains_operational(honcho_wire_journal):
    observe, stop = honcho_wire_journal
    event = dict(id='gen-source', model=MODEL, provider='Amazon Bedrock',
                 choices=[dict(index=0, message={'content': '{"representation":"native output"}'}, finish_reason='stop')])
    row = observe('source', json.dumps(event).encode(), slot={'honcho_daily_release': {'sha256': 'release'}})
    assert row['state'] == 'receipt_missing'
    assert row['error_label'] == 'actual_cost_unknown'
    assert 'billing_receipt' not in row and 'settled_charge_usd' not in row
    assert answer.usable_response(row)
    answer.verify_receipts({'source': row})
    assert not stop.exists()


@pytest.mark.parametrize('full_week', [False, True])
@pytest.mark.parametrize('fault', ['success', 'startup', 'native', 'snapshot', 'billing'])
def test_honcho_launcher_preserves_failure_and_shutdown_evidence(tmp_path, monkeypatch, full_week, fault):
    from contextlib import nullcontext
    from unittest.mock import Mock

    from pacbench import honcho_first_answer as first, honcho_week as week_runner

    campaign = tmp_path/'campaign'
    campaign.mkdir()
    (campaign/'execution.lock').touch()
    plugin_home = tmp_path/'plugin-home'
    (plugin_home/'plugins').mkdir(parents=True)
    (plugin_home/'plugins/.install-metadata.json').write_text('{}')
    root = campaign/(week_runner.RUN if full_week else first.RUN)/'honcho'
    upstream = dict(id='request', state='settled', http_status=200, finished='yes',
                    response_body_complete=True, dispatch_execution='response_ended',
                    served_model=MODEL, served_provider='Amazon Bedrock', upstream_request_id='gen',
                    response_validation={'finish_reasons': ['stop']}, billing_receipt={'proof': True})

    primary = {
        'startup': RuntimeError('private startup detail'),
        'native': TimeoutError('private native detail'),
        'snapshot': OSError('private snapshot detail'),
        'billing': ValueError('private billing detail'),
    }.get(fault)
    stop = Mock(side_effect=ChildProcessError('private shutdown detail') if fault in ('startup', 'native') else None)
    verify_cleanup = Mock(return_value=True)
    ledger = Mock()
    ledger.snapshot.return_value = [upstream]
    if fault == 'snapshot':
        ledger.snapshot.side_effect = primary
    billing = Mock(return_value={'complete_cost_usd': '0.001'})
    if fault in ('startup', 'native', 'billing'):
        billing.side_effect = primary if fault == 'billing' else ValueError('private billing detail')
    execute = Mock(side_effect=primary if fault == 'native' else None)

    def start(*args, register_stop, **kwargs):
        register_stop(stop)
        if fault == 'startup':
            raise primary

    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline')
    for name in (first.JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY',
                 'PACBENCH_OFFLINE_CONFORMANCE', 'PACBENCH_JUDGE_OUTPUT_RECOVERY'):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        'version': lambda _: '2.9.0',
        'PROFILE_NAME': first.capability.PROFILE,
        'selected': lambda: None,
        'validate_recall_environment': lambda: None,
        'verify_retained_catalog_provider': lambda *_: None,
        'fetch_catalog': lambda **_: None,
        'read_prepaid_evidence': lambda *_: ({}, b'{}', {'receipt_sha256': 'offline'}),
        'direct_environment': lambda _: nullcontext(),
        'client_settings': lambda _: ('offline-url', 'offline-key'),
        'upload': lambda *_: {'sha256': 'online-source'},
        'check_stops': lambda *_: None,
        'cleanup_verified': verify_cleanup,
        'receipt_summary': billing,
        'ask': execute,
    }.items():
        monkeypatch.setattr(first, name, value)
    monkeypatch.setattr(week_runner, 'answers', execute)
    monkeypatch.setattr(first.harness, 'provision_home', lambda home, **_: (home/'plugins').mkdir(parents=True))
    monkeypatch.setattr(first.gate, 'materialize_canonical_files', lambda *_, **__: {'path': 'file', 'sha256': 'source'})
    monkeypatch.setattr(first.gate, 'create', lambda *_, **__: {})
    monkeypatch.setattr(first.capability, 'budget', lambda: ledger)
    monkeypatch.setattr(first.capability, 'load', lambda: {
        'projects': {'honcho': {'driver': 'driver', 'memory': 'memory'}}, 'plan': [{'source_ids': ['message']}]})
    monkeypatch.setattr('provider_adapters.honcho_local.start', start)

    if primary is None:
        expected = 'stock-week-answers-complete' if full_week else 'stock-first-answer-qualified'
        assert first.run(campaign, tmp_path, plugin_home, full_week=full_week)['state'] == expected
    else:
        with pytest.raises(type(primary)) as observed:
            first.run(campaign, tmp_path, plugin_home, full_week=full_week)
        assert observed.value is primary
    result = json.loads((root/'result.json').read_text())
    assert result['cleanup'] == 'owned-services-stopped'
    assert 'private' not in json.dumps(result)
    stop.assert_called_once()
    verify_cleanup.assert_called_once_with(root)
    ledger.snapshot.assert_called_once()
    if fault == 'startup':
        execute.assert_not_called()
    else:
        execute.assert_called_once()
    if fault == 'snapshot':
        billing.assert_not_called()
    else:
        billing.assert_called_once_with(root, [upstream], root.parent.name)
    if primary is not None:
        stage = {'startup': 'native_start', 'native': 'native_execution',
                 'snapshot': 'receipt_snapshot', 'billing': 'receipt_summary'}[fault]
        assert result['state'] == 'incomplete'
        assert result['failure_type'] == type(primary).__name__
        assert result['failure_stage'] == stage
    if fault in ('startup', 'native'):
        assert result['secondary_failures'] == [
            {'stage': 'native_shutdown', 'failure_type': 'ChildProcessError'},
            {'stage': 'receipt_summary', 'failure_type': 'ValueError'},
        ]
