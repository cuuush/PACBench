"""Synthetic sequence integrity tests, not memory-provider performance evidence."""
import json
from types import SimpleNamespace

import pytest

from pacbench.evaluation import exposure_batches, resolve_checks
from pacbench.followups import freeze_policy
from pacbench.harness import TurnResult
from pacbench.native_checks import audit_checks, run_checks
from pacbench.semantic_contract import ContractClaim, EvaluationContract, build_contract


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
    return json.dumps({'verdicts': [{'id': c['id'], 'verdict': 'correct', 'reason': 'Synthetic harness judgment.'} for c in claims]})


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
    assert audit_checks(checks, context, tmp_path / 'arm', ['exposure-session']) == []
    with pytest.raises(FileExistsError):
        run_checks(checks, context, tmp_path / 'arm', correct_judge, '', {},
            question_dir=tmp_path / 'questions', run_turn=runner)
    path = tmp_path / 'arm/turns/event-2/judgment.json'
    judgment['conversation'] = [{'role': 'assistant', 'content': 'Invented history'}]
    path.write_text(json.dumps(judgment))
    assert any('judge conversation mismatch' in e for e in audit_checks(checks, context, tmp_path / 'arm', ['exposure-session']))


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


def test_scene_delivery_covers_exactly_once_and_cannot_omit_evidence():
    messages = [{'source_id': f'm{i}', 'channel': 'text', 'thread': 'same-thread',
        'written_at': f'2026-09-10T1{i}:00:00-04:00', 'collected_at': f'2026-09-10T1{i}:00:00-04:00'} for i in (1,2)]
    resolved = {'exposure_delivery': 'scenes', 'scenes': [{'id': f's{i}', 'channel': 'text', 'thread': 'same-thread',
        'window_start': m['written_at'], 'window_end': m['written_at']} for i,m in enumerate(messages)]}
    assert [len(batch) for _,batch in exposure_batches(messages, resolved)] == [1,1]
    resolved['scenes'].pop()
    with pytest.raises(ValueError, match='exactly once'):
        exposure_batches(messages, resolved)


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
            return json.dumps({'verdicts':[{'id':'fact-1','verdict':'missed','reason':'Synthetic zero recall.'}]})
        assert len(judge_calls) == 2  # no Spark follow-up author/reviewer or recovery judge
        return correct_judge(prompt)
    root = tmp_path / 'arm'
    report = run_checks(checks, 'shared', root, judge, '', {},
        question_dir=tmp_path / 'questions', run_turn=runner, budget_snapshot=lambda: {'projects': []})
    assert report['state'] == 'diagnostic' and len(turns) == len(judge_calls) == 2
    assert turns[1]['session_id'] == 'evaluation-session'
    skipped = json.loads((root / 'turns/event-1/followups/recovery.json').read_text())
    assert skipped['skip_reason'] == 'no_initial_correct_facts' and skipped['attempts'] == []
    assert audit_checks(checks, 'shared', root, ['ingestion-session']) == []
