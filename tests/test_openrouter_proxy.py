import json
from decimal import Decimal
import pytest
from pacbench.openrouter_proxy import Budget, MODEL, ENDPOINT


def budget(path):
    return Budget(path, cap='1', context_tokens=100, prompt_per_million='1',
                  completion_per_million='2', max_completion_tokens=50)


def test_route_and_settlement(tmp_path):
    sent = []
    def send(body):
        sent.append(body)
        return 200, json.dumps({'id':'request-id','model':MODEL,'provider':'DeepInfra',
                               'choices':[{'finish_reason':'stop'}],
                               'usage': {'cost': 0.0001, 'prompt_tokens': 20, 'completion_tokens': 10}}).encode()
    b = budget(tmp_path / 'ledger')
    b.request('driver', {'model': MODEL, 'messages': [], 'max_tokens': 50,
                         'provider': {'only': [ENDPOINT]}}, send)
    assert sent[0]['provider']['only'] == [ENDPOINT]
    assert sent[0]['provider']['allow_fallbacks'] is False
    calls = b.snapshot()
    assert calls[0]['state'] == 'settled'
    assert calls[0]['project'] == 'driver'
    assert calls[0]['upstream_request_id'] == 'request-id'
    assert calls[0]['served_model'] == MODEL and calls[0]['served_provider'] == 'DeepInfra'
    assert calls[0]['finish_reason'] == 'stop'
    assert Decimal(calls[0]['accounted']) == Decimal('0.0001')


def test_uncertain_retained_on_restart_and_conflict_rejected(tmp_path):
    path = tmp_path / 'ledger'
    b = budget(path)
    with pytest.raises(OSError):
        b.request('mem0', {'model': MODEL, 'messages': []}, lambda _: (_ for _ in ()).throw(OSError()))
    assert budget(path).snapshot()[0]['state'] == 'uncertain'
    assert Decimal(b.snapshot()[0]['accounted']) == Decimal('0.0002')
    with pytest.raises(ValueError):
        b.request('mem0', {'model': MODEL, 'provider': {'only': ['other']}}, lambda _: None)
    assert len(b.snapshot()) == 1


def test_snapshot_keeps_active_reservation_without_waiting_for_inference(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    b = budget(tmp_path/'ledger')
    entered, finish = threading.Event(), threading.Event()
    def send(body):
        entered.set()
        assert finish.wait(5)
        return 200, b'{"usage":{"cost":0.00001,"prompt_tokens":3,"completion_tokens":2}}'
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(b.request, 'provider', {'model':MODEL}, send)
        assert entered.wait(2)
        try:
            rows = pool.submit(b.snapshot).result(timeout=0.5)
            assert rows[0]['state'] == 'uncertain'
            assert Decimal(rows[0]['accounted']) == Decimal('0.0002')
        finally:
            finish.set()
        pending.result(timeout=2)
    assert b.snapshot()[0]['state'] == 'settled'


def test_shutdown_drains_inference_after_client_timeout(tmp_path):
    import threading
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from pacbench.openrouter_proxy import make_server
    b = budget(tmp_path/'ledger')
    entered, finish = threading.Event(), threading.Event()
    def send(body):
        entered.set()
        assert finish.wait(5)
        return 200, b'{"usage":{"cost":0.00001,"prompt_tokens":3,"completion_tokens":2}}'
    server = make_server(0, 'local', 'unused', b, send=send)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def client():
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/p/test/v1/chat/completions',
            data=json.dumps({'model':MODEL}).encode(), headers={'Authorization':'Bearer local'})
        try:
            urllib.request.urlopen(req, timeout=0.1)
        except (OSError, TimeoutError):
            pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(client)
        assert entered.wait(2)
        server.shutdown()
        closing = pool.submit(server.server_close)
        try:
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.1)
        finally:
            finish.set()
        closing.result(timeout=2)
        request.result(timeout=2)
    worker.join(timeout=2)
    assert b.snapshot()[0]['state'] == 'settled'


def test_refuses_model_substitution_and_over_budget(tmp_path):
    b = Budget(tmp_path / 'ledger', cap='0.0001', context_tokens=100,
               prompt_per_million='1', completion_per_million='2', max_completion_tokens=50)
    with pytest.raises(ValueError):
        b.request('x', {'model': 'other'}, lambda _: None)
    with pytest.raises(ValueError):
        b.request('x', {'model': MODEL}, lambda _: None)
    assert b.snapshot() == []


def test_stream_usage_and_auth_latch(tmp_path):
    b = budget(tmp_path / 'ledger')
    stream = b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"cost":0.00001}}\n\ndata: [DONE]\n\n'
    b.request('x', {'model': MODEL, 'stream': True}, lambda _: (200, stream))
    assert b.snapshot()[0]['state'] == 'settled'
    b.request('x', {'model': MODEL}, lambda _: (401, b'{}'))
    with pytest.raises(ValueError, match='authentication blocked'):
        b.request('x', {'model': MODEL}, lambda _: (200, b'{}'))


