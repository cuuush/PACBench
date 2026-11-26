"""Offline integration checks for admission data derived from the receipt log."""
import multiprocessing
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor

import pytest

from pacbench import receipt_projection as projection
from pacbench.observational_receipts import RunReceiptJournal
from pacbench.providers.supermemory.week_admission import ADMISSION_SPEC


def worker(ident, update):
    return {'id': ident, 'project': 'test-memory', 'finished': None,
            'accounting_role': 'memory_provider',
            'workflow_slot': {'stock_source_effect': {'update_id': update}}}


def terminal(generation):
    return dict(finished='done', upstream_request_id=generation, http_status=200,
                dispatch_execution='response_ended',
                response_validation=dict(envelope_errors=[], wire_parse_errors=[]))


@pytest.mark.parametrize('version', [1, 2])
def test_pending_recovery_preserves_admission_index(tmp_path, monkeypatch, version):
    path = tmp_path/'receipts.jsonl'
    budget = RunReceiptJournal(path)
    with budget.journal() as (rows, append):
        append('prepared', 'a', worker('a', 'document-a'))
        if version == 2:
            rows.enable_admission(ADMISSION_SPEC)
        assert rows.db.execute('PRAGMA user_version').fetchone() == (version,)

    def interrupted(stream, db, rows, raw, written):
        stream.write(raw[:15])
        os.fsync(stream.fileno())
        raise RuntimeError('simulated killed writer')

    with monkeypatch.context() as m:
        m.setattr(projection.Projection, '_finish', staticmethod(interrupted))
        # Simulate a writer dying during a partial write with its cursor pending.
        with pytest.raises(RuntimeError, match='killed'):
            with budget.journal() as (_, append):
                append('outcome', 'a', terminal('generation-a'))

    with pytest.raises(ValueError, match='unfinished append'):
        budget.snapshot()
    with budget.journal() as (rows, _):
        authority = path.read_bytes()
        rows.enable_admission(ADMISSION_SPEC)
        assert rows.db.execute('PRAGMA user_version').fetchone() == (2,)
        assert rows.last_for_scope('stock_update', 'document-a')['upstream_request_id'] == 'generation-a'
        assert not rows.invalid_terminal_responses()
    assert path.read_bytes() == authority
    assert len(authority.splitlines()) == 2
    assert budget.snapshot()[0]['finished'] == 'done'

def test_external_admission_edit_cannot_hide_duplicate_generation(tmp_path):
    budget = RunReceiptJournal(tmp_path/'receipts.jsonl')
    with budget.journal() as (rows, append):
        rows.enable_admission(ADMISSION_SPEC)
        for ident in ('one', 'two'):
            append('prepared', ident, worker(ident, 'document'))
            append('outcome', ident, terminal('shared'))
        assert rows.duplicate_generations()
    with sqlite3.connect(projection.Projection(budget.path).index) as db:
        db.execute('DELETE FROM admission_generations')
    with pytest.raises(ValueError, match='projection changed'):
        budget.has_active_attempts()
    assert len(budget.snapshot()) == 2


def test_admission_indexes_follow_generation_finish_and_scope_patches(tmp_path):
    budget = RunReceiptJournal(tmp_path/'receipts.jsonl')
    with budget.journal() as (rows, append):
        rows.enable_admission(ADMISSION_SPEC)
        append('prepared', 'first', worker('first', 'document-a'))
        append('prepared', 'second', worker('second', 'document-b'))
        append('response', 'first', {'upstream_request_id': 'shared'})
        append('response', 'second', {'upstream_request_id': 'shared'})
        assert rows.duplicate_generations()  # Supermemory checks active attempts too.
        assert not rows.invalid_terminal_responses()
    with budget.journal() as (rows, append):
        append('outcome', 'first', terminal('replacement'))
        assert not rows.duplicate_generations()
        assert not rows.invalid_terminal_responses()
        append('outcome', 'second', {**terminal('replacement'), 'http_status': 500})
        assert rows.duplicate_generations() and rows.invalid_terminal_responses()
        append('outcome', 'second', {'upstream_request_id': None, 'finished': None})
        assert not rows.duplicate_generations() and not rows.invalid_terminal_responses()
        append('outcome', 'second', {'finished': 'done'})
        assert rows.invalid_terminal_responses()
        append('outcome', 'second', {'response_validation': None})
        append('outcome', 'second', {'http_status': 200})
        with pytest.raises(TypeError):
            rows.invalid_terminal_responses()
        append('outcome', 'second', terminal('new'))
        assert not rows.invalid_terminal_responses()
        append('classification', 'second', {'workflow_slot': worker('second', 'document-a')['workflow_slot']})
        assert rows.for_scope('stock_update', 'document-b') == []
        assert [r['id'] for r in rows.for_scope('stock_update', 'document-a', 'document-a')] == ['first', 'second']
        assert rows.count_for_scope('stock_update', 'document-a') == 2
        assert rows.last_for_scope('stock_update', 'document-a')['id'] == 'second'


def _write_attempt(path_and_id):
    path, ident = path_and_id
    budget = RunReceiptJournal(path)
    with budget.journal() as (rows, append):
        rows.enable_admission(ADMISSION_SPEC)
        append('prepared', ident, worker(ident, 'document'))
        append('outcome', ident, terminal('shared'))


def test_independent_writers_share_admission_counts(tmp_path):
    path = tmp_path/'receipts.jsonl'
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context('spawn')) as pool:
        list(pool.map(_write_attempt, [(path, str(i)) for i in range(12)]))
    budget = RunReceiptJournal(path)
    with budget.journal() as (rows, append):
        assert rows.duplicate_generations()
        assert not rows.invalid_terminal_responses()
        assert rows.count_for_scope('stock_update', 'document') == 12
        budget_rows = rows.for_scope('stock_update', 'document')[:-1]
        for row in budget_rows:
            append('outcome', row['id'], {'upstream_request_id': None})
        assert len(budget_rows) == 11
        assert not rows.duplicate_generations()


def test_clean_global_gates_and_scoped_lookup_use_indexed_work(tmp_path):
    budget = RunReceiptJournal(tmp_path/'receipts.jsonl')
    with budget.journal() as (rows, append):
        rows.enable_admission(ADMISSION_SPEC)
        for i in range(1500):
            ident = str(i)
            append('prepared', ident, worker(ident, 'document-' + ident))
            append('outcome', ident, terminal('generation-' + ident))
        statements = []
        rows.db.set_trace_callback(statements.append)
        work = []
        rows.db.set_progress_handler(lambda: work.append(1) or 0, 1)
        assert not rows.duplicate_generations()
        assert not rows.invalid_terminal_responses()
        assert rows.for_scope('stock_update', 'document-999')[0]['id'] == '999'
        rows.db.set_progress_handler(None, 0)
        rows.db.set_trace_callback(None)
        assert len(work) < 120
        plans = [' '.join(r[-1] for r in rows.db.execute('EXPLAIN QUERY PLAN ' + query))
                 for query in statements]
        assert 'admission_duplicate' in plans[0]
        assert 'admission_invalid' in plans[1]
        assert 'SEARCH admission_scopes USING PRIMARY KEY' in plans[2]
