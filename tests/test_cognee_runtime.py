from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_adapters.cognee_runtime import plan_config


def test_cognee_native_model_and_embedding_routes_are_explicit():
    model='deepseek/deepseek-v4.1-flash-1017'
    result=plan_config(Path('/offline/cognee'),model,'https://openrouter.ai/api/v1','inert')
    assert result['LLM_PROVIDER']=='openai'
    assert result['LLM_MODEL']=='openai/'+model  # LiteLLM's provider prefix, stripped before the wire.
    assert result['EMBEDDING_ENDPOINT']=='http://127.0.0.1:11435/api/embed'
    assert result['EMBEDDING_PROVIDER']=='ollama'
    assert result['DATA_ROOT_DIRECTORY']=='/offline/cognee/cognee-data'
    assert result['SYSTEM_ROOT_DIRECTORY']=='/offline/cognee/cognee-system'


def test_cognee_native_process_uses_setup_cache_with_isolated_home(monkeypatch, tmp_path):
    from provider_adapters import cognee_runtime as runtime, embedding_health, tokenizer_cache

    checkout = tmp_path / 'checkout'
    private_home = tmp_path / 'fallback-home'
    cache = tmp_path / 'shared-hub'
    monkeypatch.setenv('HF_HUB_CACHE', str(cache))
    monkeypatch.setattr(runtime, 'REPO', checkout)
    monkeypatch.setattr(runtime, 'validate_route', lambda *_: None)
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **k: runtime.PINS['cognee']['commit'])
    monkeypatch.setattr(embedding_health, 'check', lambda *a, **k: {'state': 'ready'})
    monkeypatch.setattr(tokenizer_cache, 'configure', lambda *a: True)

    def run(*args, env, **kwargs):
        assert env['HOME'] == str(private_home)
        assert env['HF_HUB_CACHE'] == str(cache)
        assert env['HF_HUB_OFFLINE'] == '1'
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runtime.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='plugin installer failed'):
        runtime.start(tmp_path, {'HOME': str(private_home), 'PACBENCH_WORKFLOW_SCOPE': 'unit'},
                      'selected-model', 'http://127.0.0.1:1/v1', 'inert')
