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
