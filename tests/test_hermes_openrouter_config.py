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
