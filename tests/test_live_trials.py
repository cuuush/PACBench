"""An upgrade with an unowned persistent gateway cannot run live trials."""
from pathlib import Path
from unittest.mock import patch

import pytest

from pacbench import harness, native_pilot, native_resume


def test_live_entrypoints_stop_before_backend_and_gateway(tmp_path):
    with patch('pacbench.gateway_client.require_live_cost_boundary') as cost, \
         patch('pacbench.native_pilot.provision') as provider, \
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


def test_direct_provision_stops_before_model_configuration(tmp_path):
    with patch('pacbench.native_pilot.client_settings') as settings:
        with pytest.raises(RuntimeError, match='Native trials blocked'):
            native_pilot.provision(tmp_path, 'memcal')
    settings.assert_not_called()
