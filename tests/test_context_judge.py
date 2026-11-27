"""Offline request, receipt, and parser controls. Model behavior needs blinded calibration."""
import json

import pytest

from pacbench.judging.grade import Claim, CONTEXT_JUDGE_PROTOCOL, DATE_SCOPED_JUDGE_PROTOCOL, grade_semantic, judge_prompt


@pytest.mark.parametrize('instant,local,day,tomorrow', [
    ('2026-03-08T04:30:00Z', '2026-03-07T23:30:00-05:00', 'Saturday', '2026-03-08'),
    ('2026-03-08T07:30:00Z', '2026-03-08T03:30:00-04:00', 'Sunday', '2026-03-09'),
    ('2026-11-01T05:30:00Z', '2026-11-01T01:30:00-04:00', 'Sunday', '2026-11-02'),
    ('2026-11-01T06:30:00Z', '2026-11-01T01:30:00-05:00', 'Sunday', '2026-11-02'),
])
def test_calendar_converts_instant_across_midnight_and_dst(instant, local, day, tomorrow):
    prompt = judge_prompt('What about tomorrow?', 'My call.', (), question_at=instant,
                          judge_protocol=CONTEXT_JUDGE_PROTOCOL)
    assert f'{local} ({day}, America/New_York)' in prompt
    assert f'tomorrow is {tomorrow}' in prompt


@pytest.mark.parametrize('instant', [None, '2026-10-09T09:00:00', 'bad'])
def test_missing_clock_fails_before_generation_and_v2_stays_reconstructible(instant):
    claims = (Claim('day', 'Friday'),)
    with pytest.raises(ValueError):
        grade_semantic('q', 'When?', 'Today.', claims, lambda _: pytest.fail('dispatched'),
                       question_at=instant, judge_protocol=CONTEXT_JUDGE_PROTOCOL)
    assert judge_prompt('When?', 'Today.', claims, judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL,
                        question_at=instant) == judge_prompt('When?', 'Today.', claims,
                                                             judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL)


def test_attribute_and_roster_controls_preserve_independent_ordered_verdicts():
    # Synthetic reviewer responses exercise the scoring boundary for the paired cases below.
    # Their expected labels can also be used in a future blinded live comparison.
    claims = (Claim('time', 'Their lunch is at 12:30.'), Claim('venue', 'Their lunch is at Pine Cafe.'),
              Claim('roster', 'Only the user and Lee are confirmed for lunch.'))
    cases = [
        ("Lunch for you and Lee, just the two of you, at 12:30 at Oak Cafe.",
         ['correct', 'misrepresented', 'correct']),
        ("Nora's separate booking is at 12:30 at Pine Cafe. Lee might join you for lunch.",
         ['missed', 'missed', 'missed']),
        ("Your lunch at Pine Cafe is at noon. Lee and Morgan are both confirmed with you.",
         ['misrepresented', 'correct', 'misrepresented']),
    ]
    for answer, expected in cases:
        def judge(prompt):
            assert 'Identify the occurrence' in prompt and 'collectively expressed' in prompt
            assert answer in prompt
            return json.dumps({'verdicts': [
                {'id': claim.id, 'verdict': value, 'reason': 'Recorded test label.'}
                for claim, value in zip(claims, expected, strict=True)]})
        grade = grade_semantic('lunch', 'Who, when and where?', answer, claims, judge,
                               question_at='2026-10-08T09:00:00-04:00', judge_protocol=CONTEXT_JUDGE_PROTOCOL)
        assert [slot.verdict for slot in grade.slots] == expected


def test_context_receipt_replay_rejects_clock_substitution(tmp_path):
    from pacbench.model_calls import ModelOutput
    from pacbench.judging.grade import grade_native_semantic, audit_native_judge_attempts

    calls = tmp_path/'model-calls'
    calls.mkdir()
    receipt = tmp_path/'turn'/'judgment.json'
    claims = (Claim('day', 'The lunch is on Friday.'),)
    context = dict(question='When?', answer='Today.', claims=claims,
                   judge_protocol=CONTEXT_JUDGE_PROTOCOL, question_at='2026-10-09T08:30:00-04:00')

    def recorded_response(prompt):
        text = json.dumps({'verdicts': [{'id':'day','verdict':'correct','reason':'Fixture.'}]})
        call = calls/'fixture.json'
        call.write_text(json.dumps({'status':'ok','text':text,'request':{'messages':[{'role':'user','content':prompt}]}}))
        return ModelOutput(text,'fixture',{},str(call))

    grade_native_semantic('lunch', generate=recorded_response, record_path=receipt, **context)
    audit_native_judge_attempts(receipt, **context)
    with pytest.raises(ValueError, match='mismatch'):
        audit_native_judge_attempts(receipt, **{**context, 'question_at':'2026-10-08T08:30:00-04:00'})
