import json

import os

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import subprocess

import sys

import threading

import urllib.error

import urllib.request

import pytest

from pacbench import openrouter_run as r

from pacbench.openrouter_route import MODEL, ENDPOINT

def catalog():
    return {'data': {'id': MODEL, 'endpoints': [{
        'model_id': MODEL, 'tag': ENDPOINT, 'quantization': 'fp8', 'service_tier': 'flex',
        'supported_parameters': ['tools', 'tool_choice', 'response_format'],
        'context_length': 1048576, 'max_completion_tokens': 393216,
        'pricing': {'prompt': '0.0000000784', 'completion': '0.0000002352',
                    'input_cache_read': '0.000000002352', 'discount': 0.51}}]}}

def test_catalog_binds_full_endpoint_bounds():
    params = r.catalog_parameters(catalog())
    assert params['max_completion_tokens'] == 393216
    assert params['context_tokens'] == 1048576
    assert params['prompt_per_million'] == '0.0784000000'
    assert params['completion_per_million'] == '0.2352000000'

@pytest.mark.parametrize('mutation', ['endpoint', 'pricing', 'missing_price', 'context', 'tools'])
def test_catalog_refuses_unknown_costs_or_capabilities(mutation):
    data = catalog()
    row = data['data']['endpoints'][0]
    if mutation == 'endpoint': row['tag'] = 'deepinfra'
    if mutation == 'pricing': row['pricing']['request'] = '0.1'
    if mutation == 'missing_price': del row['pricing']['completion']
    if mutation == 'context': row['context_length'] = None
    if mutation == 'tools': row['supported_parameters'] = []
    with pytest.raises(ValueError): r.catalog_parameters(data)

