"""Exercise paid Honcho receipt decisions without opening a provider connection."""
import json
from unittest.mock import Mock

import pytest

from pacbench import execution_capability
from pacbench.providers.honcho import first_answer_admission as answer
from pacbench.providers.honcho import first_answer_peer as peer
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.honcho.receipt_index import chat_attempts, peer_history, release_attempts, turn_answers
from pacbench.live_dispatch import CONDITION
from pacbench.observational_receipts import RunReceiptJournal, replay_attempts
from pacbench.openrouter_route import PROFILES
from pacbench.receipt_projection import IndexedAttempts

MODEL = PROFILES[answer.PROFILE]['model']
TOOL = [{'id': 'tool-1', 'name': 'honcho_reasoning', 'arguments_sha256': 'bound-tool'}]


def _legacy(i, **updates):
    return dict(id=f'prior-{i}', project='historical', finished=True, state='settled', http_status=200,
                dispatch_execution='response_ended', response_body_complete=True,
                served_model=MODEL, served_provider='Amazon Bedrock', upstream_request_id=f'gen-{i}',
                response_validation={'finish_reasons': ['stop']}, billing_receipt={'proof': True},
                receipt_scale='fixture', **updates)


def _tool_row(question, current):
    row = _legacy('latest', workflow_slot={'kind': 'answer', 'question_id': question,
                                           'honcho_first_answer': current})
    row.update(project='offline-driver', state='receipt_missing', error_label='actual_cost_unknown',
               honcho_wire_identity={'id': row['upstream_request_id'], 'model': MODEL,
                                     'provider': 'Amazon Bedrock', 'stream': True},
               honcho_selected_tools=TOOL, billing_receipt=None,
               response_validation={'finish_reasons': ['tool_calls'], 'sse_done_observed': True})
    return row


def _write_prefix(path, rows):
    path.write_text(''.join(json.dumps(dict(kind='prepared', id=row['id'], row=row))+'\n' for row in rows))


def _wire_prepare(monkeypatch, root, slot):
    permit = dict(root=str(root), run_id='run', slot=slot, capability={'sha256': 'capability'})
    monkeypatch.setattr(execution_capability, 'admission', lambda *_: permit)
    monkeypatch.setattr(execution_capability, 'fields', lambda scope: {
        f'workflow_{key}': scope[key] for key in ('root', 'capability', 'slot')})
    monkeypatch.setenv('PACBENCH_LUNA_COMPATIBILITY', 'temperature-omitted-default-v1')
    monkeypatch.setenv('PACBENCH_EFFECTIVE_OUTPUT_TOKENS', '32768')
    monkeypatch.setenv('PACBENCH_DISPATCH_TRACKING', CONDITION)
    monkeypatch.delenv('PACBENCH_OBSERVATIONAL_COHORT_STOP', raising=False)
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(root/'STOP'))


@pytest.mark.parametrize('prefix_size', [20, 200])
def test_honcho_real_prepare_dispatch_and_peer_lookup_decode_only_relevant_receipts(tmp_path, monkeypatch, prefix_size):
    root = tmp_path/'honcho'
    root.mkdir()
    journal = RunReceiptJournal(root/'receipts.jsonl')
    question = 'current-question'
    current = {'native_request_id': 'active-native', 'turn_nonce': 'active', 'tool_rounds': []}
    prefix = [_legacy(i, workflow_slot=None if i % 2 else {
        'kind': 'answer', 'question_id': f'earlier-{i}',
        'honcho_first_answer': {'native_request_id': f'earlier-{i}', 'tool_rounds': []},
    }) for i in range(prefix_size)]
    latest = _tool_row(question, current)
    _write_prefix(journal.path, [*prefix, latest])
    with journal.journal() as (rows, _):
        answer.verify_receipts(rows)  # Build the projection before measuring a warmed prepare.

    next_slot = dict(provider='honcho', kind='answer', question_id=question,
                     honcho_first_answer={**current, 'tool_rounds': [TOOL]})
    _wire_prepare(monkeypatch, root, next_slot)
    confirm = Mock()
    monkeypatch.setattr(answer, 'confirm_sender', confirm)

    full_scan = Mock(side_effect=AssertionError('Honcho hot path decoded the entire receipt journal'))
    monkeypatch.setattr(IndexedAttempts, 'values', full_scan)
    monkeypatch.setattr(RunReceiptJournal, 'snapshot', full_scan)
    decoded = []
    load = json.loads

    def track_load(raw, *args, **kwargs):
        value = load(raw, *args, **kwargs)
        if isinstance(value, dict) and value.get('receipt_scale') == 'fixture':
            decoded.append(value['id'])
        return value

    monkeypatch.setattr(json, 'loads', track_load)
    assert peer._last_driver({'root': str(root)}, {'native_request_id': 'active-native'}) == latest
    prepared = journal.prepare('offline-driver', {'model': MODEL, 'stream': True})
    journal.mark_dispatch_possible(prepared['id'])
    confirm.assert_called_once()
    assert journal.get_attempt(prepared['id'])['dispatch_execution'] == 'ambiguous'
    assert decoded == [latest['id'], latest['id']]
    full_scan.assert_not_called()
    assert not (root/'STOP').exists()


@pytest.mark.parametrize('fault', ['none', 'duplicate', 'unfinished-duplicate', 'wrong-route',
                                   'missing-route', 'missing-output', 'malformed-outcome'])
