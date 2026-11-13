import json
import os
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


def test_plan_cannot_fetch_or_infer(monkeypatch, capsys):
    monkeypatch.setattr(r, 'fetch_catalog', lambda: pytest.fail('plan made a network call'))
    from pacbench import native_pilot
    monkeypatch.setattr(native_pilot, 'provision', lambda *a, **k: pytest.fail('plan provisioned a provider'))
    monkeypatch.setattr(native_pilot, 'client_settings', lambda *a, **k: pytest.fail('plan read credentials'))
    assert r.main(['--plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['paid_calls_enabled'] is False
    assert plan['provider']['only'] == [ENDPOINT]
    assert set(plan['memory_providers']) == {'memcal', 'mem0', 'hindsight', 'supermemory', 'honcho', 'openviking', 'reme', 'cognee', 'memos'}
    assert all(p['paid_calls_enabled'] is False for p in plan['provider_plans'])


def test_plan_provider_selection_and_duplicate_rejection(capsys):
    assert r.main(['--plan', '--providers', 'memcal', 'honcho']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [p['provider'] for p in plan['provider_plans']] == ['memcal', 'honcho']
    with pytest.raises(SystemExit):
        r.main(['--plan', '--providers', 'memcal', 'memcal'])


def test_explicit_judge_reasoning_is_disclosed_without_changing_native_provider_plan(capsys):
    r.main(['--plan','--providers','honcho'])
    normal=json.loads(capsys.readouterr().out)
    r.main(['--plan','--providers','honcho','--judge-reasoning-effort','low'])
    configured=json.loads(capsys.readouterr().out)
    assert configured['judge_reasoning_effort']=='low'
    assert configured['provider_plans']==normal['provider_plans']


def test_judge_deadline_is_bounded_and_does_not_change_provider_plan(capsys):
    r.main(['--plan','--providers','mem0'])
    normal=json.loads(capsys.readouterr().out)
    r.main(['--plan','--providers','mem0','--judge-timeout-s','600','--recall-timeout-s','600'])
    configured=json.loads(capsys.readouterr().out)
    assert configured['judge_wall_deadline_s']==600
    assert configured['recall_wall_deadline_s']==600
    assert configured['provider_plans']==normal['provider_plans']
    for invalid in ['0','901']:
        with pytest.raises(SystemExit):r.main(['--plan','--judge-timeout-s',invalid])
        with pytest.raises(SystemExit):r.main(['--plan','--recall-timeout-s',invalid])


def test_catalog_binds_full_endpoint_bounds():
    params = r.catalog_parameters(catalog())
    assert params['max_completion_tokens'] == 393216
    assert params['context_tokens'] == 1048576
    assert params['prompt_per_million'] == '0.0784000000'
    assert params['completion_per_million'] == '0.2352000000'


@pytest.mark.parametrize('mutation', ['endpoint', 'pricing', 'context', 'tools'])
def test_catalog_refuses_unknown_costs_or_capabilities(mutation):
    data = catalog()
    row = data['data']['endpoints'][0]
    if mutation == 'endpoint': row['tag'] = 'deepinfra'
    if mutation == 'pricing': row['pricing']['request'] = '0.1'
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


def test_missing_cap_never_checks_key_or_network(monkeypatch, tmp_path):
    monkeypatch.setattr(r, 'fetch_catalog', lambda: pytest.fail('unexpected call'))
    with pytest.raises(SystemExit):
        r.main(['--prepared', str(tmp_path), '--output', str(tmp_path / 'run')])


def test_execution_fails_closed_before_catalog_or_ledger(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-key')
    monkeypatch.setattr(r, 'fetch_catalog', lambda: pytest.fail('unexpected network call'))
    output = tmp_path / 'run'
    with pytest.raises(SystemExit):
        r.main(['--prepared', str(tmp_path), '--output', str(output), '--spend-limit-usd', '20'])
    assert 'Preparation audit failed' in capsys.readouterr().err
    assert not output.exists()
    assert not (tmp_path / 'run.openrouter-private').exists()


def test_scenario_preparation_cannot_bypass_route_cap_or_conflicting_inputs(monkeypatch,tmp_path):
    monkeypatch.setattr(r,'fetch_catalog',lambda:pytest.fail('unexpected network call'))
    with pytest.raises(SystemExit):
        r.main(['--spec','specs/pizza_week.json','--output',str(tmp_path/'week')])
    with pytest.raises(SystemExit):
        r.main(['--spec','specs/pizza_week.json','--prepared',str(tmp_path),'--output',str(tmp_path/'week'),'--spend-limit-usd','20'])
    with pytest.raises(SystemExit):
        r.main(['--spec','specs/pizza_week.json','--output',str(tmp_path/'week'),'--spend-limit-usd','20','--max-messages','-1'])
