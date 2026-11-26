"""Offline checks for the exact authorized Hermes OpenRouter route."""
import pytest
import yaml
from unittest.mock import patch
from pacbench.harness import provision_home

MODEL = 'deepseek/deepseek-v4.1-flash-1017'


def test_openrouter_exact_endpoint_and_model(tmp_path):
    home = tmp_path / 'home'
    with patch('pacbench.gateway_client.validate_route') as validate:
        record = provision_home(home, provider_plugin_src=None, provider_name='mem0',
                                model=MODEL, model_provider='openrouter', model_base_url='http://127.0.0.1:9999/p/unit/v1')
    validate.assert_called_once_with('http://127.0.0.1:9999/p/unit/v1')
    config = yaml.safe_load((home / 'config.yaml').read_text())
    assert config['model'] == {'default': MODEL, 'provider': 'openrouter',
                               'base_url': 'http://127.0.0.1:9999/p/unit/v1'}
    assert config['provider_routing'] == {'only': ['deepinfra/fp8/flex']}
    assert record['auth_copied'] is False
    assert not (home / 'auth.json').exists()


@pytest.mark.parametrize('model,url', [
    ('deepseek/deepseek-v4.1-flash', None),
    (MODEL, None),
])
def test_openrouter_refuses_substitution(tmp_path, model, url):
    home = tmp_path / 'home'
    with pytest.raises(ValueError):
        provision_home(home, provider_plugin_src=None, provider_name='mem0',
                       model=model, model_provider='openrouter', model_base_url=url)
    assert not home.exists()


def test_native_hermes_uses_run_owned_openrouter(tmp_path, monkeypatch):
    """Exercise the pinned Hermes process, SDK and loopback proxy together."""
    import json
    import os

    from pacbench import harness, openrouter_proxy, openrouter_run
    from pacbench.gateway_client import client_settings
    from pacbench.openrouter_route import ENDPOINT, MODEL, PROVIDER
    from pacbench.usage import snapshot
    from tests.test_openrouter_run import catalog

    if not os.access(harness.ISOLATED_BIN, os.X_OK):
        pytest.skip('requires the pinned native runtime installed by ./scripts/setup')

    upstream_key = 'fixture-upstream-key'
    monkeypatch.setenv('OPENROUTER_API_KEY', upstream_key)
    for variable in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.setenv(variable, 'http://127.0.0.1:1')
    for variable in ('NO_PROXY', 'no_proxy'):
        monkeypatch.setenv(variable, '127.0.0.1,localhost')
    monkeypatch.setattr(openrouter_run, 'fetch_catalog',
                        lambda: (openrouter_run.catalog_parameters(catalog()), {}))
    requests = []

    def upstream(request, key, **_):
        assert key == upstream_key
        assert os.environ['OPENROUTER_API_KEY'] == ''
        assert request['model'] == MODEL
        assert {field: request['provider'][field] for field in PROVIDER} == PROVIDER
        requests.append(request)
        choice = {'index': 0, 'finish_reason': 'stop',
                  'delta': {'role': 'assistant', 'content': 'Fixture completion.'}}
        response = {'id': 'offline-native-hermes', 'object': 'chat.completion.chunk',
                    'model': MODEL, 'provider': 'DeepInfra', 'choices': [choice],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 10,
                              'total_tokens': 110, 'cost': 0.00002}}
        return 200, ('data: ' + json.dumps(response) + '\n\ndata: [DONE]\n\n').encode()

    monkeypatch.setattr(openrouter_proxy, 'send_openrouter', upstream)
    home = tmp_path / 'hermes-home'
    fallback = tmp_path / 'fallback-home'
    fallback.mkdir()
    output = tmp_path / 'run'
    project = 'pacbench-test-hermes-driver'

    with openrouter_run.public_session(output, '5'):
        url, token = client_settings(project)
        assert token != upstream_key
        provision_home(home, provider_plugin_src=None, provider_name='', model=MODEL,
                       model_provider='openrouter', model_base_url=url)
        assert not (home / 'auth.json').exists()
        turn = harness.run_turn(
            'What is the result of this smoke test?', at='2026-10-11T21:00:00-04:00',
            home=home, allowlist='', timeout_s=30,
            extra_env={'HOME': str(fallback), 'OPENROUTER_API_KEY': token,
                       'OPENROUTER_API_BASE': url, 'OPENROUTER_BASE_URL': url,
                       'PACBENCH_NO_BUILTIN_MEMORY': '1', 'PACBENCH_PROVIDER_ONLY': '1'})
        assert turn.returncode == 0 and turn.answer == 'Fixture completion.'
        assert turn.context_isolation['state'] == 'pass'
        auth_path = home / 'auth.json'
        if auth_path.exists():
            auth = json.loads(auth_path.read_text())
            assert not auth['providers']
            assert set(auth['credential_pool']) <= {'openrouter'}
            assert all(entry['source'] == 'env:OPENROUTER_API_KEY'
                       for entry in auth['credential_pool'].get('openrouter', []))
            assert upstream_key not in auth_path.read_text()
        usage = snapshot({project: 'hermes'})
        assert usage['state'] == 'captured'
        call, = usage['calls']
        assert (call['project'], call['role'], call['state']) == (project, 'hermes', 'settled')
        assert (call['input_tokens'], call['output_tokens']) == (100, 10)
        assert call['actual_cost_usd_decimal'] == '0.00002'
        assert call['provider_request']['only'] == [ENDPOINT]

    assert len(requests) == 1
    ledger = tmp_path / 'run.openrouter-private' / 'ledger.json'
    assert upstream_key not in ledger.read_text()
