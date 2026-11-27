"""Typed verdicts remain decodable in saved comparison records."""
from pacbench.judging import paired as plan


def test_frozen_verdict_types():
    score = {'criterion': {'type': 'score', 'score': 1}}
    assert plan.decisions_verdict({'answers': score}) == plan.Verdict('invalid')
    assert plan.decisions_verdict({'answers': {'foreign': score}}) == plan.Verdict('invalid')
    assert plan.decisions_verdict({'answers': {'criterion': {'type': 'refusal'}}}) == plan.Verdict('abstained')
    invalid_probability = {'type': 'choice', 'choice': 'correct', 'probabilities': {'correct': True}}
    assert plan.decisions_verdict({'answers': {'criterion': invalid_probability}}) == plan.Verdict('invalid')
    assert plan.chat_verdict('{"verdicts":[{"id":"criterion","verdict":"abstain","reason":"refused"}]}') == plan.Verdict('abstained')
    assert plan.chat_verdict('{"verdicts":[{"id":"criterion","verdict":"correct","reason":""}]}') == plan.Verdict('invalid')
