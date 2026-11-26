"""Render and compare a synthetic four-arm judge calibration offline."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Literal

from .. import judge_development_challenge as challenge
from ..openrouter_route import CHAT_COMPLETIONS_URL, with_route
from ..semantic_grade import VERDICTS


DECISIONS_URL = 'https://openrouter.ai/api/alpha/decisions'
PROTOCOL = 'pizza-week-judge-calibration-development-v2'
CASE_COUNT = 24
CALIBRATION_PROFILE = 'luna-bedrock-global'
CRITERION_ID = 'criterion'


@dataclass(frozen=True)
class Route:
    model: str
    endpoint: str
    served_model: str
    served_provider: str

    def routing(self):
        return dict(only=[self.endpoint], order=[self.endpoint], allow_fallbacks=False)


ROUTES = MappingProxyType({
    'luna-decisions': Route('openai/gpt-6-luna-decisions', 'openai',
                            'openai/gpt-6-luna-decisions-20261006', 'OpenAI'),
    'jev-decisions': Route('typesafe/jev-1.14', 'typesafe', 'typesafe/jev-1.14-20261014', 'TypeSafe'),
})
ARMS = ('v4', 'explicit', *ROUTES)
CHOICES = MappingProxyType({
    'correct': 'The latest answer conveys the required fact about the intended event and time.',
    'missed': 'The latest answer omits the required fact or says it is unknown.',
    'misrepresented': 'The latest answer asserts a wrong value or contradicts the required fact.',
})
CHAT_OUTPUT = (
    "Return ONLY one JSON object with 'verdicts', an array in the same "
    "order, each containing id, verdict (correct/missed/misrepresented), "
    "and a short reason grounded in the answer.\n\n"
)


def wire_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON field')
    return result


def _nonfinite(number):
    raise ValueError('Nonfinite JSON number')


def strict_json(raw):
    return json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique_object, parse_constant=_nonfinite)


@dataclass(frozen=True)
class Cell:
    case_id: str
    arm: str
    body: bytes

    @property
    def id(self):
        return self.case_id + ':' + self.arm

    @property
    def url(self):
        return DECISIONS_URL if self.arm in ROUTES else CHAT_COMPLETIONS_URL

    @property
    def digest(self):
        return sha256(self.body).hexdigest()


@dataclass(frozen=True)
class Plan:
    cells: tuple[Cell, ...]

    @property
    def manifest(self):
        return {
            'protocol': PROTOCOL,
            'split': 'development',
            'development_revision': challenge.REVISION,
            'fixture_sha256': challenge.CELLS_SHA256,
            'reference_sha256': challenge.REFERENCE_SHA256,
            'requests': [{'id': cell.id, 'method': 'POST', 'url': cell.url, 'body_sha256': cell.digest}
                         for cell in self.cells],
        }

    @property
    def digest(self):
        return sha256(wire_bytes(self.manifest)).hexdigest()


def _decisions_body(route, prompt):
    standard, delimiter, context = prompt.partition(CHAT_OUTPUT)
    if not delimiter:
        raise ValueError('Context rubric output instructions changed')
    return {
        'model': route.model,
        'state': standard + '\n\n' + context,
        'questions': {
            CRITERION_ID: {
                'type': 'choice',
                'instructions': 'Evaluate the sole criterion using the question, latest answer and context.',
                'criteria': dict(CHOICES),
            },
        },
        'provider': route.routing(),
    }


def _body(arm, prompt):
    if arm in ROUTES:
        return _decisions_body(ROUTES[arm], prompt)
    return with_route({
        'messages': [{'role': 'user', 'content': prompt}],
        'response_format': {'type': 'json_object'},
        'stream': True,
        'stream_options': {'include_usage': True},
        'max_completion_tokens': 32768,
    }, profile=CALIBRATION_PROFILE)


def render_plan(seed):
    """Freeze four bare wire bodies per blind ID without opening the reference labels."""
    baseline = challenge.plan(seed, 'v4')
    explicit = challenge.plan(seed, 'explicit')
    if len(baseline) != CASE_COUNT or len({case['id'] for case in baseline}) != CASE_COUNT:
        raise ValueError(f'Expected {CASE_COUNT} distinct frozen development cases')
    cells = []
    for index, (old, new) in enumerate(zip(baseline, explicit, strict=True)):
        if old['id'] != new['id']:
            raise ValueError('Frozen rubric readiness differs by blind ID')
        # Balanced order and adjacent carryover over each four consecutive cases.
        for offset in (0, 1, 3, 2):
            arm = ARMS[(index + offset) % len(ARMS)]
            prompt = old['prompt'] if arm == 'v4' else new['prompt']
            cells.append(Cell(old['id'], arm, wire_bytes(_body(arm, prompt))))
    return Plan(tuple(cells))


@dataclass(frozen=True)
class Verdict:
    status: Literal['valid', 'abstained', 'invalid']
    choice: Literal['correct', 'missed', 'misrepresented'] | None = None

    def __post_init__(self):
        if self.status == 'valid' and isinstance(self.choice, str) and self.choice in VERDICTS:
            return
        if self.status not in ('abstained', 'invalid') or self.choice is not None:
            raise ValueError('Invalid normalized calibration verdict')


def _unit_number(value):
    return type(value) in (int, Decimal) and Decimal(value).is_finite() and 0 <= value <= 1


def decisions_verdict(value):
    answers = value.get('answers') if isinstance(value, dict) else None
    if not isinstance(answers, dict) or set(answers) != {CRITERION_ID}:
        return Verdict('invalid')
    answer = answers[CRITERION_ID]
    if answer == {'type': 'refusal'}:
        return Verdict('abstained')
    if not isinstance(answer, dict) or answer.get('type') != 'choice':
        return Verdict('invalid')
    choice = answer.get('choice')
    if not isinstance(choice, str) or choice not in CHOICES:
        return Verdict('invalid')
    if 'confidence' in answer and not _unit_number(answer['confidence']):
        return Verdict('invalid')
    if 'probabilities' in answer:
        probabilities = answer['probabilities']
        if (not isinstance(probabilities, dict) or not probabilities or set(probabilities) - CHOICES.keys()
                or not all(_unit_number(item) for item in probabilities.values())):
            return Verdict('invalid')
    return Verdict('valid', choice)


def chat_verdict(raw):
    """Normalize a saved Chat Completion's assistant text for the same cross-arm report."""
    try:
        rows = strict_json(raw)['verdicts']
        if not isinstance(rows, list) or len(rows) != 1 or rows[0]['id'] != CRITERION_ID:
            return Verdict('invalid')
        choice, reason = rows[0]['verdict'], rows[0].get('reason')
        if isinstance(reason, str) and reason.strip():
            if choice == 'abstain':
                return Verdict('abstained')
            if isinstance(choice, str) and choice in VERDICTS:
                return Verdict('valid', choice)
    except (ValueError, TypeError, KeyError):
        pass
    return Verdict('invalid')


