"""Connect to the shared Muse gateway; never fall back to the upstream API."""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

STATE = Path(os.environ.get('MUSE_GATEWAY_STATE', '/workspace/shared/muse-gateway'))


class SpendingBlocked(RuntimeError):
    """A monetary refusal is terminal for this live trial."""


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
    daily, total = health.get('daily_limit_usd'), health.get('total_limit_usd')
    if (health.get('service') != 'muse-budget-gateway' or
            type(daily) not in (int, float) or not 0 < daily <= 10 or
            'total_limit_usd' not in health or
            total is not None and (type(total) not in (int, float) or not 0 < total <= 10)):
        raise RuntimeError('gateway cannot establish the $10 daily spending boundary')
    if health.get('budget_blocked') or health.get('accounted_today_usd', 0) >= daily:
        raise SpendingBlocked('shared daily spending gateway blocked; stop live work until reset')
    return health


def validate_route(url: str, key: str | None = None) -> None:
    """Fail before transport if a Spark route or credential bypasses our gateway."""
    import hmac
    import re
    from urllib.parse import urlsplit
    base, expected_key = client_settings('route-check')
    expected, actual = urlsplit(base), urlsplit(url)
    if ((actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or
        actual.query or actual.fragment or
        not re.fullmatch(r'/p/[a-z0-9][a-z0-9_.-]{0,63}/v1(?:/(?:models|responses|chat/completions))?', actual.path) or
        key is not None and not hmac.compare_digest(key, expected_key)):
        raise RuntimeError('Spark calls must use the authenticated shared spending gateway')
