"""Synthetic sequence integrity tests, not memory-provider performance evidence."""
import json
from types import SimpleNamespace

import pytest

from pacbench.evaluation import resolve_checks
from pacbench.followups import freeze_policy
from pacbench.harness import TurnResult
from pacbench.native_checks import audit_checks, run_checks
from pacbench.semantic_contract import ContractClaim, EvaluationContract, build_contract


@pytest.mark.parametrize('required', ['first', ['missing'], ['first','first'], ['second'], [None]])
def test_authored_prerequisites_reject_unknown_forward_duplicate_or_invalid_ids(required):
    rows=[{'id':'first','prompt':'Remember this.','expect':'Acknowledge.'},
          {'id':'second','prompt':'What changed?','expect':'The earlier correction.','requires':required}]
    with pytest.raises(ValueError,match='Required turns'):
        resolve_checks({'checks':rows},'2026-10-22T10:00:00-04:00','America/New_York')


@pytest.mark.parametrize('change', ['unchanged', 'source', 'prompt', 'expect', 'at', 'facts', 'corruption', 'authoring_version'])
def test_resume_reuses_only_unchanged_hash_verified_contracts(tmp_path, change):
    from pacbench.evaluation import freeze_evaluations
    observations = [{'source_id':'m1', 'written_at':'2026-09-10T18:00:00-04:00',
                     'collected_at':'2026-09-10T18:00:00-04:00', 'sender':'Me', 'text':'Breakfast at the cafe.'}]
    check = {'id':'breakfast', 'at':'2026-09-11T09:00:00-04:00',
             'prompt':'What about breakfast?', 'expect':'Breakfast is at the cafe.', 'facts':[]}
    resolved = {'checks':[check], 'evaluation_context':'shared'}
    calls = []
    def model(prompt):
        calls.append(prompt)
        if 'Check this evaluation prompt' in prompt:
            return '{"safe":true,"faithful":true,"elicits_all":true,"reason":"Synthetic review"}'
        if 'Review each proposed claim' in prompt:
            return '{"complete":true,"reviews":[{"id":"fact-1","supported":true,"relevant":true,"unambiguous":true,"reason":"Synthetic review"}]}'
        return '{"claims":[{"id":"fact-1","statement":"Breakfast is at the cafe.","evidence":["m1"]}]}'
    first = freeze_evaluations(resolved, observations, None, model, tmp_path)
    assert not first.get('errors') and len(calls)==3
    if change=='source': observations[0]['text']+=' Bring a sweater.'
    elif change in ('prompt','expect'): check[change]+=' Please.'
    elif change=='at': check['at']='2026-09-11T10:00:00-04:00'
    elif change=='facts': check['facts']=['Breakfast is at the cafe.']
    elif change=='corruption':
        path=tmp_path/'evaluations/breakfast/contract.json'
        path.write_text(path.read_text().replace('Breakfast is', 'Lunch is'))
    elif change=='authoring_version':
        from pacbench.followups import digest
        path=tmp_path/'evaluations/breakfast/contract.json'
        old=json.loads(path.read_text());old.pop('contract_hash');old['authoring_version']='old'
        path.write_text(json.dumps({**old,'contract_hash':digest(old)}))
    calls.clear()
    second = freeze_evaluations(resolved, observations, None, model, tmp_path, resume=True)
    assert not second.get('errors')
    assert len(calls)==(0 if change=='unchanged' else 3)


def test_frozen_contract_keeps_authored_prerequisites_even_without_conditional_citations(tmp_path):
    from pacbench.evaluation import freeze_evaluations,load_evaluations
    from pacbench.followups import digest,save
    at='2026-10-22T10:00:00-04:00'
    rows=[{'id':'first','session':'early','prompt':'Remember breakfast is at the cafe.','expect':'Breakfast is at the cafe.'},
          {'id':'later','session':'fresh','prompt':'Where is breakfast?','expect':'Breakfast is at the cafe.','requires':['first']}]
    resolved={**resolve_checks({'checks':rows},at,'America/New_York'),
              'question_at':at,'timezone':'America/New_York'}
    observations=[{'source_id':'m1','written_at':at,'collected_at':at,'sender':'Me','text':'Breakfast is at the cafe.'}]
    def model(prompt):
        if 'Check this evaluation prompt' in prompt:return '{"safe":true,"faithful":true,"elicits_all":true,"reason":"Synthetic review"}'
        if 'Review each proposed claim' in prompt:return '{"complete":true,"reviews":[{"id":"fact-1","supported":true,"relevant":true,"unambiguous":true,"reason":"Synthetic review"}]}'
        return '{"claims":[{"id":"fact-1","statement":"Breakfast is at the cafe.","evidence":["m1"]}]}'
    plan=freeze_evaluations(resolved,observations,None,model,tmp_path)
    assert plan['checks'][1]['requires']==['first']
    load_evaluations(tmp_path,observations,resolved)
    plan['checks'][1]['requires']=[]
    plan['plan_hash']=digest({k:v for k,v in plan.items() if k!='plan_hash'})
    save(tmp_path/'evaluation-plan.json',plan)
    with pytest.raises(ValueError,match='dependency mismatch'):load_evaluations(tmp_path,observations,resolved)


