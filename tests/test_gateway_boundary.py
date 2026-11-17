"""Live work requires a valid daily bound, including authorized daily-only mode."""
import io
import json
from unittest.mock import patch
import pytest
from pacbench.gateway_client import require_live_cost_boundary

@pytest.mark.parametrize('extra,accepted', [
    ({'total_limit_usd':None},True),({'total_limit_usd':10},True),
    ({'daily_limit_usd':11},False),({'daily_limit_usd':None},False),
    ({'daily_limit_usd':True},False),({'daily_limit_usd':'10'},False),
    ({'budget_blocked':True},False),({'accounted_today_usd':10},False),
    ({'total_limit_usd':0},False),({'service':'other'},False)])
def test_daily_cost_boundary(extra,accepted):
    payload={'service':'muse-budget-gateway','daily_limit_usd':10,'total_limit_usd':None,**extra}
    with patch('pacbench.gateway_client.client_settings',return_value=('http://127.0.0.1:8099/p/test/v1','test-key')),patch(
            'pacbench.gateway_client.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
        if accepted:assert require_live_cost_boundary()==payload
        else:
            with pytest.raises(RuntimeError,match='daily spending boundary|gateway blocked'):require_live_cost_boundary()


def test_auth_block_is_distinct_from_daily_budget_refusal():
    from pacbench.gateway_client import ModelAccessBlocked
    payload={'service':'muse-budget-gateway','daily_limit_usd':10,'total_limit_usd':None,
             'upstream_auth_blocked':True,'accounted_today_usd':0.1}
    with patch('pacbench.gateway_client.client_settings',return_value=('http://127.0.0.1:8099/p/test/v1','key')),patch(
            'pacbench.gateway_client.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
        with pytest.raises(ModelAccessBlocked,match='authentication'):require_live_cost_boundary()


@pytest.mark.parametrize('service', ['pacbench-budget-gateway', 'muse-budget-gateway'])
def test_embedded_and_existing_gateway_service_identities(service):
    payload = {'service': service, 'daily_limit_usd': 10, 'total_limit_usd': None}
    with patch('pacbench.gateway_client.client_settings', return_value=('http://127.0.0.1:8099/p/test/v1', 'key')), \
         patch('pacbench.gateway_client.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
        assert require_live_cost_boundary() == payload


@pytest.mark.parametrize('preferred,legacy,expected', [
    ('/tmp/pacbench-fixture', '/tmp/legacy-fixture', '/tmp/pacbench-fixture'),
    (None, '/tmp/legacy-fixture', '/tmp/legacy-fixture'),
    ('', '/tmp/legacy-fixture', '/tmp/legacy-fixture'),
    (None, None, '/workspace/shared/muse-gateway'),
])
def test_state_environment_precedence(preferred, legacy, expected):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in ('PACBENCH_GATEWAY_STATE', 'MUSE_GATEWAY_STATE')}
    if preferred is not None: env['PACBENCH_GATEWAY_STATE'] = preferred
    if legacy is not None: env['MUSE_GATEWAY_STATE'] = legacy
    result = subprocess.run([sys.executable, '-c', 'from pacbench.gateway_client import STATE; print(STATE)'],
                            env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == expected


def test_missing_state_blocks_without_initializing_it(tmp_path, monkeypatch):
    from pacbench import gateway_client
    missing = tmp_path / 'missing-private-state'
    monkeypatch.setattr(gateway_client, 'STATE', missing)
    with pytest.raises(RuntimeError, match='gateway unavailable'):
        gateway_client.require_live_cost_boundary()
    assert not missing.exists()
