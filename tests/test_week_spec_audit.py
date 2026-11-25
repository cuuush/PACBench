"""Local scope regression checks against the committed public r2 inputs."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess
import sys

import pytest

from pacbench import week_spec_audit as audit
from pacbench.judge_blind_controls import evaluate
from pacbench.semantic_grade import CONTEXT_JUDGE_PROTOCOL


@pytest.fixture(scope='module')
def schedules():
    return {track: audit.compile_week(track) for track in (audit.PRIMARY, audit.CONTROL)}


def test_observed_inventory_and_frozen_identity():
    report, _, _ = audit.audit_week()
    assert report['summary'] == {
        'authored_fact_lists': 37,
        'primary_turns_by_basis': {'explicit-facts': 36, 'prose-expectation': 5, 'workflow': 8},
        'primary_claims_by_basis': {'explicit-facts': 151, 'prose-expectation': 34, 'workflow': 7},
        'isolated_only': ['lunch-fresh'],
        'scope_exceptions': ['rsvp-before', 'withdrawal', 'visit-final', 'hall-alias'],
    }
    frozen = report['frozen_primary']
    assert (frozen['turns'], frozen['gradable_turns'], frozen['criteria'], frozen['records']) == (49, 47, 192, 142)
    assert frozen['contract_sha256'] == '5eed5aea85bbca691d776fe1edf497fcb13285d5f49daff4b3c1049417e7cef0'
    by_id = {r['id']: r for r in report['turns']}
    assert (by_id['rsvp-before']['authored_facts'], by_id['rsvp-before']['scored_claims']) == (5, 4)
    assert by_id['withdrawal']['differences'] == ['question', 'criteria']
    assert by_id['visit-final']['differences'] == by_id['hall-alias']['differences'] == ['question']
    assert by_id['friday-lunch-time']['source_claims'] == {'question': 'assistant-friday-plans', 'ids': ['lunch-time']}
    assert by_id['weekend-persistence']['source_turn'] == 'weekend-correction'
    assert by_id['weekend-correction']['scored_claims'] == by_id['ben-correction']['scored_claims'] == 0
    assert by_id['lunch-fresh']['scored_claims'] == 3 and by_id['lunch-fresh']['track'] == audit.CONTROL


def test_unchanged_claim_text_cannot_hide_a_workflow_source_change(schedules, monkeypatch):
    workflow, docs, turns, source = schedules[audit.PRIMARY]
    edited = deepcopy(workflow)
    followup = next(q for q in edited['additional_turns'] if q['id'] == 'friday-lunch-time')
    followup['source_claims']['question'] = 'saturday-dinner-time'
    modified = {**schedules, audit.PRIMARY: (edited, docs, turns, source)}
    monkeypatch.setattr(audit, 'compile_week', modified.__getitem__)
    with pytest.raises(ValueError, match='compiled workflow declarations'):
        audit.audit_week()


@pytest.mark.parametrize('change', ['missing-exception', 'new-exception', 'obsolete-exception', 'altered-exception',
                                    'missing-expect', 'changed-expect', 'removed-control', 'duplicate-author'])
def test_scope_changes_require_review(schedules, change):
    workflow, _, turns, _ = schedules[audit.PRIMARY]
    _, _, control, _ = schedules[audit.CONTROL]
    questions = audit.authored_questions()
    registry = audit.read_spec(audit.REGISTRY)
    if change == 'missing-exception':
        del registry['fact_list_exceptions']['hall-alias']
    elif change == 'new-exception':
        registry['fact_list_exceptions']['unknown'] = registry['fact_list_exceptions']['visit-final']
    elif change == 'obsolete-exception':
        question = next(q for q in questions if q['id'] == 'visit-final')
        question['prompt'] = next(t.prompt for t in turns if t.id == 'visit-final')
    elif change == 'altered-exception':
        question = next(q for q in questions if q['id'] == 'withdrawal')
        question['facts'].reverse()
    elif change == 'missing-expect':
        del registry['expect_contracts']['assistant-next-pizza']
    elif change == 'changed-expect':
        next(q for q in questions if q['id'] == 'assistant-friday-plans')['expect'] += ' Another outing.'
    elif change == 'removed-control':
        control = [t for t in control if t.id != 'lunch-fresh']
    else:
        questions.append(deepcopy(questions[0]))
    with pytest.raises(ValueError):
        audit.compare_questions(questions, workflow, turns, control, registry, audit.read_spec(audit.EXCEPTION))


@pytest.mark.parametrize('change', ['claim-count', 'claim-order', 'claim-id', 'claim-evidence', 'citation-order',
                                    'question', 'clock', 'parent', 'release-cutoff', 'turn-order', 'isolated-claim'])
def test_archive_rejects_silent_changes_even_where_an_authored_list_cannot(schedules, monkeypatch, change):
    # The follow-up has no separate authored list. Mutate downstream of compilation
    # to prove the final audit checks what would actually have been scored.
    track = audit.CONTROL if change == 'isolated-claim' else audit.PRIMARY
    wf, docs, turns, source = schedules[track]
    turns = list(turns)
    target = 'lunch-fresh' if track == audit.CONTROL else 'saturday-dinner-time'
    index = next(i for i, t in enumerate(turns) if t.id == target)
    t = turns[index]
    first, *rest = t.claims
    if change == 'claim-count':
        t = replace(t, claims=(first,))
    elif change == 'claim-order':
        t = replace(t, claims=t.claims[::-1])
    elif change in ('claim-id', 'isolated-claim'):
        t = replace(t, claims=(replace(first, id='same-text-new-id'), *rest))
    elif change == 'claim-evidence':
        t = replace(t, claims=(replace(first, evidence=rest[0].evidence + ('incorrect-source',)), *rest))
    elif change == 'citation-order':
        assert len(first.evidence) > 1
        t = replace(t, claims=(replace(first, evidence=first.evidence[::-1]), *rest))
    elif change == 'question':
        t = replace(t, prompt=t.prompt + ' And Sunday?')
    elif change == 'clock':
        t = replace(t, at='2026-10-09T09:11:00-04:00')
    elif change == 'parent':
        t = replace(t, after='game-reference')
    elif change == 'release-cutoff':
        cited = first.evidence[0]
        docs = [replace(d, cutoff='2026-10-09T21:00:00-04:00') if cited in d.source_ids else d for d in docs]
    turns[index] = t
    if change == 'turn-order':
        turns.reverse()
    modified = {**schedules, track: (wf, docs, turns, source)}
    monkeypatch.setattr(audit, 'compile_week', modified.__getitem__)
    with pytest.raises(ValueError, match='Frozen|isolated-control schedule'):
        audit.audit_week()


def test_public_refund_fixture_checks_source_and_timing(schedules):
    _, docs, turns, _ = schedules[audit.PRIMARY]
    t = next(t for t in turns if t.id == 'refund')
    cases, key = audit.refund_controls(t, docs)
    assert audit.refund_controls(t, docs[::-1]) == (cases, key)
    assert set(evaluate(cases, CONTEXT_JUDGE_PROTOCOL)) == set(key['verdicts'])
    labels = {case['answer']: key['verdicts'][case['id']] for case in cases}
    base = 'The pottery class was cancelled.'
    assert labels == {base: ['missed'], base + ' They confirm they have issued your refund.': ['correct'],
                      base + ' They say your refund is still pending and has yet to be issued.': ['misrepresented']}
    with pytest.raises(ValueError, match='at the cutoff'):
        audit.refund_controls(replace(t, at='2026-10-09T20:59:59-04:00'), docs)
    bad_docs = [replace(d, bodies=tuple('The refund has been requested.' if sid == audit.REFUND.source else body
                                        for sid, body in zip(d.source_ids, d.bodies, strict=True))) for d in docs]
    with pytest.raises(ValueError, match='exact cited report'):
        audit.refund_controls(t, bad_docs)


def test_offline_command_exports_separate_cases_and_key():
    cmd = [sys.executable, '-B', '-m', 'pacbench.week_spec_audit']
    cases = json.loads(subprocess.check_output([*cmd, '--emit', 'cases']))
    key = json.loads(subprocess.check_output([*cmd, '--emit', 'key']))
    assert all(set(c) == {'id', 'at', 'question', 'answer', 'claims'} for c in cases)
    assert {c['id'] for c in cases} == set(key['verdicts'])
