"""Retirement blocks new work, including discovery through an installed plugin."""
from pathlib import Path
from unittest.mock import patch
import pytest

from pacbench import native_pilot, openrouter_run
from pacbench.openrouter_direct import provider_plan
from pacbench.provider_lineup import ACTIVE_PROVIDERS


def test_retired_plugin_cannot_reenter_inventory(tmp_path,monkeypatch):
    plugin=tmp_path/'plugins/memory/byterover';plugin.mkdir(parents=True)
    (plugin/'plugin.yaml').write_text('name: byterover')
    monkeypatch.setattr(native_pilot.harness,'ISOLATED_TREE',tmp_path)
    assert 'byterover' not in native_pilot.provider_names()
    assert set(ACTIVE_PROVIDERS)<=set(native_pilot.provider_names())


def test_retired_selection_is_rejected_before_network_or_budget(monkeypatch,tmp_path):
    monkeypatch.setattr(openrouter_run,'fetch_catalog',lambda:pytest.fail('network called'))
    for args in [['--plan','--providers','byterover'],['--providers','byterover']]:
        with pytest.raises(SystemExit):openrouter_run.main(args)
    with patch('pacbench.native_pilot.require_live_cost_boundary') as boundary, \
         patch('pacbench.native_pilot.subprocess.run') as process:
        for route in ('muse-gateway','openrouter','codex-subscription'):
            with pytest.raises(ValueError,match='removed from the active benchmark'):
                native_pilot.provision(tmp_path,'byterover',inference_route=route)
        with pytest.raises(ValueError,match='removed from the active benchmark'):
            native_pilot.probe(tmp_path,'byterover',{},smoke=True)
        with pytest.raises(ValueError):native_pilot.run(None,tmp_path/'run',providers=['byterover'])
    boundary.assert_not_called();process.assert_not_called()
    assert not (tmp_path/'run').exists()


def test_retired_plans_and_connector_do_not_touch_sdk_or_local_setup(tmp_path):
    from provider_adapters.document_connector import main
    from provider_adapters.byterover_local import configure
    with patch('pacbench.gateway_client.validate_route') as route, \
         patch('provider_adapters.byterover_local.subprocess.run') as process:
        for action in [lambda:provider_plan('byterover',tmp_path),
                       lambda:native_pilot.openrouter_provider_plan('byterover',tmp_path),
                       lambda:main(['byterover',str(tmp_path),str(tmp_path),'now','now']),
                       lambda:configure(tmp_path,{},'unused','unused','unused')]:
            with pytest.raises(ValueError,match='removed from the active benchmark'):action()
    route.assert_not_called();process.assert_not_called()
