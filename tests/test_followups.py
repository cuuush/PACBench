"""Offline follow-up policy regressions; synthetic outputs are not provider evidence."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from pacbench.followups import (
    audit_followups, cost_window, digest, freeze_questions, load_questions,
    recovery_score, run_followups, tool_profile, turn_metrics,
)
from pacbench.harness import TurnResult
from pacbench.semantic_grade import Grade, GradedClaim


def contract():
    return SimpleNamespace(contract_hash='bound-contract', question='What is the plan?', claims=(
        SimpleNamespace(id='date', statement='It is Friday.', evidence=('m1',)),
        SimpleNamespace(id='time', statement='It is at seven.', evidence=('m1',))))


def plan():
    body = {'contract_hash': 'bound-contract', 'questions': [
        {'claim_id': 'date', 'question': 'Which day is it?'},
        {'claim_id': 'time', 'question': 'What time is it?'}],
        'reviews': [{'claim_id': i, 'safe': True, 'reason': 'No answer values.'} for i in ('date', 'time')]}
    return {**body, 'plan_hash': digest(body)}


def turn(question='What is the plan?', answer='Not sure.', calls=(), results=(), tokens=(100, 20), sid='recall'):
    at = '2026-09-11T09:00:00-04:00'
    prompt = f'[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{question}'
    return TurnResult(prompt, at, answer, list(calls), list(results), 0, 1.25, 0, '', 2,
                      session_id=sid, session_input_tokens=tokens[0], session_output_tokens=tokens[1])


def grade(verdicts=('missed', 'misrepresented')):
    return Grade('checkpoint', slots=[GradedClaim(i, v, 'reason') for i, v in zip(('date', 'time'), verdicts)],
                 correct=verdicts.count('correct'), missed=verdicts.count('missed'),
                 misrepresented=verdicts.count('misrepresented'))


def judge(prompt):
    # Single target only, so the follow-up cannot silently regrade other facts.
    cid = 'date' if '"id": "date"' in prompt else 'time'
    return json.dumps({'verdicts': [{'id': cid, 'verdict': 'correct', 'reason': 'Answered detail.'}]})


def test_exactly_one_try_per_failed_fact_preserves_penalty_and_session(tmp_path):
    calls = []
    def runner(question, **kwargs):
        calls.append((question, kwargs))
        return turn(question, 'Recovered answer', tokens=(100 + 30 * len(calls), 20 + 10 * len(calls)))
    initial, original_grade = turn(), grade()
    report = run_followups(contract=contract(), plan=plan(), grade=original_grade, initial_turn=initial,
        output=tmp_path, judge=judge, home=tmp_path, allowlist='memory', extra_env={},
        run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert len(calls) == 2
    assert [kwargs['session_id'] for _, kwargs in calls] == ['recall', 'recall']
    assert original_grade.correct == 0  # no replacement of first-answer judgment
    assert report['score']['points'] == 1.0  # two half-credit recoveries, not two perfect answers
    assert [a['metrics']['input_tokens'] for a in report['attempts']] == [30, 30]
    assert 'Friday' not in str(calls) and 'seven' not in str(calls)
    assert audit_followups(contract=contract(), plan=plan(), initial_turn=initial,
        initial_grade={'claims': [asdict(s) for s in original_grade.slots],
                       'correct': 0, 'missed': 1, 'misrepresented': 1}, output=tmp_path) == []
    with pytest.raises(FileExistsError):
        run_followups(contract=contract(), plan=plan(), grade=original_grade, initial_turn=initial,
            output=tmp_path, judge=judge, home=tmp_path, allowlist='memory', extra_env={},
            run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert len(calls) == 2
    data = json.loads((tmp_path / 'recovery.json').read_text())
    data['attempts'].append(data['attempts'][0])
    (tmp_path / 'recovery.json').write_text(json.dumps(data))
    assert any('exactly one' in e for e in audit_followups(contract=contract(), plan=plan(),
        initial_turn=initial, initial_grade={'claims': [asdict(s) for s in original_grade.slots],
                       'correct': 0, 'missed': 1, 'misrepresented': 1}, output=tmp_path))


def test_correct_fact_gets_no_followup_and_failed_recovery_is_not_reasked(tmp_path):
    questions = []
    def runner(question, **kwargs):
        questions.append(question)
        return turn(question, '')
    report = run_followups(contract=contract(), plan=plan(), grade=grade(('correct', 'missed')),
        initial_turn=turn(), output=tmp_path, judge=judge, home=tmp_path, allowlist='',
        extra_env={}, run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert questions == ['What time is it?']
    assert report['state'] == 'incomplete'
    assert report['score']['points'] == 1.0
    assert (tmp_path / 'fact-2/turn.json').exists()


@pytest.mark.parametrize('name,arguments,category,points', [
    ('memcal_open', {'ref': 'E1'}, 'memory_detail', .5),
    ('memcal_search_archive', {'query': 'dinner'}, 'archive_search', 0),
    ('memcal_open_source', {'source': 'E1'}, 'source_evidence', 0),
    ('mcp__imessage__search', {'query': 'dinner'}, 'source_messages', 0),
    ('new_unknown_tool', {}, 'unclassified', 0),
])
def test_correct_answer_from_source_fallback_still_fails_retention(name, arguments, category, points):
    metrics = turn_metrics(turn(calls=[{'name': name, 'input': arguments}]))
    assert metrics['categories'] == {category: 1}
    result = recovery_score(grade(('correct', 'missed')), turn_metrics(turn()), [
        {'claim_id': 'time', 'state': 'graded', 'verdict': 'correct', 'metrics': metrics}])
    assert result['claims'][1]['points'] == points
    assert result['claims'][1]['recovered_answer'] is True
    assert result['claims'][1]['retention_failed'] is (category in {'archive_search', 'source_evidence', 'source_messages'})


def test_discovery_dispatch_nested_search_and_failures_are_logged_without_losing_attempts():
    t = turn(calls=[{'name': 'tool_search', 'input': {'query': 'memory'}},
        {'name': 'tool_call', 'input': {'calls': [
            {'name': 'memcal_open', 'arguments': {'ref': 'E1'}},
            {'name': 'memcal_search_archive', 'arguments': {'query': 'dinner'}}]}},
        {'name': 'memcal_search_archive', 'input': {'query': 'dinner'}}],
        results=[{'success': True}, {'success': True, 'text': '{"results":[{}, {"error":"blocked"}]}'}])
    metrics = tool_profile(t)
    assert metrics['top_level_calls'] == 3 and metrics['total_tool_operations'] == 5
    assert metrics['source_fallback_attempted'] is True
    assert metrics['failed_operations'] == 1 and metrics['repeated_operations'] == 1
    assert metrics['operations'][-1]['success'] is None  # missing result isn't fake success


def test_unknown_usage_is_not_zero_and_project_windows_do_not_claim_tool_prices():
    assert turn_metrics(turn(tokens=(None, None)))['input_tokens'] is None
    assert turn_metrics(turn(tokens=(99, 21)), turn())['input_tokens'] is None
    assert turn_metrics(turn(sid='wrong'), turn())['output_tokens'] is None
    window = cost_window({'projects': [{'project': 'a', 'settled_upper_bound_usd': .1, 'held_usd': .2}]},
        {'projects': [{'project': 'a', 'settled_upper_bound_usd': .12, 'held_usd': 0}]})
    assert window['projects'][0]['held_usd_delta'] == -.2
    assert 'not tool prices' in window['scope']


def test_leading_question_rejected_and_saved_plan_cannot_change(tmp_path):
    outputs = iter([{'questions': plan()['questions']}, {'reviews': [
        {'claim_id': i, 'safe': False, 'reason': 'Leading question.'} for i in ('date', 'time')]}])
    with pytest.raises(ValueError, match='neutral-question review'):
        freeze_questions(contract(), lambda _: json.dumps(next(outputs)), tmp_path / 'plan.json')
    assert not (tmp_path / 'plan.json').exists()
    p = plan()
    (tmp_path / 'plan.json').write_text(json.dumps(p))
    assert load_questions(tmp_path / 'plan.json', contract()) == p
    p['questions'][0]['question'] = 'Friday, right?'
    (tmp_path / 'plan.json').write_text(json.dumps(p))
    with pytest.raises(ValueError, match='hash/contract'):
        load_questions(tmp_path / 'plan.json', contract())


def test_resumed_turn_recovers_only_its_full_tool_results(tmp_path):
    import sqlite3
    from pacbench.harness import _recover_tool_results
    db_path = tmp_path / 'state.db'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, tool_name TEXT, content TEXT)')
        db.execute("INSERT INTO messages VALUES (1,'recall','tool','memcal_list_days','old result')")
        db.execute("INSERT INTO messages VALUES (2,'recall','tool','memcal_open',?)", ('full detail ' + 'x' * 6000,))
    t = turn(results=[{'tool': 'memcal_open', 'text': 'clipped', 'truncated': True}])
    _recover_tool_results(t, db_path)
    assert t.tool_results[0]['text'] == 'full detail ' + 'x' * 6000
    assert t.tool_results[0]['capture'] == 'hermes_session_db'


def test_timeout_preserves_tool_attempts_and_errors(tmp_path, monkeypatch):
    import subprocess
    from pacbench import harness
    monkeypatch.setattr(harness, '_check_paths', lambda _: None)
    monkeypatch.setattr(harness, 'turn_env', lambda *args: {})
    def timeout(*args, **kwargs):
        events = [{'type': 'tool_use', 'name': 'memcal_search_archive', 'input': {'query': 'plan'}, 'session_id': 'recall'}]
        raise subprocess.TimeoutExpired('hermes', 1, output=('\n'.join(json.dumps(e) for e in events)).encode())
    monkeypatch.setattr(harness.subprocess, 'run', timeout)
    t = harness.run_turn('Which street?', at='2026-09-11T09:00:00-04:00', home=tmp_path, allowlist='', timeout_s=1)
    assert t.returncode == -1
    assert t.tool_calls[0]['name'] == 'memcal_search_archive'
    assert turn_metrics(t)['source_fallback_attempted'] is True
    assert t.session_input_tokens is None
