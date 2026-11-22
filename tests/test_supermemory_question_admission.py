from copy import deepcopy
from hashlib import sha256
import json
import os
import sqlite3

import pytest

from pacbench import supermemory_question_admission as question, workflow_capability as cap
from pacbench.api_input_trace import record
from pacbench.canary_capability import binding, digest, sha
from pacbench.direct_accounting import live_budget
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.live_dispatch import CONDITION, process_identity
from pacbench.openrouter_route import with_route
from pacbench.supermemory_source_admission import source_release
from pacbench.supermemory_source_effect import BOUNDARY
from pacbench.supermemory_source_qualify import account_lock
from pacbench.workflow_spec import SPEC
from pacbench.workflow_tracks import HERMES_TOOLS


CALL = {'id': 'call-1', 'type': 'function',
        'function': {'name': 'supermemory_search', 'arguments': '{"query":"lunch"}'}}


@pytest.fixture
def opening(tmp_path, monkeypatch):
    pytest.importorskip('agent.memory_context')
    from agent.portal_tags import reset_affinity_scope, reset_conversation_context, set_affinity_scope, set_conversation_context
    campaign = tmp_path / 'campaign'
    root = campaign / question.QUESTION_RUN / 'supermemory'
    home = root / 'hermes-home'
    home.mkdir(parents=True)
    (campaign / 'execution.lock').touch()
    cached = root / 'prepaid-verification'
    cached.mkdir()
    raw = b'{"data":{"total_credits":2,"total_usage":1}}'
    credit_sha = sha256(raw).hexdigest()
    (cached / (credit_sha + '.private.json')).write_bytes(raw)
    confirmation = root / 'confirmation.json'
    confirmation.write_text('{"auto_recharge":"off","confirmed_by":"fixture","confirmed_at":"2026-11-22T00:00:00Z"}')
    first, _, basis = source_release()
    turn = cap.schedule(sha(SPEC), False)[1][0]
    parent = process_identity()
    monkeypatch.setattr(question, 'ready', lambda _: turn)
    monkeypatch.setattr(question, 'driver_owner', lambda *_: ('session', 'nonce', 'submit'))
    (root / 'reuse-context.jsonl').write_text(json.dumps(dict(session_id='session', query=turn.prompt, prefetch='')) + '\n')
    with sqlite3.connect(home / 'state.db') as conn:
        conn.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, '
                     'api_content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT)')
    tools = [{'type': 'function', 'function': {'name': name, 'parameters': {}}} for name in HERMES_TOOLS['supermemory']]
    monkeypatch.setattr(question, 'TOOLS_SHA', digest(tools))
    body = with_route(dict(messages=[{'role': 'system', 'content': 'Synthetic system'},
                                     {'role': 'user', 'content': turn.prompt}], tools=tools,
                           stream=True, stream_options={'include_usage': True},
                           reasoning={'enabled': True, 'effort': 'medium'}, session_id='declared-affinity'),
                      profile=question.PROFILE)
    for key, value in {
        'PACBENCH_DISPATCH_TRACKING': CONDITION, 'PACBENCH_STOP_FILE': str(root / 'STOP'),
        'PACBENCH_OBSERVATIONAL_COHORT_STOP': str(campaign / 'ACCOUNT_STOP'),
        'PACBENCH_RECEIPT_JOURNAL': str(root / 'receipts.jsonl'), 'PACBENCH_OPENROUTER_PROFILE': question.PROFILE,
        'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768', 'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
        'PACBENCH_WORKFLOW_CAPTURE': question.PROTOCOL, 'HERMES_HOME': str(home),
        'PACBENCH_API_INPUT_TRACE': str(root / 'api-inputs.jsonl'), 'PACBENCH_PYTHON_CLOCK_NONCE': 'process-nonce',
        'PACBENCH_PYTHON_CLOCK_ROLE': 'hermes-gateway',
    }.items():
        monkeypatch.setenv(key, value)
    with account_lock(campaign) as fd:
        scope = dict(admission=question.SCOPE, protocol=question.PROTOCOL, root=str(root), provider='supermemory',
                     run_id=root.parent.name, controller=parent, lock_fd=fd, native_effect=BOUNDARY,
                     namespace=f'supermemory-data-{question.SOURCE_RUN}-supermemory', cutoff=first[0].cutoff,
                     frozen_inputs=basis, spec_sha256=sha(SPEC), projects=question.projects(root),
                     prepaid={'receipt_sha256': credit_sha, 'remaining_usd': '1', 'source': 'OpenRouter GET /api/v1/credits'},
                     auto_recharge_evidence=binding(confirmation))
        scope_path = root / 'workflow-scope.json'
        scope_path.write_text(json.dumps(scope))
        monkeypatch.setenv(cap.ENV, json.dumps(binding(scope_path)))
        context = set_conversation_context('session')
        affinity = set_affinity_scope('declared-affinity')
        try:
            yield root, scope, body
        finally:
            reset_affinity_scope(affinity)
            reset_conversation_context(context)


def continuation(root, body):
    with sqlite3.connect(root / 'hermes-home/state.db') as conn:
        conn.executemany('INSERT INTO messages(session_id,role,content,tool_call_id,tool_calls,tool_name) '
                         'VALUES (?,?,?,?,?,?)', [
            ('session', 'user', body['messages'][1]['content'], None, None, None),
            ('session', 'assistant', None, None, json.dumps([CALL]), None),
            ('session', 'tool', 'native result\n', CALL['id'], None, CALL['function']['name']),
        ])
    return {**body, 'messages': [*body['messages'], {'role': 'assistant', 'content': None, 'tool_calls': [CALL]},
                                 {'role': 'tool', 'content': 'native result', 'tool_call_id': CALL['id']}]}


