import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pacbench.judge_format_review import (VERSION, audit_review, complete_core,
                                         create_review, review_response)
from pacbench.model_calls import ModelOutput
from pacbench.semantic_grade import (Claim, DATE_SCOPED_JUDGE_PROTOCOL,
    DIAGNOSTIC_VERSION, grade_native_semantic, grade_semantic, judge_prompt)


CLAIMS = (Claim('day', 'Dinner is Saturday October 24.', ('source-1',)),)
INPUTS = {'answer_visible': [{'id': 'prefetch', 'text': 'Dinner is Saturday October 24.'}],
          'source_evidence': [{'id': 'source-1', 'text': 'Dinner is Saturday October 24.'}]}
KWARGS = dict(assess_recovery=True, assess_interaction=True,
              diagnostic_inputs=INPUTS, diagnostic_version=DIAGNOSTIC_VERSION,
              judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL)


def core(verdict='correct'):
    return {'verdicts': [{'id': 'day', 'verdict': verdict, 'reason': 'Date checked.'}],
            'recovery': [{'id': 'day', 'kind': {'correct': 'provided', 'missed': 'unavailable',
                          'misrepresented': 'incorrect'}[verdict], 'reason': 'Same fact.'}],
            'interaction': 'answer', 'benchmark_error': None}


def truncated(value=None):
    value = core() if value is None else value
    return json.dumps(value)[:-1] + ', "evidence_diagnostic": {"claims": [{"id": "day"}'


def doc(response):
    question, answer = 'When is dinner?', 'Saturday October 24.'
    return {'checkpoint': 'dinner', 'question': question, 'answer': answer,
            'claims': [asdict(c) for c in CLAIMS],
            'prompt': judge_prompt(question, answer, CLAIMS, **KWARGS),
            'response': response, 'judge_protocol': DATE_SCOPED_JUDGE_PROTOCOL,
            'diagnostic_protocol': DIAGNOSTIC_VERSION, 'diagnostic_inputs': INPUTS}


@pytest.mark.parametrize('verdict', ['correct', 'missed', 'misrepresented'])
def test_recovery_preserves_every_semantic_verdict_without_crediting_diagnostics(verdict):
    response = truncated(core(verdict))
    result = review_response(doc(response))
    assert result['state'] == 'recovered'
    assert result['grade']['slots'] == core(verdict)['verdicts']
    assert result['grade']['recovery'] == core(verdict)['recovery']
    assert result['grade']['evidence_diagnostic']['state'] == 'ungraded'
    assert complete_core(response, assess_recovery=True, assess_interaction=True) == core(verdict)
    assert 'retention' not in result['grade']


def test_no_fragment_of_a_semantic_field_can_be_recovered():
    prefix = json.dumps(core())[:-1]
    for end in range(len(prefix)):
        result = review_response(doc(prefix[:end] + ', "evidence_diagnostic": {"claims": ['))
        assert result['state'] == 'ungraded', end


@pytest.mark.parametrize('tail', [
    '{"claims": [{"reason": "unfinished',
    '{"claims": [{"reason": "invalid\\q"}',
    '{"claims": [true,',
    '{"claims": [1e',
    '{"claims": [NaN',
    '{"claims": [{"id":"a", "id":"b"}',
    '{"claims": [}',
    '{"claims": []}, "verdicts": []',
    '{"claims": []}',  # only the outer object is missing
    '[]',
    'null',
])
def test_rejects_ambiguous_or_damaged_diagnostic_tail(tail):
    response = json.dumps(core())[:-1] + ', "evidence_diagnostic": ' + tail
    assert review_response(doc(response))['state'] == 'ungraded'


@pytest.mark.parametrize('change', [
    lambda c: c.pop('verdicts'),
    lambda c: c.pop('recovery'),
    lambda c: c.pop('interaction'),
    lambda c: c.pop('benchmark_error'),
    lambda c: c.update({'benchmark_error': 'Missing necessary ground truth.'}),
    lambda c: c.update({'interaction': 'invalid'}),
    lambda c: c.update({'unknown': 'value'}),
    lambda c: c['verdicts'][0].update({'id': 'wrong'}),
    lambda c: c['verdicts'][0].update({'verdict': 'pass'}),
    lambda c: c['verdicts'][0].update({'reason': ''}),
    lambda c: c['recovery'][0].update({'kind': 'incorrect'}),
])
def test_missing_or_invalid_core_fields_never_become_grades(change):
    value = core()
    change(value)
    assert review_response(doc(truncated(value)))['state'] == 'ungraded'


def test_duplicate_fields_and_embedded_json_are_not_recovered():
    responses = [
        truncated().replace('"verdicts":', '"verdicts": [], "verdicts":', 1),
        truncated().replace('"id": "day"', '"id": "wrong", "id": "day"', 1),
        '```json\n' + truncated(),
        'Here is the judgment: ' + truncated(),
    ]
    assert all(review_response(doc(r))['state'] == 'ungraded' for r in responses)


def test_quotes_brackets_and_unicode_in_complete_strings_are_not_delimiters():
    value = core()
    value['verdicts'][0]['reason'] = 'The answer says "Saturday"; it contains ] } and \\ and 〔E2〕.'
    assert complete_core(truncated(value)) == value


