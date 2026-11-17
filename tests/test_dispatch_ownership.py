"""Offline staged protocol: immutable causality, crash ambiguity and concurrent dedupe."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest
from pacbench.dispatch_ownership import (AttemptContext, OwnershipConflict,
                                        StagedOwnership, create_staging_journal)


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / 'staged-ownership.sqlite3'
    create_staging_journal(path)
    return StagedOwnership(path)


@pytest.fixture
def context():
    return AttemptContext('run', 'mem0', 'ingest', 'op', 'attempt', 1,
                          'worker', 'boot-uuid:process-uuid', 'reservation', '.17')


def test_missing_state_not_recreated_and_existing_file_never_replaced(tmp_path):
    path = tmp_path / 'missing'
    with pytest.raises(sqlite3.OperationalError):
        StagedOwnership(path)
    assert not path.exists()
    path.write_bytes(b'original-evidence')
    with pytest.raises(FileExistsError):
        create_staging_journal(path)
    assert path.read_bytes() == b'original-evidence'


def test_concurrent_prepare_and_predispatch_boundary_exactly_once(journal, context):
    def prepare(_):
        return StagedOwnership(journal.path).prepare(context)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(prepare, range(12))) == 1
    def mark(_):
        return StagedOwnership(journal.path).mark_dispatch_possible(context)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(mark, range(12))) == 1
    assert journal.inspect(context)['execution'] == 'ambiguous'
    assert journal.inspect(context)['production_dispatch_allowed'] is False


def test_logical_operation_and_reservation_cannot_acquire_second_owner(journal, context):
    journal.prepare(context)
    with pytest.raises(OwnershipConflict):
        journal.prepare(replace(context, worker_id='other'))
    with pytest.raises(OwnershipConflict):
        journal.prepare(replace(context, attempt_id='different'))
    with pytest.raises(OwnershipConflict):
        journal.prepare(replace(context, attempt_id='different', operation_id='another'))


def test_queue_identity_survives_delayed_callback_and_changed_worker_rejected(journal, context):
    journal.prepare(context)
    queued = AttemptContext.from_queue(context.queue_envelope())
    journal.mark_dispatch_possible(queued)
    journal.record_outcome(queued, 'disconnect', status='local_disconnect')
    # A delayed callback uses the original immutable attempt, regardless of current controller state.
    restarted = StagedOwnership(journal.path)
    assert restarted.observe_receipt(queued, 'receipt', generation_id='gen-observed', cost_usd='.001') == 'recorded'
    assert restarted.inspect(context)['execution'] == 'ambiguous'
    with pytest.raises(OwnershipConflict):
        restarted.mark_dispatch_possible(replace(context, process_instance_id='new-process'))


def test_timeout_missing_receipt_and_unknown_legacy_never_permit_retry(journal, context):
    journal.prepare(context)
    journal.mark_dispatch_possible(context)
    journal.record_outcome(context, 'disconnect', status='local_disconnect')
    retry = replace(context, attempt_id='retry', attempt_number=2, retry_of=context.attempt_id,
                    reservation_id='retry-reservation', retry_authorization_sha256='a' * 64)
    with pytest.raises(OwnershipConflict, match='Ambiguous'):
        journal.prepare(retry)
    with pytest.raises(OwnershipConflict, match='legacy'):
        journal.prepare(replace(retry, retry_of='legacy-unowned'))
    assert journal.inspect(context)['billing'] == 'unchanged_in_original_ledger'


def test_explicit_retry_preserves_original_exposure_and_separate_identity(journal, context):
    journal.prepare(context)
    journal.mark_dispatch_possible(context)
    journal.record_outcome(context, 'eof', status='response_ended', proof_sha256='a' * 64)
    retry = replace(context, attempt_id='retry', attempt_number=2, retry_of=context.attempt_id,
                    reservation_id='retry-reservation', retry_authorization_sha256='b' * 64)
    assert journal.prepare(retry)
    assert journal.inspect(context)['context']['exposure_bound_usd'] == '.17'
    assert journal.inspect(retry)['context']['retry_of'] == context.attempt_id


def test_receipts_idempotent_conflicting_costs_and_provider_ids_preserved(journal, context):
    journal.prepare(context)
    for _ in range(2):
        journal.observe_receipt(context, 'receipt', generation_id='gen-original', cost_usd='.01')
    journal.observe_receipt(context, 'conflicting-cost', generation_id='gen-original', cost_usd='.02')
    second = replace(context, operation_id='other', attempt_id='second', reservation_id='second-reservation')
    journal.prepare(second)
    assert journal.observe_receipt(second, 'duplicate-provider', generation_id='gen-original', cost_usd='.01') == 'conflict'
    assert len(journal.inspect(context)['events']) == 2
    assert journal.inspect(second)['events'][0]['kind'] == 'receipt_identity_conflict'
    with pytest.raises(OwnershipConflict):
        journal.observe_receipt(context, 'receipt', generation_id='gen-original', cost_usd='.03')


def test_saved_answer_grade_pending_survives_crash_and_late_receipt(journal, context):
    journal.prepare(context)
    journal.mark_dispatch_possible(context)
    journal.record_outcome(context, 'answer', status='answer_saved_grade_pending', proof_sha256='a' * 64)
    journal.record_outcome(context, 'disconnect', status='local_disconnect')
    journal = StagedOwnership(journal.path)
    journal.observe_receipt(context, 'late-cost', generation_id='gen-late', cost_usd='0')
    assert journal.inspect(context)['grade_pending']
    with journal.connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM attempts')
    journal.record_outcome(context, 'grade', status='grade_saved', proof_sha256='b' * 64)
    assert not journal.inspect(context)['grade_pending']


def test_journal_failure_prevents_predispatch_progress(journal, context):
    journal.prepare(context)
    with journal.connect() as db:
        db.execute("CREATE TRIGGER disk_failure BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='disk failure'):
        journal.mark_dispatch_possible(context)
    assert journal.inspect(context)['execution'] == 'prepared'


def test_no_prompt_or_payload_fields_in_queue_or_receipts(journal, context):
    journal.prepare(context)
    with pytest.raises(TypeError):
        journal.observe_receipt(context, 'receipt', generation_id='gen-id', prompt='private')
    with pytest.raises(ValueError):
        journal.observe_receipt(context, 'receipt', generation_id='gen-id', token_counts={'payload': 'private'})
