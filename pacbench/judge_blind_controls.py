"""Render blinded qualitative controls or compare saved responses, entirely offline."""
import argparse
import json
from pathlib import Path

from .semantic_grade import Claim, CONTEXT_JUDGE_PROTOCOL, DATE_SCOPED_JUDGE_PROTOCOL, grade_semantic, judge_prompt


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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
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
