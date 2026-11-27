"""Read-only model-call attribution; never guess a tool's token cost."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pacbench.evidence_store import save
from pacbench.gateway_client import STATE
from pacbench.receipt_costs import request_record, retained_metadata
from pacbench.receipt_costs import summarize as total

from .gateway_client import STATE


def snapshot(roles, state=STATE, *, nonblocking=False):
    if not roles:
        return {'state':'unavailable','reason':'No isolated gateway project mapping','calls':[]}
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        import urllib.request

        from .gateway_client import client_settings
        base, token = client_settings('usage-check')
        request = urllib.request.Request(base.split('/p/', 1)[0] + '/usage',
                                         headers={'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                capture = json.load(response)
            calls = [{**row, 'role': roles[row['project']]} for row in capture['calls']
                     if row['project'] in roles]
            return {'state': 'captured', 'at': datetime.now(timezone.utc).isoformat(),
                    'calls': calls, 'transport': 'openrouter'}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {'state': 'unavailable', 'reason': type(exc).__name__, 'calls': []}
    try:
        with sqlite3.connect(f'file:{Path(state) / "ledger.sqlite3"}?mode=ro', uri=True,
                             timeout=0 if nonblocking else 5) as db:
            db.row_factory = sqlite3.Row
            marks = ','.join('?' for _ in roles)
            columns = {r['name'] for r in db.execute('PRAGMA table_info(calls)')}
            status = 'http_status' if 'http_status' in columns else 'NULL AS http_status'
            error = 'error_label' if 'error_label' in columns else 'NULL AS error_label'
            diagnostics=','.join(name if name in columns else 'NULL AS '+name
                                 for name in ('finish_reason','reasoning_tokens','upstream_request_id','generation_id',
                                              'usage_detail','actual_cost_nano','actual_cost_source','upstream_origin','reconciled_at',
                                              'actual_cost_usd_decimal','generation_usage_detail','reconcile_status',
                                              'reconcile_attempts','reconcile_last_attempt','reconcile_next_attempt'))
            rows = db.execute(f'SELECT id,project,model,endpoint,started,finished,state,input_tokens,output_tokens,accounted,reserved,{status},{error},{diagnostics} FROM calls WHERE project IN ({marks}) ORDER BY started,id',tuple(roles)).fetchall()
        return {'state':'captured','at':datetime.now(timezone.utc).isoformat(), 'calls':[ledger_call(r, roles[r['project']]) for r in rows]}
    except (OSError,sqlite3.Error) as exc:
        return {'state':'unavailable','reason':type(exc).__name__,'calls':[]}


# Only numeric token dimensions are exported; raw upstream payloads never leave the ledger.
TOKEN_DETAILS = ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
                 'audio_tokens', 'image_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens')

GENERATION_TOKEN_DETAILS = ('native_tokens_prompt', 'native_tokens_completion', 'native_tokens_reasoning',
                            'native_tokens_cached', 'tokens_prompt', 'tokens_completion', 'native_tokens_completion_images')


def token_details(raw):
    """Flatten numeric token counters; raw billing receipts stay unchanged."""
    if not isinstance(raw, dict):return {}
    nested = ('prompt_tokens_details', 'completion_tokens_details', 'input_tokens_details', 'output_tokens_details')
    result = {k:v for k,v in raw.items() if (k in TOKEN_DETAILS or
        (k.rpartition('.')[0] in nested and k.rpartition('.')[2] in TOKEN_DETAILS))
        and type(v) is int and v >= 0}
    for name in nested:
        if isinstance(raw.get(name),dict):
            result.update({name+'.'+k:v for k,v in raw[name].items()
                           if k in TOKEN_DETAILS and type(v) is int and v >= 0})
    return result


def direct_call(row, role):
    """Project verified direct usage.cost settlements; reservations are not charges.

    DirectBudget settles only a successful response with usable token usage and
    an upstream cost within its full reservation. Read the stored decimal without
    rewriting the historical ledger or applying Muse's nanodollar units.
    """
    call={**row,'role':role,'actual_cost_usd':None,'actual_cost_usd_decimal':None,
          'actual_cost_source':None,'actual_cost_guard_rounded_usd':None}
    if call.get('state')=='settled':
        value=Decimal(str(call.get('settled_charge_usd',call.get('accounted'))))
        if not value.is_finite() or value<0:raise ValueError('Invalid direct settled charge')
        call.update(actual_cost_usd=float(value),actual_cost_usd_decimal=format(value,'f'),
                    actual_cost_source=('openrouter.generation.total_cost'
                        if row.get('reconciliation',{}).get('cost_source')=='total_cost'
                        else 'openrouter.usage.cost'))
    call['usage_detail']=token_details(call.get('usage_detail'))
    return call

def ledger_call(row, role):
    call = dict(row)
    raw = call.pop('usage_detail', None)
    try:
        detail = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        detail = None
    call['usage_detail'] = {k: v for k, v in (detail or {}).items()
                            if (k in TOKEN_DETAILS or (k.rpartition('.')[0] in ('prompt_tokens_details', 'completion_tokens_details', 'input_tokens_details', 'output_tokens_details') and k.rpartition('.')[2] in TOKEN_DETAILS)) and type(v) is int and v >= 0} if isinstance(detail, dict) else {}
    generation = call.pop('generation_usage_detail', None)
    try:
        generation = json.loads(generation) if isinstance(generation, str) else generation
    except (ValueError, TypeError):
        generation = None
    call['generation_usage_detail'] = {k:v for k,v in (generation or {}).items()
        if k in GENERATION_TOKEN_DETAILS and type(v) is int and v >= 0} if isinstance(generation, dict) else {}
    nano = call.pop('actual_cost_nano', None)
    call['actual_cost_guard_rounded_usd'] = nano / 1e9 if type(nano) is int and nano >= 0 else None
    exact = call.get('actual_cost_usd_decimal')
    try:
        amount = Decimal(exact) if isinstance(exact, str) else None
        if amount is not None and (not amount.is_finite() or amount < 0): amount = None
    except InvalidOperation:
        amount = None
    call['actual_cost_usd_decimal'] = exact if amount is not None else None
    # Legacy reconciled rows only carry rounded nanos. Preserve them, labeled as rounded.
    call['actual_cost_usd'] = float(amount) if amount is not None else call['actual_cost_guard_rounded_usd']
    call['role'] = role
    call['accounted_upper_bound_usd'] = call['accounted'] / 1e9
    return call

def summarize_calls(calls):
    """Keep measured charges, estimates and unresolved guard amounts separate."""
    def complete_sum(key):
        values = [c.get(key) for c in calls]
        return sum(values) if all(v is not None for v in values) else None
    actual = [c.get('actual_cost_usd') for c in calls]
    return {'model_calls': len(calls),
            'input_tokens': complete_sum('input_tokens'), 'output_tokens': complete_sum('output_tokens'),
            'token_details': {key: sum(c.get('usage_detail', {}).get(key, 0) for c in calls)
                              if all(key in c.get('usage_detail', {}) for c in calls) else None
                              for key in sorted(set(TOKEN_DETAILS) | {k for c in calls for k in c.get('usage_detail', {})})},
            'actual_cost_usd': sum(actual) if all(v is not None for v in actual) else None,
            'actual_cost_known_usd': sum(v for v in actual if v is not None),
            'actual_cost_unknown_calls': sum(v is None for v in actual),
            'token_price_estimate_usd': sum(c.get('accounted_upper_bound_usd') or 0 for c in calls
                                            if c.get('state') == 'settled' and c.get('actual_cost_usd') is None),
            'unconfirmed_budget_bound_usd': sum(c.get('accounted_upper_bound_usd') or 0 for c in calls
                                                if c.get('state') != 'settled'),
            'accounted_upper_bound_usd': complete_sum('accounted_upper_bound_usd'),
            'unsettled_calls': sum(c.get('state') != 'settled' for c in calls)}


def provider_model_health(capture):
    """Stop paid evaluation after repeated bad requests, without guessing recall quality.

    A single rejected request can be a successful native fallback. Count only
    completed, definitive 400/422 responses in this arm's provider projects;
    pending requests and successful responses with missing usage aren't HTTP
    errors. Repeated completed unmetered requests have a separate cost stop.
    """
    groups = {};unmetered={}
    for call in capture.get('calls', []):
        if (call.get('role') == 'memory_provider' and call.get('finished') and
                call.get('http_status') in (400, 422)):
            groups.setdefault(call['project'], []).append(call)
        if (call.get('role')=='memory_provider' and call.get('finished') and
                call.get('state') in {'unknown','uncertain'} and call.get('http_status')==200 and
                (call.get('input_tokens') is None or call.get('output_tokens') is None)):
            unmetered.setdefault(call['project'],[]).append(call)
    repeated = [c for calls in groups.values() if len(calls) >= 3 for c in calls]
    unknown = [c for calls in unmetered.values() if len(calls)>=3 for c in calls]
    return {'state': 'stop' if repeated or unknown else capture.get('state', 'unavailable'),
            'failed_requests': [{k: c.get(k) for k in
                ('id', 'project', 'http_status', 'state', 'accounted_upper_bound_usd')}
                for c in repeated],
            'unmetered_requests':[{k:c.get(k) for k in
                ('id','project','http_status','state','error_label','accounted_upper_bound_usd')}
                for c in unknown],
            'reason': ('Repeated provider-side HTTP 400/422 responses; stop further paid checks '
                       'in this arm. Native completion alone does not establish healthy model execution.'
                       if repeated else
                       'Three completed provider requests lack usable token usage; stop this arm '
                       'before unmetered work accumulates. This is a cost-observability blocker, '
                       'not an HTTP or memory-recall failure.' if unknown else ''),
            'scope': 'Execution/cost barrier, not a semantic memory grade. Actual charges may be unknown; safety bounds remain in force.'}


def window(before, after):
    if before['state'] != 'captured' or after['state'] != 'captured':
        return {'state':'unavailable','reason':'Gateway ledger capture unavailable','per_tool_tokens':None}
    prior = {c['id']:c for c in before['calls']}
    new = [c for c in after['calls'] if c['id'] not in prior]
    late = [c for c in after['calls'] if c['id'] in prior and c != prior[c['id']]]
    totals = {}
    for role in ('hermes','memory_provider'):
        calls = [c for c in new if c['role'] == role]
        totals[role] = summarize_calls(calls)
    grouped = {}
    for call in new:
        key = (call.get('role'), call.get('project'), call.get('model'))
        grouped.setdefault(key, []).append(call)
    groups = [{'role': key[0], 'project': key[1], 'model': key[2], **summarize_calls(calls)}
              for key, calls in grouped.items()]
    return {'state':'captured','started':before['at'],'finished':after['at'],'roles':totals,
        'model_calls':new,'groups':groups,'late_settlements':late,'per_tool_tokens':None,
        'scope':'Actual model requests started during this turn in isolated run projects. Background calls may outlive the turn. Late settlements belong to earlier requests. Per-tool tokens are not causally attributable from native traces.'}


def refresh_window(capture, latest):
    """Refresh only the window's request IDs; keep the original receipt untouched."""
    if not capture or capture.get('state')!='captured' or not latest or latest.get('state')!='captured':
        return None
    known={c['id']:c for c in latest.get('calls',[])}
    calls=[known.get(c['id'],c) for c in capture.get('model_calls',[])]
    if calls==capture.get('model_calls',[]):return None
    updated=window({'state':'captured','at':capture['started'],'calls':[]},
        {'state':'captured','at':capture['finished'],'calls':calls})
    return {**capture,'model_calls':calls,'roles':updated['roles'],'groups':updated['groups'],
            'settlement_observed_at':latest.get('at')}


