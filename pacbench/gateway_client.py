"""Connect to PACBench’s shared budget gateway; never bypass it."""
from __future__ import annotations

import dataclasses
import ipaddress
import json
import math
import os
import urllib.error
import urllib.request
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from pacbench import gateway as transport
from pacbench.model_calls import GenerationError, load_config

STATE = Path(os.environ.get('PACBENCH_GATEWAY_STATE') or
             os.environ.get('MUSE_GATEWAY_STATE') or '/workspace/shared/muse-gateway')


class SpendingBlocked(RuntimeError):
    """A monetary refusal is terminal for this live trial."""


class ModelAccessBlocked(RuntimeError):
    """Stop paid work until upstream authentication is confirmed by a free probe."""


class GatewayConfigurationError(RuntimeError):
    """The configured gateway cannot establish a live spending boundary."""


_CAMPAIGN_SETTINGS = (
    'PACBENCH_ROLE_ROUTES', 'PACBENCH_EXECUTION_SCOPE', 'PACBENCH_RESERVATION_POLICY',
    'PACBENCH_FUNDED_RUN_ID', 'PACBENCH_RECEIPT_JOURNAL', 'PACBENCH_WORKFLOW_SCOPE',
    'PACBENCH_R2_LIVE_SCOPE', 'PACBENCH_CHECKPOINT_CANARY', 'PACBENCH_CHECKPOINT_FULL_RUN',
    'PACBENCH_NATIVE_DIAGNOSTIC_SCOPE', 'PACBENCH_SUPERMEMORY_STOCK_SOURCE',
)


def require_supported_transport():
    if os.environ.get('PACBENCH_DIRECT') == '1' or any(os.environ.get(k) for k in _CAMPAIGN_SETTINGS):
        raise GatewayConfigurationError(
            'This campaign requires its original reviewed checkout and private state.')


@contextmanager
def cli_inference_route(parser, output, budget_usd, *, requested=None, before_start=None):
    """Select a configured historical route or hold a fresh bounded session."""
    try:
        require_supported_transport()
    except GatewayConfigurationError as exc:
        parser.error(str(exc))
    if budget_usd is not None:
        from .openrouter_run import PublicSessionError, public_session

        with ExitStack() as resources:
            try:
                resources.enter_context(public_session(output, budget_usd, before_start=before_start))
            except (FileExistsError, PublicSessionError) as exc:
                parser.error(str(exc))
            yield 'openrouter'
        return

    transport = os.environ.get('PACBENCH_TRANSPORT', '')
    if transport not in ('', 'muse', 'muse-gateway', 'openrouter'):
        parser.error('Set PACBENCH_TRANSPORT to muse-gateway or openrouter.')
    if transport == 'openrouter':
        if requested:
            parser.error(f'--inference-route {requested} conflicts with PACBENCH_TRANSPORT=openrouter')
        yield 'openrouter'
        return
    if not (requested or transport or os.environ.get('PACBENCH_GATEWAY_STATE') or os.environ.get('MUSE_GATEWAY_STATE')):
        parser.error('Set OPENROUTER_API_KEY and pass --budget-usd AMOUNT for a fresh OpenRouter session.')
    yield requested or 'muse-gateway'


def require_muse_state(state: Path) -> tuple[str, ...]:
    """Check the original files without opening the ledger."""
    required = ('ledger.sqlite3', 'client-token', 'address.json')
    missing = [name for name in required if not (state / name).is_file()]
    if missing:
        raise GatewayConfigurationError(
            'Muse gateway unavailable. Missing files: ' + ', '.join(missing) +
            '. Set PACBENCH_GATEWAY_STATE to the authorized state directory. See CONTRIBUTING.md for historical campaign setup. '
            'If resuming a study, restore its original private files.')
    return required


def client_settings(project: str) -> tuple[str, str]:
    require_supported_transport()
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        import re
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
            'The Muse gateway token or address file is unreadable. Check your authorized state directory and CONTRIBUTING.md.') from exc
    base = address.get('base') if isinstance(address, dict) else None
    if not isinstance(base, str) or not key:
        raise GatewayConfigurationError('Invalid Muse gateway address or client token. See the historical campaign setup in CONTRIBUTING.md.')
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

        from .openrouter_route import ENDPOINT, MODEL
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
            if health.get('spending_stop_reason') == 'upstream_payment_required':
                raise SpendingBlocked('OpenRouter rejected payment for this run (HTTP 402)')
            raise SpendingBlocked('OpenRouter run spending limit cannot admit further requests')
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


