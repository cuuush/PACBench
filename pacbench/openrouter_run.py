"""Run-owned OpenRouter sessions for public preparation and native evaluation."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .openrouter_route import (
    CATALOG_URL,
    ENDPOINT,
    MODEL,
    PROFILE_NAME,
    PROFILES,
)

_PUBLIC_SESSION = ContextVar('public_openrouter_session', default=None)


@contextlib.contextmanager
def public_input_trace(arm_root, env):
    session = _PUBLIC_SESSION.get()
    if session is None:
        yield
        return
    from pacbench.context_isolation import TRACE_NAME

    project, = [project for project, role in json.loads(env['PACBENCH_USAGE_ROLES']).items() if role == 'hermes']
    budget, output = session
    arm = Path(arm_root)
    resolved = arm.resolve(strict=True)
    output = output.resolve(strict=True)
    if arm.is_symlink() or resolved == output or not resolved.is_relative_to(output):
        raise ValueError('API input observer requires an arm inside the run output')
    with budget.input_trace(project, resolved):
        old_trace = env.get('PACBENCH_API_INPUT_TRACE')
        env['PACBENCH_API_INPUT_TRACE'] = str(resolved / TRACE_NAME)
        try:
            yield
        finally:
            if old_trace is None:
                env.pop('PACBENCH_API_INPUT_TRACE', None)
            else:
                env['PACBENCH_API_INPUT_TRACE'] = old_trace


class CatalogError(ValueError):
    """A validation failure safe to report without the catalog response."""


def catalog_parameters(data, *, profile=None):
    selected=PROFILES[profile or PROFILE_NAME]
    model=selected['model'];endpoint=selected['endpoint']
    if data.get('data', {}).get('id') != model:
        raise CatalogError('catalog model mismatch')
    rows = [r for r in data['data']['endpoints'] if r.get('tag') == endpoint]
    if len(rows) != 1:
        raise CatalogError('exact endpoint unavailable or ambiguous')
    row = rows[0]
    if row.get('model_id') != model or row.get('quantization') != selected['quantization'] or row.get('service_tier') != selected['service_tier']:
        raise CatalogError('endpoint metadata mismatch')
    if not {'tools', 'tool_choice', 'response_format'} <= set(row.get('supported_parameters', [])):
        raise CatalogError('endpoint lacks required request parameters')
    prices = row['pricing']
    allowed = {'prompt','completion','input_cache_read','input_cache_write','discount','overrides','web_search'}
    tiers=[prices,*prices.get('overrides',[])]
    bounds=[]
    for tier in tiers:
        if set(tier)-allowed-{'min_prompt_tokens'}:
            raise CatalogError('unaccounted endpoint pricing dimension')
        def price(name):
            value=Decimal(str(tier.get(name,prices.get(name,0))))
            if not value.is_finite() or value<0:raise CatalogError('invalid catalog price')
            return value*1000000
        # Cover all context tiers and additive cache writes. Paid built-in
        # tools/search remain forbidden by direct request admission.
        prompt=max(price('prompt'),price('input_cache_read'))+price('input_cache_write')
        if os.environ.get('PACBENCH_RESERVATION_POLICY')=='openai-cache-total-shared-v1':
            if (profile or PROFILE_NAME)!='luna-bedrock-global' or price('input_cache_write')!=price('prompt')*Decimal('1.25'):
                raise CatalogError('Default OpenAI cache-total billing contract differs')
            prompt=max(price('prompt'),price('input_cache_read'),price('input_cache_write'))
            if prompt>Decimal('.25'):raise CatalogError('Cache-total price exceeds reviewed ceiling')
        bounds.append((prompt,price('completion')))
    prompt=max(p for p,c in bounds);completion=max(c for p,c in bounds)
    if prompt <= 0 or completion <= 0:
        raise CatalogError('endpoint lacks positive input and output price bounds')
    for key in ('context_length', 'max_completion_tokens'):
        if type(row.get(key)) is not int or row[key] <= 0:
            raise CatalogError('missing endpoint token bounds')
    return dict(context_tokens=row['context_length'], max_completion_tokens=row['max_completion_tokens'],
                prompt_per_million=str(prompt), completion_per_million=str(completion))


def fetch_catalog(*, profile=None):
    url=CATALOG_URL if profile is None else f"https://openrouter.ai/api/v1/models/{PROFILES[profile]['model']}/endpoints"
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise CatalogError('catalog exceeds size limit')
    data=json.loads(raw);selected=PROFILES[profile or PROFILE_NAME]
    params = catalog_parameters(data, profile=profile)
    endpoint=next(r for r in data['data']['endpoints'] if r.get('tag')==selected['endpoint'])
    return params, dict(catalog_url=url,supported_parameters=endpoint.get('supported_parameters',[]),
        observed_at=datetime.now(timezone.utc).isoformat(), response_sha256=hashlib.sha256(raw).hexdigest())


@contextlib.contextmanager
def transport_environment(access, cap):
    no_proxy = os.environ.get('no_proxy', os.environ.get('NO_PROXY', ''))
    no_proxy = ','.join(filter(None, (no_proxy, '127.0.0.1', 'localhost')))
    values = {'PACBENCH_TRANSPORT': 'openrouter',
              'PACBENCH_OPENROUTER_PROXY_URL': access['url'],
              'PACBENCH_OPENROUTER_PROXY_TOKEN': access['token'],
              'PACBENCH_OPENROUTER_LIMIT_USD': str(cap),
              'OPENROUTER_API_KEY': '', 'OPENAI_API_KEY': '', 'META_API_KEY': '', 'META_BASE_URL': '',
              'CUSTOM_BASE_URL': '', 'OPENROUTER_BASE_URL': '', 'OPENROUTER_API_BASE': '',
              'OPENAI_BASE_URL': '', 'PACBENCH_DIRECT_UPSTREAM_KEY': '',
              'NO_PROXY': no_proxy, 'no_proxy': no_proxy}
    before = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PublicSessionError(ValueError):
    """The run-owned OpenRouter spending boundary could not be started."""


def _catalog_failure(exc):
    if isinstance(exc, CatalogError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        exc.close()
        return f'HTTP {exc.code}'
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return 'catalog response was invalid JSON'
    if isinstance(exc, OSError):
        cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        proxy_status = re.match(r'Tunnel connection failed: ([1-5][0-9]{2})\b', str(cause))
        return f'HTTPS proxy returned HTTP {proxy_status[1]}' if proxy_status else 'catalog connection failed'
    if isinstance(exc, ArithmeticError):
        return 'invalid catalog price'
    return 'catalog response had invalid metadata'


@contextlib.contextmanager
def public_session(output, spend_limit_usd, *, before_start=None):
    """Own an authenticated inference transport for a single fresh output."""
    from pacbench.gateway_client import (
        GatewayConfigurationError,
        require_supported_transport,
    )
    from pacbench.openrouter_proxy import running_proxy
    from pacbench.run_budget import Budget, _money

    try:
        cap = _money(spend_limit_usd)
        if cap <= 0:
            raise ValueError
    except ValueError:
        raise PublicSessionError('Supply a positive finite --budget-usd.') from None
    transport = os.environ.get('PACBENCH_TRANSPORT', '')
    try:
        require_supported_transport()
    except GatewayConfigurationError as exc:
        raise PublicSessionError('Clear the existing transport configuration before starting a fresh OpenRouter session.') from exc
    if transport not in ('', 'openrouter') or any(os.environ.get(name) for name in (
        'PACBENCH_OPENROUTER_PROXY_URL', 'PACBENCH_OPENROUTER_PROXY_TOKEN', 'PACBENCH_OPENROUTER_LIMIT_USD',
    )):
        raise PublicSessionError('Clear the existing transport configuration before starting a fresh OpenRouter session.')
    api_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not api_key or any(char.isspace() for char in api_key):
        raise PublicSessionError('Set OPENROUTER_API_KEY to a valid OpenRouter API key.')

    output = Path(output).absolute()
    evidence = output.with_name(output.name + '.openrouter-private')
    for path in (output, evidence):
        if os.path.lexists(path):
            raise FileExistsError(f'OpenRouter run output or spending evidence already exists: {path}')
    if before_start is not None:
        before_start()
    try:
        params, catalog = fetch_catalog()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as exc:
        reason = _catalog_failure(exc)
        raise PublicSessionError(f'Could not verify pricing and token bounds for {MODEL} on {ENDPOINT}. {reason}.') from None

    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    budget = Budget(evidence / 'ledger.json', cap=cap, new_run=True, **params)
    (evidence / 'catalog.json').write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    with contextlib.ExitStack() as stack:
        try:
            access = stack.enter_context(running_proxy(budget, api_key=api_key))
        except OSError as exc:
            (evidence / 'catalog.json').write_text(json.dumps({**catalog, 'startup_error': 'listener_unavailable'}, indent=2) + '\n', encoding='utf-8')
            raise PublicSessionError(f'Could not start the run-owned OpenRouter listener. Spending evidence remains at {evidence}.') from exc
        stack.enter_context(transport_environment(access, cap))
        context_token = _PUBLIC_SESSION.set((budget, output))
        stack.callback(_PUBLIC_SESSION.reset, context_token)
        yield