def tracked_turn(run_turn, prompt, *, usage_path, **kwargs):
    from pacbench.evidence_store import save
    if (kwargs.get('extra_env') or {}).get('PACBENCH_INFERENCE_ROUTE') == 'codex-subscription':
        # Subscription requests do not appear in the Muse ledger. In
        # particular, an empty ledger is not evidence of zero calls or cost.
        # Hermes counters are session-cumulative, so preserve them as such
        # rather than attributing their totals to this individual turn.
        roles = json.loads((kwargs.get('extra_env') or {}).get('PACBENCH_USAGE_ROLES', '{}'))
        before = snapshot(roles) if roles else None
        result = None
        try:
            result = run_turn(prompt, **kwargs)
            return result
        finally:
            provider_window = (window(before, snapshot(roles)) if roles else
                               {'state': 'unavailable', 'reason': 'No provider gateway project mapping'})
            capture = {
                'provider_gateway_window': provider_window,
                'state': 'unavailable', 'route': 'codex-subscription',
                'reason': 'Subscription request-level cost and model-call count are not reported by the gateway',
                'cost_usd': None, 'cost_status': 'unpriced subscription usage',
                'model_calls': [], 'model_call_count': None, 'per_tool_tokens': None,
                'session_cumulative_tokens': {
                    'input_tokens': getattr(result, 'session_input_tokens', None),
                    'output_tokens': getattr(result, 'session_output_tokens', None),
                },
                'path_attribution_version': 'native-completion-v1',
            }
            save(usage_path, capture)
    roles = json.loads((kwargs.get('extra_env') or {}).get('PACBENCH_USAGE_ROLES','{}'))
    before = snapshot(roles)
    try:
        result=run_turn(prompt, **kwargs)
    finally:
        capture=window(before,snapshot(roles))
        capture['path_attribution_version']='native-completion-v1'
        save(usage_path,capture)
    attribute_provider_paths(result,capture)
    return result