def test_complete_valid_and_invalid_diagnostics_preserve_existing_grade():
    for diagnostic in (None, {'claims': [], 'extra_assertions': []}):
        value = core()
        value['evidence_diagnostic'] = diagnostic
        response = json.dumps(value)
        original = grade_semantic('dinner', 'When is dinner?', 'Saturday October 24.',
                                  CLAIMS, lambda _: response, **KWARGS)
        result = review_response(doc(response))
        assert result == {'state': 'unchanged', 'grade': asdict(original)}


def test_no_diagnostic_request_cannot_use_format_recovery():
    value = doc(truncated())
    value.pop('diagnostic_inputs')
    assert review_response(value)['state'] == 'ungraded'


def make_run(tmp_path):
    root = tmp_path / 'run'
    turns = root / 'repeat-1' / 'provider' / 'turns'
    for name, response in [('dinner', truncated()), ('wrong', truncated(core('misrepresented'))),
                           ('valid', json.dumps(core()))]:
        directory = turns / name
        directory.mkdir(parents=True)
        question, answer = 'When is dinner?', 'Saturday October 24.'
        (directory / 'turn.json').write_text(json.dumps({'prompt': question, 'answer': answer}))
        contract = root / 'evaluations' / name / 'contract.json'
        contract.parent.mkdir(parents=True)
        contract.write_text(json.dumps({'claims': [{'id': 'day', 'statement': CLAIMS[0].description,
                                                  'evidence': ['source-1']}]}))
        counter = 0

        def model(prompt):
            nonlocal counter
            counter += 1
            path = root / 'model-calls' / f'{name}-{counter}.json'
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({'status': 'ok', 'text': response,
                                        'request': {'messages': [{'role': 'user', 'content': prompt}]}}))
            return ModelOutput(response, 'fixture', {}, str(path))

        try:
            grade_native_semantic(name, question, answer, CLAIMS, model,
                                   record_path=directory / 'judgment.json', **KWARGS)
        except ValueError:
            pass
    return root


def test_review_is_hash_bound_immutable_and_audited_without_model_calls(tmp_path):
    root = make_run(tmp_path)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    overlay = root / 'reviews' / 'format-only'
    receipt = create_review(root, overlay)
    assert receipt['version'] == VERSION
    assert receipt['recovered'] == 2 and receipt['unchanged'] == 1
    assert receipt['case_count'] == 3 and receipt['state'] == 'pass'
    assert audit_review(overlay) == receipt
    assert all((root / path).read_bytes() == content for path, content in before.items())
    value = json.loads((overlay / 'review.json').read_text())
    assert value['retention_recomputed'] is False
    assert value['semantic_only'] is True
    assert all('points' not in c for c in value['cases'])
    with pytest.raises(FileExistsError):
        create_review(root, overlay)
    value['cases'][0]['grade']['correct'] = 999
    (overlay / 'review.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='changed'):
        audit_review(overlay)


@pytest.mark.parametrize('target', ['contract', 'attempt', 'model', 'turn', 'added_case'])
def test_original_evidence_changes_invalidate_review(tmp_path, target):
    root = make_run(tmp_path)
    overlay = root / 'reviews' / 'format-only'
    create_review(root, overlay)
    paths = {
        'contract': root / 'evaluations/dinner/contract.json',
        'attempt': root / 'repeat-1/provider/turns/dinner/judgment-attempts/attempt-1.json',
        'model': root / 'model-calls/dinner-1.json',
        'turn': root / 'repeat-1/provider/turns/dinner/turn.json',
    }
    if target == 'added_case':
        path = root / 'repeat-1/provider/turns/new/judgment.json'
        path.parent.mkdir()
        path.write_text('{}')
    else:
        path = paths[target]
        path.write_text(path.read_text() + ' ')
    with pytest.raises((ValueError, KeyError)):
        audit_review(overlay)


def test_review_rejects_unsafe_destination_and_symlinks(tmp_path):
    root = make_run(tmp_path)
    with pytest.raises(ValueError):
        create_review(root, tmp_path / 'wrong-location')
    overlay = root / 'reviews' / 'format-only'
    create_review(root, overlay)
    (overlay / 'escape').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        audit_review(overlay)


@pytest.mark.parametrize('provider,checkpoint', [
    ('memcal', 'maya-options'), ('mem0', 'friday-count'), ('mem0', 'ben-thursday'),
    ('hindsight', 'friday-count'), ('hindsight', 'pond-current'), ('hindsight', 'devon-friday'),
])
def test_both_actual_month_failure_receipts_recover_only_existing_semantics(provider, checkpoint):
    from pacbench.evidence_store import read_json
    from pacbench.rejudge import _args
    root = Path(__file__).parents[1] / 'runs/pizza-month-local-20261105/repeat-1'
    directory = root / provider / 'turns' / checkpoint / 'judgment-attempts'
    for number in (1, 2):
        saved = read_json(directory / f'attempt-{number}.json')
        claims, kwargs = _args(saved)
        with pytest.raises(ValueError):
            grade_semantic(checkpoint, saved['question'], saved['answer'], claims,
                           lambda _: saved['response'], judge_protocol=saved['judge_protocol'], **kwargs)
        reviewed = review_response(saved)
        assert reviewed['state'] == 'recovered'
        assert reviewed['grade']['evidence_diagnostic']['state'] == 'ungraded'
        exact = complete_core(saved['response'], assess_recovery=True, assess_interaction=True)
        assert reviewed['grade']['slots'] == exact['verdicts']
        assert reviewed['grade']['recovery'] == exact['recovery']