def test_secrets_and_route_overrides_are_scrubbed_then_restored(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-upstream-key')
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://other.invalid')
    with r.transport_environment({'url': 'http://127.0.0.1:1234', 'token': 'local'}, '1'):
        assert not os.environ['OPENROUTER_API_KEY']
        assert not os.environ['CUSTOM_BASE_URL']
        assert os.environ['PACBENCH_OPENROUTER_PROXY_TOKEN'] == 'local'
    assert os.environ['OPENROUTER_API_KEY'] == 'fake-upstream-key'
    assert os.environ['CUSTOM_BASE_URL'] == 'https://other.invalid'

def test_public_session_forwards_with_key_enforces_cap_and_cleans_up(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from pacbench.context_isolation import PROXY_PROTOCOL, input_rows as rows
    from pacbench.context_isolation import NATIVE_QUESTION as NATIVE, delivery
    from pacbench import openrouter_proxy
    from pacbench.gateway_client import client_settings, require_live_cost_boundary, SpendingBlocked
    from pacbench.usage import snapshot

    sent = []
    key = 'synthetic-upstream-key'
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            sent.append((self.path, self.headers['Authorization'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body = json.dumps({'model': MODEL, 'usage': {'cost': '0.0001', 'prompt_tokens': 20, 'completion_tokens': 10}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(openrouter_proxy, 'UPSTREAM', f'http://127.0.0.1:{upstream.server_port}/chat/completions')
    monkeypatch.setattr(r, 'fetch_catalog', lambda: (dict(context_tokens=100, max_completion_tokens=50,
        prompt_per_million='1', completion_per_million='2'), {'observed_at': 'fixture'}))
    monkeypatch.setenv('OPENROUTER_API_KEY', key)
    monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY', key)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', 'example.invalid')
    output = tmp_path / 'run'
    try:
        with pytest.raises(RuntimeError, match='run failed'):
            with r.public_session(output, '0.00025'):
                assert require_live_cost_boundary()['limit_usd'] == '0.00025'
                env = subprocess.check_output([sys.executable, '-c', 'import os; print(dict(os.environ))'], text=True)
                assert key not in env
                assert 'example.invalid,127.0.0.1,localhost' in env
                base, token = client_settings('test-driver')
                url = base + '/chat/completions'
                messages = [{'role': 'system', 'content': 'native system prompt'},
                            {'role': 'user', 'content': 'What happened?'}]
                body = json.dumps({'model': MODEL, 'messages': messages, 'max_tokens': 50}).encode()
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=2)
                assert error.value.code == 401
                req = urllib.request.Request(url, data=body, headers={'Authorization': f'Bearer {token}'})
                arm = output / 'arm'
                arm.mkdir(parents=True)
                trace = arm / 'api-inputs.jsonl'
                arm_env = {'PACBENCH_USAGE_ROLES': json.dumps({'test-driver': 'hermes'})}
                with pytest.raises(ValueError, match='inside the run output'):
                    with r.public_input_trace(tmp_path, arm_env):
                        pytest.fail('traced outside run output')
                with r.public_input_trace(arm, arm_env):
                    assert arm_env['PACBENCH_API_INPUT_TRACE'] == str(trace)
                    with urllib.request.urlopen(req, timeout=2) as response:
                        assert response.status == 200
                assert 'PACBENCH_API_INPUT_TRACE' not in arm_env
                call = snapshot({'test-driver': 'hermes'})['calls'][0]
                assert call['actual_cost_usd_decimal'] == '0.0001'
                trace_row, = rows(trace)
                assert trace_row['protocol'] == PROXY_PROTOCOL
                assert trace_row['request_id'] == call['id']
                assert trace_row['messages'] == messages
                assert trace.stat().st_mode & 0o777 == 0o600
                assert key not in trace.read_text() and token not in trace.read_text()
                turn = SimpleNamespace(at='fixture', prompt=messages[1]['content'], prefetch_context=None, session_id='fixture',
                    context_isolation={'state': 'pass', 'system_prompt_sha256': hashlib.sha256(messages[0]['content'].encode()).hexdigest()})
                (arm / 'context.jsonl').write_text(json.dumps({'session_id': turn.session_id, 'query': turn.prompt, 'prefetch': None}) + '\n')
                assert delivery(arm, turn, identifiers=[call['id']], protocol=NATIVE)['state'] == 'pass'
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req, timeout=2)
                assert error.value.code == 429
                assert json.load(error.value)['error']['type'] == 'budget_refused'
                with pytest.raises(SpendingBlocked, match='run spending limit'):
                    require_live_cost_boundary()
                other_base, _ = client_settings('other-provider')
                small_body = json.dumps({'model': MODEL, 'messages': [], 'max_tokens': 1}).encode()
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(other_base + '/chat/completions',
                        data=small_body, headers={'Authorization': f'Bearer {token}'}), timeout=2)
                assert error.value.code == 429
                raise RuntimeError('run failed')
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()

    assert len(sent) == 1
    path, auth, forwarded = sent[0]
    assert (path, auth) == ('/chat/completions', f'Bearer {key}')
    assert forwarded['provider']['only'] == [ENDPOINT]
    assert forwarded['provider']['allow_fallbacks'] is False
    assert os.environ['OPENROUTER_API_KEY'] == key
    assert os.environ['no_proxy'] == 'example.invalid'
    with pytest.raises(OSError):
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=1)
    evidence = tmp_path / 'run.openrouter-private'
    for file in evidence.iterdir():
        assert key not in file.read_text() and token not in file.read_text()
    ledger = json.loads((evidence / 'ledger.json').read_text())
    assert ledger['calls'][0]['accounted'] == '0.0001'
    assert ledger['spending_stop']['reason'] == 'budget_refused'
    with pytest.raises(FileExistsError):
        with r.public_session(output, '1'):
            pytest.fail('restarted existing spending evidence')

def test_public_upstream_payment_refusal_stops_model_client_and_health(tmp_path):
    from pacbench.gateway_client import client_settings, require_live_cost_boundary, SpendingBlocked
    from pacbench.model_calls import ModelClient
    from pacbench.run_budget import Budget
    from pacbench.openrouter_proxy import make_server

    path = tmp_path / 'ledger.json'
    budget = Budget(path, cap='1', context_tokens=100, max_completion_tokens=50,
                    prompt_per_million='1', completion_per_million='2', new_run=True)
    sent = []

    def payment_required(request):
        sent.append(request)
        return 402, b'private upstream response'

    server = make_server(0, 'local', 'fixture', budget, send=payment_required)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with r.transport_environment({'url': f'http://127.0.0.1:{server.server_port}', 'token': 'local'}, '1'):
            for project in ('author', 'judge'):
                base, token = client_settings(project)
                client = ModelClient(key=token, model=MODEL, record_dir=tmp_path / project,
                                     url=base + '/chat/completions')
                with pytest.raises(SpendingBlocked, match='rejected payment'):
                    client('fixture prompt')
                assert 'private upstream response' not in next((tmp_path / project).glob('*.json')).read_text()
            with pytest.raises(SpendingBlocked, match='rejected payment'):
                require_live_cost_boundary()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert len(sent) == 1
    ledger = json.loads(path.read_text())
    assert ledger['calls'][0]['http_status'] == 402
    assert ledger['spending_stop']['reason'] == 'upstream_payment_required'
    assert ledger['spending_stop']['request_id'] == ledger['calls'][0]['id']

def test_public_session_rejects_missing_inputs_before_network_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'fetch_catalog', lambda: pytest.fail('unexpected catalog call'))
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    output = tmp_path / 'run'
    for cap in ('0', '-1', 'NaN', 'Infinity', 'invalid'):
        with pytest.raises(r.PublicSessionError, match='budget-usd'):
            with r.public_session(output, cap):
                pytest.fail('entered public session')
    with pytest.raises(r.PublicSessionError, match='OPENROUTER_API_KEY'):
        with r.public_session(output, '1'):
            pytest.fail('entered public session')
    assert list(tmp_path.iterdir()) == []

@pytest.mark.parametrize(('failure', 'reason'), [
    (urllib.error.HTTPError('https://example.invalid', 404, 'fixture private text', None, None), 'HTTP 404'),
    (urllib.error.URLError(OSError('Tunnel connection failed: 403 fixture private text')), 'HTTPS proxy returned HTTP 403'),
    (json.JSONDecodeError('invalid', 'fixture private text', 0), 'invalid JSON'),
    (r.CatalogError('exact endpoint unavailable or ambiguous'), 'exact endpoint unavailable or ambiguous'),
])
def test_public_session_catalog_failure_leaves_no_claim_to_output(tmp_path, monkeypatch, failure, reason):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fixture-upstream-key')
    def unavailable():
        raise failure
    monkeypatch.setattr(r, 'fetch_catalog', unavailable)

    with pytest.raises(r.PublicSessionError, match='Could not verify pricing') as error:
        with r.public_session(tmp_path / 'run', '1'):
            pytest.fail('entered public session')
    message = str(error.value)
    assert MODEL in message and ENDPOINT in message and reason in message
    assert 'fixture private text' not in message
    assert list(tmp_path.iterdir()) == []

@pytest.mark.parametrize('name', ['PACBENCH_CHECKPOINT_FULL_RUN', 'PACBENCH_WORKFLOW_SCOPE'])
def test_public_session_rejects_campaign_scope_before_start(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, 'old-scope')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'synthetic')
    monkeypatch.setattr(r, 'fetch_catalog', lambda: pytest.fail('read catalog for a retired campaign'))
    with pytest.raises(r.PublicSessionError, match='existing transport'):
        with r.public_session(tmp_path / 'run', '1'):
            pytest.fail('started campaign')
    assert list(tmp_path.iterdir()) == []
