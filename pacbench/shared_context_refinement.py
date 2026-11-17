"""Explicit, audited refinement of legacy DeepSeek unknown bounds; no inference.

Only the endpoint-wide maximum is used. Smaller legacy completion caps whose
outgoing payload is unavailable, allocations and protected abandoned requests
remain unchanged. This does not establish tokens, charges or refunds.
"""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .direct_accounting import DirectBudget, VERSION, money, now, request_cost_bound

POLICY = 'deepseek-shared-window-endpoint-maximum-v1'
MODEL = 'deepseek/deepseek-v4.1-flash-1017'
ENDPOINT = 'deepinfra/fp8/flex'
CONFIG = dict(context_tokens=1048576, max_completion_tokens=393216,
              prompt_per_million='0.0784', completion_per_million='0.2352')


def verify_proof(path):
    raw = path.read_bytes(); proof = json.loads(raw)
    expected = {
        'deepinfra-model': 'https://deepinfra.com/blog/deepseek-v4-1-flash-model-overview-integration',
        'deepinfra-api': 'https://docs.deepinfra.com/api-reference/chat-completions/openai-chat-completions',
        'openrouter-endpoint': f'https://openrouter.ai/api/v1/models/{MODEL}/endpoints'}
    sources = {s['name']: s for s in proof['sources']}
    if set(sources) != set(expected): raise ValueError('Incomplete source proof')
    for name, url in expected.items():
        s = sources[name]; body = Path(s['path']).read_bytes()
        if s['url'] != url or hashlib.sha256(body).hexdigest() != s['sha256']:
            raise ValueError('Source proof changed')
    catalog = json.loads(Path(sources['openrouter-endpoint']['path']).read_bytes())['data']
    endpoints = [e for e in catalog['endpoints'] if e['tag'] == ENDPOINT]
    if catalog['id'] != MODEL or len(endpoints) != 1: raise ValueError('Exact endpoint missing')
    e = endpoints[0]
    if (e['context_length'] != CONFIG['context_tokens'] or
        e['max_completion_tokens'] != CONFIG['max_completion_tokens'] or
        e['service_tier'] != 'flex' or e['quantization'] != 'fp8' or
        any(k not in {'prompt', 'completion', 'input_cache_read', 'discount'} for k in e['pricing']) or
        money(e['pricing'].get('input_cache_read', 0)) > money(e['pricing']['prompt'])):
        raise ValueError('Endpoint token/pricing dimensions changed')
    return hashlib.sha256(raw).hexdigest()


def refine(budget, *, proof_sha256, authorization, apply=False):
    if len(proof_sha256) != 64 or not all(c in '0123456789abcdef' for c in proof_sha256) or not authorization.strip():
        raise ValueError('Reviewed proof and authorization required')
    with budget.locked() as state:
        cfg = state['config']
        if cfg.get('model') != MODEL or cfg.get('endpoint') != ENDPOINT or any(
            money(cfg[k]) != money(v) for k, v in CONFIG.items()):
            raise ValueError('Historical configuration differs')
        before = sum(money(r['accounted']) for r in state['calls'])
        selected = []; excluded = {}
        old = request_cost_bound(CONFIG, CONFIG['max_completion_tokens'])
        new = request_cost_bound(CONFIG, CONFIG['max_completion_tokens'], shared_context=True)
        route = {'only': [ENDPOINT], 'order': [ENDPOINT], 'allow_fallbacks': False,
                 'require_parameters': True, 'max_price': {'prompt': .0784, 'completion': .2352, 'request': 0}}
        for r in state['calls']:
            if r.get('model') != MODEL or r.get('state') not in {'uncertain', 'reserved', 'abandoned_unknown'}: continue
            reason = None
            if r['state'] == 'abandoned_unknown': reason = 'protected_abandoned_hold'
            elif r.get('shared_context_refinement'): reason = 'already_refined'
            elif r.get('transport') != VERSION or not r.get('request_sha256'): reason = 'missing_direct_request_binding'
            elif r.get('provider_request') != route: reason = 'route_or_price_ceiling_differs'
            elif r.get('completion_bound_tokens') != CONFIG['max_completion_tokens']: reason = 'smaller_cap_requires_payload_proof'
            elif money(r['accounted']) != old or money(r['reserved']) != old: reason = 'original_bound_differs'
            elif r.get('input_tokens') is not None or r.get('output_tokens') is not None or r.get('reported_liability_exceeds_bound'): reason = 'usage_or_exception_requires_reconciliation'
            if reason: excluded[reason] = excluded.get(reason, 0) + 1; continue
            selected.append(r)
        reduction = (old - new) * len(selected)
        event = {'policy': POLICY, 'at': now(), 'proof_sha256': proof_sha256,
            'authorization': authorization, 'model': MODEL, 'endpoint': ENDPOINT,
            'formula': 'C*p + min(M,C)*max(0,c-p), with I+O<=C',
            'original_bound_per_request_usd': str(old), 'refined_unknown_bound_per_request_usd': str(new),
            'requests': [r['id'] for r in selected], 'excluded': excluded,
            'exposure_before_usd': str(before), 'exposure_after_usd': str(before-reduction),
            'bound_reduction_usd': str(reduction), 'provider_reported_charge_usd': None,
            'original_states_and_reservations_preserved': True, 'applied': bool(apply)}
        if apply and selected:
            for r in selected:
                r['shared_context_refinement'] = {'policy': POLICY, 'proof_sha256': proof_sha256,
                    'at': event['at'], 'original_accounted_usd': r['accounted'], 'original_state': r['state'],
                    'request_sha256': r['request_sha256'], 'unknown_bound_usd': str(new)}
                r['accounted'] = str(new)
            state.setdefault('unknown_bound_refinements', []).append(event)
        return event


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proof', type=Path, required=True); p.add_argument('--authorization', required=True)
    p.add_argument('--apply', action='store_true'); a = p.parse_args()
    print(json.dumps(refine(DirectBudget(profile='deepseek-flex'), proof_sha256=verify_proof(a.proof),
                            authorization=a.authorization, apply=a.apply), indent=2))


if __name__ == '__main__': main()