def attribute_provider_paths(turn, capture):
    """A native response completed after every Hermes input cannot supply it.

    Honcho's journal and Hindsight's native trace API prove completion timestamps. Keep
    all operations and model cost, but do not turn unrelated late profile
    work into an answer's source-search penalty. Missing proof stays unknown.
    """
    starts=[]
    for call in capture.get('model_calls',[]):
        if call.get('role')!='hermes':continue
        try:starts.append(datetime.fromisoformat(call['started']).timestamp())
        except (KeyError,TypeError,ValueError):return
    if not starts:return
    cutoff=max(starts)
    for trace in getattr(turn,'provider_traces',[]) or []:
        # Honcho journals use the provider wall clock. Gateway timestamps stay
        # real; their ordering is unknowable across simulated clock advances.
        if trace.get('provider') == 'honcho' and getattr(turn,'provider_clock',None):
            continue
        completed=trace.get('record',{}).get('timestamp')
        if (trace.get('provider') in {'honcho','hindsight'} and type(completed) in (int,float) and completed>cutoff):
            trace['path_attribution']={
                'version':'native-completion-v1','affects_answer':False,
                'final_hermes_input_at':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
                'native_completed_at':datetime.fromtimestamp(completed,timezone.utc).isoformat(),
                'reason':'Native response completed after the final Hermes request input; background operations and costs remain recorded'}


