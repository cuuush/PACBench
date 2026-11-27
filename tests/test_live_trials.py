"""An upgrade with an unowned persistent gateway cannot run live trials."""
import pacbench.providers.provision as provider_setup
from pathlib import Path
from unittest.mock import patch

import pytest

from pacbench import harness, providers as live_trials
from pacbench.workflows import native_run as native_pilot
from pacbench.providers import openviking as native_resume


def test_live_entrypoints_stop_before_backend_and_gateway(tmp_path, monkeypatch):
    monkeypatch.setitem(live_trials.LIVE_TRIALS, "enabled", False)
    with patch('pacbench.gateway_client.require_live_cost_boundary') as cost, \
         patch('pacbench.providers.provision.provision') as provider, \
         patch('pacbench.harness.subprocess.run') as child:
        with pytest.raises(RuntimeError, match='Native trials blocked'):
            native_pilot.run(None, tmp_path / 'new', providers=['memcal'])
        with pytest.raises(RuntimeError, match='Native trials blocked'):
            native_resume.resume(tmp_path)
        with pytest.raises(RuntimeError, match='Native trials blocked'):
            harness.run_turn('test', at='2026-09-09T09:00:00-04:00', home=tmp_path, allowlist='')
    cost.assert_not_called()
    provider.assert_not_called()
    child.assert_not_called()
    assert not (tmp_path / 'new').exists()


def test_direct_provision_stops_before_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setitem(live_trials.LIVE_TRIALS, "enabled", False)
    with patch('pacbench.workflows.native_run.client_settings') as settings:
        with pytest.raises(RuntimeError, match='Native trials blocked'):
            provider_setup.provision(tmp_path, 'memcal')
    settings.assert_not_called()
