"""Run-owned Hermes gateways never attach to or stop an unverified owner."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pacbench import hermes_gateway as gateway


def test_live_unknown_gateway_refuses_without_starting_or_signalling(tmp_path):
    (tmp_path / 'gateway.lock').write_text(json.dumps({'pid': 4242}))
    with patch.object(gateway, '_live', return_value=True), \
         patch.object(gateway.subprocess, 'Popen') as launch, \
         patch.object(gateway.os, 'kill') as signal:
        with pytest.raises(gateway.GatewayLifecycleError, match='live process'):
            with gateway.turn_gateway(tmp_path, Path('/python'), {'HERMES_HOME': str(tmp_path), 'HOME': str(tmp_path)}, cwd=tmp_path):
                pass
    launch.assert_not_called()
    signal.assert_not_called()


def test_control_symlink_and_remote_gateway_are_refused(tmp_path):
    (tmp_path / 'gateway.lock').symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(gateway.GatewayLifecycleError, match='symlinks'):
        gateway._assert_clear(tmp_path)
    with pytest.raises(gateway.GatewayLifecycleError, match='private home'):
        with gateway.turn_gateway(tmp_path, Path('/python'),
                                  {'HERMES_HOME': str(tmp_path), 'HERMES_TUI_GATEWAY_URL': 'ws://example'}, cwd=tmp_path):
            pass


def test_stop_checks_identity_before_planned_signal(tmp_path):
    proc = Mock(pid=42)
    proc.poll.return_value = None
    with patch.object(gateway, '_identity', side_effect=gateway.GatewayLifecycleError('foreign')):
        with pytest.raises(gateway.GatewayLifecycleError, match='foreign'):
            gateway._stop(tmp_path, proc, timeout=0)
    proc.terminate.assert_not_called()
    assert not (tmp_path / '.gateway-planned-stop.json').exists()


def test_stop_uncertain_refuses_new_turn(tmp_path):
    proc = Mock(pid=42)
    proc.poll.return_value = None
    proc.wait.side_effect = gateway.subprocess.TimeoutExpired('gateway', 1)
    with patch.object(gateway, '_identity', return_value={'start_time': 12}):
        with pytest.raises(gateway.GatewayLifecycleError, match='did not stop cleanly'):
            gateway._stop(tmp_path, proc, timeout=1)
    marker = json.loads((tmp_path / '.gateway-planned-stop.json').read_text())
    assert marker['target_pid'] == 42 and marker['target_start_time'] == 12
    proc.terminate.assert_called_once()
    with pytest.raises(gateway.GatewayLifecycleError, match='remains'):
        gateway._assert_clear(tmp_path)


def test_simulated_process_clock_refuses_unverified_planned_stop(tmp_path):
    env = {'HERMES_HOME': str(tmp_path), 'HOME': str(tmp_path), 'LD_PRELOAD': '/clock.so'}
    with patch.object(gateway.subprocess, 'Popen') as launch:
        with pytest.raises(gateway.GatewayLifecycleError, match='Faketime'):
            with gateway.turn_gateway(tmp_path, Path('/python'), env, cwd=tmp_path):
                pass
    launch.assert_not_called()


def test_timed_out_turn_still_exits_gateway_context(tmp_path, monkeypatch):
    import subprocess
    from contextlib import contextmanager
    from pacbench import harness

    events = []
    @contextmanager
    def owned(*args, **kwargs):
        witness = {'stopped': False}
        try:
            yield witness
        finally:
            witness['stopped'] = True
            events.append('stopped')
    monkeypatch.setattr('pacbench.live_trials.require_live_trials_ready', lambda: None)
    monkeypatch.setattr('pacbench.hermes_gateway.turn_gateway', owned)
    monkeypatch.setattr(harness, '_check_paths', lambda home: None)
    monkeypatch.setattr('pacbench.context_isolation.workspace_for', lambda home: tmp_path)
    monkeypatch.setattr('pacbench.context_isolation.actual_context', lambda *a: {'state': 'pass'})
    monkeypatch.setattr(harness, 'provider_trace_snapshot', lambda env: {})
    monkeypatch.setattr(harness, 'capture_provider_traces', lambda *a: None)
    monkeypatch.setattr(harness, 'enforce_provider_tools', lambda *a: None)
    monkeypatch.setattr(harness, '_recover_tool_results', lambda *a: None)
    monkeypatch.setattr(harness.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('hermes', 1)))
    result = harness.run_turn('q', at='2026-09-09T09:00:00-04:00', home=tmp_path, allowlist='')
    assert events == ['stopped'] and result.hermes_gateway['stopped']
    assert result.returncode == -1


def test_simulated_clock_is_blocked_before_native_clock_advance(tmp_path, monkeypatch):
    from pacbench import harness
    monkeypatch.setattr('pacbench.live_trials.require_live_trials_ready', lambda: None)
    monkeypatch.setattr(harness, '_check_paths', lambda home: None)
    home = tmp_path / 'hermes-home'
    (tmp_path / 'provider-clock.json').write_text('{}')
    with patch('provider_adapters.honcho_clock.before_turn') as advance:
        with pytest.raises(gateway.GatewayLifecycleError, match='Simulated native gateway clocks'):
            harness.run_turn('q', at='2026-09-09T09:00:00-04:00', home=home, allowlist='')
    advance.assert_not_called()