def test_indexed_honcho_global_terminal_gate_matches_authority(tmp_path, monkeypatch, fault):
    journal = RunReceiptJournal(tmp_path/'receipts.jsonl')
    latest = _tool_row('question', {'tool_rounds': []})
    _write_prefix(journal.path, [_legacy('one'), latest])
    with journal.journal() as (rows, append):
        answer.verify_receipts(rows)
        if fault in ('duplicate', 'unfinished-duplicate'):
            duplicate = _legacy('duplicate')
            duplicate.update(upstream_request_id=latest['upstream_request_id'], finished=fault == 'duplicate')
            append('prepared', duplicate['id'], duplicate)
        elif fault == 'wrong-route':
            append('classification', latest['id'], {'honcho_wire_identity': {
                **latest['honcho_wire_identity'], 'provider': 'Other provider'}})
        elif fault == 'missing-route':
            append('classification', latest['id'], {'honcho_wire_identity': None})
        elif fault == 'missing-output':
            append('classification', latest['id'], {'honcho_selected_tools': None})
        elif fault == 'malformed-outcome':
            append('classification', latest['id'], {'response_validation': []})

    stop = tmp_path/'STOP'
    monkeypatch.delenv('PACBENCH_DIRECT_STOP_ON_429', raising=False)
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(stop))
    with journal.path.open() as stream:
        authority = replay_attempts(stream)

    def check(rows):
        if fault in ('none', 'unfinished-duplicate'):
            answer.verify_receipts(rows)
            assert not stop.exists()
        else:
            with pytest.raises(OwnershipConflict, match='unique usable'):
                answer.verify_receipts(rows)
            assert json.loads(stop.read_bytes())['reason'] == 'honcho_unusable_or_duplicate_terminal_response'
            stop.unlink()

    check(authority)
    with journal.journal() as (rows, _):
        check(rows)

    if fault not in ('none', 'unfinished-duplicate'):
        slot = dict(provider='honcho', kind='answer', question_id='next',
                    honcho_first_answer={'native_request_id': 'native', 'tool_rounds': []})
        _wire_prepare(monkeypatch, tmp_path, slot)
        with pytest.raises(OwnershipConflict, match='unique usable'):
            journal.prepare('offline-driver', {'model': MODEL, 'stream': True})
        assert set(authority) == {row['id'] for row in journal.snapshot()}
        assert stop.exists()


def test_honcho_index_matches_mixed_scopes_and_queue_overlap_in_insertion_order(tmp_path):
    bound = {'path': 'release.json', 'sha256': 'release'}
    claim = {'operation': 'peer', 'run_id': 'run', 'claim': 'owned'}
    history = [
        _legacy(0, workflow_slot={'honcho_chat_work': {'purpose': 'representation', 'queue_ids': [1, 2, 3]}}),
        _legacy(1, workflow_slot=None),
        _legacy(2, workflow_slot={'kind': 'answer', 'question_id': 'question'}),
        _legacy(3, workflow_slot={'honcho_daily_release': {'sha256': 'release', 'path': 'elsewhere.json'}}),
        _legacy(4, workflow_slot={'honcho_daily_release': bound}),
        _legacy(5, workflow_slot={'honcho_chat_work': {'purpose': 'summary', 'queue_ids': [2]}}),
        _legacy(6, workflow_slot={'honcho_chat_work': {'purpose': 'representation', 'queue_ids': [2, 7]}}),
        _legacy(7, workflow_slot={'honcho_peer_read': claim}),
        _legacy(8, workflow_slot={'honcho_peer_read': {**claim, 'claim': 'foreign'}}),
        _legacy(9, workflow_slot={'honcho_peer_read': {**claim, 'operation': 'other'}}),
    ]
    journal = RunReceiptJournal(tmp_path/'receipts.jsonl')
    _write_prefix(journal.path, history)

    def check(rows):
        assert turn_answers(rows, 'question') == [history[2]]
        assert release_attempts(rows, bound) == [history[4]]
        assert chat_attempts(rows, {'purpose': 'representation', 'queue_ids': [7, 3, 2]}) == [history[0], history[6]]
        assert peer_history(rows, claim) == (2, 1)

    check({row['id']: row for row in history})
    with journal.journal() as (rows, _):
        check(rows)


def test_malformed_historical_chat_witness_does_not_poison_other_scopes(tmp_path):
    journal = RunReceiptJournal(tmp_path/'receipts.jsonl')
    bad = _legacy('bad-chat', workflow_slot={'honcho_chat_work': {'purpose': 'summary', 'queue_ids': 1}})
    _write_prefix(journal.path, [bad])

    def check(rows):
        answer.verify_receipts(rows)
        assert chat_attempts(rows, {'purpose': 'representation', 'queue_ids': [1]}) == []
        with pytest.raises(OwnershipConflict, match='lineage is malformed'):
            chat_attempts(rows, {'purpose': 'summary', 'queue_ids': [1]})

    check({bad['id']: bad})
    with journal.journal() as (rows, append):
        check(rows)
        append('classification', bad['id'], {
            'workflow_slot': {'honcho_chat_work': {'purpose': 'summary', 'queue_ids': [1]}}})
        assert [row['id'] for row in chat_attempts(rows, {'purpose': 'summary', 'queue_ids': [1]})] == [bad['id']]
