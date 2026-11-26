"""Connect to PACBench’s shared budget gateway; never bypass it."""
from __future__ import annotations
import json
import math
import os
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit
from .gateway import transport

STATE = Path(os.environ.get('PACBENCH_GATEWAY_STATE') or
             os.environ.get('MUSE_GATEWAY_STATE') or '/workspace/shared/muse-gateway')


class SpendingBlocked(RuntimeError):
    """A monetary refusal is terminal for this live trial."""


class ModelAccessBlocked(RuntimeError):
    """Stop paid work until upstream authentication is confirmed by a free probe."""


class GatewayConfigurationError(RuntimeError):
    """The configured gateway cannot establish a live spending boundary."""


def public_inference_route() -> str:
    from .openrouter_direct import active
    if active():
        raise ValueError('Direct OpenRouter requires its authorized launcher. See `python -m pacbench.openrouter_run --help`.')
    transport = os.environ.get('PACBENCH_TRANSPORT', '')
    if transport not in ('', 'muse', 'muse-gateway', 'openrouter'):
        raise ValueError('Set PACBENCH_TRANSPORT to muse-gateway or openrouter.')
    return 'openrouter' if transport == 'openrouter' else 'muse-gateway'


def require_muse_state(state: Path) -> tuple[str, ...]:
    """Check the original files without opening the ledger."""
    required = ('ledger.sqlite3', 'client-token', 'address.json')
    missing = [name for name in required if not (state / name).is_file()]
    if missing:
        raise GatewayConfigurationError(
            'Muse gateway unavailable. Missing files: ' + ', '.join(missing) +
            '. See README.md, follow the gateway setup steps, and set PACBENCH_GATEWAY_STATE to the authorized state directory. '
            'If resuming a study, restore its original private files.')
    return required


def client_settings(project: str) -> tuple[str, str]:
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        import re
        from .openrouter_direct import active, BASE_URL
        if active():
            if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',project):
                raise RuntimeError('Invalid direct accounting project')
            from .direct_accounting import MARKER
            return BASE_URL, MARKER+project
        base = os.environ.get('PACBENCH_OPENROUTER_PROXY_URL', '')
        key = os.environ.get('PACBENCH_OPENROUTER_PROXY_TOKEN', '')
        try:
            parsed = urlsplit(base)
            port = parsed.port
        except ValueError:
            raise GatewayConfigurationError('Set PACBENCH_OPENROUTER_PROXY_URL to the authorized loopback gateway.') from None
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                not port or parsed.path or parsed.query or parsed.fragment or
                parsed.username or parsed.password or not key or
                not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}', project)):
            raise GatewayConfigurationError(
                'run-owned OpenRouter transport is not configured. See README.md and check '
                'PACBENCH_OPENROUTER_PROXY_URL and PACBENCH_OPENROUTER_PROXY_TOKEN.')
        return base + f'/p/{project}/v1', key
    require_muse_state(STATE)
    try:
        key = (STATE / 'client-token').read_text().strip()
        address = json.loads((STATE / 'address.json').read_text())
    except (OSError, ValueError) as exc:
        raise GatewayConfigurationError(
            'The Muse gateway token or address file is unreadable. Check your authorized state directory and README.md.') from exc
    base = address.get('base') if isinstance(address, dict) else None
    if not isinstance(base, str) or not key:
        raise GatewayConfigurationError('Invalid Muse gateway address or client token. See the gateway setup in README.md.')
    return base + f'/p/{project}/v1', key


def _gateway_health() -> dict:
    base, key = client_settings('pacbench-check')
    address = base.removesuffix('/p/pacbench-check/v1')
    parsed = urlsplit(address)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or not key):
        raise ValueError('Invalid gateway address or client token')
    req = urllib.request.Request(address.rstrip('/') + '/health', headers={'Authorization': 'Bearer ' + key})
    with transport.open_without_redirect(req, timeout=10) as response:
        health = json.load(response)
    if not isinstance(health, dict):
        raise ValueError('Invalid gateway health response')
    return health


