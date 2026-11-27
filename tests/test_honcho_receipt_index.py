"""Exercise paid Honcho receipt decisions without opening a provider connection."""

import json

import pytest

from pacbench.providers import honcho_evidence as answer

from pacbench.workflows.progress import OwnershipConflict

from pacbench.providers.honcho_evidence import chat_attempts, peer_history, release_attempts, turn_answers

from tests.receipt_fixtures import JournalFixture as RunReceiptJournal

from pacbench.openrouter_route import PROFILES

MODEL = PROFILES[answer.PROFILE]['model']

def _legacy(i, **updates):
    return dict(id=f'prior-{i}', project='historical', finished=True, state='settled', http_status=200,
                dispatch_execution='response_ended', response_body_complete=True,
                served_model=MODEL, served_provider='Amazon Bedrock', upstream_request_id=f'gen-{i}',
                response_validation={'finish_reasons': ['stop']}, billing_receipt={'proof': True},
                receipt_scale='fixture', **updates)

def _write_prefix(path, rows):
    path.write_text(''.join(json.dumps(dict(kind='prepared', id=row['id'], row=row))+'\n' for row in rows))

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
        assert chat_attempts(rows, {'purpose': 'representation', 'queue_ids': [1]}) == []
        with pytest.raises(OwnershipConflict, match='lineage is malformed'):
            chat_attempts(rows, {'purpose': 'summary', 'queue_ids': [1]})

    check({bad['id']: bad})
    with journal.journal() as (rows, append):
        check(rows)
        append('classification', bad['id'], {
            'workflow_slot': {'honcho_chat_work': {'purpose': 'summary', 'queue_ids': [1]}}})
        assert [row['id'] for row in chat_attempts(rows, {'purpose': 'summary', 'queue_ids': [1]})] == [bad['id']]
