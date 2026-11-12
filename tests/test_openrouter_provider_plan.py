"""Offline native settings, not provider or route compatibility claims."""
import json

import pytest

from pacbench import native_pilot
from pacbench.openrouter_route import MODEL, ENDPOINT


PROVIDERS = ['memcal', 'mem0', 'hindsight', 'openviking', 'supermemory', 'honcho']
BASE = 'http://127.0.0.1:1/p/provider/v1'


@pytest.mark.parametrize('provider', PROVIDERS)
def test_offline_plans_are_inert_and_have_exact_route(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(native_pilot.subprocess, 'run', lambda *a, **k: pytest.fail('started subprocess'))
    monkeypatch.setattr(native_pilot, 'client_settings', lambda *a: pytest.fail('read live credentials'))
    root = tmp_path / 'not-created'
    plan = native_pilot.openrouter_provider_plan(provider, root)
    assert not root.exists()
    assert plan['model'] == MODEL and plan['endpoint'] == ENDPOINT
    assert not plan['paid_calls_enabled']
    assert MODEL in json.dumps(plan['native_settings'])
    assert BASE in json.dumps(plan['native_settings'])


def test_native_model_configuration_matches_real_setup_fields(tmp_path):
    memcal = native_pilot.openrouter_provider_plan('memcal', tmp_path)['native_settings']
    assert all(memcal['MEMCAL_' + name + '_MODEL'] == MODEL for name in ('PROPOSE', 'SWEEP', 'MATCH'))
    assert memcal['MEMCAL_LLM_PROVIDER'] == 'openai-compatible'
    hindsight = native_pilot.openrouter_provider_plan('hindsight', tmp_path)['native_settings']
    assert hindsight['config']['mode'] == 'local_external'
    assert hindsight['config']['llm_provider'] == hindsight['environment']['HINDSIGHT_API_LLM_PROVIDER'] == 'openai'
    spark_config, spark_env = native_pilot.hindsight_model_settings('test', 'muse-spark-1.3-contributor', BASE, 'local')
    assert spark_config['llm_provider'] == spark_env['HINDSIGHT_API_LLM_PROVIDER'] == 'meta'
    mem0 = native_pilot.openrouter_provider_plan('mem0', tmp_path)['native_settings']
    assert mem0['openrouter_base_url'] == mem0['openai_base_url'] == BASE
    assert 'is_reasoning_model' not in mem0


def test_direct_openrouter_provision_is_blocked_before_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: (_ for _ in ()).throw(RuntimeError('task accounting unavailable')))
    root = tmp_path / 'new'
    with pytest.raises(RuntimeError, match='task accounting unavailable'):
        native_pilot.provision(root, 'mem0', MODEL, inference_route='openrouter')
    assert not root.exists()


def test_direct_openrouter_run_is_blocked_before_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: (_ for _ in ()).throw(RuntimeError('task accounting unavailable')))
    root = tmp_path / 'new'
    with pytest.raises(RuntimeError, match='task accounting unavailable'):
        native_pilot.run(None, root, providers=['mem0'], inference_route='openrouter')
    assert not root.exists()