def validate_route(url: str, key: str | None = None) -> None:
    """Fail before transport if a Spark route or credential bypasses our gateway."""
    import hmac
    import re
    base, expected_key = client_settings('route-check')
    expected, actual = urlsplit(base), urlsplit(url)
    if ((actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or
        actual.query or actual.fragment or
        not re.fullmatch(r'/p/[a-z0-9][a-z0-9_.-]{0,63}/v1(?:/(?:models|responses|chat/completions))?', actual.path) or
        key is not None and not hmac.compare_digest(key, expected_key)):
        raise RuntimeError('Spark calls must use the authenticated shared spending gateway')


REPO = Path(__file__).resolve().parents[1]


DEFAULT_ENV = REPO / ".env"


PROVIDER = "meta-ai"


BASE_URL = ""


KEY_ENV_VAR = "META_API_KEY"


@dataclasses.dataclass(frozen=True)
class DriverConfig:
    """Resolved driver model. ``repr`` never carries the credential."""

    configured_model: str
    model_id: str
    provider: str = PROVIDER
    base_url: str = BASE_URL

    def __repr__(self) -> str:
        return (f"DriverConfig(configured_model={self.configured_model!r}, "
                f"model_id={self.model_id!r}, provider={self.provider!r})")


def available_model_ids(credential: str, base_url: str = BASE_URL,
                        timeout: float = 30.0) -> list[str]:
    """Model ids the Meta API currently serves (credential kept out of errors).

    Bounded retries (3) cover transient gateway blips during setup resolution.
    Scored native turns never retry.
    """
    base_url = base_url or client_settings("pacbench-driver")[0]
    validate_route(base_url, credential)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {credential}"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            break
        except Exception as exc:
            last = exc
            import time
            time.sleep(2 * (attempt + 1))
    else:
        raise GenerationError(
            f"driver model resolution failed: cannot list Meta API models "
            f"({type(last).__name__})") from last
    try:
        return [m["id"] for m in data["data"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerationError(
            "driver model resolution failed: unusable model list payload") from exc


def resolve_model_id(configured: str, available: list[str]) -> str:
    """The configured MODEL must be served verbatim. No silent fallback."""
    if configured in available:
        return configured
    raise GenerationError(
        f"driver model {configured!r} is not served by the Meta API "
        f"(checked {len(available)} ids). "
        f"Fix PACBench .env MODEL; will not substitute another model.")


def load_driver(env_path: str | Path = DEFAULT_ENV) -> DriverConfig:
    """Resolve the driver from PACBench-owned `.env` and install the key.

    Reads ``MODEL``/``META`` via the existing generation loader (same parsing,
    same errors), validates the id against the live Meta model list, exports
    ``META_API_KEY`` into this process only, and returns the config.
    Raises ``GenerationError`` on any problem — callers fail the run, never
    fall back to another model or to live-profile credentials.
    """
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        from pacbench.openrouter_route import MODEL
        require_live_cost_boundary()
        base_url, client_key = client_settings('pacbench-driver')
        os.environ[KEY_ENV_VAR] = client_key
        return DriverConfig(configured_model=MODEL, model_id=MODEL,
                            provider='openrouter', base_url=base_url)
    config, credential = load_config(env_path)
    base_url, client_key = client_settings("pacbench-driver")
    model_id = resolve_model_id(config.model, available_model_ids(client_key, base_url))
    os.environ[KEY_ENV_VAR] = client_key
    os.environ["META_BASE_URL"] = base_url
    return DriverConfig(configured_model=config.model, model_id=model_id, base_url=base_url)


def loopback_url(value: str) -> str:
    """Require a literal loopback endpoint; do not resolve arbitrary DNS names.

    This check protects the memory-service destination, not every downstream
    model configured inside that service. Those require separate verification.
    """
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('A loopback HTTP(S) URL is required')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Credentials, query and fragment are forbidden in service URLs')
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if not local:
        raise ValueError('Memory service must be on loopback, not a hosted endpoint')
    # Accessing .port also rejects malformed/non-numeric port values.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid port')
    return value.rstrip('/')


def local_endpoint(value: str) -> str:
    """Require a literal loopback address, not a potentially remote DNS alias."""
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid local endpoint port") from error
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if (parsed.scheme not in {"http", "https"} or not loopback
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("A credential-free loopback HTTP(S) endpoint is required")
    return value.rstrip("/")
