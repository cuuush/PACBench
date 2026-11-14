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

from .openrouter_route import MODEL, ENDPOINT, PROVIDER, CATALOG_URL, PROFILE, PROFILES, PROFILE_NAME
from .provider_lineup import ACTIVE_PROVIDERS


def catalog_parameters(data, *, profile=None):
    selected=PROFILES[profile or PROFILE_NAME]
    model=selected['model'];endpoint=selected['endpoint']
    if data.get('data', {}).get('id') != model:
        raise ValueError('catalog model mismatch')
    rows = [r for r in data['data']['endpoints'] if r.get('tag') == endpoint]
    if len(rows) != 1:
        raise ValueError('exact endpoint unavailable or ambiguous')
    row = rows[0]
    if row.get('model_id') != model or row.get('quantization') != selected['quantization'] or row.get('service_tier') != selected['service_tier']:
        raise ValueError('endpoint metadata mismatch')
    if not {'tools', 'tool_choice', 'response_format'} <= set(row.get('supported_parameters', [])):
        raise ValueError('endpoint lacks required request parameters')
    prices = row['pricing']
    allowed = {'prompt','completion','input_cache_read','input_cache_write','discount','overrides','web_search'}
    tiers=[prices,*prices.get('overrides',[])]
    bounds=[]
    for tier in tiers:
        if set(tier)-allowed-{'min_prompt_tokens'}:
            raise ValueError('unaccounted endpoint pricing dimension')
        def price(name):
            value=Decimal(str(tier.get(name,prices.get(name,0))))
            if not value.is_finite() or value<0:raise ValueError('invalid catalog price')
            return value*1000000
        # Cover all context tiers and additive cache writes. Paid built-in
        # tools/search remain forbidden by direct request admission.
        prompt=max(price('prompt'),price('input_cache_read'))+price('input_cache_write')
        if os.environ.get('PACBENCH_RESERVATION_POLICY')=='openai-cache-total-shared-v1':
            if (profile or PROFILE_NAME)!='luna-bedrock-global' or price('input_cache_write')!=price('prompt')*Decimal('1.25'):
                raise ValueError('Default OpenAI cache-total billing contract differs')
            prompt=max(price('prompt'),price('input_cache_read'),price('input_cache_write'))
            if prompt>Decimal('.25'):raise ValueError('Cache-total price exceeds reviewed ceiling')
        bounds.append((prompt,price('completion')))
    prompt=max(p for p,c in bounds);completion=max(c for p,c in bounds)
    for key in ('context_length', 'max_completion_tokens'):
        if type(row.get(key)) is not int or row[key] <= 0:
            raise ValueError('missing endpoint token bounds')
    return dict(context_tokens=row['context_length'], max_completion_tokens=row['max_completion_tokens'],
                prompt_per_million=str(prompt), completion_per_million=str(completion))