def test_english_behavior_uses_exact_citation_aliases_and_reuses_verified_contract(tmp_path):
    from pacbench.evaluation import freeze_evaluations
    at='2026-10-22T10:00:00-04:00'
    row={'id':'correction','session':'morning','kind':'behavior','at':at,
         'prompt':'Actually, Sunday at 11.','expect':'Acknowledge the correction.','facts':[]}
    resolved={'checks':[row],'evaluation_context':'shared'}
    messages=[{'source_id':'m1','written_at':at,'collected_at':at,'sender':'Me','text':'Pickup is Saturday.'}]
    calls=[]
    def model(prompt):
        calls.append(prompt)
        if 'Turn the English expectation' in prompt:return '{"criteria":["Acknowledge the correction."]}'
        assert 's2 |' in prompt and 'assistant-user:correction |' not in prompt
        return '{"complete":true,"reviews":[{"id":"fact-1","supported":true,"relevant":true,"unambiguous":true,"reason":"Observable acknowledgement.","evidence":["s2"]}]}'
    first=freeze_evaluations(resolved,messages,None,model,tmp_path)
    assert not first.get('errors') and len(calls)==2
    body=json.loads((tmp_path/'evaluations/correction/contract.json').read_text())
    assert body['author_fact_count']==0
    assert body['claims'][0]['evidence']==['assistant-user:correction']
    calls.clear()
    assert freeze_evaluations(resolved,messages,None,model,tmp_path,resume=True)==first
    assert calls==[]


def checkpoints(tmp_path):
    result = []
    for i in (1, 2):
        c = EvaluationContract((ContractClaim('fact-1', f'Independent detail {i}.', ('m1',)),),
            f'Tell me about event {i}.', 'input-hash', f'contract-{i}')
        policy = freeze_policy(c, tmp_path / f'policy-{i}.json')
        result.append(({'id': f'event-{i}', 'at': f'2026-09-11T09:0{i}:00-04:00'}, c, policy))
    return result


def correct_judge(prompt):
    claims = json.loads(prompt.split('Claims (hidden from the assistant): ')[-1])
    return json.dumps({'verdicts': [{'id': c['id'], 'verdict': 'correct', 'reason': 'Synthetic harness judgment.'} for c in claims], 'recovery':[{'id':c['id'],'kind':'provided','reason':'Synthetic classification.'} for c in claims]})