def paired_errors(seed, observations):
    """Compare normalized observations after the caller verifies their request provenance."""
    plan = render_plan(seed)
    known = {cell.id for cell in plan.cells}
    observed = {}
    for ident, verdict in observations:
        if ident not in known or ident in observed or not isinstance(verdict, Verdict):
            raise ValueError('Unknown, duplicate or unnormalized calibration verdict')
        observed[ident] = verdict
    reference = challenge._frozen(challenge.REFERENCE, challenge.REFERENCE_SHA256)['families']
    labels = {}
    for cell in challenge._blinded_cells(seed):
        family = cell.family['id']
        key = reference[family]['cases'][cell.key]
        labels[cell.id] = (family, key['verdict'], key['reason'])
    by_arm = {}
    paired = {}
    for arm in ARMS:
        families = {}
        for ident, (family, gold, _) in labels.items():
            verdict = observed.get(ident + ':' + arm)
            status = 'missing' if verdict is None else verdict.choice or verdict.status
            families.setdefault(family, Counter())[f'{gold}->{status}'] += 1
        counts = sum(families.values(), Counter())
        by_arm[arm] = dict(
            exact=sum(counts[f'{gold}->{gold}'] for gold in VERDICTS),
            false_accepts=sum(counts[f'{gold}->correct'] for gold in VERDICTS - {'correct'}),
            false_rejects=sum(counts[f'correct->{choice}'] for choice in VERDICTS - {'correct'}),
            **{status: sum(counts[f'{gold}->{status}'] for gold in VERDICTS)
               for status in ('invalid', 'abstained', 'missing')},
            by_family=families,
        )
    comparisons = [('explicit', 'v4'), *((candidate, baseline)
                   for candidate in ROUTES for baseline in ('v4', 'explicit'))]
    for candidate, baseline in comparisons:
        paired[f'{candidate}_vs_{baseline}'] = _comparison(labels, observed, candidate, baseline)
    return dict(protocol=PROTOCOL, manifest_sha256=plan.digest, cases=len(labels), arms=by_arm, comparisons=paired)


def _comparison(labels, observed, candidate, baseline):
    counts, families, changes, false_accepts = Counter(), {}, [], []
    for ident, (family, gold, reason) in labels.items():
        old = observed.get(ident + ':' + baseline)
        new = observed.get(ident + ':' + candidate)
        if new and new.choice == 'correct' and gold != 'correct' and (not old or old.choice != 'correct'):
            false_accepts.append(ident)
        if old is None or new is None or old.status != 'valid' or new.status != 'valid':
            transition = 'unpaired'
        else:
            transition = {
                (False, True): 'resolved_error', (True, False): 'new_error',
                (True, True): 'both_correct', (False, False): 'both_wrong',
            }[(old.choice == gold, new.choice == gold)]
            if old.choice != new.choice:
                changes.append(dict(id=ident, family=family, gold=gold, baseline=old.choice,
                                    candidate=new.choice, transition=transition, reference_reason=reason))
        counts[transition] += 1
        families.setdefault(family, Counter())[transition] += 1
    # Same preregistered thresholds as compare() in the frozen challenge.
    delta = {family: row['resolved_error'] - row['new_error'] for family, row in families.items()}
    checks = {
        'complete_family_with_semantic_gain': any(
            delta[family] > 0 and row['unpaired'] == 0 for family, row in families.items()),
        'no_paired_family_regression': all(value >= 0 for value in delta.values()),
        'no_new_or_unverified_false_accepts': not false_accepts,
        'all_candidate_outputs_usable': all(
            (verdict := observed.get(ident + ':' + candidate)) is not None and verdict.status == 'valid'
            for ident in labels),
        'all_baseline_outputs_observed': all(ident + ':' + baseline in observed for ident in labels),
    }
    complete = all(ident + ':' + arm in observed for ident in labels for arm in (baseline, candidate))
    result = 'incomplete'
    if complete:
        result = 'candidate-development-improvement' if all(checks.values()) else 'no-winner'
    return dict(counts=counts, by_family=families, changes=changes, new_or_unverified_false_accepts=false_accepts,
                complete=complete, comparison_checks=checks, comparison=result)
