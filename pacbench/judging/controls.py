"""Prepare offline booking controls and compare responses with separate blind keys."""
import argparse
import json
from pathlib import Path

from pacbench.corpus.prepare import read_spec
from ..spec_first_pilot import FIXTURES
from ..workflows.spec import instant

from ..semantic_grade import Claim, CONTEXT_JUDGE_PROTOCOL, DATE_SCOPED_JUDGE_PROTOCOL, grade_semantic, judge_prompt


def evaluate(cases, protocol, responses=None, verdicts=None):
    ids = [case['id'] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate blind case')
    if responses is not None and (set(responses) != set(ids) or set(verdicts) != set(ids)):
        raise ValueError('all blind cases require a response and key')
    results = {}
    for case in cases:
        key = case['id']
        claims = tuple(Claim(*pair) for pair in case['claims'])
        args = (case['question'], case['answer'], claims)
        options = dict(judge_protocol=protocol, question_at=case['at'])
        if responses is None:
            results[key] = judge_prompt(*args, **options)
        else:
            grade = grade_semantic(key, *args, lambda _: responses[key], **options)
            expected = verdicts[key]
            actual = [slot.verdict for slot in grade.slots]
            if len(actual) != len(expected):
                raise ValueError('incorrect human key length')
            results[key] = dict(actual=actual, expected=expected, agrees=actual==expected)
    return results


FIXTURE = FIXTURES / 'prose_booking_controls_v1.json'


def _at_cutoff(spec, receipts):
    cutoff = instant(spec['question_at'])
    notices = spec['notices']
    ids = {n['id'] for n in notices}
    if len(ids) != len(notices) or receipts.keys() != ids:
        raise ValueError('each notice must have a unique ID and one receipt')
    required = spec['required']
    slots = set(required)
    facts, references = {}, []
    # Apply visible amendments in authored order. A late receipt cannot unlock
    # any fact or reference from that notice, even when it was written earlier.
    previous = None
    for notice in notices:
        written, received = instant(notice['written_at']), instant(receipts[notice['id']])
        if received < written or (previous is not None and written <= previous):
            raise ValueError('invalid notice or receipt chronology')
        previous = written
        if received > cutoff:
            continue
        if notice['facts'].keys() - slots:
            raise ValueError('notice updates an unknown criterion')
        references.append(dict(id=notice['id'], text=notice['text'], written_at=notice['written_at'],
                               received_at=receipts[notice['id']]))
        for slot, statement in notice['facts'].items():
            facts[slot] = (slot, statement, (notice['id'],))
    if slots != facts.keys():
        raise ValueError('a required criterion has no evidence at the question cutoff')
    return [facts[slot] for slot in required], references


def prepare_cases(spec=None):
    """Return six label-free case inputs. No renderer or inference is invoked."""
    if spec is None:
        spec = read_spec(FIXTURE)
    if spec['revision'] != 'prose-booking-controls-v1' or spec['required'] != ['room', 'booking']:
        raise ValueError('unexpected booking fixture revision or required criterion order')
    snapshots = {name: _at_cutoff(spec, receipts) for name, receipts in spec['receipts'].items()}
    cases = []
    for row in spec['cases']:
        claims, references = snapshots[row['receipts']]
        cases.append(dict(id=row['id'], at=spec['question_at'], question=spec['question'], answer=row['answer'],
                          claims=list(claims), references=[dict(r) for r in references]))
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate prose case ID')
    return cases


def blind_main():
    parser = argparse.ArgumentParser(description='Render blinded controls or compare saved responses offline.')
    parser.add_argument('cases', type=Path)
    parser.add_argument('protocol', choices=[DATE_SCOPED_JUDGE_PROTOCOL, CONTEXT_JUDGE_PROTOCOL])
    parser.add_argument('--responses', type=Path)
    parser.add_argument('--key', type=Path)
    args = parser.parse_args()
    if bool(args.responses) != bool(args.key):
        parser.error('scoring a saved blind pass requires both responses and key')
    print(json.dumps(evaluate(json.loads(args.cases.read_text()), args.protocol,
                              json.loads(args.responses.read_text()) if args.responses else None,
                              json.loads(args.key.read_text())['verdicts'] if args.key else None), indent=2))


if __name__ == "__main__":
    blind_main()