def record_prepared(root, project, prepared):
    record(project, prepared)
    # A real persistent clock trace carries the turn nonce, not its process nonce.
    trace = root / 'api-inputs.jsonl'
    rows = question.jsonl(trace)
    rows[-1]['turn_nonce'] = 'nonce'
    trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def send(root, budget, project, request, *, generation='gen-opening', finish='stop', complete=True):
    prepared = budget.reserve(project, request)
    record_prepared(root, project, prepared)
    budget.mark_dispatch_possible(prepared['id'])
    budget.settle(prepared['id'], 200, json.dumps({
        'id': generation, 'model': budget.model, 'provider': 'Amazon Bedrock',
        'choices': [{'message': {'content': 'Synthetic response'}, 'finish_reason': finish}],
        'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'cost': '0.0005'},
    }), body_complete=complete)


def test_original_opening_and_real_tool_round_require_distinct_receipts(opening):
    root, scope, body = opening
    project = scope['projects']['supermemory']['driver']
    budget = live_budget(project=project)
    assert isinstance(budget, question.QuestionJournal)
    send(root, budget, project, body, finish='tool_calls')
    send(root, budget, project, continuation(root, body), generation='gen-followup')
    rows = budget.snapshot()
    assert [r['dispatch_execution'] for r in rows] == ['response_ended', 'response_ended']
    assert [r['workflow_slot']['supermemory_first_question']['tool_rounds'] for r in rows] == [[], [['call-1']]]
    assert [r['settled_charge_usd'] for r in rows] == ['0.0005', '0.0005']
    assert not (root / 'STOP').exists()


@pytest.mark.parametrize('fault', ('prompt', 'context', 'affinity', 'tool-schema', 'route', 'future', 'memory', 'judge', 'cohort-stop'))
def test_refused_scope_does_not_prepare_transport(opening, fault):
    root, scope, body = opening
    project = scope['projects']['supermemory']['driver']
    if fault == 'prompt':
        body['messages'][1]['content'] += ' Hint.'
    elif fault == 'context':
        body['messages'][1]['content'] += '\n\n<memory-context>\nInjected\n</memory-context>'
    elif fault == 'affinity':
        body['session_id'] = 'another-native-session'
    elif fault == 'tool-schema':
        body['tools'][0]['function']['parameters']['extra'] = 'changed'
    elif fault == 'route':
        body['provider']['allow_fallbacks'] = True
    elif fault == 'future':
        first, all_docs, _ = source_release()
        body['messages'][0]['content'] = next(s for s in all_docs[len(first)].bodies if len(s) >= 40)
    elif fault == 'memory':
        project = scope['projects']['supermemory']['memory']
    elif fault == 'judge':
        project = project.removesuffix('driver') + 'judge'
    elif fault == 'cohort-stop':
        (root.parent.parent / 'ACCOUNT_STOP').touch()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        live_budget(project=project).reserve(project, body)
    assert question.QuestionJournal(root / 'receipts.jsonl').snapshot() == []


@pytest.mark.parametrize('fault', ('repeat', 'changed-replay', 'tool-text', 'interrupt', 'bad-generation'))
def test_no_replay_or_ambiguous_continuation(opening, fault):
    root, scope, body = opening
    project = scope['projects']['supermemory']['driver']
    budget = live_budget(project=project)
    send(root, budget, project, body, finish='tool_calls', complete=fault != 'interrupt')
    if fault in ('tool-text', 'interrupt', 'bad-generation'):
        body = continuation(root, body)
    if fault == 'changed-replay':
        body['messages'][0]['content'] += ' A new digest does not authorize replay.'
    if fault == 'tool-text':
        body['messages'][-1]['content'] = 'forged tool result'
    if fault == 'bad-generation':
        send(root, budget, project, body, finish='tool_calls', generation='gen-opening')
    with pytest.raises((ValueError, OwnershipConflict)):
        budget.reserve(project, body)
    assert len(budget.snapshot()) == (2 if fault == 'bad-generation' else 1)


def test_wire_body_mutation_is_rejected_after_prepare(opening):
    root, scope, body = opening
    project = scope['projects']['supermemory']['driver']
    budget = live_budget(project=project)
    prepared = budget.reserve(project, body)
    tampered = deepcopy(prepared)
    tampered['request']['messages'][1]['content'] += ' Changed after preparation.'
    record_prepared(root, project, tampered)
    with pytest.raises(OwnershipConflict, match='changed before dispatch'):
        budget.mark_dispatch_possible(prepared['id'])
    assert budget.snapshot()[0]['dispatch_execution'] == 'prepared'


def test_lock_and_prepaid_proof_are_required(opening, monkeypatch):
    root, scope, body = opening
    project = scope['projects']['supermemory']['driver']
    cap_path = root / 'workflow-scope.json'
    wrong = {**scope, 'lock_fd': os.dup(scope['lock_fd'])}
    os.close(wrong['lock_fd'])
    cap_path.write_text(json.dumps(wrong))
    monkeypatch.setenv(cap.ENV, json.dumps(binding(cap_path)))
    with pytest.raises((OSError, OwnershipConflict)):
        live_budget(project=project).reserve(project, body)
    cap_path.write_text(json.dumps(scope))
    monkeypatch.setenv(cap.ENV, json.dumps(binding(cap_path)))
    (root / 'confirmation.json').write_text('{"auto_recharge":"on"}')
    with pytest.raises(RuntimeError, match='bound evidence changed'):
        live_budget(project=project).reserve(project, body)
