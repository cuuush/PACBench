"""The setup diagnostic only reads the configured gateway health endpoint."""

import io
import json
from urllib.error import HTTPError

import pytest

from pacbench import gateway_client
from pacbench.gateway import transport


@pytest.fixture(autouse=True)
def isolated_route(monkeypatch, tmp_path):
    for name in ('PACBENCH_TRANSPORT', 'PACBENCH_DIRECT', 'PACBENCH_OPENROUTER_PROXY_URL',
                 'PACBENCH_OPENROUTER_PROXY_TOKEN', 'PACBENCH_OPENROUTER_LIMIT_USD'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gateway_client, 'STATE', tmp_path / 'private-gateway')
    monkeypatch.setattr(transport, 'open_without_redirect', lambda *_a, **_kw: pytest.fail('unexpected request'))


def test_missing_private_gateway_blocks_without_creating_files(tmp_path):
    result = gateway_client.check_live_route()
    assert (result['state'], result['route']) == ('blocked', 'muse-gateway')
    assert all(name in result['detail'] for name in ('ledger.sqlite3', 'client-token', 'address.json'))
    assert not list(tmp_path.iterdir())


def test_upstream_key_does_not_replace_run_local_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'private-upstream-key')
    result = gateway_client.check_live_route()
    assert (result['state'], result['route']) == ('blocked', 'openrouter')
    assert 'PACBENCH_OPENROUTER_PROXY_TOKEN' in result['detail']
    assert 'private-upstream-key' not in str(result)
    assert not list(tmp_path.iterdir())


def test_direct_openrouter_only_points_to_its_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv('PACBENCH_DIRECT', '1')
    result = gateway_client.check_live_route()
    assert (result['state'], result['route']) == ('blocked', 'openrouter-direct')
    assert 'python -m pacbench.openrouter_run --help' in result['detail']
    assert not list(tmp_path.iterdir())


def test_unknown_transport_does_not_fall_back(monkeypatch):
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'mistyped-route')
    result = gateway_client.check_live_route()
    assert (result['state'], result['route']) == ('blocked', 'unrecognized')
    assert 'PACBENCH_TRANSPORT' in result['detail']


@pytest.mark.parametrize('route', ['muse-gateway', 'openrouter'])
@pytest.mark.parametrize('status', [200, 401, 302])
def test_route_uses_only_authenticated_get_and_preserves_files(monkeypatch, tmp_path, route, status):
    token = 'private-fixture-token'
    service = 'http://127.0.0.1:1234'
    state = gateway_client.STATE
    if route == 'muse-gateway':
        state.mkdir()
        (state / 'client-token').write_text(token)
        (state / 'address.json').write_text(json.dumps({'base': service}))
        (state / 'ledger.sqlite3').write_bytes(b'An unopened placeholder')
        health = {'service': 'muse-budget-gateway', 'daily_limit_usd': 10, 'total_limit_usd': None}
    else:
        from pacbench.openrouter_route import ENDPOINT, MODEL

        monkeypatch.setenv('PACBENCH_TRANSPORT', route)
        monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_URL', service)
        monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_TOKEN', token)
        monkeypatch.setenv('PACBENCH_OPENROUTER_LIMIT_USD', '1.25')
        health = {'service': 'pacbench-openrouter', 'model': MODEL, 'endpoint': ENDPOINT, 'limit_usd': '1.25'}
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    requests = []

    def respond(request, *, timeout):
        requests.append(request.full_url)
        assert request.get_method() == 'GET' and request.data is None
        assert request.get_header('Authorization') == f'Bearer {token}'
        if status != 200:
            raise HTTPError(request.full_url, status, token, {}, None)
        return io.BytesIO(json.dumps(health).encode())

    monkeypatch.setattr(transport, 'open_without_redirect', respond)
    result = gateway_client.check_live_route()
    assert requests == [service + '/health']
    assert result['route'] == route
    assert result['state'] == ('pass' if status == 200 else 'blocked')
    assert token not in str(result)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