def require_live_cost_boundary() -> dict:
    from .openrouter_direct import active
    if active():
        from .direct_accounting import live_budget
        budget=live_budget();calls=budget.snapshot()
        from .observational_receipts import enabled
        if enabled():return {'service':'pacbench-openrouter-direct','accounting_mode':'prepaid-observational-v1','local_reservations_applied':False,'limit_usd':None}
        from .funded_run_budget import selected
        with budget.locked(write=False) as state:funded=selected(state)
        if funded:
            calls=[c for c in calls if c.get('funded_run_id')==funded['id']]
        from decimal import Decimal
        total=sum(Decimal(c['accounted']) for c in calls)
        allocated=sum(Decimal(c['accounted']) for c in calls if c.get('kind')=='external_budget_allocation')
        if any(c.get('http_status') in (401,403) for c in calls):
            raise ModelAccessBlocked('Direct OpenRouter authentication blocked')
        limit=Decimal(funded['limit_usd']) if funded else Decimal('20')
        if total>=limit:raise SpendingBlocked('Funded run spending cap reached' if funded else 'Task spending cap reached')
        return {'service':'pacbench-openrouter-direct','limit_usd':float(limit),'accounted_usd':float(total),'funded_run_id':funded['id'] if funded else None,
                'external_allocations_usd':float(allocated),'audit_allowance_usd':float(Decimal(20)-allocated),
                'audit_spend_and_bounds_usd':float(total-allocated)}
    try:
        health = _gateway_health()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GatewayConfigurationError(
                'Gateway rejected client authentication. Check the authorized gateway token and README.md.') from exc
        raise GatewayConfigurationError(
            f'Gateway health returned HTTP {exc.code}. Check that the authorized gateway is running. See README.md.') from exc
    except OSError as exc:
        raise GatewayConfigurationError(
            'The configured gateway is unreachable. Check that the authorized gateway is running. See README.md.') from exc
    except ValueError as exc:
        raise GatewayConfigurationError(
            'The gateway address or health response is invalid. Check the configured gateway and README.md.') from exc
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        from decimal import Decimal, InvalidOperation
        from .openrouter_route import MODEL, ENDPOINT
        try:
            limit = Decimal(os.environ['PACBENCH_OPENROUTER_LIMIT_USD'])
            actual = Decimal(str(health['limit_usd']))
        except (KeyError, InvalidOperation):
            raise GatewayConfigurationError(
                'OpenRouter spending authorization is missing or invalid. Check PACBENCH_OPENROUTER_LIMIT_USD and the authorized proxy.') from None
        if (not limit.is_finite() or limit <= 0 or actual != limit or
                health.get('service') != 'pacbench-openrouter' or
                health.get('model') != MODEL or health.get('endpoint') != ENDPOINT):
            raise GatewayConfigurationError('OpenRouter route or spending boundary mismatch')
        if health.get('auth_blocked'):
            raise ModelAccessBlocked('OpenRouter authentication blocked')
        if health.get('budget_blocked'):
            raise SpendingBlocked('OpenRouter run spending limit reached')
        return health
    daily, total = health.get('daily_limit_usd'), health.get('total_limit_usd')
    spent = health.get('accounted_today_usd', 0)
    if (health.get('service') not in ('pacbench-budget-gateway', 'muse-budget-gateway') or
            type(daily) not in (int, float) or not 0 < daily <= 10 or
            'total_limit_usd' not in health or
            total is not None and (type(total) not in (int, float) or not 0 < total <= 10) or
            type(spent) not in (int, float) or not 0 <= spent < math.inf):
        raise GatewayConfigurationError('gateway cannot establish the $10 daily spending boundary')
    if health.get('budget_blocked') or spent >= daily:
        raise SpendingBlocked('shared daily spending gateway blocked; stop live work until reset')
    if health.get('upstream_auth_blocked'):
        raise ModelAccessBlocked('upstream model authentication failed; confirm access with a free model inventory before resuming')
    return health


def check_live_route() -> dict:
    """Check the selected inference route without creating state or running inference."""
    from .openrouter_direct import active

    try:
        route = public_inference_route()
    except ValueError as exc:
        route = 'openrouter-direct' if active() else 'unrecognized'
        return {'state': 'blocked', 'route': route, 'detail': str(exc)}

    try:
        require_live_cost_boundary()
    except (ModelAccessBlocked, SpendingBlocked, GatewayConfigurationError) as exc:
        return {'state': 'blocked', 'route': route, 'detail': str(exc)}
    detail = 'Authenticated gateway health and its configured spending boundary passed. Upstream model availability and run-specific admission remain separate checks.'
    return {'state': 'pass', 'route': route, 'detail': detail}


def validate_route(url: str, key: str | None = None) -> None:
    """Fail before transport if a Spark route or credential bypasses our gateway."""
    import hmac
    import re
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
