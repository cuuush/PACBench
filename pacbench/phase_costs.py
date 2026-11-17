"""Offline receipt-backed phase costs; never mutate a ledger or infer causality from time."""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .receipt_costs import request_record, retained_metadata, summarize as total

PHASES = ('import_processing', 'answering_retrieval', 'judging', 'embeddings',
          'provider_background_or_ambiguous')
PHASE_ALIASES = {'ingest': 'import_processing', 'retrieval': 'answering_retrieval',
                 'answering': 'answering_retrieval', 'background': 'provider_background_or_ambiguous'}


def load(path):
    return json.loads(Path(path).read_text(), parse_float=Decimal)


def report(calls, components):
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--ledger', type=Path, help='Retained direct JSON ledger; read only')
    source.add_argument('--snapshots', type=Path, nargs='+', help='Retained model-usage.json files; read only')
    p.add_argument('--since-index', type=int, default=0)
    p.add_argument('--generation-index', type=Path)
    p.add_argument('--components', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
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
    result = report(rows, args.components)
    result['ledger_offset'] = args.since_index
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, indent=2, default=decimal_json) + '\n'
    with args.output.open('x') as stream:
        stream.write(encoded)


if __name__ == '__main__':
    main()
