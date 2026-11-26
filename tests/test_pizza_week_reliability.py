"""Offline faults at Pizza Week's real release, conversation, and checkpoint boundaries."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from pacbench.workflows.conformance import RecordingAccount
from pacbench.campaigns.pizza_week import compile_daily_week, daily_documents
from pacbench.workflows.runner import Condition, Journal, captured_turn, run_workflow
from pacbench.workflows.spec import Document, Turn

AT = '2026-10-06T21:00:00-04:00'
QUESTION = 'Did you find the lunch invite?'
TOOLS = ('lookup',)
COND = Condition('cognee', 'nightly-document', 'continuing-account', 'recording',
                 'sentinel', 'recording', 'test', '1.6.8', 'none', 'recording', TOOLS)
TURN = Turn('turn-one', '2026-10-07T11:00:00-04:00', QUESTION, 'chat', 'initial', ())


def _call(name):
    return {'id': name, 'type': 'function',
            'function': {'name': 'lookup', 'arguments': json.dumps({'q': name})}}


def _result(name):
    return {'role': 'tool', 'tool_name': 'lookup', 'tool_call_id': name, 'content': 'result ' + name}


def _surface():
    history = [
        {'role': 'user', 'content': QUESTION},
        {'role': 'assistant', 'tool_calls': [_call('a'), _call('b')]},
        _result('b'), _result('a'),
        {'role': 'assistant', 'tool_calls': [_call('c')]},
        _result('c'),
        {'role': 'assistant', 'content': 'The native final answer.'},
    ]
    return dict(session_id='hermes-1', gateway_identity=[1, 2], question_at=TURN.at,
                answer=history[-1]['content'], history=history, source_files=[],
                write_barrier={'session_id': 'hermes-1', 'pending_writes': 0, 'readback': 'yes'},
                prefetch='', offered_tools=list(TOOLS), attempts=[],
                tool_calls=[{'tool_call_id': n, 'name': 'lookup', 'input': {'q': n}} for n in ('a', 'b', 'c')],
                tool_results=[{'tool_call_id': n, 'tool': 'lookup', 'text': 'result ' + n, 'success': True}
                              for n in ('b', 'a', 'c')],
                model_requests=[{'request_id': str(n), 'model': 'sentinel', 'tools': list(TOOLS),
                                 'messages': deepcopy(history[:n])} for n in (1, 4, 6)])


def test_two_complete_native_tool_rounds_allow_parallel_result_order():
    assert captured_turn(_surface(), TURN, None, COND, [])['explicit_attempts'] == 3


@pytest.mark.parametrize('fault', ('out-of-order-requests', 'mid-round-request', 'cross-round-result'))
def test_rejects_inconsistent_tool_round_even_when_final_answer_matches(fault):
    surface = _surface()
    requests = surface['model_requests']
    if fault == 'out-of-order-requests':
        requests[0], requests[1] = requests[1], requests[0]
    elif fault == 'mid-round-request':
        requests.insert(1, {**requests[1], 'request_id': 'mid-round', 'messages': surface['history'][:3]})
    else:
        surface['history'][2], surface['history'][5] = surface['history'][5], surface['history'][2]
        surface['tool_results'][0], surface['tool_results'][2] = surface['tool_results'][2], surface['tool_results'][0]
        for length, request in zip((1, 4, 6), requests, strict=True):
            request['messages'] = deepcopy(surface['history'][:length])
    with pytest.raises(ValueError, match='tool group'):
        captured_turn(surface, TURN, None, COND, [])


def test_duplicate_turn_id_is_rejected_before_any_provider_effect(tmp_path):
    from pacbench.campaigns.pizza_week import compile_week

    _, docs, _, basis = compile_week('continuing-account')
    account = RecordingAccount(COND)
    duplicate = replace(TURN, at='2026-10-07T11:01:00-04:00', prompt='A different message')
    try:
        with pytest.raises(ValueError, match='unique identities'), Journal(tmp_path / 'duplicate') as journal:
            run_workflow(COND, account, docs, [TURN, duplicate], journal, spec_digest=basis['sha256'],
                         evidence_kind=account.evidence_kind)
        assert not account.files and not account.native_writes
    finally:
        account.close()


@pytest.mark.parametrize('field', ('written_at', 'arrived_at', 'available_at'))
def test_rejects_same_creation_day_message_unavailable_at_cutoff(field):
    row = dict(source_id='example', sender='Jo', from_owner=False, channel='imessage', thread='duo',
               written_at='2026-10-06T08:00:00-04:00', arrived_at='2026-10-06T08:00:00-04:00',
               structured={'participants': ['Me', 'Jo']}, text='Authored text.')
    future = '2026-10-06T22:00:00-04:00'
    row[field] = future
    if field == 'written_at':
        row['arrived_at'] = future
    originals = [Document('old.txt', AT, 'imessage', row['text'], ('example',), (row['text'],))]
    with pytest.raises(ValueError, match='cutoff'):
        daily_documents(originals, [row], 'America/New_York')


def test_daily_archive_bytes_and_full_schedule_stay_frozen():
    _, docs, turns, _, manifest = compile_daily_week()
    assert len(docs) == 7 and len(turns) == 49
    assert len({turn.session for turn in turns}) == 31
    assert sum(len(turn.claims) for turn in turns) == 192
    assert [len(d.source_ids) for d in docs] == [39, 23, 27, 24, 9, 10, 10]
    assert manifest['normalized_index_sha256'] == '55e204958b1c38d0dc4177d47789bc78473c44b96f183a554f98390bb5b22ce9'


def test_retained_intake_rejects_later_release_submitted_before_question():
    from pacbench.cohort_regrade_retained import document_readbacks
    from tests.test_cohort_regrade import journal_documents

    _, docs, _, _, _ = compile_daily_week()
    dispatch = {'phase': 'dispatch', 'question': TURN.id, 'at': TURN.at, 'prompt': TURN.prompt}
    ready = journal_documents(docs[:2])
    assert document_readbacks([*ready, dispatch], docs, [TURN], answered=[TURN])['ready_documents'] == 2
    future = journal_documents(docs[2:3])[0]
    assert future['phase'] == 'submit'
    with pytest.raises(ValueError, match='original cutoff'):
        document_readbacks([*ready, future, dispatch], docs, [TURN], answered=[TURN])


def test_parallel_tool_provenance_uses_call_id_instead_of_return_order():
    from pacbench.workflows.native import tool_provenance

    calls = [{'tool_call_id': 'a', 'name': 'lookup', 'input': {}},
             {'tool_call_id': 'b', 'name': 'lookup', 'input': {}}]
    results = [{'tool_call_id': 'b', 'tool': 'lookup', 'text': 'B', 'success': False},
               {'tool_call_id': 'a', 'tool': 'lookup', 'text': 'A', 'success': True}]
    assert [(row['tool_call_id'], row['success']) for row in tool_provenance(calls, results)] == [
        ('a', True), ('b', False)]
    results[1]['tool_call_id'] = 'b'
    with pytest.raises(ValueError, match='one result'):
        tool_provenance(calls, results)


def test_tool_status_is_joined_to_the_actual_native_row_before_provenance():
    from pacbench.workflows.native import capture_tool_results

    native = [_result('a'), _result('b')]
    events = [{'tool_call_id': 'b', 'tool': 'lookup', 'success': False, 'text': 'preview'},
              {'tool_call_id': 'a', 'tool': 'lookup', 'success': True, 'text': 'preview'}]
    saved = capture_tool_results(native, events)
    assert [(row['text'], row['success']) for row in saved] == [('result a', True), ('result b', False)]
    events[0]['tool_call_id'] = 'foreign'
    with pytest.raises(ValueError, match='native tool-result identity'):
        capture_tool_results(native, events)


def test_spec_cli_validates_the_frozen_daily_layout(capsys):
    from pacbench.specs import main
    from pacbench.campaigns.pizza_week import DAILY_SPEC

    assert main([str(DAILY_SPEC)]) == 0
    assert '7 | frozen | 47 | frozen' in capsys.readouterr().out