def provider_paths_match(turn, capture):
    if capture.get('path_attribution_version')!='native-completion-v1':return True
    from copy import deepcopy
    expected=deepcopy(turn)
    for trace in expected.provider_traces:trace.pop('path_attribution',None)
    attribute_provider_paths(expected,capture)
    return expected.provider_traces==turn.provider_traces


PHASES = ('import_processing', 'answering_retrieval', 'judging', 'embeddings',
          'provider_background_or_ambiguous')


PHASE_ALIASES = {'ingest': 'import_processing', 'retrieval': 'answering_retrieval',
                 'answering': 'answering_retrieval', 'background': 'provider_background_or_ambiguous'}


def load(path):
    return json.loads(Path(path).read_text(), parse_float=Decimal)


def phase_cost_report(calls, components):
    if len({r['id'] for r in calls}) != len(calls):
        raise ValueError('Duplicate ledger request ID')
    # Quarantine duplicate provider identities globally, including across arms/phases.
    owners = defaultdict(set)
    for row in calls:
        for identifier in request_record(row)['generation_ids']:
            owners[identifier].add(row['id'])
    duplicates = {k: sorted(v) for k, v in owners.items() if len(v) > 1}
    calls = [{**r, '_duplicate_generation_id': any(r['id'] in ids for ids in duplicates.values())} for r in calls]
    projects = {}
    attempts = []
    for component in components:
        component = Path(component).resolve()
        summary = load(component / 'summary.json')
        if not (component / 'repeat-1').is_dir():
            continue
        for arm in sorted((component / 'repeat-1').iterdir()):
            if not arm.is_dir():
                continue
            provider = arm.name
            run = hashlib.sha256(str(arm).encode()).hexdigest()[:12]
            record = {'attempt': component.parent.name + '/' + provider + '@' + run,
                'provider': provider, 'run_id': summary.get('run_id'), 'mode': summary.get('mode'), 'recorded_state': summary['state'],
                'attempt_kind': 'import_qualification' if summary.get('mode') == 'native-import-validation' else 'benchmark_attempt',
                'retry': None, 'attempt_identity_source': 'legacy_component_path_not_request_bound', 'phases': {}, '_observed': defaultdict(set)}
            observed_projects = {}
            for pattern, phase in [('ingestion/*.json', 'import_processing'), ('turns/**/usage.json', 'answering_retrieval')]:
                for path in arm.glob(pattern):
                    data = load(path)
                    for row in data.get('usage', data).get('model_calls', []):
                        record['_observed'][row['id']].add(phase)
            snapshot = arm / 'model-usage.json'
            if snapshot.exists():
                for row in load(snapshot).get('calls', []):
                    role = row.get('role')
                    if role in ('hermes', 'memory_provider'):
                        observed_projects[row['project']] = 'driver' if role == 'hermes' else 'memory'
            for role in ('driver', 'memory'):
                observed_projects[f'pacbench-{run}-{provider}-{role}'] = role
            for key, role in observed_projects.items():
                if key in projects:
                    raise ValueError('Component/project supplied twice')
                projects[key] = (record, role)
            # Read scores without rewriting answers/grades. Expected counts come from frozen plan.
            scores = []
            for path in sorted(arm.glob('turns/*/followups/recovery.json')):
                value = load(path).get('score')
                if isinstance(value, dict):
                    scores.append(value)
            if not scores:
                scores = [c['recovery']['score'] for r in summary.get('results', []) if r.get('provider') == provider
                    for c in r.get('checks', []) if isinstance(c.get('recovery', {}).get('score'), dict)]
            plan = load(component / 'evaluation-plan.json') if (component / 'evaluation-plan.json').exists() else {}
            checks = plan.get('checks')
            expected = len(checks) if isinstance(checks, list) else None
            record['score'] = {'graded_questions': len(scores), 'total_questions': expected,
                'initial_correct_criteria': sum(s['initial_correct'] for s in scores),
                'graded_criteria': sum(s['max_points'] for s in scores),
                'retention_points': sum(s['points'] for s in scores),
                'complete': len(scores) == expected if expected is not None else None,
                'scope': 'Persisted grades only; saved answers with pending grades remain preserved and ungraded.'}
            attempts.append(record)
    bins = defaultdict(list)
    shared = []
    unattributed = []
    details = []
    for row in calls:
        binding = projects.get(row.get('project'))
        context = row.get('dispatch_context') or {}
        observed = []
        if row.get('project') == 'pacbench-judge':
            shared.append(row)
            arm, phase, basis = None, 'judging', 'shared_judge_project'
        elif binding:
            record, role = binding
            arm = record['attempt']
            observed = sorted(record['_observed'].get(row['id'], set()))
            candidate = PHASE_ALIASES.get(context.get('phase'), context.get('phase'))
            if (record['run_id'] is not None and context.get('run_id') == record['run_id']
                    and context.get('arm_id') == record['provider'] and candidate in PHASES):
                phase, basis = candidate, 'immutable_dispatch_context'
            elif role == 'driver':
                phase, basis = 'answering_retrieval', 'recorded_driver_project'
            else:
                phase, basis = 'provider_background_or_ambiguous', 'no_request_bound_causal_phase'
            bins[(arm, phase)].append(row)
        else:
            unattributed.append(row)
            arm, phase, basis = None, 'unattributed', 'no_verified_arm_binding'
        detail = request_record(row)
        detail.update(arm=arm, phase=phase, attribution_basis=basis, observed_window_phases=observed)
        details.append(detail)
    for record in attempts:
        record['phases'] = {phase: total(bins[(record['attempt'], phase)]) for phase in PHASES}
        record['total'] = total(r for (attempt, _), rows in bins.items() if attempt == record['attempt'] for r in rows)
        del record['_observed']
    dimensions = defaultdict(list)
    for row in calls:
        r = request_record(row)
        dimensions[(r['billing_provider'], r['upstream_provider'], r['model'], r['requested_endpoint'])].append(row)
    assert sum(a['total']['requests'] for a in attempts) + len(shared) + len(unattributed) == len(calls)
    return {'version': 'request-phase-costs-v2', 'attempts': attempts,
        'shared_judging': total(shared), 'other_setup_or_unmapped': total(unattributed),
        'all_in_scope': total(calls), 'requests': details, 'duplicate_generation_ids': duplicates,
        'billing_dimensions': [dict(zip(('billing_provider', 'upstream_provider', 'model', 'requested_endpoint'), key),
                                    totals=total(rows)) for key, rows in dimensions.items()],
        'coverage': {'retained_request_receipts': total(calls)['actual_receipt_requests'],
            'retained_requests': len(calls), 'lifecycle_coverage_complete': False,
            'unknowns': ['Requests absent from retained snapshots', 'Shared judging absent from supplied inputs',
                         'Unobserved deferred/native work and embeddings', 'Costs beyond recorded cutoffs'],
            'scope': 'Retained requests only. Empty bins are not authoritative zero spend; this is not full-run or account coverage.'},
        'attribution': 'Request-bound context identifies causal phase. Legacy driver projects identify answering. Memory window observations are timing evidence only, retained per request; background work and embeddings without explicit causality stay ambiguous. Shared judging is counted once.',
        'cost_policy': 'Only exact usage.cost or identity-bound generation total_cost is actual. Settled estimates and conservative accounting exposure remain separate. Conflicts and duplicate generation identities are excluded from actual totals pending review. Token details are subsets, not additional tokens.',
        'comparison_limit': 'This is an observed receipt subtotal, not a complete lifecycle invoice or cost leaderboard.',
        'billing_scope': 'Benchmark per-request charges exclude account-level credit purchase fees and any separately documented provider invoices or BYOK charges. Unknown/shared charges are not allocated to arms.',
        'route_policy': 'Origin labels alone are recorded-only; direct_meta was a legacy hard-coded insert label even with configurable upstream. Explicit receipt sources preserve costs; conflicting/missing route provenance withholds named upstream attribution. Same-generation collisions across known routes remain quarantined, with original receipts retained, rather than allocated or summed twice.'}


