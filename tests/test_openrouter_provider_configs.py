"""Offline native configuration only; no services or model calls."""
import json

from pacbench.openrouter_route import MODEL
from provider_adapters import byterover_local, honcho_local, openviking_local, supermemory_local

BASE = 'http://127.0.0.1:9000/p/provider/v1'
KEY = 'test-local-token'


def test_honcho_routes_all_generation_models_and_preserves_defaults(tmp_path):
    cfg = honcho_local.plan_config(tmp_path, MODEL, BASE, KEY, db_port=5432)
    model_configs = [json.loads(cfg[name]) for name in (
        'DERIVER_MODEL_CONFIG', 'SUMMARY_MODEL_CONFIG',
        'DREAM_DEDUCTION_MODEL_CONFIG', 'DREAM_INDUCTION_MODEL_CONFIG')]
    model_configs += [entry['MODEL_CONFIG'] for entry in json.loads(cfg['DIALECTIC_LEVELS']).values()]
    for model in model_configs:
        assert model == {'transport': 'openai', 'model': MODEL,
                         'overrides': {'base_url': BASE, 'api_key_env': 'PACBENCH_MEMORY_API_KEY'}}
    assert 'DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS' not in cfg
    assert 'DERIVER_FLUSH_ENABLED' not in cfg
    assert json.loads(cfg['EMBEDDING_MODEL_CONFIG'])['overrides']['base_url'] == 'http://127.0.0.1:11435/v1'


def test_openviking_generation_and_embedding_are_distinct(tmp_path):
    cfg = openviking_local.plan_config(tmp_path, MODEL, BASE, KEY, port=1234)
    assert cfg['vlm'] == {'provider': 'openai', 'model': MODEL, 'api_base': BASE, 'api_key': KEY}
    assert cfg['embedding']['dense']['api_base'] == 'http://127.0.0.1:11435/v1'


def test_byterover_uses_native_openai_compatible_setup(tmp_path):
    commands = byterover_local.plan_config(tmp_path, MODEL, BASE, KEY)
    assert commands == [['vc', 'init'], ['providers', 'connect', 'openai-compatible',
            '--base-url', BASE, '--api-key', KEY, '--model', MODEL, '--format', 'json']]


def test_supermemory_routes_all_generation_models_and_keeps_local_embeddings(tmp_path):
    cfg = supermemory_local.plan_config(tmp_path, MODEL, BASE, KEY, port=1234)
    assert [cfg[name] for name in ('OPENAI_MODEL', 'OPENAI_FAST_MODEL', 'OPENAI_TEXT_MODEL')] == [MODEL] * 3
    assert cfg['OPENAI_BASE_URL'] == BASE and cfg['OPENAI_API_KEY'] == KEY
    assert cfg['SUPERMEMORY_EMBEDDING_BASE_URL'] == 'http://127.0.0.1:11435/v1'


def test_honcho_does_not_carry_spark_output_allowance_to_deepseek():
    child = honcho_local.child_environment(
        {'DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS': '8192'}, {}, {}, MODEL)
    assert 'DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS' not in child
    spark = honcho_local.child_environment(
        {'DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS': '8192'}, {}, {}, 'spark')
    assert spark['DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS'] == '8192'
