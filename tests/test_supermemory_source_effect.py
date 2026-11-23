import pytest

from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.supermemory_source_effect import SourceEffect, check_update_history

DOCUMENT = 'd' * 22
UPDATE = 'u' * 22
RELEASED = {DOCUMENT}


def witness(update=UPDATE, document=DOCUMENT):
    return {'updateId': update, 'documentId': document}


def history(effect, *, execution='response_ended', cost=True, request='wire-a'):
    return [{'request_sha256': request, 'finished': 'now', 'dispatch_execution': execution,
             'state': 'settled' if cost else 'receipt_missing', 'http_status': 200,
             'workflow_slot': {'stock_source_effect': vars(effect), 'batch_id': 'old-stage'}}]


@pytest.mark.parametrize('bad', [None, 'step:123', witness(update=None), witness(document='f' * 22)])
def test_unbound_or_foreign_native_update_refused(bad):
    with pytest.raises(OwnershipConflict):
        SourceEffect.from_native(bad, RELEASED)


@pytest.mark.parametrize('prior_wire', ['wire-a', 'wire-different'])
def test_durable_update_cannot_replay_even_after_wire_or_stage_changes(prior_wire):
    effect = SourceEffect.from_native(witness(), RELEASED)
    with pytest.raises(OwnershipConflict, match='already prepared'):
        check_update_history(history(effect, request=prior_wire), effect)


def test_distinct_update_to_same_document_can_follow_a_settled_receipt():
    first = SourceEffect.from_native(witness(), RELEASED)
    second = SourceEffect.from_native(witness(update='v' * 22), RELEASED)
    assert first != second
    check_update_history(history(first), second)
    check_update_history(history(first, cost=False), second)
    with pytest.raises(OwnershipConflict, match='unambiguous end'):
        check_update_history(history(first, execution='ambiguous'), second)
