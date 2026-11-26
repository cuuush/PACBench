"""Honcho receipt lookups shared by the live index and retained dictionary readers."""
import json

from ...dispatch_ownership import OwnershipConflict
from ...receipt_projection import AdmissionSpec


def _generation(row):
    if row.get('finished'):
        # A finished row without an ID also participates in duplicate detection.
        return _key(row.get('upstream_request_id'))


def _invalid(row):
    from .first_answer_admission import usable_response

    return bool(row.get('finished') and not usable_response(row))


def _key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _scopes(row):
    slot = row.get('workflow_slot')
    if not isinstance(slot, dict):
        return
    if 'honcho_first_answer' in slot:
        yield 'driver', 'honcho'
        yield 'driver_question', slot.get('question_id')
    if slot.get('kind') == 'answer':
        yield 'answer_question', slot.get('question_id')
    if (release := slot.get('honcho_daily_release')) is not None:
        yield 'source_release', _key(release)
    if job := slot.get('honcho_chat_work'):
        if not isinstance(job, dict) or 'purpose' not in job:
            yield 'malformed_chat_scope', 'honcho'
        else:
            purpose = job['purpose']
            queues = job.get('queue_ids')
            if not isinstance(queues, (list, tuple)) or any(type(q) not in (int, str) for q in queues):
                yield 'malformed_chat_queue', purpose
            else:
                for queue_id in queues:
                    yield 'chat_queue', (purpose, queue_id)
    if isinstance(peer := slot.get('honcho_peer_read'), dict):
        operation = peer.get('operation')
        yield 'peer_operation', operation
        yield 'peer_claim', (operation, peer.get('run_id'), peer.get('claim'))


HONCHO_RECEIPTS = AdmissionSpec('honcho-v1', _generation, _invalid, _scopes)


def _for_scope(rows, kind, *keys):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.for_scope(kind, *keys)
    wanted = [(kind, key) for key in keys]
    return [row for row in rows.values() if any(scope in wanted for scope in _scopes(row))]


def has_unusable_responses(rows):
    """A duplicate or unusable finished response blocks further Honcho work."""
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.duplicate_generations() or rows.invalid_terminal_responses()

    completed = [row for row in rows.values() if row.get('finished')]
    return (len({_generation(row) for row in completed}) != len(completed)
            or any(map(_invalid, completed)))


def last_driver(rows):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.last_for_scope('driver', 'honcho')
    drivers = _for_scope(rows, 'driver', 'honcho')
    return drivers[-1] if drivers else None


def driver_history(rows, question_id):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return (rows.count_for_scope('driver_question', question_id),
                rows.last_for_scope('driver_question', question_id))
    previous = _for_scope(rows, 'driver_question', question_id)
    return len(previous), previous[-1] if previous else None


def turn_answers(rows, question_id):
    return _for_scope(rows, 'answer_question', question_id)


def release_attempts(rows, release):
    # Match the whole receipt binding, including its path.
    return _for_scope(rows, 'source_release', _key(release))


def chat_attempts(rows, job):
    purpose = job['purpose']
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        malformed = (rows.count_for_scope('malformed_chat_scope', 'honcho')
                     or rows.count_for_scope('malformed_chat_queue', purpose))
    else:
        malformed = any(_for_scope(rows, kind, key) for kind, key in
                        (('malformed_chat_scope', 'honcho'), ('malformed_chat_queue', purpose)))
    if malformed:
        raise OwnershipConflict('A retained Honcho chat queue lineage is malformed')
    return _for_scope(rows, 'chat_queue', *((purpose, queue_id) for queue_id in job['queue_ids']))


def peer_history(rows, proof):
    operation = proof['operation']
    witness = (operation, proof['run_id'], proof['claim'])
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return (rows.count_for_scope('peer_operation', operation),
                rows.count_for_scope('peer_claim', witness))
    previous = _for_scope(rows, 'peer_operation', operation)
    matched = sum(('peer_claim', witness) in _scopes(row) for row in previous)
    return len(previous), matched
