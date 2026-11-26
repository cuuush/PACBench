"""Exercise the real Week receipt gate using local synthetic journals."""
import json
import sqlite3

import pytest

from pacbench import direct_accounting, receipt_projection
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.supermemory.week_admission import ADMISSION_SPEC, WeekJournal


def driver(question, tool_rounds=()):
    return {'question_id': question, 'supermemory_week_claim': 'claim',
            'supermemory_first_question': {'tool_rounds': list(tool_rounds)}}


def worker(update, ordinal=0):
    return {'stock_source_effect': {'update_id': update}, 'worker_round': {
        'ordinal': ordinal, 'visit': 'visit', 'messages_sha256': f'text-{ordinal}', 'messages_count': ordinal + 2,
        'prefix_sha256': f'text-{ordinal - 1}', 'prefix_count': ordinal + 1, 'options_sha256': 'options',
        'reply': {'verified': ordinal - 1}, 'results': {},
    }}


def receipt(identifier, slot, role='memory_provider'):
    return {'id': identifier, 'project': 'fixture', 'accounting_role': role, 'finished': 'done',
            'http_status': 200, 'dispatch_execution': 'response_ended', 'dispatch_owner': 'owner',
            'upstream_request_id': f'gen-{identifier}', 'state': 'settled', 'workflow_slot': slot,
            'response_validation': {'envelope_errors': [], 'wire_parse_errors': 0, 'finish_reasons': ['tool_calls']},
            'stock_worker_tool_response': {'generation_id': f'gen-{identifier}', 'reply': {'verified': 0}}}


def seed(path, rows):
    with path.open('w') as stream:
        for row in rows:
            stream.write(json.dumps({'kind': 'prepared', 'id': row['id'], 'row': row}) + '\n')
    journal = WeekJournal(path)
    with journal.journal() as (attempts, _):
        attempts.enable_admission(ADMISSION_SPEC)
    return journal


@pytest.mark.parametrize('count', [64, 640])
def test_week_admission_only_decodes_current_history_at_1x_and_10x(tmp_path, monkeypatch, count):
    background = [receipt(str(i), worker(f'historical-{i}')) for i in range(count)]
    driver_open = receipt('driver', driver('active'), 'hermes')
    worker_open = receipt('worker', worker('active'))
    same_question_other_role = receipt('other-role', driver('active'))
    history = [*background, driver_open, worker_open, same_question_other_role]
    journal = seed(tmp_path / 'receipts.jsonl', history)
    decoded = []
    json_loads = json.loads

    def observe_loads(payload, **kwargs):
        data = json_loads(payload, **kwargs)
        if isinstance(data, dict) and 'project' in data:
            decoded.append(data['id'])
        return data

    monkeypatch.setattr(receipt_projection.json, 'loads', observe_loads)
    monkeypatch.setattr(receipt_projection.IndexedAttempts, 'values', lambda _: pytest.fail('full-history decode'))
    next_driver = receipt('next-driver', driver('active', ['tool']), 'hermes')
    next_worker = receipt('next-worker', worker('active', 1))
    with journal.journal() as (rows, _):
        journal.check_prepared(rows, next_driver)
        journal.check_prepared(rows, next_worker)
    assert decoded == ['driver', 'worker']

    with journal.journal() as (_, append):
        append('outcome', str(count - 1), {'dispatch_execution': 'ambiguous'})
    decoded.clear()
    with journal.journal() as (rows, _):
        with pytest.raises(OwnershipConflict, match='worker response remains uncertain'):
            journal.check_prepared(rows, next_driver)
    assert decoded == ['driver']

    with journal.journal() as (_, append):
        append('outcome', str(count - 1), {'upstream_request_id': 'gen-0', 'finished': None})
    decoded.clear()
    with journal.journal() as (rows, _):
        with pytest.raises(OwnershipConflict, match='duplicates a generation'):
            journal.check_prepared(rows, next_worker)
    assert decoded == []