def fetch_catalog(*, profile=None):
    url=CATALOG_URL if profile is None else f"https://openrouter.ai/api/v1/models/{PROFILES[profile]['model']}/endpoints"
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('catalog exceeds size limit')
    return catalog_parameters(json.loads(raw),profile=profile), dict(catalog_url=url,
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
    parser.add_argument('--incremental',action='store_true',help='Admit each request at the shared hard cap without pre-funding a whole cohort')
    parser.add_argument('--cohort-spend-limit-usd',help='Optional additional shared cohort liability ceiling, including unresolved and in-flight calls; never raises task cap')
    parser.add_argument('--role-routes',choices=['luna-memory-deepseek-v1','luna-all-roles-v1'],help='Explicit per-role model route condition; all roles recorded separately')
    parser.add_argument('--plan', action='store_true', help='Print configuration without network or inference')
    parser.add_argument('--providers', nargs='+',
                        choices=ACTIVE_PROVIDERS, default=list(ACTIVE_PROVIDERS),
                        help='Memory providers to inspect; defaults to all active local providers')
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--probe-source-ids',nargs=4,help='Explicit isolated four-message conversation probe; import-only and one provider required')
    parser.add_argument('--import-only',action='store_true',help='Validate native ingestion only; send no benchmark questions or judgments')
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
    parser.add_argument('--judge-timeout-s',type=int,default=180,
                        help='Native-trial judge HTTP wall deadline, 1–900 real seconds')
    parser.add_argument('--recall-timeout-s',type=int,default=180,
                        help='Native-trial Hermes wall deadline, 1–900 real seconds')
    args = parser.parse_args(argv)
    if args.role_routes:
        os.environ['PACBENCH_ROLE_ROUTES']=args.role_routes
    from .role_routes import split_enabled, record as role_record
    split_enabled()
    if args.role_routes and not args.plan and not args.cohort_spend_limit_usd and not args.incremental:
        parser.error('Split scored run requires an explicit shared cohort liability ceiling')
    if args.cohort_spend_limit_usd:
        value=Decimal(args.cohort_spend_limit_usd)
        if not value.is_finite() or not 0<value<=20 or not args.output:parser.error('Cohort limit requires output and a positive value <=20')
        os.environ.setdefault('PACBENCH_COHORT_ID',hashlib.sha256(str(args.output.resolve()).encode()).hexdigest()[:32])
        os.environ['PACBENCH_COHORT_LIMIT_USD']=str(value)
    if args.probe_source_ids and (not args.import_only or len(args.providers)!=1):parser.error('Conversation probe requires import-only and exactly one provider')
    if args.import_only and not args.prepared:parser.error('--import-only requires --prepared')
    if not 0<args.judge_timeout_s<=900:parser.error('Judge deadline must be 1–900 seconds')
    if not 0<args.recall_timeout_s<=900:parser.error('Recall deadline must be 1–900 seconds')
    if args.judge_timeout_s!=180 and not (args.prepared or args.plan):parser.error('Judge deadline override requires a prepared native trial')
    if args.recall_timeout_s!=180 and not (args.prepared or args.plan):parser.error('Recall deadline override requires a prepared native trial')
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
                          'role_routes':role_record(),
                          'judge_reasoning_effort':args.judge_reasoning_effort,
                          'judge_wall_deadline_s':args.judge_timeout_s,
                          'recall_wall_deadline_s':args.recall_timeout_s,
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
    if args.cohort_spend_limit_usd and not args.incremental:
        from .role_routes import liability_envelope
        envelope=liability_envelope(budget.snapshot(),args.cohort_spend_limit_usd,os.environ['PACBENCH_COHORT_ID'])
        if not envelope['funded']:
            parser.error('Cohort liability envelope unfunded; additionalUSD='+envelope['additional_headroom_usd']+'; no inference sent')
    params,catalog=fetch_catalog()
    for name in ('prompt_per_million','completion_per_million'):
        if Decimal(params[name])>Decimal(budget.config[name]):
            parser.error('Catalog price exceeds the existing task ceiling; no inference sent')
    if (params['context_tokens']>budget.config['context_tokens'] or
            params['max_completion_tokens']>budget.config['max_completion_tokens']):
        parser.error('Catalog token bounds exceed task reservation bounds; no inference sent')
    if split_enabled():
        from .direct_accounting import live_budget
        native_budget=live_budget(profile='luna-bedrock-global')
        native_params,_=fetch_catalog(profile='luna-bedrock-global')
        for field in ('prompt_per_million','completion_per_million','context_tokens','max_completion_tokens'):
            if Decimal(str(native_params[field]))>Decimal(str(native_budget.config[field])):
                parser.error('Native Luna catalog exceeds retained accounting ceiling; no inference sent')
    with direct_environment(key):
        judge_options=({'judge_reasoning_effort':args.judge_reasoning_effort}
                       if args.judge_reasoning_effort is not None else {})
        if args.prepared:
            if args.import_only:
                from .import_trial import run as import_run
                result=import_run(args.prepared,args.output,providers=args.providers,
                    honcho_clock=args.honcho_clock,max_messages=args.max_messages,probe_source_ids=args.probe_source_ids)
                print(json.dumps(result,indent=2))
                return int(result['state']!='processing-complete')
            from .native_pilot import run
            judge_options['judge_timeout_s']=args.judge_timeout_s
            judge_options['recall_timeout_s']=args.recall_timeout_s
            result=run(args.prepared,args.output,providers=args.providers,
                       inference_route='openrouter',honcho_clock=args.honcho_clock,max_messages=args.max_messages,**judge_options)
        else:
            from .prepare import prepare
            result=prepare(spec,args.output,inference_route='openrouter',imported_scenes=imported,**judge_options)
    print(json.dumps(result,indent=2))
    return int(result.get('state') in ('incomplete','blocked','invalid'))


if __name__ == '__main__':
    raise SystemExit(main())
