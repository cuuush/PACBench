"""Connect to PACBench’s shared budget gateway; never bypass it."""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

STATE = Path(os.environ.get('PACBENCH_GATEWAY_STATE') or
             os.environ.get('MUSE_GATEWAY_STATE') or '/workspace/shared/muse-gateway')


class SpendingBlocked(RuntimeError):
    """A monetary refusal is terminal for this live trial."""


class ModelAccessBlocked(RuntimeError):
    """Stop paid work until upstream authentication is confirmed by a free probe."""


def client_settings(project: str) -> tuple[str, str]:
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        import re
        from .openrouter_direct import active, BASE_URL
        if active():
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',project):
                raise RuntimeError('Invalid direct accounting project')
            from .direct_accounting import MARKER
            return BASE_URL, MARKER+project
        from urllib.parse import urlsplit
        base = os.environ.get('PACBENCH_OPENROUTER_PROXY_URL', '')
        key = os.environ.get('PACBENCH_OPENROUTER_PROXY_TOKEN', '')
        parsed = urlsplit(base)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                not parsed.port or parsed.path or parsed.query or parsed.fragment or
                parsed.username or parsed.password or not key or
                not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', project)):
            raise RuntimeError('run-owned OpenRouter transport is not configured')
        return base + f'/p/{project}/v1', key
    key = (STATE / 'client-token').read_text().strip()
    base = json.loads((STATE / 'address.json').read_text())['base']
    return base + f'/p/{project}/v1', key


def require_live_cost_boundary() -> dict:
    from .openrouter_direct import active
    if active():
        from .direct_accounting import live_budget
        budget=live_budget();calls=budget.snapshot()
        from decimal import Decimal
        total=sum(Decimal(c['accounted']) for c in calls)
        allocated=sum(Decimal(c['accounted']) for c in calls if c.get('kind')=='external_budget_allocation')
        if any(c.get('http_status') in (401,403) for c in calls):
            raise ModelAccessBlocked('Direct OpenRouter authentication blocked')
        if total>=Decimal('20'):raise SpendingBlocked('Task spending cap reached')
        return {'service':'pacbench-openrouter-direct','limit_usd':20,'accounted_usd':float(total),
                'external_allocations_usd':float(allocated),'audit_allowance_usd':float(Decimal(20)-allocated),
                'audit_spend_and_bounds_usd':float(total-allocated)}
    try:
        base, key = client_settings('pacbench-check')
        address = base.split('/p/',1)[0]
        req = urllib.request.Request(address + '/health', headers={'Authorization':'Bearer '+key})
        with urllib.request.urlopen(req, timeout=10) as response:
            health=json.load(response)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        raise RuntimeError('shared spending gateway unavailable; live calls blocked') from exc
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        from decimal import Decimal, InvalidOperation
        from .openrouter_route import MODEL, ENDPOINT
        try:
            limit = Decimal(os.environ['PACBENCH_OPENROUTER_LIMIT_USD'])
            actual = Decimal(str(health['limit_usd']))
        except (KeyError, InvalidOperation):
            raise RuntimeError('OpenRouter spending authorization is missing') from None
        if (not limit.is_finite() or limit <= 0 or actual != limit or
                health.get('service') != 'pacbench-openrouter' or
                health.get('model') != MODEL or health.get('endpoint') != ENDPOINT):
            raise RuntimeError('OpenRouter route or spending boundary mismatch')
        if health.get('auth_blocked'):
            raise ModelAccessBlocked('OpenRouter authentication blocked')
        if health.get('budget_blocked'):
            raise SpendingBlocked('OpenRouter run spending limit reached')
        return health
    daily, total = health.get('daily_limit_usd'), health.get('total_limit_usd')
    if (health.get('service') not in ('pacbench-budget-gateway', 'muse-budget-gateway') or
            type(daily) not in (int, float) or not 0 < daily <= 10 or
            'total_limit_usd' not in health or
            total is not None and (type(total) not in (int, float) or not 0 < total <= 10)):
        raise RuntimeError('gateway cannot establish the $10 daily spending boundary')
    if health.get('budget_blocked') or health.get('accounted_today_usd', 0) >= daily:
        raise SpendingBlocked('shared daily spending gateway blocked; stop live work until reset')
    if health.get('upstream_auth_blocked'):
        raise ModelAccessBlocked('upstream model authentication failed; confirm access with a free model inventory before resuming')
    return health


def validate_route(url: str, key: str | None = None) -> None:
    """Fail before transport if a Spark route or credential bypasses our gateway."""
    import hmac
    import re
    from urllib.parse import urlsplit
    from .openrouter_direct import active,BASE_URL
    if active():
        from .direct_accounting import MARKER
        if (url not in {BASE_URL,BASE_URL+'/',BASE_URL+'/chat/completions',BASE_URL+'/models'} or
                key is not None and not re.fullmatch(re.escape(MARKER)+r'[a-z0-9][a-z0-9_.-]{0,63}',key)):
            raise RuntimeError('Direct OpenRouter URL or accounting capability differs')
        return
    base, expected_key = client_settings('route-check')
    expected, actual = urlsplit(base), urlsplit(url)
    if ((actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or
        actual.query or actual.fragment or
        not re.fullmatch(r'/p/[a-z0-9][a-z0-9_.-]{0,63}/v1(?:/(?:models|responses|chat/completions))?', actual.path) or
        key is not None and not hmac.compare_digest(key, expected_key)):
        raise RuntimeError('Spark calls must use the authenticated shared spending gateway')
