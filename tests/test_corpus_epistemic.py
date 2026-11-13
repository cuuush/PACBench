"""Uncertain world state needs grounded complete-history evidence, not certainty."""
import pytest
from pacbench.corpus_review import review_prompt,verdict_passes,validation_errors
from pacbench.corpus_epistemic import VERSION,eligible_evidence
from pacbench.followups import digest


def fixture():
    messages=[{'source_id':'booking','sender':'Studio','from_owner':False,'channel':'email','thread':'studio',
        'written_at':'2026-10-05T09:00:00-04:00','collected_at':'2026-10-05T09:00:00-04:00',
        'text':'Your workshop is Tuesday October 6 at 10 am.'}]
    resolved={'scenes':[{'scene':'HIDDEN_AUTHOR_INTENT','facts':['HIDDEN_FACT']}],
        'checks':[{'id':'attendance','at':'2026-10-07T09:00:00-04:00','prompt':'Did I attend the workshop?',
                   'facts':['HIDDEN_EXPECTATION']}]}
    row={'check_id':'attendance','event_date':'2026-10-06','answerability':'bounded_unknown',
        'assessable':True,'ambiguous':True,'current_state_conflict':False,
        'grounding_kind':'source_record',
        'known_fields':['A workshop was booked for October 6 at 10 am.'],
        'unresolved_fields':[{'attribute':'attendance','missing':'No attendance outcome is established by the complete eligible history.',
            'alternatives':['attended','did not attend']}],
        'reason':'The booking establishes a plan, not whether it happened. No eligible outcome report exists.',
        'full_eligible_history_reviewed':True,'alternatives':['attended','did not attend'],
        'source_evidence':[{'source_id':'booking','written_at':messages[0]['written_at'],'quote':messages[0]['text']}]}
    verdict={'consistent':True,'questions_answerable':True,'natural_enough':True,'issues':[],
        'assessments':[row],'notes':'Grounded uncertainty, not an attendance assertion.'}
    return messages,resolved,verdict


def test_v10_accepts_grounded_unknown_without_reinterpreting_v9_rejection():
    messages,resolved,verdict=fixture()
    assert verdict_passes(verdict,messages,resolved,version=VERSION)
    assert not verdict_passes(verdict,messages,resolved,version='source-realism-v9')
    prompt=review_prompt(messages,resolved,version=VERSION)
    assert all(secret not in prompt for secret in ['HIDDEN_AUTHOR_INTENT','HIDDEN_FACT','HIDDEN_EXPECTATION'])
    assert messages[0]['source_id'] in prompt and messages[0]['collected_at'] in prompt
    assert digest(eligible_evidence(messages,resolved,'attendance')) in prompt


@pytest.mark.parametrize('mutation',[
    {'full_eligible_history_reviewed':False}, {'grounding_kind':'presupposition_only'},
    {'unresolved_fields':[]},{'known_fields':[]},{'current_state_conflict':True},
    {'answerability':'invalid'},{'assessable':False},{'check_id':[]},{'answerability':[]},
])
def test_unknown_is_not_an_escape_from_grounding_scope_or_conflict(mutation):
    messages,resolved,verdict=fixture();verdict['assessments'][0].update(mutation)
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)


def test_future_or_late_collected_attendance_cannot_be_cited():
    messages,resolved,verdict=fixture()
    future={**messages[0],'source_id':'outcome','text':'I attended.',
        'written_at':'2026-10-07T10:00:00-04:00','collected_at':'2026-10-07T10:00:00-04:00'}
    messages.append(future)
    assert verdict_passes(verdict,messages,resolved,version=VERSION)
    verdict['assessments'][0]['source_evidence']=[{'source_id':'outcome','written_at':future['written_at'],'quote':'I attended.'}]
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)
    future['written_at']='2026-10-06T11:00:00-04:00'
    verdict['assessments'][0]['source_evidence'][0]['written_at']=future['written_at']
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)


def test_question_presupposition_is_not_independent_event_grounding():
    messages,resolved,verdict=fixture()
    row=resolved['checks'][0]
    verdict['assessments'][0]['source_evidence']=[{'source_id':'public-input:attendance','written_at':row['at'],'quote':row['prompt']}]
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)


def test_conditional_public_inputs_and_dependents_are_not_known_history():
    messages,resolved,verdict=fixture();first=resolved['checks'][0]
    first['trigger']='clarification'
    second={'id':'dependent','at':'2026-10-07T09:05:00-04:00','prompt':'It was the pottery class.', 'requires':['attendance']}
    third={'id':'later','at':'2026-10-07T09:10:00-04:00','prompt':'What happened?'}
    resolved['checks']+=[second,third]
    scope=eligible_evidence(messages,resolved,'later')
    assert [r['id'] for r in scope['public_inputs']]==['later']


def test_clarification_requires_material_alternatives_and_full_history():
    messages,resolved,verdict=fixture();row=verdict['assessments'][0]
    row['answerability']='clarification_warranted'
    assert verdict_passes(verdict,messages,resolved,version=VERSION)
    row['unresolved_fields'][0]['alternatives']=['none']
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)


def test_exact_identity_and_quote_are_both_required():
    messages,resolved,verdict=fixture();row=verdict['assessments'][0]
    row['source_evidence'][0]['source_id']='other'
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)
    row['source_evidence'][0]['source_id']='booking';row['source_evidence'][0]['quote']='A plausible paraphrase.'
    assert not verdict_passes(verdict,messages,resolved,version=VERSION)
