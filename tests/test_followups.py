"""Offline follow-up policy regressions; synthetic outputs are not provider evidence."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from pacbench.followups import (
    audit_followups, combined_plan, cost_window, digest, freeze_policy, load_questions,
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
    return Grade('checkpoint', recovery=[{'id':i,'kind':'provided' if v=='correct' else 'incorrect' if v=='misrepresented' else 'implicit_omission','reason':'Synthetic eligibility.'} for i,v in zip(('date','time'),verdicts)], slots=[GradedClaim(i, v, 'reason') for i, v in zip(('date', 'time'), verdicts)],
                 correct=verdicts.count('correct'), missed=verdicts.count('missed'),
                 misrepresented=verdicts.count('misrepresented'))


def judge(prompt):
    claims = json.loads(prompt.split('Claims (hidden from the assistant): ')[1])
    return json.dumps({'verdicts': [{'id': c['id'], 'verdict': 'correct', 'reason': 'Answered detail.'}
                                  for c in claims]})


def question_writer(prompt):
    if prompt.startswith('Write '):
        return json.dumps({'question': 'Which day and what time is it?'})
    return json.dumps({'safe': True, 'covers_claim_ids': ['date', 'time'], 'reason': 'No expected values.'})


def test_one_message_covers_all_failed_facts_preserves_penalty_and_session(tmp_path):
    calls = []
    def runner(question, **kwargs):
        calls.append((question, kwargs))
        return turn(question, 'Recovered answer', tokens=(100 + 30 * len(calls), 20 + 10 * len(calls)))
    c = contract()
    c.claims += (SimpleNamespace(id='companion', statement='Devon is going.', evidence=('m1',)),)
    original_grade = grade(('missed', 'missed'))
    original_grade.slots.append(GradedClaim('companion', 'correct', 'Already recalled.'))
    original_grade.correct = 1
    original_grade.recovery.append({'id':'companion','kind':'provided','reason':'Already recalled.'})
    p = freeze_policy(c, tmp_path / 'policy.json')
    initial = turn()
    report = run_followups(contract=c, plan=p, grade=original_grade, initial_turn=initial,
        output=tmp_path, judge=judge, home=tmp_path, allowlist='memory', extra_env={},
        run_turn=runner, budget_snapshot=lambda: {'projects': []}, question_model=question_writer)
    assert len(calls) == 1
    assert calls[0][0] == 'Which day and what time is it?'
    assert calls[0][1]['session_id'] == 'recall'
    assert report['attempts'][0]['claim_ids'] == ['date', 'time']
    assert original_grade.correct == 1  # no replacement of first-answer judgment
    assert report['score']['points'] == 2.0  # two half-credit recoveries, not two perfect answers
    assert [a['metrics']['input_tokens'] for a in report['attempts']] == [30]
    assert 'Friday' not in str(calls) and 'seven' not in str(calls)
    assert audit_followups(contract=c, plan=p, initial_turn=initial,
        initial_grade={'claims': [asdict(s) for s in original_grade.slots],
                       'correct': 1, 'missed': 2, 'misrepresented': 0, 'recovery': original_grade.recovery}, output=tmp_path) == []
    with pytest.raises(FileExistsError):
        run_followups(contract=c, plan=p, grade=original_grade, initial_turn=initial,
            output=tmp_path, judge=judge, home=tmp_path, allowlist='memory', extra_env={},
            run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert len(calls) == 1
    data = json.loads((tmp_path / 'recovery.json').read_text())
    data['attempts'].append(data['attempts'][0])
    (tmp_path / 'recovery.json').write_text(json.dumps(data))
    assert any('exactly one' in e for e in audit_followups(contract=c, plan=p,
        initial_turn=initial, initial_grade={'claims': [asdict(s) for s in original_grade.slots],
                       'correct': 1, 'missed': 2, 'misrepresented': 0, 'recovery': original_grade.recovery}, output=tmp_path))


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
    assert (tmp_path / 'combined/turn.json').exists()


def test_three_missing_facts_use_one_spark_message_and_independent_partial_credit(tmp_path):
    c = contract()
    c.claims += (SimpleNamespace(id='venue', statement='It is at the diner.', evidence=('m1',)),)
    p = plan()
    p['questions'].append({'claim_id': 'venue', 'question': 'Where is it?'})
    p['reviews'].append({'claim_id': 'venue', 'safe': True, 'reason': 'No expected value.'})
    p['plan_hash'] = digest({k: v for k, v in p.items() if k != 'plan_hash'})
    c.claims += (SimpleNamespace(id='companion', statement='Devon is going.', evidence=('m1',)),)
    p = freeze_policy(c, tmp_path / 'policy.json')
    g = Grade('test', slots=[GradedClaim(i, 'missed', 'Not stated.') for i in ('date', 'time', 'venue')] +
              [GradedClaim('companion', 'correct', 'Already recalled.')], correct=1, missed=3)
    g.recovery = [{'id':slot.id,'kind':'provided' if slot.verdict=='correct' else 'implicit_omission','reason':'Synthetic eligibility.'} for slot in g.slots]
    questions, writer_calls = [], []
    def writer(prompt):
        writer_calls.append(prompt)
        return json.dumps({'question': 'Which day, what time, and where is it?'} if prompt.startswith('Write ')
                          else {'safe': True, 'covers_claim_ids': ['date', 'time', 'venue'], 'reason': 'Neutral and complete.'})
    def runner(question, **kwargs):
        questions.append(question)
        return turn(question, 'Day and place, but no time.')
    def partial_judge(prompt):
        return json.dumps({'verdicts': [{'id': i, 'verdict': v, 'reason': 'Details assessed separately.'}
            for i, v in zip(('date', 'time', 'venue'), ('correct', 'missed', 'correct'))]})
    report = run_followups(contract=c, plan=p, grade=g, initial_turn=turn(), output=tmp_path / 'a',
        judge=partial_judge, home=tmp_path, allowlist='', extra_env={}, run_turn=runner,
        budget_snapshot=lambda: {'projects': []}, question_model=writer, question_dir=tmp_path / 'shared')
    assert questions == ['Which day, what time, and where is it?']
    assert len(writer_calls) == 2  # one Spark author call and one independent safety review
    assert report['score']['points'] == 2.0
    assert [s['points'] for s in report['score']['claims']] == [.5, 0, .5, 1]
    second = run_followups(contract=c, plan=p, grade=g, initial_turn=turn(), output=tmp_path / 'b',
        judge=partial_judge, home=tmp_path, allowlist='', extra_env={}, run_turn=runner,
        budget_snapshot=lambda: {'projects': []}, question_model=writer, question_dir=tmp_path / 'shared')
    assert len(writer_calls) == 2  # same failed set reuses the same frozen Spark question
    assert second['attempts'][0]['question_hash'] == report['attempts'][0]['question_hash']


def test_all_correct_facts_get_no_recovery_message(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('No follow-up should run')
    result = run_followups(contract=contract(), plan=plan(), grade=grade(('correct', 'correct')),
        initial_turn=turn(), output=tmp_path, judge=forbidden, home=tmp_path, allowlist='', extra_env={},
        run_turn=forbidden, budget_snapshot=forbidden, question_model=forbidden)
    assert result['attempts'] == [] and result['score']['points'] == 2.0


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
    policy = freeze_policy(contract(), tmp_path / 'policy.json')
    outputs = iter([{'question': 'Friday at seven, right?'},
                    {'safe': False, 'covers_claim_ids': ['date', 'time'], 'reason': 'Leading question.'}])
    with pytest.raises(ValueError, match='neutral/complete coverage review'):
        combined_plan(contract(), policy, list(contract().claims),
                      lambda _: json.dumps(next(outputs)), tmp_path / 'combined')
    assert list((tmp_path / 'combined').glob('*.rejected.json'))
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


@pytest.mark.parametrize('verdicts', [('missed', 'missed'), ('misrepresented', 'misrepresented'), ('missed', 'misrepresented')])
def test_zero_correct_facts_skip_all_recovery_work_and_are_auditable(tmp_path, verdicts):
    def forbidden(*args, **kwargs):
        raise AssertionError('Zero recall must not author, judge, budget, or run a recovery turn')
    c, initial, g = contract(), turn(), grade(verdicts)
    p = freeze_policy(c, tmp_path / 'policy.json')
    output = tmp_path / 'followups'
    result = run_followups(contract=c, plan=p, grade=g, initial_turn=initial,
        output=output, judge=forbidden, home=tmp_path, allowlist='', extra_env={},
        run_turn=forbidden, question_model=forbidden, budget_snapshot=forbidden)
    assert result['state'] == 'complete'
    assert result['skip_reason'] == 'no_initial_correct_facts'
    assert result['attempts'] == [] and result['score']['points'] == 0
    assert not (output / 'combined').exists() and not (output / 'question-plans').exists()
    initial_grade = {'claims': [asdict(s) for s in g.slots], 'correct': g.correct,
                     'missed': g.missed, 'misrepresented': g.misrepresented, 'recovery': g.recovery}
    assert audit_followups(contract=c, plan=p, initial_turn=initial,
        initial_grade=initial_grade, output=output) == []
    result['skip_reason'] = 'all_correct'
    (output / 'recovery.json').write_text(json.dumps(result))
    assert any('skip reason' in e for e in audit_followups(contract=c, plan=p,
        initial_turn=initial, initial_grade=initial_grade, output=output))
