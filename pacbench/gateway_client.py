"""Connect to the shared Muse gateway; never fall back to the upstream API."""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

STATE = Path(os.environ.get('MUSE_GATEWAY_STATE', '/workspace/shared/muse-gateway'))


def client_settings(project: str) -> tuple[str, str]:
    key = (STATE / 'client-token').read_text().strip()
    base = json.loads((STATE / 'address.json').read_text())['base']
    return base + f'/p/{project}/v1', key


def require_live_cost_boundary() -> dict:
    try:
        base, key = client_settings('pacbench-check')
        address = base.split('/p/',1)[0]
        req = urllib.request.Request(address + '/health', headers={'Authorization':'Bearer '+key})
        with urllib.request.urlopen(req, timeout=10) as response:
            health=json.load(response)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        raise RuntimeError('shared spending gateway unavailable; live calls blocked') from exc
    if (health.get('service') != 'muse-budget-gateway' or
            not 0 < health.get('daily_limit_usd', 999) <= 5 or
            not 0 < health.get('total_limit_usd', 999) <= 10):
        raise RuntimeError('gateway cannot establish $5/day and $10 task boundaries')
    return health