@pytest.mark.parametrize('context', ['shared', 'isolated'])
def test_sequence_continuity_and_source_access_cannot_be_laundered(tmp_path, context):
    checks, calls = checkpoints(tmp_path), []
    def runner(prompt, **kwargs):
        calls.append(kwargs)
        index = len(calls)
        sid = kwargs['session_id'] or f'session-{index}'
        tool = [{'name': 'memcal_open_source', 'input': {'source': 'E1'}}] if index == 1 else []
        return TurnResult(prompt=f"[Simulated now: {kwargs['at']}. Treat this as the current date and time for everything in this turn.]\n\n{prompt}",
            at=kwargs['at'], answer='Synthetic current answer.', tool_calls=tool,
            tool_results=[{'tool': 'memcal_open_source', 'success': True, 'text': '{}'}] if tool else [],
            policy_blocks=0, latency_s=1, returncode=0, stderr_tail='', raw_events=0, session_id=sid,
            session_input_tokens=100 * index if context == 'shared' else 100,
            session_output_tokens=20 * index if context == 'shared' else 20)
    report = run_checks(checks, context, tmp_path / 'arm', correct_judge, '', {},
        question_dir=tmp_path / 'questions', run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert report['state'] == 'diagnostic'
    assert calls[0]['session_id'] is None
    assert calls[1]['session_id'] == ('session-1' if context == 'shared' else None)
    assert report['checks'][0]['recovery']['score']['points'] == 0
    assert report['checks'][1]['recovery']['score']['points'] == (0 if context == 'shared' else 1)
    second = json.loads((tmp_path / 'arm/turns/event-2/followups/recovery.json').read_text())
    assert second['initial_metrics']['input_tokens'] == 100
    judgment = json.loads((tmp_path / 'arm/turns/event-2/judgment.json').read_text())
    assert len(judgment['conversation']) == (2 if context == 'shared' else 0)
    assert 'Do not award a fact merely because' in judgment['prompt'] if context == 'shared' else True
    assert audit_checks(checks, context, tmp_path / 'arm') == []
    with pytest.raises(FileExistsError):
        run_checks(checks, context, tmp_path / 'arm', correct_judge, '', {},
            question_dir=tmp_path / 'questions', run_turn=runner)
    path = tmp_path / 'arm/turns/event-2/judgment.json'
    judgment['conversation'] = [{'role': 'assistant', 'content': 'Invented history'}]
    path.write_text(json.dumps(judgment))
    assert any('judge conversation mismatch' in e for e in audit_checks(checks, context, tmp_path / 'arm'))


def test_supplied_prompt_is_not_rewritten_and_nonelicitable_facts_fail_closed(tmp_path):
    observations = [{'source_id': 'm1', 'written_at': '2026-09-10T18:00:00-04:00',
                     'sender': 'Me', 'text': 'Breakfast at the cafe.'}]
    def generator(_):
        return json.dumps({'claims': [{'id': 'fact-1', 'statement': 'Breakfast is at the cafe.', 'evidence': ['m1']}]})
    def reviewer(prompt):
        if 'Check this evaluation prompt' in prompt:
            return json.dumps({'safe': True, 'faithful': True, 'elicits_all': True, 'reason': 'Synthetic prompt review.'})
        return json.dumps({'complete': True, 'reviews': [{'id': 'fact-1', 'supported': True, 'relevant': True, 'unambiguous': True, 'reason': 'Cited.'}]})
    def forbidden(_):
        raise AssertionError('Supplied prompts must not be authored again')
    c = build_contract(scene='Breakfast', test_intent='Recap breakfast', observations=observations,
        author_facts=('Breakfast is at the cafe.',), generate=generator, verify=reviewer,
        question_generate=forbidden, output_path=tmp_path / 'contract.json',
        user_question='Catch me up on breakfast', question_at='2026-09-11T09:00:00-04:00')
    assert c.question == 'Catch me up on breakfast'
    def rejected(prompt):
        return '{"safe":true,"faithful":true,"elicits_all":false,"reason":"Wrong scope"}' if 'Check this evaluation prompt' in prompt else reviewer(prompt)
    with pytest.raises(ValueError, match='fairly elicit'):
        build_contract(scene='Breakfast', test_intent='Breakfast', observations=observations,
            author_facts=('Breakfast is at the cafe.',), generate=generator, verify=rejected,
            question_generate=forbidden, output_path=tmp_path / 'rejected.json', user_question='What about dinner?')
    assert not (tmp_path / 'rejected.json').exists()


def test_arbitrary_prompt_fact_schema_rejects_unsafe_ids_and_backwards_clocks():
    spec = {'checks': [{'id': 'two-events', 'prompt': 'Which one comes first?',
                       'facts': ['The gallery visit is before the train ride.']}]}
    resolved = resolve_checks(spec, '2026-09-11T09:00:00-04:00', 'America/New_York')
    assert resolved['checks'][0]['facts'] == spec['checks'][0]['facts']
    spec['checks'][0]['id'] = '../escape'
    with pytest.raises(ValueError, match='safe id'):
        resolve_checks(spec, '2026-09-11T09:00:00-04:00', 'America/New_York')
    spec['checks'][0].update(id='event', at='2026-09-10T09:00:00-04:00')
    with pytest.raises(ValueError, match='time order'):
        resolve_checks(spec, '2026-09-11T09:00:00-04:00', 'America/New_York')


def test_zero_recall_skips_recovery_but_continues_planned_shared_questions(tmp_path):
    checks, turns, judge_calls = checkpoints(tmp_path), [], []
    def runner(prompt, **kwargs):
        turns.append(kwargs)
        return TurnResult(prompt=f"[Simulated now: {kwargs['at']}. Treat this as the current date and time for everything in this turn.]\n\n{prompt}",
            at=kwargs['at'], answer='Synthetic answer.', tool_calls=[], tool_results=[], policy_blocks=0,
            latency_s=1, returncode=0, stderr_tail='', raw_events=0,
            session_id=kwargs['session_id'] or 'evaluation-session',
            session_input_tokens=100 * len(turns), session_output_tokens=20 * len(turns))
    def judge(prompt):
        judge_calls.append(prompt)
        if len(judge_calls) == 1:
            return json.dumps({'verdicts':[{'id':'fact-1','verdict':'missed','reason':'Synthetic zero recall.'}], 'recovery':[{'id':'fact-1','kind':'implicit_omission','reason':'Synthetic omission.'}]})
        assert len(judge_calls) == 2  # no Spark follow-up author/reviewer or recovery judge
        return correct_judge(prompt)
    root = tmp_path / 'arm'
    report = run_checks(checks, 'shared', root, judge, '', {},
        question_dir=tmp_path / 'questions', run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert report['state'] == 'diagnostic' and len(turns) == len(judge_calls) == 2
    assert turns[1]['session_id'] == 'evaluation-session'
    skipped = json.loads((root / 'turns/event-1/followups/recovery.json').read_text())
    assert skipped['skip_reason'] == 'no_initial_correct_facts' and skipped['attempts'] == []
    assert audit_checks(checks, 'shared', root) == []


def test_evaluation_clock_obeys_eastern_dst_and_rejects_ambiguous_wall_time():
    spec = {'checks': [{'id':'event', 'prompt':'What is the plan?',
        'facts':['The outing is confirmed.'], 'at':'2026-11-01T01:30:00'}]}
    with pytest.raises(ValueError, match='ambiguous local time'):
        resolve_checks(spec, '2026-10-31T09:00:00-04:00', 'America/New_York')
    spec['checks'][0]['at'] = '2026-11-01T01:30:00-05:00'
    assert resolve_checks(spec, '2026-10-31T09:00:00-04:00', 'America/New_York')['checks'][0]['at'] == spec['checks'][0]['at']
    spec['checks'][0]['at'] = '2026-11-02T09:00:00-04:00'
    with pytest.raises(ValueError, match='offset conflicts'):
        resolve_checks(spec, '2026-10-31T09:00:00-04:00', 'America/New_York')


@pytest.mark.parametrize('repair_succeeds',[True,False])
def test_rejected_english_criteria_get_one_independently_reviewed_repair(tmp_path,repair_succeeds):
    observations=[{'source_id':'m1','written_at':'2026-09-10T18:00:00-04:00',
                  'sender':'Me','text':'The outing is cancelled; another day, not picked yet.'}]
    calls=[];reviews=[]
    def generate(prompt):
        calls.append(prompt)
        if len(calls)==2:
            assert 'Revise the rejected draft' in prompt
            assert 'Irrelevant detail' in prompt
        ids=['cancelled'] if len(calls)==2 and repair_succeeds else ['cancelled','incidental']
        return json.dumps({'claims':[{'id':i,'statement':i,'evidence':['m1']} for i in ids]})
    def verify(prompt):
        if 'Check this evaluation prompt' in prompt:
            return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'Question unchanged.'})
        rows=json.loads(prompt.split('Proposed claims: ',1)[1]);reviews.append(rows)
        return json.dumps({'complete':True,'reviews':[{'id':r['id'],'supported':True,
          'relevant':r['id']!='incidental','unambiguous':True,
          'reason':'Irrelevant detail' if r['id']=='incidental' else 'Supported required outcome'} for r in rows]})
    def run():
        return build_contract(scene='An outing',test_intent='The outing was cancelled.',
            observations=observations,generate=generate,verify=verify,
            question_generate=lambda _:pytest.fail('Public question must stay unchanged'),
            user_question='What happened with our outing?',output_path=tmp_path/'contract.json')
    if repair_succeeds:
        result=run();assert [c.id for c in result.claims]==['cancelled']
        assert result.question=='What happened with our outing?'
    else:
        with pytest.raises(ValueError,match='review rejected'):run()
        assert not (tmp_path/'contract.json').exists()
    assert len(calls)==len(reviews)==2


def test_judge_session_fields_follow_execution_context_not_turn_ids():
    from pacbench.evaluation import contract_evidence
    from pacbench.semantic_contract import _dialogue
    checks=[{'id':'details','session':'same','at':'2026-10-15T10:00:00-04:00','prompt':'What is the plan with Maya?'},
            {'id':'other','session':'same','at':'2026-10-15T10:02:00-04:00','prompt':'Is Rosa’s still the place?'},
            {'id':'fresh','at':'2026-10-15T10:04:00-04:00','prompt':'What about Friday?'}]
    evidence=contract_evidence([],checks,checks[1])
    assert [m['thread'] for m in evidence]==['same','same']
    assert 'channel assistant | thread same' in _dialogue(evidence)
    assert contract_evidence([],checks,checks[2],'isolated')[-1]['thread']=='fresh'
    assert contract_evidence([],checks,checks[2],'shared')[-1]['thread']=='shared'
    legacy=contract_evidence([],checks,checks[2],structured_sessions=False)
    assert all('thread' not in m and 'channel' not in m for m in legacy)
    assert legacy[-1]['text'].startswith('[assistant session shared]')
