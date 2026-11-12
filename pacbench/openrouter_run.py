"""Review direct native OpenRouter configuration for the Pizza Day task.

OpenRouter accounting is independent of the missing historical Muse ledger.
Requests use direct HTTPS with durable client-side accounting. No local
inference proxy is started.
"""
from __future__ import annotations
import argparse
import contextlib
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from .openrouter_route import MODEL, ENDPOINT, PROVIDER, CATALOG_URL
from .provider_lineup import ACTIVE_PROVIDERS


def catalog_parameters(data):
    if data.get('data', {}).get('id') != MODEL:
        raise ValueError('catalog model mismatch')
    rows = [r for r in data['data']['endpoints'] if r.get('tag') == ENDPOINT]
    if len(rows) != 1:
        raise ValueError('exact endpoint unavailable or ambiguous')
    row = rows[0]
    if row.get('model_id') != MODEL or row.get('quantization') != 'fp8' or row.get('service_tier') != 'flex':
        raise ValueError('endpoint metadata mismatch')
    if not {'tools', 'tool_choice', 'response_format'} <= set(row.get('supported_parameters', [])):
        raise ValueError('endpoint lacks required request parameters')
    prices = row['pricing']
    allowed = {'prompt', 'completion', 'input_cache_read', 'input_cache_write', 'discount'}
    if set(prices) - allowed:
        raise ValueError('unaccounted endpoint pricing dimension')
    def price(name):
        value = Decimal(str(prices[name]))
        if not value.is_finite() or value < 0:
            raise ValueError('invalid catalog price')
        return value * 1000000
    # Cache writes can be an additional charge. Summing bounds avoids relying
    # on a provider's cache billing interpretation; reads cannot cost more here.
    prompt = max(price('prompt'), price('input_cache_read') if 'input_cache_read' in prices else Decimal(0))
    if 'input_cache_write' in prices:
        prompt += price('input_cache_write')
    for key in ('context_length', 'max_completion_tokens'):
        if type(row.get(key)) is not int or row[key] <= 0:
            raise ValueError('missing endpoint token bounds')
    return dict(context_tokens=row['context_length'], max_completion_tokens=row['max_completion_tokens'],
                prompt_per_million=str(prompt), completion_per_million=str(price('completion')))


