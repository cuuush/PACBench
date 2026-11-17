"""OpenRouter transport stays explicit and leaves the Muse route unchanged."""
import json
from io import BytesIO
from unittest.mock import Mock

import pytest

from pacbench import gateway_client as g
from pacbench import native_pilot, usage
from pacbench.openrouter_route import MODEL, ENDPOINT


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_URL', 'http://127.0.0.1:18999')
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_TOKEN', 'local-test-token')
    monkeypatch.setenv('PACBENCH_OPENROUTER_LIMIT_USD', '1.25')


def response(value):
    return BytesIO(json.dumps(value).encode())


def test_local_settings_do_not_read_muse_state(route, monkeypatch):
    monkeypatch.setattr(g, 'STATE', None)
    assert g.client_settings('judge') == ('http://127.0.0.1:18999/p/judge/v1', 'local-test-token')
    g.validate_route('http://127.0.0.1:18999/p/judge/v1/chat/completions', 'local-test-token')
    for url in ['https://openrouter.ai/api/v1', 'http://127.0.0.1:19000/p/judge/v1',
                'http://127.0.0.1:18999/p/judge/v1?route=other']:
        with pytest.raises(RuntimeError):
            g.validate_route(url, 'local-test-token')


@pytest.mark.parametrize('url', ['http://localhost:1234', 'https://127.0.0.1:1234',
                                 'http://remote.test:1234', 'http://user@127.0.0.1:1234'])
def test_proxy_settings_reject_noncanonical_target(route, monkeypatch, url):
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_URL', url)
    with pytest.raises(RuntimeError):
        g.client_settings('judge')


def test_health_must_match_confirmed_cap_and_endpoint(route, monkeypatch):
    health = dict(service='pacbench-openrouter', model=MODEL, endpoint=ENDPOINT,
                  limit_usd='1.25', budget_blocked=False, auth_blocked=False)
    monkeypatch.setattr(g.urllib.request, 'urlopen', lambda *a, **k: response(health))
    assert g.require_live_cost_boundary() == health
    health['endpoint'] = 'deepinfra'
    with pytest.raises(RuntimeError, match='mismatch'):
        g.require_live_cost_boundary()
    health['endpoint'] = ENDPOINT
    health['limit_usd'] = 2
    with pytest.raises(RuntimeError, match='mismatch'):
        g.require_live_cost_boundary()


def test_snapshot_filters_other_projects(route, monkeypatch):
    monkeypatch.setattr(g.urllib.request, 'urlopen', lambda *a, **k: response({'calls': [
        {'id': 1, 'project': 'wanted'}, {'id': 2, 'project': 'other'}]}))
    captured = usage.snapshot({'wanted': 'memory_provider'})
    assert captured['state'] == 'captured'
    assert captured['calls'] == [{'id': 1, 'project': 'wanted', 'role': 'memory_provider'}]


def test_pilot_does_not_create_output_without_transport(tmp_path, monkeypatch):
    monkeypatch.setattr('pacbench.live_trials.require_live_trials_ready', lambda: None)
    monkeypatch.delenv('PACBENCH_TRANSPORT', raising=False)
    output = tmp_path / 'new-run'
    with pytest.raises(RuntimeError, match='run-local transport'):
        native_pilot.run(tmp_path / 'prepared', output, providers=['mem0'], inference_route='openrouter')
    assert not output.exists()


def test_pilot_does_not_route_unsupported_providers(route, tmp_path, monkeypatch):
    monkeypatch.setattr('pacbench.live_trials.require_live_trials_ready', lambda: None)
    output = tmp_path / 'new-run'
    with pytest.raises(ValueError, match='Unsupported OpenRouter'):
        native_pilot.run(None, output, providers=['holographic'], inference_route='openrouter')
    assert not output.exists()
