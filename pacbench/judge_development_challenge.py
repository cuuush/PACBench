"""Render blinded development prompts and compare two sets of saved replies offline.

Use each prompt in isolation. Keep the seed and reference file out of judging.
For comparison, supply JSON arrays of {id, prompt_sha256, response} records.
The response is the original judge text. Omit a row if no reply was saved.
The comparison binds text to prompt digests. Judge provenance is external.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path

from .judge_format_review import DECODER
from .semantic_grade import (CONTEXT_JUDGE_PROTOCOL, Claim, UnusableJudgeResponse,
                             grade_semantic, judge_prompt)


REVISION = 'luna-judge-development-challenge-v2'
FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
CELLS = FIXTURES / 'luna_judge_development_challenge_v2.json'
REFERENCE = FIXTURES / 'luna_judge_development_challenge_gold_v2.json'
CELLS_SHA256 = '2aa4429035e797cafe2caf7aae724de4338ff55362931452b2059b342ab8892f'
REFERENCE_SHA256 = '9a3f8fe5e70202100c70ce1415eba7e6d0392d363ccc265962ca42b346a14728'
RUBRICS = ('v4', 'explicit')


@dataclass(frozen=True)
class _Cell:
    id: str
    key: str
    family: dict

    @property
    def judge_input(self):
        family = self.family
        answer = f"{family['cases'][self.key]} {family['distractor']}"
        args = (family['question'], answer, (Claim('criterion', family['claim']),))
        options = dict(question_at=family['question_at'], conversation=family['conversation'],
                       judge_protocol=CONTEXT_JUDGE_PROTOCOL)
        return args, options


def _frozen(path, digest):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f'frozen fixture digest differs for {path.name}')
    return DECODER.decode(raw.decode('utf-8'))


def _blinded_cells(seed):
    try:
        secret = bytes.fromhex(seed)
    except ValueError as exc:
        raise ValueError('seed must be 64 hexadecimal characters') from exc
    if len(seed) != 64 or len(secret) != 32:
        raise ValueError('seed must be 64 hexadecimal characters')

    def digest(namespace, family_id, case_key):
        text = f'{REVISION}/{namespace}/{family_id}/{case_key}'
        return hmac.digest(secret, text.encode(), 'sha256').hex()

    ordered = []
    for family in _frozen(CELLS, CELLS_SHA256)['families']:
        for key in family['cases']:
            order = digest('order', family['id'], key)
            blind_id = digest('id', family['id'], key)[:32]
            ordered.append((order, _Cell(blind_id, key, family)))
    return [cell for _, cell in sorted(ordered, key=lambda item: item[0])]


def _suffix(cell, rubric):
    if rubric == 'v4':
        return ''
    if rubric == 'explicit':
        return '\nExperimental criterion rule: ' + cell.family['explicit_rule']
    raise ValueError(f'unknown development rubric {rubric!r}')


def _request(cell, rubric):
    args, options = cell.judge_input
    prompt = judge_prompt(*args, **options) + _suffix(cell, rubric)
    return dict(id=cell.id, prompt=prompt, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())


def plan(seed, rubric):
    """No human key, arm label, or family identifier is loaded into the plan."""
    return [_request(cell, rubric) for cell in _blinded_cells(seed)]


def _judge_outcome(cell, rubric, response):
    if not isinstance(response, str):
        return dict(state='invalid', reason='response is not text')
    try:
        decoded = DECODER.decode(response)
    except ValueError:
        return dict(state='invalid', reason='response is not strict JSON')
    args, options = cell.judge_input
    try:
        grade = grade_semantic(cell.id, *args, lambda _: response, **options,
                              prompt_suffix=_suffix(cell, rubric))
    except UnusableJudgeResponse:
        rows = decoded.get('verdicts') if isinstance(decoded, dict) else None
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            reason = row.get('reason')
            if (row.get('id') == 'criterion' and row.get('verdict') == 'abstain' and
                    isinstance(reason, str) and reason.strip()):
                return dict(state='abstained', reason='abstention is outside the v4 output contract')
        return dict(state='invalid', reason='response lacks a usable criterion verdict and reason')
    slot, = grade.slots
    return dict(state='valid', verdict=slot.verdict, reason=slot.reason)


def _read_arm(cells, rubric, observations):
    if rubric not in RUBRICS:
        raise ValueError(f'unknown development rubric {rubric!r}')
    if not isinstance(observations, list):
        raise ValueError('saved judge replies must be a JSON array')
    lookup = {cell.id: cell for cell in cells}
    outcomes = {}
    for row in observations:
        if not isinstance(row, dict) or row.keys() != {'id', 'prompt_sha256', 'response'}:
            raise ValueError('saved reply must contain id, prompt_sha256, and response')
        cell_id = row['id']
        if not isinstance(cell_id, str) or cell_id not in lookup or cell_id in outcomes:
            raise ValueError('unknown or duplicate blind ID in saved replies')
        cell = lookup[cell_id]
        if row['prompt_sha256'] != _request(cell, rubric)['prompt_sha256']:
            raise ValueError(f'prompt digest differs for blind ID {cell_id}')
        outcomes[cell_id] = _judge_outcome(cell, rubric, row['response'])
    return outcomes


def _arm_counts(rows, arm):
    verdicts = [(row['gold'], row[arm].get('verdict')) for row in rows]
    return dict(
        states={state: sum(row[arm]['state'] == state for row in rows)
                for state in ('valid', 'missing', 'invalid', 'abstained')},
        exact=sum(expected == actual for expected, actual in verdicts),
        positives_correct=sum(expected == actual == 'correct' for expected, actual in verdicts),
        omissions_correct=sum(expected == actual == 'missed' for expected, actual in verdicts),
        conflicts_correct=sum(expected == actual == 'misrepresented' for expected, actual in verdicts),
        false_accepts=sum(expected != 'correct' and actual == 'correct' for expected, actual in verdicts),
    )


def _family_report(rows):
    summary = {arm: _arm_counts(rows, arm) for arm in ('baseline', 'candidate')}
    paired = [row for row in rows if row['baseline']['state'] == row['candidate']['state'] == 'valid']
    changes = [(row['baseline']['verdict'] == row['gold'], row['candidate']['verdict'] == row['gold'])
               for row in paired]
    gains, losses = changes.count((False, True)), changes.count((True, False))
    return dict(**summary, paired_valid=len(paired), paired_gains=gains, paired_losses=losses,
                paired_delta=gains - losses, both_sides_correct={
                    arm: counts['exact'] == len(rows) for arm, counts in summary.items()})


def compare(seed, baseline_rubric, candidate_rubric, baseline, candidate):
    """Count paired semantic changes only where both replies were usable.

    A development improvement requires a gain in a fully paired family and no
    family regression, new false accepts, unusable candidate replies, or missing
    observations. Recovering invalid replies alone cannot meet that standard.
    """
    cells = _blinded_cells(seed)
    observations = dict(baseline=_read_arm(cells, baseline_rubric, baseline),
                        candidate=_read_arm(cells, candidate_rubric, candidate))
    reference = _frozen(REFERENCE, REFERENCE_SHA256)['families']
    rows = []
    for cell in cells:
        family_id = cell.family['id']
        gold = reference[family_id]['cases'][cell.key]
        question, answer, _ = cell.judge_input[0]
        rows.append(dict(id=cell.id, family=family_id, question=question, answer=answer,
                         claim=cell.family['claim'], question_at=cell.family['question_at'],
                         gold=gold['verdict'], reference_reason=gold['reason'], **{
                             arm: outcomes.get(cell.id, {'state': 'missing'})
                             for arm, outcomes in observations.items()}))

    families = {
        family: dict(_family_report([row for row in rows if row['family'] == family]),
                     allowed_entailments=key['allowed_entailments'])
        for family, key in reference.items()
    }
    summaries = {arm: _arm_counts(rows, arm) for arm in observations}
    false_accepts = [row for row in rows if row['gold'] != 'correct' and
                     row['candidate'].get('verdict') == 'correct' and
                     row['baseline'].get('verdict') != 'correct']
    new_invalids = [row['id'] for row in rows if row['candidate']['state'] == 'invalid' and
                    row['baseline']['state'] != 'invalid']
    improved = [name for name, family in families.items()
                if family['paired_delta'] > 0 and family['paired_valid'] == 4]
    regressed = [name for name, family in families.items() if family['paired_delta'] < 0]
    checks = {
        'complete_family_with_semantic_gain': bool(improved),
        'no_paired_family_regression': not regressed,
        'no_new_or_unverified_false_accepts': not false_accepts,
        'all_candidate_outputs_usable': summaries['candidate']['states']['valid'] == len(rows),
        'all_baseline_outputs_observed': summaries['baseline']['states']['missing'] == 0,
    }
    return dict(
        revision=REVISION, scope='synthetic development evidence', cells_sha256=CELLS_SHA256,
        reference_sha256=REFERENCE_SHA256, judge_protocol=CONTEXT_JUDGE_PROTOCOL,
        baseline_rubric=baseline_rubric, candidate_rubric=candidate_rubric, cells=len(rows),
        arms=summaries, families=families, paired_improved_families=improved, paired_regressed_families=regressed,
        both_sides_correct={arm: sum(f['both_sides_correct'][arm] for f in families.values())
                            for arm in observations},
        previously_missed_correct_rescued=[row['id'] for row in rows if row['gold'] == 'correct' and
                                            row['baseline'].get('verdict') == 'missed' and
                                            row['candidate'].get('verdict') == 'correct'],
        new_false_accepts=[row['id'] for row in false_accepts if row['baseline']['state'] == 'valid'],
        false_accepts_without_usable_baseline=[row['id'] for row in false_accepts
                                               if row['baseline']['state'] != 'valid'],
        new_invalids=new_invalids,
        invalid_to_valid=[row['id'] for row in rows if row['baseline']['state'] == 'invalid' and
                          row['candidate']['state'] == 'valid'],
        error_examples=[row for row in rows if any(row[arm].get('verdict') != row['gold'] for arm in observations)],
        comparison_checks=checks,
        comparison='candidate-development-improvement' if all(checks.values()) else 'no-winner',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    render = commands.add_parser('plan', help='print only the 24 blinded prompts and their digests')
    render.add_argument('--seed', required=True)
    render.add_argument('--rubric', choices=RUBRICS, required=True)
    evaluate = commands.add_parser('compare', help='compare two previously saved response arrays')
    evaluate.add_argument('--seed', required=True)
    evaluate.add_argument('--baseline-rubric', choices=RUBRICS, required=True)
    evaluate.add_argument('--candidate-rubric', choices=RUBRICS, required=True)
    evaluate.add_argument('baseline', type=Path)
    evaluate.add_argument('candidate', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'plan':
            result = plan(args.seed, args.rubric)
        else:
            result = compare(args.seed, args.baseline_rubric, args.candidate_rubric,
                             DECODER.decode(args.baseline.read_text(encoding='utf-8')),
                             DECODER.decode(args.candidate.read_text(encoding='utf-8')))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