@pytest.mark.parametrize(('fault', 'error', 'match'), [
    ('unfinished-driver', OwnershipConflict, 'lacks a witnessed terminal'),
    ('claim-changed', OwnershipConflict, 'already prepared this question'),
    ('driver-no-tool-call', OwnershipConflict, 'unambiguous preceding tool response'),
    ('driver-stale-transcript', OwnershipConflict, 'unambiguous preceding tool response'),
    ('worker-owner', OwnershipConflict, 'differs from its completed tool-call'),
    ('worker-skipped', OwnershipConflict, 'already prepared or skipped'),
    ('worker-reused-tool-id', OwnershipConflict, 'reused an earlier tool-call identity'),
    ('ambiguous-sse-verified', None, None),
    ('ambiguous-sse-truthy', OwnershipConflict, 'worker response remains uncertain'),
    ('malformed-terminal', KeyError, 'response_validation'),
    ('malformed-driver-scope', AttributeError, 'get'),
    ('malformed-worker-scope', KeyError, 'workflow_slot'),
])
def test_indexed_week_admission_matches_dictionary_gate(tmp_path, monkeypatch, fault, error, match):
    stopped = []
    monkeypatch.setattr(direct_accounting, 'stop_measured_admission', stopped.append)
    old_driver = receipt('driver', driver('active'), 'hermes')
    old_worker = receipt('worker', worker('active'))
    unrelated = receipt('unrelated', worker('other'))
    prior = [old_driver, old_worker, unrelated]
    next_driver = receipt('next-driver', driver('active', ['tool']), 'hermes')
    next_worker = receipt('next-worker', worker('active', 1))
    row = next_worker if fault.startswith('worker-') or fault == 'malformed-worker-scope' else next_driver
    if fault == 'unfinished-driver':
        old_driver['finished'] = None
    elif fault == 'claim-changed':
        old_driver['workflow_slot']['supermemory_week_claim'] = 'foreign'
    elif fault == 'driver-no-tool-call':
        old_driver['response_validation']['finish_reasons'] = ['stop']
    elif fault == 'driver-stale-transcript':
        old_driver['workflow_slot']['supermemory_first_question']['tool_rounds'].append('foreign')
    elif fault == 'worker-owner':
        old_worker['dispatch_owner'] = 'foreign'
    elif fault == 'worker-skipped':
        next_worker['workflow_slot']['worker_round']['ordinal'] = 2
    elif fault == 'worker-reused-tool-id':
        old_worker['workflow_slot']['worker_round']['results']['already-seen'] = 'first'
        next_worker['workflow_slot']['worker_round']['results']['already-seen'] = 'second'
    elif fault.startswith('ambiguous-sse'):
        unrelated['dispatch_execution'] = 'ambiguous'
        unrelated['response_validation']['sse_done_observed'] = True if fault.endswith('verified') else 1
    elif fault == 'malformed-terminal':
        del unrelated['response_validation']
    elif fault == 'malformed-driver-scope':
        unrelated['workflow_slot'] = None
    elif fault == 'malformed-worker-scope':
        del unrelated['workflow_slot']

    journal = seed(tmp_path / 'receipts.jsonl', prior)
    with journal.journal() as (indexed, _):
        for rows in ({item['id']: item for item in prior}, indexed):
            if error is None:
                journal.check_prepared(rows, row)
            else:
                with pytest.raises(error, match=match):
                    journal.check_prepared(rows, row)
    expected = ['supermemory_week_receipt_or_replay_refused'] * 2 if error is OwnershipConflict else []
    assert stopped == expected


def test_week_scopes_use_prepared_order_after_an_older_attempt_changes(tmp_path):
    first = receipt('z-first', driver('active'), 'hermes')
    latest = receipt('a-latest', driver('active', ['first']), 'hermes')
    journal = seed(tmp_path / 'receipts.jsonl', [first, latest])
    with journal.journal() as (rows, append):
        append('classification', first['id'], {'state': 'settled'})
        journal.check_prepared(rows, receipt('incoming', driver('active', ['first', 'second']), 'hermes'))


@pytest.mark.parametrize('damage', ['truncated-source', 'unowned-tail', 'modified-index'])
def test_week_admission_refuses_stale_receipt_authority(tmp_path, damage):
    path = tmp_path / 'receipts.jsonl'
    journal = seed(path, [receipt('history', worker('historical'))])
    if damage == 'truncated-source':
        path.write_bytes(path.read_bytes()[:-1])
    elif damage == 'unowned-tail':
        with path.open('a') as stream:
            stream.write('{}\n')
    else:
        with sqlite3.connect(receipt_projection.Projection(path).index) as db:
            db.execute('DELETE FROM admission_verdicts')
    with pytest.raises(ValueError, match='Receipt .* (?:changed|tail)'):
        with journal.journal() as (rows, _):
            journal.check_prepared(rows, receipt('incoming', driver('active'), 'hermes'))
