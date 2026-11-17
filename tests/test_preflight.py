"""Setup checks never create a gateway, run a model, or certify recall."""
import json
from pathlib import Path

import pytest

from pacbench import preflight


def test_provider_inventory_is_read_only_and_reports_route_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.shutil, 'which', lambda _: None)
    binary = tmp_path / 'vendor/venv-openviking/bin/openviking-server'
    binary.parent.mkdir(parents=True)
    binary.touch()
    result = preflight.provider_inventory(tmp_path)
    rows = {row['provider']: row for row in result['providers']}
    assert set(rows) == {'memcal', 'mem0', 'hindsight', 'openviking', 'supermemory', 'honcho', 'reme', 'cognee', 'memos'}
    assert rows['openviking']['installed_paths_present']
    assert rows['honcho']['missing_paths']
    assert all(row['openrouter_pilot'] == 'native route implemented; installation and live readiness must be verified'
               for row in rows.values())
    assert result['inference_calls'] == 0


def test_offline_provider_catalog_does_not_require_vendor_checkout(tmp_path, monkeypatch):
    from pacbench import native_pilot
    monkeypatch.setattr(native_pilot.harness, 'ISOLATED_TREE', tmp_path / 'missing')
    assert {'memcal', 'mem0', 'hindsight', 'openviking', 'supermemory', 'honcho', 'reme', 'cognee', 'memos'} <= set(native_pilot.provider_names())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / 'PACBench'
    (repo / 'vendor').mkdir(parents=True)
    (repo / 'vendor/HERMES_PIN').write_text('HERMES_PIN=known-pin\n')
    (repo / 'patches').mkdir()
    (repo / 'patches/hermes-isolated.diff').write_text('exact patch\n')
    plugin = tmp_path / 'memcal/integrations/hermes/memcal'
    plugin.mkdir(parents=True)
    (plugin / '__init__.py').write_text('')
    prepared = repo / 'preparation'
    prepared.mkdir()
    (prepared / 'week.json').write_text('{}')
    state = tmp_path / 'private-gateway'
    monkeypatch.setenv('MODEL', 'synthetic-model')
    monkeypatch.setenv('META', 'SECRET_DO_NOT_PRINT')
    monkeypatch.setattr(preflight, 'audit_prepared', lambda p: {'state': 'pass', 'scope': 'integrity only'})
    monkeypatch.setattr(preflight, '_runtime_metadata', lambda p: {'memcal': 'test-version'})
    monkeypatch.setattr(preflight, '_scratch_check', lambda: '/isolated-temp')
    def git(root, *args):
        if args == ('rev-parse', 'HEAD'): return 'known-pin\n'
        if args == ('diff', '--abbrev=8', 'HEAD'): return 'exact patch\n'
        return ''
    monkeypatch.setattr(preflight, '_git', git)
    monkeypatch.setattr('pacbench.provider_versions.verify_memcal_source',
                        lambda repo: {'revision': 'known-pin', 'version': '0.8.11', 'working_tree_changes': False})
    return repo, prepared, state


def states(result):
    return {row['check']: row for row in result['checks']}


def test_missing_state_blocks_without_network_or_replacement(setup, monkeypatch):
    repo, prepared, state = setup
    monkeypatch.setattr(preflight, 'require_live_cost_boundary', lambda: pytest.fail('Network called'))
    result = preflight.inspect(prepared, repo=repo, gateway_state=state, check_gateway=True)
    assert result['state'] == 'blocked' and result['inference_calls'] == 0
    assert not state.exists()
    assert states(result)['existing_gateway_state']['state'] == 'blocked'
    assert 'Do not initialize a replacement ledger' in json.dumps(result)
    assert 'SECRET_DO_NOT_PRINT' not in json.dumps(result)


def test_openrouter_missing_ledger_is_independent_of_muse(setup, monkeypatch):
    repo, prepared, state = setup
    monkeypatch.setattr('pacbench.openrouter_task.STATE', state)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'SECRET_DO_NOT_PRINT')
    result = preflight.inspect(prepared, repo=repo, inference_route='openrouter')
    assert result['state'] == 'blocked'
    assert states(result)['existing_openrouter_accounting']['state'] == 'blocked'
    assert 'existing_gateway_state' not in states(result)
    assert 'driver_configuration' not in states(result)
    assert not state.exists()
    assert 'SECRET_DO_NOT_PRINT' not in json.dumps(result)


