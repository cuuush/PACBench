"""Prose controls for two required facts. Reviewer outputs below are parser doubles."""
import json

import pytest

from pacbench.judging import paired as adapter
from pacbench.judging import paired as controls
from pacbench.judging.grade import CONTEXT_JUDGE_PROTOCOL, UnusableJudgeResponse, question_context


KEY = controls.FIXTURES / 'prose_booking_key_v1.json'
CC = ['correct', 'correct']


def test_authored_vectors_and_minimal_changes():
    cases = {c['id']: c for c in controls.prepare_cases()}
    key = controls.read_spec(KEY)
    assert key['revision'] == controls.read_spec(controls.FIXTURE)['revision']
    assert key['criterion_order'] == ['room', 'booking']
    assert key['verdicts'] == {
        'p17': CC, 'p36': CC, 'p28': ['correct', 'missed'], 'p54': ['misrepresented', 'correct'],
        'p63': ['correct', 'misrepresented'], 'p45': CC,
    }
    assert cases.keys() == key['verdicts'].keys()
    for c in cases.values():
        assert set(c) == {'id', 'at', 'question', 'answer', 'claims', 'references'}
        assert [claim[0] for claim in c['claims']] == key['criterion_order']
        assert all(set(r) == {'id', 'text', 'written_at', 'received_at'} for r in c['references'])
    base = cases['p17']
    assert base['answer'] == 'Bell Loft is on the sheet. They’re holding it for now, pending sign-off.'
    assert cases['p36']['answer'] == base['answer'] + ' Use the side entrance.'
    assert 'Use the side entrance.' in base['references'][0]['text']
    assert cases['p28']['answer'] == 'Bell Loft is on the sheet for Thursday.'
    assert cases['p54']['answer'] == base['answer'].replace('Bell Loft', 'Wren Studio')
    assert all(cases[k]['claims'] == base['claims'] for k in ('p36', 'p28', 'p54', 'p63'))


def test_identical_answer_changes_only_with_receipt_time():
    spec = controls.read_spec(controls.FIXTURE)
    a, b = spec['receipts']['a'], spec['receipts']['b']
    assert (a.keys(), a['desk-41']) == (b.keys(), b['desk-41'])
    cutoff = controls.instant(spec['question_at'])
    assert (cutoff - controls.instant(a['desk-42'])).total_seconds() == 1
    assert (controls.instant(b['desk-42']) - cutoff).total_seconds() == 1
    cases = {c['id']: c for c in controls.prepare_cases(spec)}
    early, late = cases['p45'], cases['p63']
    assert (early['question'], early['at'], early['answer'].encode()) == (
        late['question'], late['at'], late['answer'].encode())
    assert late['answer'] == 'They’ve booked Bell Loft for Thursday’s recording.'
    assert early['claims'][0] == late['claims'][0] == (
        'room', 'Bell Loft is the room listed for Thursday’s recording.', ('desk-41',))
    assert late['claims'][1] == ('booking', 'Thursday’s recording has a provisional hold and still awaits final confirmation.', ('desk-41',))
    assert early['claims'][1] == ('booking', 'The booking for Thursday’s recording is confirmed.', ('desk-42',))
    assert [r['id'] for r in early['references']] == ['desk-41', 'desk-42']
    assert late['references'] == early['references'][:1]
    for c in cases.values():
        sources = {r['id'] for r in c['references'] if controls.instant(r['received_at']) <= cutoff}
        assert all(set(claim[2]) <= sources for claim in c['claims'])
    prompts = adapter.evaluate(list(cases.values()), CONTEXT_JUDGE_PROTOCOL)
    for case_id, prompt in prompts.items():
        c = cases[case_id]
        expected = [dict(id=k, claim=text, evidence=list(refs)) for k, text, refs in c['claims']]
        assert json.loads(prompt.rsplit('Claims (hidden from the assistant): ', 1)[1]) == expected
        assert f"Question: {c['question']}\nAnswer: {c['answer']}\n" in prompt
        assert prompt.startswith(question_context(c['at']))
    assert 'desk-42' not in json.dumps(late) and 'desk-42' not in prompts['p63']
    assert 'desk-42' in prompts['p45']


@pytest.mark.parametrize('mutation', ['initial-notice-late', 'missing-receipt', 'receipt-predates-notice'])
def test_bad_or_missing_cutoff_evidence_fails_before_render(monkeypatch, mutation):
    spec = controls.read_spec(controls.FIXTURE)
    receipts = spec['receipts']['b']
    if mutation == 'initial-notice-late':
        receipts['desk-41'] = '2026-06-10T16:01:00-04:00'
    elif mutation == 'missing-receipt':
        del receipts['desk-42']
    else:
        receipts['desk-42'] = '2026-06-10T15:00:00-04:00'
    monkeypatch.setattr(adapter, 'judge_prompt', lambda *a, **kw: pytest.fail('renderer called before cutoff validation'))
    with pytest.raises(ValueError):
        adapter.evaluate(controls.prepare_cases(spec), CONTEXT_JUDGE_PROTOCOL)


def _mock_response(room='correct', booking='correct'):
    return json.dumps({'verdicts': [dict(id=k, verdict=v, reason='Fixed parser control.')
                                    for k, v in [('room', room), ('booking', booking)]]})


def test_saved_response_comparison_is_by_case_and_ordered_criterion():
    cases = controls.prepare_cases()
    key = controls.read_spec(KEY)['verdicts']
    # Constant predictions are independent of the authored labels. In particular
    # the same model response must disagree on the early/late pair's booking slot.
    responses = {c['id']: _mock_response() for c in reversed(cases)}
    output = adapter.evaluate(cases, CONTEXT_JUDGE_PROTOCOL, responses, key)
    assert {k for k, r in output.items() if r['agrees']} == {'p17', 'p36', 'p45'}
    assert all(r['expected'] == key[k] and r['actual'] == CC for k, r in output.items())
    responses['p63'] = _mock_response(booking='misrepresented')
    assert adapter.evaluate(cases, CONTEXT_JUDGE_PROTOCOL, responses, key)['p63']['agrees']
    swapped = json.loads(responses['p63'])
    swapped['verdicts'].reverse()
    responses['p63'] = json.dumps(swapped)
    with pytest.raises(UnusableJudgeResponse):
        adapter.evaluate(cases, CONTEXT_JUDGE_PROTOCOL, responses, key)
    del responses['p63']
    with pytest.raises(ValueError, match='all blind cases'):
        adapter.evaluate(cases, CONTEXT_JUDGE_PROTOCOL, responses, key)
