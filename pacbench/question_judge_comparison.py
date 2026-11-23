"""Paired, scenario-weighted analysis. Run only after both arms' judgments are sealed."""
import random
from collections import Counter
from statistics import fmean

from .question_judge import BASELINE, PROTOCOL, fields, fingerprint, interpret, nonblank

GOLD = {'correct', 'missed', 'misrepresented', 'reviewers_unresolved'}


def _interval(values):
    """Paired scenario bootstrap, percentile endpoints with linear interpolation."""
    if len(values) < 2:
        return None
    rng = random.Random(1729)
    draws = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(10000))

    def percentile(q):
        pos = q * (len(draws) - 1)
        i = int(pos)
        return draws[i] + (draws[i + 1] - draws[i]) * (pos - i)

    return [percentile(0.025), percentile(0.975)]


def comparison_plan(cases):
    """Freeze case membership, scenario clustering, both wire identities and call order; no labels."""
    groups = {}
    seen = set()
    for key, (scenario, pair) in cases.items():
        if not nonblank(key) or not nonblank(scenario):
            raise ValueError('case and scenario IDs are required')
        binding = pair.manifest()
        digest = fingerprint(binding)
        if digest in seen:
            raise ValueError('same planned pair appears twice')
        seen.add(digest)
        groups.setdefault(scenario, []).append((key, binding))
    if not groups:
        raise ValueError('comparison has no scenarios')
    order = sorted(groups, key=lambda s: fingerprint([1729, s]))
    cells = []
    for index, scenario in enumerate(order):
        arms = (PROTOCOL, BASELINE) if index % 2 else (BASELINE, PROTOCOL)
        for key, binding in sorted(groups[scenario]):
            cells.append(dict(case=key, scenario=scenario, pair_sha256=fingerprint(binding),
                              arms=[dict(arm=a, request_sha256=binding['requests'][a]) for a in arms]))
    return dict(protocol='paired-question-contract-comparison-v1', cells=cells)


def compare(cases, responses, reference, *, plan_sha256, reference_sha256):
    """cases are sealed {case_id: (scenario_id, Pair)}. Gold never enters request assembly.

    A missing case/arm in responses stays in the planned denominator. A foreign reply
    or reference identity is an integrity error. This function cannot certify the
    provenance of a model receipt, the independence of scenarios or reviewer blinding.
    """
    if fingerprint(comparison_plan(cases)) != plan_sha256 or fingerprint(reference) != reference_sha256:
        raise ValueError('committed comparison plan or reference changed')
    if set(cases) != set(reference) or responses.keys() - cases.keys():
        raise ValueError('reference or response outside the planned cases')
    scenarios, errors, unresolved = {}, [], []
    judge_states = {arm: Counter() for arm in (BASELINE, PROTOCOL)}
    relation_counts = Counter()
    semantic_errors = {a: dict(false_accepts=0, false_rejects=0, wrong_noncredit=0) for a in judge_states}
    protected_observed = set()
    incomplete_controls = 0
    for case_id in sorted(cases):
        scenario, pair = cases[case_id]
        identity = fingerprint(pair.manifest())
        ref = reference[case_id]
        fields(ref, ('scenario', 'pair_sha256', 'criteria'))
        if ref['scenario'] != scenario or ref['pair_sha256'] != identity:
            raise ValueError('reference identity differs from committed pair or scenario')
        keys = pair.manifest()['criteria']
        if not isinstance(ref['criteria'], dict) or set(ref['criteria']) != set(keys):
            raise ValueError('reference must account for every planned criterion')
        replies = responses.get(case_id, {})
        if not isinstance(replies, dict) or replies.keys() - {BASELINE, PROTOCOL}:
            raise ValueError('unrecognized comparison arm')
        outcomes = {}
        for arm in (BASELINE, PROTOCOL):
            result = interpret(pair, arm, replies.get(arm))
            judge_states[arm][result['state']] += 1
            outcomes[arm] = {row['id']: row for row in result['rows']}
        stats = scenarios.setdefault(scenario, dict(n=0, planned=0, baseline_agree=0,
            candidate_agree=0, baseline_graded=0, candidate_graded=0, both_graded_gain=0))
        for key in keys:
            cell = ref['criteria'][key]
            fields(cell, ('label', 'control'))
            label = cell['label']
            if not isinstance(label, str) or label not in GOLD or cell['control'] not in (None, 'time', 'scope', 'state'):
                raise ValueError('invalid human label or protected flag')
            old = outcomes[BASELINE].get(key, {}).get('verdict')
            new_row = outcomes[PROTOCOL].get(key, {})
            new = new_row.get('verdict')
            relation_counts[new_row.get('relation', 'ungraded_response')] += 1
            stats['planned'] += 1
            stats['baseline_graded'] += old is not None
            stats['candidate_graded'] += new is not None
            if label == 'reviewers_unresolved':
                unresolved.append((case_id, key))
                continue
            if cell['control'] and label != 'correct':
                protected_observed.add(cell['control'])
                incomplete_controls += old is None or new is None
            for arm, observed in ((BASELINE, old), (PROTOCOL, new)):
                if observed is not None and observed != label:
                    if observed == 'correct':
                        semantic_errors[arm]['false_accepts'] += 1
                    elif label == 'correct':
                        semantic_errors[arm]['false_rejects'] += 1
                    else:
                        semantic_errors[arm]['wrong_noncredit'] += 1
            stats['n'] += 1
            stats['baseline_agree'] += old == label
            stats['candidate_agree'] += new == label
            if old is not None and new is not None:
                stats['both_graded_gain'] += int(new == label) - int(old == label)
            if old != label or new != label:
                errors.append(dict(case=case_id, criterion=key, scenario=scenario, reference=label,
                                   baseline=old, candidate=new, candidate_relation=new_row.get('relation'), control=cell['control']))
    usable = [scenarios[key] for key in sorted(scenarios) if scenarios[key]['n']]
    gains = [100 * (s['candidate_agree'] - s['baseline_agree']) / s['n'] for s in usable]
    delta = fmean(gains) if gains else None
    ci = _interval(gains)
    protected_new_accepts = sum(bool(e['control']) and e['reference'] != 'correct'
        and e['candidate'] == 'correct' and e['baseline'] != 'correct' for e in errors)
    counts = {k: sum(s[k] for s in scenarios.values()) for k in next(iter(scenarios.values()))}
    return dict(plan_sha256=plan_sha256, reference_sha256=reference_sha256,
                responses_sha256=fingerprint(responses), scenarios=scenarios, planned=counts['planned'], determinate=counts['n'],
                judge_states=judge_states, candidate_relations=relation_counts, semantic_errors=semantic_errors,
                reviewers_unresolved=unresolved, paired_errors=errors, delta_pp=delta, interval95_pp=ci,
                gates=dict(practical_gain=delta is not None and delta >= 3,
                           positive_lower_bound=ci is not None and ci[0] > 0,
                           no_new_protected_false_accepts=(protected_observed == {'time', 'scope', 'state'}
                               and not incomplete_controls and protected_new_accepts == 0),
                           no_coverage_loss=counts['candidate_graded'] >= counts['baseline_graded'],
                           semantic_gain_on_valid_pairs=bool(usable) and fmean(
                               s['both_graded_gain']/s['n'] for s in usable) > 0),
                protected_new_false_accepts=protected_new_accepts)