def fetch_catalog():
    with urllib.request.urlopen(CATALOG_URL, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('catalog exceeds size limit')
    return catalog_parameters(json.loads(raw)), dict(catalog_url=CATALOG_URL,
        observed_at=datetime.now(timezone.utc).isoformat(), response_sha256=hashlib.sha256(raw).hexdigest())


@contextlib.contextmanager
def transport_environment(access, cap):
    values = {'PACBENCH_TRANSPORT': 'openrouter',
              'PACBENCH_OPENROUTER_PROXY_URL': access['url'],
              'PACBENCH_OPENROUTER_PROXY_TOKEN': access['token'],
              'PACBENCH_OPENROUTER_LIMIT_USD': str(cap),
              'OPENROUTER_API_KEY': '', 'OPENAI_API_KEY': '', 'META_API_KEY': '', 'META_BASE_URL': '',
              'CUSTOM_BASE_URL': '', 'OPENROUTER_BASE_URL': '', 'OPENROUTER_API_BASE': '',
              'OPENAI_BASE_URL': ''}
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Print configuration without network or inference')
    parser.add_argument('--providers', nargs='+',
                        choices=ACTIVE_PROVIDERS, default=list(ACTIVE_PROVIDERS),
                        help='Memory providers to inspect; defaults to all active local providers')
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--spec',type=Path,help='Prepare and freeze a scenario through the same authorized direct route')
    parser.add_argument('--prepare-from-archive', type=Path,
                        help='Import original dialogue into a new preparation with fresh reviews')
    parser.add_argument('--honcho-clock', choices=('real','simulated'), default='real',
                        help='Record the existing isolated native Honcho clock condition')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--spend-limit-usd', help='Explicitly authorized total model-inference cap for this run')
    parser.add_argument('--max-messages',type=int,default=150,help='Native source ceiling; bound explicitly for larger frozen scenarios')
    parser.add_argument('--judge-reasoning-effort',choices=('none','low','medium','high'),
                        help='Explicit benchmark-judge setting; native provider models retain their configuration')
    args = parser.parse_args(argv)
    if len(set(args.providers)) != len(args.providers):
        parser.error('Select each provider only once')
    if not 1<=args.max_messages<=100000:
        parser.error('Message ceiling must be between1 and100000')
    if args.plan:
        from .openrouter_direct import provider_plan
        plans = [provider_plan(name, Path('/offline-plan') / name) for name in args.providers]
        print(json.dumps({'model': MODEL, 'provider': PROVIDER, 'memory_providers': args.providers,
                          'provider_plans': plans,
                          'roles': ['hermes', 'memory_provider', 'judge'],
                          'judge_reasoning_effort':args.judge_reasoning_effort,
                          'paid_calls_enabled': False,
                          'transport': 'direct OpenRouter HTTPS; no local inference proxy',
                          'plan_scope': 'native settings and direct per-request reservation hooks',
                          'authorized_task_cap_usd': '20',
                          'requires': ['persistent authorized OpenRouter task ledger',
                                       'secure key', 'verified direct runtime hooks', 'reviewed preparation',
                                       'native protocol and processing validation']}, indent=2))
        return 0
    if not args.output or sum(bool(v) for v in (args.prepared,args.prepare_from_archive,args.spec))!=1:
        parser.error('Supply --output and exactly one of --prepared, --prepare-from-archive or --spec')
    if args.spend_limit_usd != '20':
        parser.error('This task requires the approved shared --spend-limit-usd 20; the cap covers all providers and retries')
    if args.output.exists():
        parser.error('Use a new output directory; preserve existing run evidence and the task ledger')
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        parser.error('OPENROUTER_API_KEY is missing')
    if args.prepared:
        from .preparation_audit import audit_prepared
        root = args.prepared.parent if args.prepared.is_file() else args.prepared
        audit = audit_prepared(root)
        if audit['state'] != 'pass':
            parser.error('Preparation audit failed: '+str(audit['errors']))
    elif args.prepare_from_archive:
        from .pizza_day_recovery import dialogue_import
        spec, imported = dialogue_import(args.prepare_from_archive)
    else:
        spec=json.loads(args.spec.read_text())
        imported=None
    from .openrouter_task import STATE
    from .direct_accounting import live_budget
    from .openrouter_direct import direct_environment
    budget=live_budget()
    params,catalog=fetch_catalog()
    for name in ('prompt_per_million','completion_per_million'):
        if Decimal(params[name])>Decimal(budget.config[name]):
            parser.error('Catalog price exceeds the existing task ceiling; no inference sent')
    if (params['context_tokens']>budget.config['context_tokens'] or
            params['max_completion_tokens']>budget.config['max_completion_tokens']):
        parser.error('Catalog token bounds exceed task reservation bounds; no inference sent')
    with direct_environment(key):
        judge_options=({'judge_reasoning_effort':args.judge_reasoning_effort}
                       if args.judge_reasoning_effort is not None else {})
        if args.prepared:
            from .native_pilot import run
            result=run(args.prepared,args.output,providers=args.providers,
                       inference_route='openrouter',honcho_clock=args.honcho_clock,max_messages=args.max_messages,**judge_options)
        else:
            from .prepare import prepare
            result=prepare(spec,args.output,inference_route='openrouter',imported_scenes=imported,**judge_options)
    print(json.dumps(result,indent=2))
    return int(result.get('state') in ('incomplete','blocked','invalid'))


if __name__ == '__main__':
    raise SystemExit(main())
