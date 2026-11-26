"""Offline judge controls for the synthetic staffing form and the public r2 scope exception."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from pacbench import spec_first_pilot as pilot
from pacbench.judging.controls import evaluate
from pacbench.semantic_grade import CONTEXT_JUDGE_PROTOCOL


@pytest.fixture
def spec():
    return pilot.read_spec(pilot.SPEC)


def test_required_role_compiles_to_one_existing_claim(spec):
    question, claim = pilot.compile_role(spec)
    assert question.prompt == 'Did Neri accept the press operator shift at the March 4 Inkfest?'
    assert claim.statement == 'Neri declined the press operator shift at the March 4 Inkfest.'
    assert claim.evidence == ('staff-17',)
    del spec['question']['optional']
    assert pilot.compile_role(spec)[1] == claim


def test_three_edits_in_each_optional_context(spec):
    cases, key = pilot.controls(spec)
    assert len(cases) == len(key) == 9
    spec['records'].reverse()
    assert pilot.controls(spec) == (cases, key)
    prompts = evaluate(cases, CONTEXT_JUDGE_PROTOCOL)
    contexts = ['The staffing sheet is up for the March 4 Inkfest.',
                'Neri is available for the March 4 Inkfest.',
                'Neri accepted the ticket desk shift at the March 4 Inkfest.']
    by_answer = {c['answer']: c for c in cases}
    for context in contexts:
        for suffix, verdict in [('', 'missed'),
                                (' Neri passed on the press operator shift.', 'correct'),
                                (' Neri signed on for the press operator shift.', 'misrepresented')]:
            answer = context + suffix
            case = by_answer[answer]
            assert key[case['id']] == [verdict]
            assert 'verdict' not in case and 'expected' not in case
            assert len(case['claims']) == 1
            assert 'Answer: ' + answer + '\n' in prompts[case['id']]


@pytest.mark.parametrize('changed', [
    {'id': 'uncited'},
    {'actor': 'another attendee'},
    {'event': 'an earlier Inkfest'},
    {'role': 'ticket desk'},
    {'kind': 'assignment'},  # a proposed assignment is not an explicit reply
    {'reply': 'unanswered'},
    {'reply': 'accepted'},
    {'at': '2026-03-03T21:00:01+00:00'},  # one second after cutoff
])
def test_decline_requires_an_explicit_scoped_and_timely_source(spec, changed):
    entry = next(r for r in spec['records'] if r['id'] == 'staff-17')
    entry.update(changed)
    with pytest.raises(ValueError):
        pilot.compile_role(spec)


def test_absence_and_generic_availability_cannot_establish_decline(spec):
    spec['records'] = [r for r in spec['records'] if r['id'] == 'availability-11']
    with pytest.raises(ValueError, match='no in-scope evidence'):
        pilot.compile_role(spec)


def test_future_correction_becomes_current_only_at_its_cutoff(spec):
    original, _ = pilot.compile_role(spec)
    spec['question']['as_of'] = '2026-03-03T22:10:00+00:00'
    with pytest.raises(ValueError, match='unique current entry'):
        pilot.compile_role(spec)
    spec['question']['required'].update(reply='accepted', evidence='staff-23')
    current, claim = pilot.compile_role(spec)
    assert current.required.reply != original.required.reply
    assert claim.statement == 'Neri accepted the press operator shift at the March 4 Inkfest.'
    cases, key = pilot.controls(spec)
    signed = [c for c in cases if 'signed on for the press operator shift' in c['answer']]
    assert len(signed) == 3 and all(key[c['id']] == ['correct'] for c in signed)


def test_unordered_tie_and_duplicate_id_are_rejected(spec):
    reply = next(r for r in spec['records'] if r['id'] == 'staff-17')
    spec['records'].append(dict(reply))
    with pytest.raises(ValueError, match='duplicate record ID'):
        pilot.compile_role(spec)
    spec['records'][-1].update(id='simultaneous', reply='accepted')
    with pytest.raises(ValueError, match='unique current entry'):
        pilot.compile_role(spec)


def test_typed_optional_field_cannot_promote_or_shadow_the_required_field(spec):
    spec['question']['optional'].append(deepcopy(spec['question']['required']))
    with pytest.raises(ValueError, match='distinct scopes'):
        pilot.compile_role(spec)
    spec['question']['optional'] = [dict(available='false', evidence='availability-11')]
    with pytest.raises(ValueError, match='availability must be a boolean'):
        pilot.compile_role(spec)
    spec['question']['optional'] = [dict(available=False, evidence='availability-11')]
    with pytest.raises(ValueError, match='does not establish available'):
        pilot.compile_role(spec)
    spec['question']['optional'] = {}
    with pytest.raises(ValueError, match='optional fields must be a list'):
        pilot.compile_role(spec)


@pytest.fixture(scope='module')
def rsvp():
    return pilot.historical_rsvp_scopes()


def test_historical_rsvp_drift_remains_visible_with_the_pinned_exception(rsvp):
    with pytest.raises(ValueError, match='unrecognized scope drift'):
        pilot.audit_scope(*rsvp)
    audit = pilot.audit_scope(*rsvp, pilot.read_spec(pilot.EXCEPTION))
    assert audit['status'] == 'existing-scope exception'
    assert audit['differences'] == ['question', 'criteria']
    assert (audit['authored_facts'], audit['scored_claims']) == (5, 4)
    assert any('No person outside' in f for f in audit['authored_only'])
    assert 'Maya sent the lunch invitation.' in audit['scored_only']


@pytest.mark.parametrize('change', ['fifth-claim', 'evidence', 'question', 'authored-fact', 'other-track',
                                    'replaced-by-roster'])
def test_scope_exception_never_covers_another_contract(rsvp, change):
    authored, scored = deepcopy(rsvp)
    if change == 'fifth-claim':
        scored['claims'].append(dict(id='fact-5', description=authored['facts'][2], evidence=()))
    elif change == 'evidence':
        scored['claims'][0]['evidence'] = ('different-source',)
    elif change == 'question':
        scored['question'] += ' Who else might come?'
    elif change == 'authored-fact':
        authored['facts'][0] += ' Updated.'
    elif change == 'other-track':
        scored['track'] = 'another-track'
    else:
        scored['question'] = authored['question']
        scored['claims'] = [dict(id=f'fact-{i}', description=f, evidence=())
                            for i, f in enumerate(authored['facts'], 1)]
    with pytest.raises(ValueError, match='unrecognized scope drift'):
        pilot.audit_scope(authored, scored, pilot.read_spec(pilot.EXCEPTION))


def test_cli_exports_feed_the_existing_offline_judge_controls(tmp_path):
    cmd = [sys.executable, '-B', '-m', 'pacbench.spec_first_pilot']
    report = json.loads(subprocess.check_output(cmd))
    assert report['control_count'] == 9
    assert report['historical_rsvp']['status'] == 'existing-scope exception'
    cases_path, key_path = tmp_path / 'cases.json', tmp_path / 'key.json'
    for name, path in [('cases', cases_path), ('key', key_path)]:
        path.write_bytes(subprocess.check_output([*cmd, '--emit', name]))
    cases = json.loads(cases_path.read_text())
    assert all(set(case) == {'id', 'at', 'question', 'answer', 'claims'} for case in cases)
    renderer = [sys.executable, '-B', '-m', 'pacbench.judge_blind_controls', str(cases_path),
                CONTEXT_JUDGE_PROTOCOL]
    prompts = json.loads(subprocess.check_output(renderer))
    assert set(prompts) == set(json.loads(key_path.read_text())['verdicts']) == {c['id'] for c in cases}
    # Constant mocked responses exercise the saved-response interface and its disagreement path.
    response = json.dumps({'verdicts': [{'id': 'role-reply', 'verdict': 'missed', 'reason': 'Mock response.'}]})
    responses_path = tmp_path / 'responses.json'
    responses_path.write_text(json.dumps(dict.fromkeys(prompts, response)))
    results = json.loads(subprocess.check_output([*renderer, '--responses', str(responses_path),
                                                  '--key', str(key_path)]))
    assert len(results) == 9
    assert sum(r['agrees'] for r in results.values()) == 3