def test_http_project_auth_and_health(tmp_path):
    import threading
    import urllib.request
    import urllib.error
    from pacbench.openrouter_proxy import make_server
    b = budget(tmp_path / 'ledger')
    server = make_server(0, 'local-token', 'not-used', b,
                         send=lambda _: (200, b'{"usage":{"cost":0.00001,"prompt_tokens":3,"completion_tokens":2}}'))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + '/health')
        assert error.value.code == 401
        headers = {'Authorization': 'Bearer local-token', 'Content-Type': 'application/json'}
        req = urllib.request.Request(base + '/p/driver/v1/chat/completions', data=json.dumps({'model': MODEL}).encode(), headers=headers)
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
        with urllib.request.urlopen(urllib.request.Request(base + '/usage', headers=headers)) as response:
            assert json.load(response)['calls'][0]['project'] == 'driver'
        with urllib.request.urlopen(urllib.request.Request(base + '/health', headers=headers)) as response:
            health = json.load(response)
            assert (health['model'], health['endpoint'], health['limit_usd']) == (MODEL, ENDPOINT, '1')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_rejects_multimodal_and_multiple_generations(tmp_path):
    b = budget(tmp_path / 'ledger')
    for body in ({'model': MODEL, 'n': 2},
                 {'model': MODEL, 'messages': [{'content': [{'type': 'image_url', 'image_url': {}}]}]},
                 {'model': MODEL, 'tools': [{'type': 'web_search'}]}):
        with pytest.raises(ValueError):
            b.request('x', body, lambda _: None)
    assert b.snapshot() == []


def test_mismatched_upstream_model_remains_uncertain(tmp_path):
    b = budget(tmp_path / 'ledger')
    status, data = b.request('x', {'model': MODEL}, lambda _: (200, b'{"model":"other","usage":{"cost":0,"prompt_tokens":1,"completion_tokens":1}}'))
    assert status == 502
    assert b.snapshot()[0]['state'] == 'uncertain'
    assert b'other' not in data


def test_http_budget_and_auth_failures_are_safe(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from pacbench.openrouter_proxy import make_server
    b = budget(tmp_path / 'ledger')
    server = make_server(0, 'token', 'unused', b, send=lambda _: (401, b'{"error":"safe"}'))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}/p/provider/v1/chat/completions'
    try:
        def call():
            req = urllib.request.Request(url, data=json.dumps({'model': MODEL}).encode(),
                                         headers={'Authorization': 'Bearer token', 'Content-Type': 'application/json'})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            return error.value.code, json.loads(error.value.read())
        assert call()[0] == 401
        code, body = call()
        assert code == 403 and body['error']['type'] == 'upstream_auth_blocked'
        assert len(b.snapshot()) == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_budget_refusal_is_429(tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from pacbench.openrouter_proxy import make_server
    b = Budget(tmp_path / 'ledger', cap='0.00001', context_tokens=100,
               prompt_per_million='1', completion_per_million='2', max_completion_tokens=50)
    server = make_server(0, 'token', 'unused', b, send=lambda _: pytest.fail('must not send'))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/p/driver/v1/chat/completions',
                                     data=json.dumps({'model': MODEL}).encode(), headers={'Authorization': 'Bearer token'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        assert error.value.code == 429
        assert json.loads(error.value.read())['error']['type'] == 'budget_refused'
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_stream_rejects_contradictory_earlier_model(tmp_path):
    b = budget(tmp_path / 'ledger')
    stream = b'data: {"model":"wrong-model","choices":[]}\n\ndata: {"usage":{"prompt_tokens":10,"completion_tokens":5,"cost":0.00001}}\n\ndata: [DONE]\n\n'
    status, _ = b.request('x', {'model': MODEL, 'stream': True}, lambda _: (200, stream))
    assert status == 502
    assert b.snapshot()[0]['state'] == 'uncertain'
    assert b.snapshot()[0]['error_label'] == 'model_mismatch'


def test_missing_completion_limit_reserves_endpoint_max_without_override(tmp_path):
    b = budget(tmp_path / 'ledger')
    sent = []
    b.request('x', {'model': MODEL}, lambda request: (sent.append(request) or 200, b'{}'))
    assert 'max_tokens' not in sent[0] and 'max_completion_tokens' not in sent[0]
    assert b.snapshot()[0]['completion_bound_tokens'] == 50
    assert Decimal(b.snapshot()[0]['reserved']) == Decimal('0.0002')


def test_real_transport_requires_durable_task_reservation(monkeypatch):
    from pacbench import openrouter_proxy
    monkeypatch.setattr(openrouter_proxy.urllib.request, 'build_opener',
                        lambda *args: pytest.fail('unexpected network call'))
    with pytest.raises(RuntimeError, match='durable task reservation'):
        openrouter_proxy.send_openrouter({}, 'fake-key')


def test_real_stream_forwards_incrementally_and_rejects_wrong_model(monkeypatch):
    from types import SimpleNamespace
    from pacbench import openrouter_proxy as proxy, openrouter_task
    monkeypatch.setattr(openrouter_task, 'require_authorization', lambda b: None)
    seen = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read1(self, size):
            chunk = chunks.pop(0)
            seen.append(('read', chunk))
            return chunk
    monkeypatch.setattr(proxy.urllib.request, 'build_opener',
                        lambda *a: SimpleNamespace(open=lambda *a, **kw: Response()))
    chunk = json.dumps({'model': MODEL, 'choices': []}).encode()
    chunks = [b'data: '+chunk+b'\n\n', b'data: [DONE]\n\n', b'']
    status, raw = proxy.send_openrouter({'model':MODEL,'stream':True}, 'unused',
        budget=SimpleNamespace(_sending=True), stream_callback=lambda c: seen.append(('forward',c)))
    assert status == 200 and raw.endswith(b'[DONE]\n\n')
    assert [k for k, _ in seen] == ['read','forward','read','forward','read']
    chunks = [b'data: {"model":"wrong-model"}\n\n', b'']
    with pytest.raises(ValueError, match='model mismatch'):
        proxy.send_openrouter({'model':MODEL,'stream':True}, 'unused',
            budget=SimpleNamespace(_sending=True), stream_callback=lambda c: pytest.fail('forwarded wrong model'))