def decimal_json(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    raise TypeError('Unsupported report value')


def phase_cost_main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--ledger', type=Path, help='Retained direct JSON ledger; read only')
    source.add_argument('--snapshots', type=Path, nargs='+', help='Retained model-usage.json files; read only')
    p.add_argument('--since-index', type=int, default=0)
    p.add_argument('--generation-index', type=Path)
    p.add_argument('--components', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    if args.since_index < 0:
        p.error('--since-index must be nonnegative')
    if args.ledger:
        rows = [retained_metadata(args.ledger,r) for r in load(args.ledger)['calls'][args.since_index:]]
    else:
        # Identical snapshots may overlap. Differing records require explicit reconciliation.
        retained = {}
        for path in args.snapshots:
            for row in load(path)['calls']:
                if row['id'] in retained and retained[row['id']] != row:
                    raise ValueError('Conflicting snapshots for the same request ID')
                retained[row['id']] = row
        rows = list(retained.values())[args.since_index:]
    if args.generation_index:
        index = load(args.generation_index)
        if index.get('version') != 'generation-observations-v1':
            p.error('--generation-index needs an explicit OpenRouter generation-observations-v1 source')
        items = index['items']
        rows = [{**r, 'generation_receipt': items.get(r['id'], {}).get('generation', {}),
                  'generation_receipt_source': 'openrouter.generation.total_cost'
                      if items.get(r['id'], {}).get('state') == 'observed' else None} for r in rows]
    protected = [*(args.snapshots or []), *([args.ledger] if args.ledger else []),
                 *([args.generation_index] if args.generation_index else [])]
    output = args.output.resolve()
    if output.exists() or any(output == p.resolve() for p in protected) or any(output.is_relative_to(p.resolve()) for p in args.components):
        p.error('--output must be a new file outside input components')
    result = phase_cost_report(rows, args.components)
    result['ledger_offset'] = args.since_index
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, indent=2, default=decimal_json) + '\n'
    with args.output.open('x') as stream:
        stream.write(encoded)


def read_cost_record(path):
    return json.loads(path.read_text())


def summarize(calls):
    unknown=[c for c in calls if c is None]
    known=[c for c in calls if c is not None]
    extra = summarize_calls(known)
    if unknown:
        for key in ('actual_cost_usd', 'token_price_estimate_usd', 'unconfirmed_budget_bound_usd'):
            extra[key] = None
    return {**extra, 'model_requests':len(calls),'unobserved_requests':len(unknown),
        'settled_upper_bound_usd':sum(c['accounted_upper_bound_usd'] for c in known if c['state']=='settled') if not unknown else None,
        'held_usd':sum(c['accounted_upper_bound_usd'] for c in known if c['state']!='settled') if not unknown else None,
        'input_tokens':sum(c['input_tokens'] for c in known) if not unknown and all(c.get('input_tokens') is not None for c in known) else None,
        'output_tokens':sum(c['output_tokens'] for c in known) if not unknown and all(c.get('output_tokens') is not None for c in known) else None,
        'unsettled_requests':sum(c['state']!='settled' for c in known)}


def processing_debt(arm,provider):
    """Inspect a native queue when available; absence is not proof of no work."""
    receipt=arm/'processing-debt.json'
    database=arm/'memcal-home/memcal.db'
    if provider=='memcal' and database.exists():
        with sqlite3.connect(f'file:{database.resolve()}?mode=ro',uri=True) as connection:
            rows=connection.execute('SELECT a.channel,count(*) FROM spool s JOIN archive a '
                'ON a.id=s.archive_id WHERE s.processed_at IS NULL GROUP BY a.channel').fetchall()
        counts=dict(rows)
        record={'state':'observed-pending' if counts else 'observed-empty',
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'method':'Read-only native Memcal spool inspection after the benchmark workload',
            'pending_by_channel':counts,'pending_items':sum(counts.values()),
            'future_model_cost_usd':None}
        save(receipt,record)
        return record
    if receipt.exists():return read_cost_record(receipt)
    return {'state':'unobserved','future_model_cost_usd':None,
        'reason':'No native queue inspection receipt; the completed workload does not prove all deferred processing finished.'}


def refreshed_calls(calls,connection):
    actual={}
    columns = {r['name'] for r in connection.execute('PRAGMA table_info(calls)')} if connection else set()
    fields = ('id','project','model','endpoint','started','finished','state','input_tokens','output_tokens',
              'accounted','reserved','http_status','error_label','finish_reason','reasoning_tokens',
              'upstream_request_id','generation_id','usage_detail','actual_cost_nano','actual_cost_source','upstream_origin','reconciled_at',
              'actual_cost_usd_decimal','generation_usage_detail','reconcile_status','reconcile_attempts',
              'reconcile_last_attempt','reconcile_next_attempt')
    select = ','.join(k if k in columns else 'NULL AS '+k for k in fields)
    for call in calls:
        from pacbench.receipt_costs import VERSION as DIRECT_VERSION
        if call.get('transport')==DIRECT_VERSION:
            actual[call['id']]=direct_call(call,call.get('role'))
            continue
        current=connection.execute(f'SELECT {select} FROM calls WHERE id=?',(call['id'],)).fetchone() if connection else None
        actual[call['id']]={**call, **(ledger_call(current, call.get('role')) if current else {})}
    return actual


def role_totals(calls):
    return {role:summarize([c for c in calls if c.get('role')==role])
            for role in ('hermes','memory_provider')}


def model_groups(calls):
    groups = {}
    for call in calls:
        key = (call.get('role'), call.get('project'), call.get('model'))
        groups.setdefault(key, []).append(call)
    return [{'role': k[0], 'project': k[1], 'model': k[2], **summarize(v)} for k, v in groups.items()]


def review(root,*,ledger=STATE/'ledger.sqlite3'):
    summary=read_cost_record(root/'summary.json');results=[];other_attempts=[];connection=None
    if ledger.exists():
        connection=sqlite3.connect(f'file:{ledger}?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    try:
        for result in summary['results']:
            arm=root/f"repeat-{result['repetition']}"/result['provider']
            if result['state']=='preflight-failed':
                results.append({'provider':result['provider'],'state':'not-run','reason':result['reason'],
                    'complete_for_cost_comparison':False,'lifecycle_cost_complete':False,'cost':None});continue
            snapshot=read_cost_record(arm/'model-usage.json') if (arm/'model-usage.json').exists() else {'calls':[]}
            capture_available=snapshot.get('state')=='captured'
            actual=refreshed_calls(snapshot['calls'],connection)
            phases={}
            patterns={'ingestion':'ingestion/*.json','initial':'turns/*/usage.json',
                      'followups':'turns/*/followups/*/usage.json','write_processing':'turns/*/write-readiness.json'}
            assigned=set()
            for phase,pattern in patterns.items():
                ids=set()
                for path in arm.glob(pattern):
                    data=read_cost_record(path);usage=data.get('usage',data)
                    ids.update(c['id'] for c in usage.get('model_calls',[]))
                    missing=[call for call in usage.get('model_calls',[]) if call['id'] not in actual]
                    actual.update(refreshed_calls(missing,connection))
                phases[phase]=ids;assigned.update(ids)
            phases['outside_captured_windows']=set(actual)-assigned
            def totals(ids):
                return role_totals([actual[id] for id in ids])
            records=[read_cost_record(p) for p in sorted((arm/'ingestion').glob('*.json'))]
            evaluation=read_cost_record(arm/'evaluation.json') if (arm/'evaluation.json').exists() else {'checks':[],'counts':{}}
            scores=[c.get('recovery',{}).get('score',{}) for c in evaluation['checks']]
            expected=len(read_cost_record(root/'evaluation-plan.json')['checks'])
            debt=processing_debt(arm,result['provider'])
            results.append({'provider':result['provider'],'state':result['state'],
                'daily_batches_attempted':len(records),'daily_batches_ready':sum(r.get('ready') is True for r in records),
                'ingestion_wall_s':round(sum(r.get('latency_s',0) for r in records),2),
                'initial_counts':evaluation.get('counts',{}),
                'retention_points':sum(s.get('points',0) for s in scores),'retention_max_graded':sum(s.get('max_points',0) for s in scores),
                'complete_for_cost_comparison':len(records)==len(summary['ingestion_schedule']) and all(r.get('ready') is True for r in records)
                    and len(evaluation['checks'])==expected and all(c.get('verdict') in ('pass','fail')
                        and c.get('recovery',{}).get('state','complete')=='complete' for c in evaluation['checks']),
                'deferred_processing':debt,
                'lifecycle_cost_complete':False if debt['state']=='observed-pending' else None,
                'cost':totals(set(actual)) if capture_available else None,
                'phases':{phase:totals(ids) for phase,ids in phases.items()},
                'request_costs':[{**call, 'provider':result['provider'],
                                  'stages':[phase for phase,ids in phases.items() if id in ids]}
                                 for id,call in actual.items()] if capture_available else None,
                'model_groups':model_groups(list(actual.values())) if capture_available else None,
                'phase_model_groups':{phase:model_groups([actual[id] for id in ids]) for phase,ids in phases.items()},
                'terminal_snapshot_missing_requests':sorted(assigned-{c['id'] for c in snapshot['calls']}),
                'overlapping_phase_request_ids':{id:[p for p,ids in phases.items() if id in ids]
                    for id in assigned if sum(id in ids for ids in phases.values())>1}})
        for attempt in summary.get('other_attempts',[]):
            arm=(root/attempt['evidence']).resolve()
            if not arm.is_relative_to(root.resolve()):raise ValueError('Attempt evidence must stay inside the run')
            captured=read_cost_record(arm/'model-usage.json') if (arm/'model-usage.json').exists() else {}
            calls=refreshed_calls(captured.get('calls',[]),connection)
            other_attempts.append({**attempt,'cost':role_totals(list(calls.values())) if captured.get('state')=='captured' else None,
                'daily_batches_ready':sum(read_cost_record(p).get('ready') is True for p in (arm/'ingestion').glob('*.json')),
                'scope':'Previous or interrupted attempt; excluded from the selected arm costs, preserved as real trial spending.'})
    finally:
        if connection:connection.close()
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'providers':results,'other_attempts':other_attempts,
        'ledger_refreshed':connection is not None,'input_hash':summary['input_hash'],
        'scope':['All native arm requests, including setup, automatic recall and background capture; benchmark authoring/judging are separate overhead.',
            'Phase windows identify request start times, not causal per-tool prices. Outside-window work is included in total costs.',
            'Usage-priced figures are conservative uncached-price bounds, not invoices. Unknown actual charges retain safety bounds.',
            'Provider identifies the benchmark arm, not a claim that every Hermes request was made by that memory provider. Stages are observed receipt windows; overlapping windows do not prove causal attribution.',
            'Only arms completing identical daily batches, initial questions and required recovery can be compared as observed workload costs.',
            'Observed workload costs stop at the recorded arm cutoff. Pending or unobserved deferred processing prevents a full lifecycle cost claim; future costs are unknown, not zero.',
            'Native canary execution and failed attempts are separate from month costs. Semantic correctness and source-assisted retention are separate scores.']}
    save(root/'cost-comparison.json',report)
    return report


def cost_review_main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    args=parser.parse_args();report=review(args.root)
    print(json.dumps(report,indent=2));return 0
