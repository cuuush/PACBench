"""Offline follow-up policy regressions; synthetic outputs are not provider evidence."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from pacbench.followups import (
    audit_followups, combined_plan, cost_window, digest, freeze_policy, load_questions,
    recovery_score, run_followups, tool_profile, turn_metrics, initial_effort,
)
from pacbench.harness import TurnResult
from pacbench.semantic_grade import Grade, GradedClaim


def contract():
    return SimpleNamespace(contract_hash='bound-contract', question='What is the plan?', claims=(
        SimpleNamespace(id='date', statement='It is Friday.', evidence=('m1',)),
        SimpleNamespace(id='time', statement='It is at seven.', evidence=('m1',))))


def plan():
    body = {'protocol_version': 3, 'contract_hash': 'bound-contract',
            'authoring_revision': 'specific-missing-facts-v3', 'minimum_initial_correct': 1,
            'eligibility_policy': 'natural-omissions-v1', 'claim_ids': ['date', 'time'],
            'policy': 'one combined Spark-authored clarification for silent implicit omissions only'}
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


@pytest.mark.parametrize('judge_protocol', ['semantic-judge-v1', 'semantic-judge-date-scope-v2'])
@pytest.mark.parametrize('clock_version', [None, 'honcho-wall-clock-v5'])
def test_one_message_covers_all_failed_facts_preserves_penalty_and_session(tmp_path, judge_protocol, clock_version):
    calls = []
    def runner(question, **kwargs):
        calls.append((question, kwargs))
        value=turn(question, 'Recovered answer', tokens=(100 + 30 * len(calls), 20 + 10 * len(calls)))
        if clock_version:
            requested=value.at
            value.at='2026-09-11T09:01:00-04:00'
            value.prompt=value.prompt.replace(requested,value.at)
            value.provider_clock={'version':clock_version,'role':'recovery','requested_at':requested}
        return value
    c = contract()
    c.claims += (SimpleNamespace(id='companion', statement='Devon is going.', evidence=('m1',)),)
    original_grade = grade(('missed', 'missed'))
    original_grade.slots.append(GradedClaim('companion', 'correct', 'Already recalled.'))
    original_grade.correct = 1
    original_grade.recovery.append({'id':'companion','kind':'provided','reason':'Already recalled.'})
    p = freeze_policy(c, tmp_path / 'policy.json')
    initial = turn()
    def recorded_judge(prompt):
        text = judge(prompt)
        if judge_protocol == 'semantic-judge-date-scope-v2':
            from pacbench.model_calls import ModelOutput
            calls_dir = tmp_path/'model-calls'; calls_dir.mkdir(exist_ok=True)
            path = calls_dir/'judge.json'
            path.write_text(json.dumps({'status':'ok','text':text,'request':{'messages':[{'role':'user','content':prompt}]}}))
            return ModelOutput(text,'model',{},str(path))
        return text
    report = run_followups(contract=c, plan=p, grade=original_grade, initial_turn=initial,
        output=tmp_path, judge=recorded_judge, home=tmp_path, allowlist='memory', extra_env={},
        run_turn=runner, budget_snapshot=lambda: {'projects': []}, question_model=question_writer, judge_protocol=judge_protocol)
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
                       'correct': 1, 'missed': 2, 'misrepresented': 0, 'recovery': original_grade.recovery}, output=tmp_path, judge_protocol=judge_protocol) == []
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
                       'correct': 1, 'missed': 2, 'misrepresented': 0, 'recovery': original_grade.recovery}, output=tmp_path, judge_protocol=judge_protocol))


def test_correct_fact_gets_no_followup_and_failed_recovery_is_not_reasked(tmp_path):
    questions = []
    def runner(question, **kwargs):
        questions.append(question)
        return turn(question, '')
    report = run_followups(contract=contract(), plan=plan(), grade=grade(('correct', 'missed')),
        initial_turn=turn(), output=tmp_path, judge=judge, home=tmp_path, allowlist='',
        extra_env={}, run_turn=runner, budget_snapshot=lambda: {'projects': []},
        question_model=lambda prompt: json.dumps({'question':'What time is it?'} if prompt.startswith('Write ') else
            {'safe':True,'covers_claim_ids':['time'],'reason':'Asks for the missing time.'}))
    assert questions == ['What time is it?']
    assert report['state'] == 'incomplete'
    assert report['score']['points'] == 1.0
    assert (tmp_path / 'combined/turn.json').exists()


def test_three_missing_facts_use_one_spark_message_and_independent_partial_credit(tmp_path):
    c = contract()
    c.claims += (SimpleNamespace(id='venue', statement='It is at the diner.', evidence=('m1',)),)
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
    ('brv_query', {'query': 'dinner'}, 'memory_search', .5),
    ('memcal_search_archive', {'query': 'dinner'}, 'archive_search', 0),
    ('memcal_open_source', {'source': 'E1'}, 'source_evidence', 0),
    ('mcp__imessage__search', {'query': 'dinner'}, 'source_messages', 0),
    ('new_unknown_tool', {}, 'unclassified', 0),
])
def test_correct_answer_from_source_fallback_still_fails_retention(name, arguments, category, points):
    metrics = turn_metrics(turn(calls=[{'name': name, 'input': arguments}]))
    assert metrics['categories'] == {category: 1}
    result = recovery_score(grade(('correct', 'missed')), turn_metrics(turn()), [
        {'claim_ids': ['time'], 'state': 'graded', 'verdicts':[{'id':'time','verdict':'correct'}], 'metrics': metrics}])
    assert result['claims'][1]['points'] == points
    assert result['claims'][0]['points'] == 1
    assert result['claims'][1]['recovered_answer'] is True
    assert result['claims'][1]['retention_failed'] is (category in {'archive_search', 'source_evidence', 'source_messages'})


def test_source_followup_penalty_carries_to_dependent_turn():
    from pacbench.native_checks import extend_path
    source = turn(calls=[{'name':'memcal_open_source','input':{'source':'E1'}}])
    path = extend_path({'source_fallback':False, 'unclassified':False}, source)
    inherited = initial_effort(turn(), inherited_path=path)
    assert recovery_score(grade(('correct','missed')), inherited, [])['claims'][0]['points'] == 0


def test_memcal_open_with_attached_citations_keeps_credit():
    opened=turn(calls=[{'name':'memcal_open','input':{'ref':'E1'}}],
        results=[{'success':True,'text':'Retained event details\n\ncited sources:\noriginal message'}])
    metrics=turn_metrics(opened, provenance_version='source-provenance-v2')
    assert recovery_score(grade(('correct','missed')), metrics, [])['claims'][0]['points'] == 1


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


@pytest.mark.parametrize('batch', [False, True])
def test_viking_generated_summary_requires_matching_native_result(batch):
    uris = ['viking://resources/day-1', 'viking://resources/day-2'] if batch else ['viking://resources/day-1']
    args = {'uris': uris} if batch else {'uri': uris[0]}
    rows = [{'uri': uri, 'resolved_uri': uri, 'level': 'overview', 'content': 'Generated summary'} for uri in uris]
    payload = {'level': 'overview', 'results': rows, 'requested': 2, 'returned': 2, 'truncated': False} if batch else rows[0]
    def profile(value, success=True):
        return tool_profile(turn(calls=[{'name': 'viking_read', 'input': args}],
            results=[{'success': success, 'text': json.dumps(value)}]))
    assert profile(payload)['categories'] == {'memory_detail': 1}
    assert profile(payload, False)['source_fallback_attempted']
    for field, value in [('fallback', 'content/read'), ('error', 'failed'),
                         ('uri', 'viking://resources/other'), ('resolved_uri', 'wrong'),
                         ('level', 'full'), ('content', None)]:
        broken = json.loads(json.dumps(payload))
        (broken['results'][0] if batch else broken)[field] = value
        assert profile(broken)['source_fallback_attempted']
    assert profile({})['source_fallback_attempted']
    args['level'] = 'full'
    assert profile(payload)['source_fallback_attempted']


def test_viking_summary_receipt_handles_native_pseudo_files_and_incomplete_capture():
    from pacbench.source_policy import proven_viking_summary
    args = {'uri': 'viking://resources/day/.abstract.md', 'level': 'overview'}
    payload = {'uri': args['uri'], 'resolved_uri': 'viking://resources/day',
               'level': 'overview', 'content': 'A generated overview'}
    result = {'success': True, 'text': json.dumps(payload)}
    assert proven_viking_summary(args, result)
    assert not proven_viking_summary(args, {**result, 'truncated': True})
    assert not proven_viking_summary(args, {**result, 'text': 'not json'})
    window = cost_window({'projects': [{'project': 'a', 'settled_upper_bound_usd': .1, 'held_usd': .2}]},
        {'projects': [{'project': 'a', 'settled_upper_bound_usd': .12, 'held_usd': 0}]})
    assert window['projects'][0]['held_usd_delta'] == -.2
    assert 'not tool prices' in window['scope']


def test_leading_question_rejected_and_saved_plan_cannot_change(tmp_path):
    policy = freeze_policy(contract(), tmp_path / 'policy.json')
    outputs = iter([{'question': 'Friday at seven, right?'},
                    {'safe': False, 'covers_claim_ids': ['date', 'time'], 'reason': 'Leading question.'}] * 2)
    with pytest.raises(ValueError, match='neutral/complete coverage review'):
        combined_plan(contract(), policy, list(contract().claims),
                      lambda _: json.dumps(next(outputs)), tmp_path / 'combined')
    assert list((tmp_path / 'combined').glob('*.rejected.json'))
    p = plan()
    (tmp_path / 'plan.json').write_text(json.dumps(p))
    assert load_questions(tmp_path / 'plan.json', contract()) == p
    p['claim_ids'][0] = 'changed'
    (tmp_path / 'plan.json').write_text(json.dumps(p))
    with pytest.raises(ValueError, match='hash/contract'):
        load_questions(tmp_path / 'plan.json', contract())


@pytest.mark.parametrize('coverage, accepted', [
    (['time', 'date'], True), (['date', 'date'], False),
    (['date'], False), (['date', 'time', 'extra'], False), ('date,time', False),
])
def test_combined_review_coverage_is_complete_and_unique_regardless_of_order(tmp_path, coverage, accepted):
    from pacbench.followups import load_combined_plan
    c=contract();policy=freeze_policy(c,tmp_path/'policy.json')
    replies=iter([{'question':'Which day and what time?'},
                  {'safe':True,'covers_claim_ids':coverage,'reason':'Synthetic coverage review.'}] * 2)
    write=lambda _: json.dumps(next(replies))
    if not accepted:
        with pytest.raises(ValueError,match='neutral/complete coverage review'):
            combined_plan(c,policy,list(c.claims),write,tmp_path/'questions')
        return
    result=combined_plan(c,policy,list(c.claims),write,tmp_path/'questions')
    binding={k:result[k] for k in ('contract_hash','component_plan_hash','claim_ids')}
    path=next((tmp_path/'questions').glob('*.json'))
    assert load_combined_plan(path,binding)==result
    assert result['claim_ids']==['date','time'] and result['review']['covers_claim_ids']==coverage


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
    from contextlib import nullcontext
    from pacbench import harness, live_trials, hermes_gateway
    monkeypatch.setattr(live_trials, 'require_live_trials_ready', lambda: None)
    monkeypatch.setattr(hermes_gateway, 'turn_gateway', lambda *a, **kw: nullcontext({'state': 'mock'}))
    monkeypatch.setattr(harness, '_check_paths', lambda _: None)
    monkeypatch.setattr(harness, 'turn_env', lambda *args: {})
    def timeout(*args, **kwargs):
        events = [{'type': 'tool_use', 'name': 'memcal_search_archive', 'input': {'query': 'plan'}, 'session_id': 'recall'}]
        raise subprocess.TimeoutExpired('hermes', 1, output=('\n'.join(json.dumps(e) for e in events)).encode())
    monkeypatch.setattr(harness.subprocess, 'run', timeout)
    t = harness.run_turn('Which street?', at='2026-09-11T09:00:00-04:00', home=tmp_path, allowlist='', timeout_s=1)
    assert t.returncode == -1
    assert t.timed_out is True and t.wall_deadline_s==1
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


def test_rejected_draft_gets_one_revision_before_one_agent_followup(tmp_path):
    c=contract()
    c.claims += (SimpleNamespace(id='companion',statement='Devon is going.',evidence=('m1',)),)
    g=grade(('missed','missed'))
    g.slots.append(GradedClaim('companion','correct','Already recalled.'))
    g.correct=1
    g.recovery.append({'id':'companion','kind':'provided','reason':'Already recalled.'})
    replies=iter([{'question':'Friday at seven, right?'},
        {'safe':False,'covers_claim_ids':[],'reason':'Leading question.'},
        {'question':'Which day and what time?'},
        {'safe':True,'covers_claim_ids':['date','time'],'reason':'Neutral and complete.'}])
    prompts,questions=[],[]
    def writer(prompt):
        prompts.append(prompt)
        return json.dumps(next(replies))
    def runner(question,**kwargs):
        questions.append(question)
        return turn(question,'Recovered answer')
    result=run_followups(contract=c,plan=freeze_policy(c,tmp_path/'policy.json'),grade=g,
        initial_turn=turn(),output=tmp_path/'followups',judge=judge,home=tmp_path,
        allowlist='',extra_env={},run_turn=runner,budget_snapshot=lambda:{'projects':[]},
        question_model=writer,question_dir=tmp_path/'shared')
    assert questions==['Which day and what time?']
    assert len(prompts)==4 and 'Leading question.' in prompts[2]
    assert result['state']=='complete' and result['score']['points']==2
    assert g.correct==1
    rejected=json.loads(next((tmp_path/'shared').glob('*.rejected.json')).read_text())
    assert len(rejected['drafts'])==1


def test_recovery_uses_actual_answer_and_reuses_matching_missing_fact_sets(tmp_path):
    c=contract();p=freeze_policy(c,tmp_path/'policy.json');prompts=[]
    def model(prompt):
        prompts.append(prompt)
        if 'Proposed message:' in prompt:
            return json.dumps({'safe':True,'covers_claim_ids':['time'],'reason':'Known Friday outing, missing time.'})
        return json.dumps({'question':'What time is the Friday pizza with Maya?'})
    directory=tmp_path/'questions'
    first=combined_plan(c,p,[c.claims[1]],model,directory,known_context=['Friday pizza with Maya.'])
    assert all('Actual answer: ["Friday pizza with Maya."]' in s for s in prompts)
    combined_plan(c,p,[c.claims[1]],model,directory,known_context=['Friday pizza with Maya.'])
    assert len(prompts)==2
    second=combined_plan(c,p,[c.claims[1]],model,directory,known_context=['You have pizza with Maya on Friday.'])
    assert len(prompts)==2 and first['question_hash']==second['question_hash']
    assert first['known_context']==['Friday pizza with Maya.'] and first['context_source']=='actual-initial-answer'


def test_failed_checkpoint_serialization_preserves_last_complete_state(tmp_path):
    from pacbench.followups import save
    file=tmp_path/'summary.json';save(file,{'state':'running','completed':4})
    with pytest.raises(TypeError):save(file,{'state':object()})
    assert json.loads(file.read_text())=={'state':'running','completed':4}
    assert list(tmp_path.iterdir())==[file]


def test_parallel_arms_author_one_neutral_question_for_the_same_missing_facts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    c=contract();policy=freeze_policy(c,tmp_path/'policy.json');calls=[]
    def model(prompt):
        calls.append(prompt);time.sleep(.01)
        if 'Proposed message:' in prompt:
            return json.dumps({'safe':True,'covers_claim_ids':['time'],'reason':'Asks for omitted time.'})
        return json.dumps({'question':'What time is it?'})
    states=[{},{}]
    def run(index):
        return combined_plan(c,policy,[c.claims[1]],model,tmp_path/'shared',
            known_context=['It is Friday.'],cache_state=states[index])
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,(0,1)))
    assert len(calls)==2 and results[0]['question_hash']==results[1]['question_hash']
    assert sorted(s['reused'] for s in states)==[False,True]


@pytest.mark.parametrize('text,changes,expected',[
    ('## Representation\n## Explicit Observations\nRetained fact.',{},True),
    ('## Summary\nGenerated summary.',{},True),
    ('## Card\nStanding fact.',{},True),
    ('No context available yet.',{},True),
    ('## Representation\nFact.\n\n## Recent messages\n[user] Raw message.',{},False),
    ('Raw message without the native formatter.',{},False),
    ('## Card\nFact.',{'truncated':True},False),
    ('## Card\nFact.',{'success':False},False),
])
def test_honcho_retained_snapshot_and_raw_history_are_distinct(text,changes,expected):
    from pacbench.source_policy import proven_honcho_snapshot
    receipt={'success':True,'policy_blocked':False,'truncated':False,'text':json.dumps({'result':text}),**changes}
    assert proven_honcho_snapshot(receipt)==expected


@pytest.mark.parametrize('history',[False,True])
def test_retained_honcho_context_keeps_credit_but_included_history_is_source_access(history):
    text='## Representation\nRetained observation.'
    if history:text+='\n\n## Recent messages\n[user] Original source.'
    result={'tool':'honcho_context','success':True,'policy_blocked':False,'truncated':False,
            'text':json.dumps({'result':text})}
    profile=tool_profile(turn(calls=[{'name':'honcho_context','input':{}}],results=[result]))
    assert profile['source_fallback_attempted']==history
    assert profile['operations'][0]['category']==('source_messages' if history else 'memory_detail')
