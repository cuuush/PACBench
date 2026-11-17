"""Subscription usage must never be mistaken for free gateway activity."""

import json
from types import SimpleNamespace

import pytest

from pacbench.usage import tracked_turn


def test_codex_usage_is_unpriced_and_session_cumulative(tmp_path, monkeypatch):
    def unexpected_ledger(*args, **kwargs):
        raise AssertionError("subscription usage must not consult the Muse ledger")
    monkeypatch.setattr("pacbench.usage.snapshot", unexpected_ledger)
    result = SimpleNamespace(session_input_tokens=17, session_output_tokens=3)
    path = tmp_path / "usage.json"
    assert tracked_turn(lambda *a, **k: result, "prompt", usage_path=path,
                        extra_env={"PACBENCH_INFERENCE_ROUTE": "codex-subscription"}) is result
    data = json.loads(path.read_text())
    assert data["cost_usd"] is None
    assert data["cost_status"] == "unpriced subscription usage"
    assert data["session_cumulative_tokens"] == {"input_tokens": 17, "output_tokens": 3}
    assert data["state"] == "unavailable"


def test_codex_failure_still_writes_unknown_usage(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("test failure")
    path = tmp_path / "usage.json"
    with pytest.raises(RuntimeError, match="test failure"):
        tracked_turn(fail, "prompt", usage_path=path,
                     extra_env={"PACBENCH_INFERENCE_ROUTE": "codex-subscription"})
    assert json.loads(path.read_text())["cost_usd"] is None


def test_codex_usage_retains_provider_gateway_window(tmp_path, monkeypatch):
    import json
    captures = iter([
        {'state': 'captured', 'at': 'before', 'calls': []},
        {'state': 'captured', 'at': 'after', 'calls': [
            {'id': 'provider-call', 'role': 'memory_provider', 'input_tokens': 2,
             'output_tokens': 3, 'accounted_upper_bound_usd': 0.01, 'state': 'settled'}]},
    ])
    monkeypatch.setattr('pacbench.usage.snapshot', lambda roles: next(captures))
    path = tmp_path / 'usage.json'
    tracked_turn(lambda *a, **k: None, 'prompt', usage_path=path,
                 extra_env={'PACBENCH_INFERENCE_ROUTE': 'codex-subscription',
                            'PACBENCH_USAGE_ROLES': json.dumps({'provider': 'memory_provider'})})
    receipt = json.loads(path.read_text())
    assert receipt['provider_gateway_window']['model_calls'][0]['id'] == 'provider-call'
    assert receipt['cost_usd'] is None