def test_openrouter_accounting_check_preserves_every_byte(setup, monkeypatch):
    from pacbench.openrouter_route import MODEL, ENDPOINT
    repo, prepared, state = setup
    state.mkdir()
    monkeypatch.setattr('pacbench.openrouter_task.STATE', state)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'secret')
    ledger = state / 'ledger.json'
    ledger.write_text(json.dumps({'version': 1, 'config': {'cap':'20', 'model':MODEL, 'endpoint':ENDPOINT},
        'calls': [{'state':'uncertain','accounted':'0.175'}, {'state':'allocated','accounted':'3'}]}, indent=4))
    (state / 'authorization.json').write_text(json.dumps({'task':'pacbench-pizza-day-20261112',
        'model':MODEL,'endpoint':ENDPOINT,'approved_total_usd':'20'}))
    before = ledger.read_bytes()
    result = preflight.inspect(prepared, repo=repo, inference_route='openrouter')
    assert result['state'] == 'prerequisites-pass'
    assert states(result)['existing_openrouter_accounting']['detail']['accounted_upper_bound_usd'] == '3.175'
    assert ledger.read_bytes() == before


def test_present_files_are_not_claimed_as_recovered_history(setup, monkeypatch):
    repo, prepared, state = setup
    state.mkdir()
    for name in ('ledger.sqlite3', 'client-token', 'address.json'):
        (state / name).write_text('PRIVATE_CONTENT')
    monkeypatch.setattr(preflight, 'require_live_cost_boundary', lambda: pytest.fail('Network called'))
    result = preflight.inspect(prepared / 'week.json', repo=repo, gateway_state=state)
    assert result['state'] == 'prerequisites-pass'
    assert 'does not prove' in states(result)['existing_gateway_state']['detail']['history']
    assert 'PRIVATE_CONTENT' not in json.dumps(result)
    assert 'gateway_health' not in states(result)


def test_health_is_opt_in_and_only_for_the_same_state(setup, monkeypatch):
    repo, prepared, state = setup
    state.mkdir()
    for name in ('ledger.sqlite3', 'client-token', 'address.json'):
        (state / name).touch()
    calls = []
    monkeypatch.setattr(preflight, 'STATE', state)
    monkeypatch.setattr(preflight, 'require_live_cost_boundary', lambda: calls.append('health') or
                        {'service': 'muse-budget-gateway', 'daily_limit_usd': 10, 'private': 'SECRET'})
    result = preflight.inspect(prepared, repo=repo, gateway_state=state, check_gateway=True)
    assert calls == ['health']
    assert states(result)['gateway_health']['state'] == 'pass'
    assert 'SECRET' not in json.dumps(result)
    monkeypatch.setattr(preflight, 'STATE', state.parent / 'different')
    result = preflight.inspect(prepared, repo=repo, gateway_state=state, check_gateway=True)
    assert calls == ['health'] and result['state'] == 'blocked'


@pytest.mark.parametrize('damage', ['pin', 'patch', 'untracked', 'preparation', 'scratch', 'driver'])
def test_each_prerequisite_fails_closed(setup, monkeypatch, damage):
    repo, prepared, state = setup
    if damage == 'pin': (repo / 'vendor/HERMES_PIN').write_text('NO_PIN=true')
    elif damage == 'patch': (repo / 'patches/hermes-isolated.diff').write_text('changed')
    elif damage == 'untracked':
        original = preflight._git
        monkeypatch.setattr(preflight, '_git', lambda root, *args:
                            'extra.py\n' if args[0] == 'ls-files' else original(root, *args))
    elif damage == 'preparation':
        monkeypatch.setattr(preflight, 'audit_prepared', lambda p: {'state': 'fail'})
    elif damage == 'scratch':
        def fail(): raise RuntimeError('Not isolated')
        monkeypatch.setattr(preflight, '_scratch_check', fail)
    else:
        monkeypatch.delenv('MODEL'); monkeypatch.delenv('META')
    result = preflight.inspect(prepared, repo=repo, gateway_state=state)
    key = {'pin': 'pinned_hermes', 'patch': 'pinned_hermes', 'untracked': 'pinned_hermes',
           'preparation': 'frozen_preparation', 'scratch': 'isolated_scratch', 'driver': 'driver_configuration'}[damage]
    assert states(result)[key]['state'] == 'blocked'
