"""Resume/idempotency protocol tests (plan.md P0-8).

Offline, no network, no spend. A fake engine stands in for the
provider; a tiny fake runner record stands in for ``import_batches``.

Covered:

* four-state coverage of classify_write → resume_action;
* simulated crash at the dangerous accept-but-unrecorded point: fake
  engine accepts, runner record missing → action is
  query-status-or-rebuild-from-prefix, never a blind append;
* op-id reuse on genuine-uncertainty retry (deterministic, matches the
  Hindsight adapter's uuid5(bank:collected_at-boundary) derivation);
* empty-success (success with nothing extracted) receives zero second
  semantic attempts.
"""

import uuid

import pytest

from pacbench.resume import (
    COMPLETED,
    FAILED,
    NEVER_RESUBMIT,
    NOT_SUBMITTED,
    QUERY_STATUS_OR_REBUILD,
    SUBMIT,
    SUBMITTED_UNKNOWN,
    classify_write,
    resume_action,
    same_operation_id,
)


# -- four-state coverage ---------------------------------------------------


@pytest.mark.parametrize(
    "submitted, recorded, status, state, action",
    [
        # Definitely not submitted: nothing could have landed → submit.
        (False, False, None, NOT_SUBMITTED, SUBMIT),
        (False, False, "failed", NOT_SUBMITTED, SUBMIT),
        # Submitted but completion unknown → query-or-rebuild, never append.
        (True, False, None, SUBMITTED_UNKNOWN, QUERY_STATUS_OR_REBUILD),
        (True, False, "ok", SUBMITTED_UNKNOWN, QUERY_STATUS_OR_REBUILD),
        (True, False, "failed", SUBMITTED_UNKNOWN, QUERY_STATUS_OR_REBUILD),
        # Submitted + recorded + ok → never resubmit.
        (True, True, "ok", COMPLETED, NEVER_RESUBMIT),
        (True, True, None, COMPLETED, NEVER_RESUBMIT),
        (True, True, "success", COMPLETED, NEVER_RESUBMIT),
        # Empty-success is still success → never resubmit.
        (True, True, "empty", COMPLETED, NEVER_RESUBMIT),
        (True, True, "empty-success", COMPLETED, NEVER_RESUBMIT),
        (True, True, "EMPTY", COMPLETED, NEVER_RESUBMIT),
        # Submitted + recorded + failed → submit again (same op id).
        (True, True, "failed", FAILED, SUBMIT),
        (True, True, "FAILED", FAILED, SUBMIT),
        (True, True, "error", FAILED, SUBMIT),
    ],
)
def test_four_states_cover_classify_and_action(
        submitted, recorded, status, state, action):
    assert classify_write(submitted, recorded, status) == state
    assert resume_action(classify_write(submitted, recorded, status)) == action


def test_resume_action_rejects_unknown_state():
    with pytest.raises(ValueError):
        resume_action("maybe-landed")


# -- simulated crash at the accept-but-unrecorded point --------------------


class FakeEngine:
    """Provider stand-in: accept() lands the batch under its op id."""

    def __init__(self):
        self.appends = []  # one entry per accepted provider write

    def accept(self, operation_id, payload):
        self.appends.append({"operation_id": operation_id,
                             "payload": list(payload)})
        return {"status": "accepted", "operation_id": operation_id}

    def append_count(self, operation_id):
        return sum(1 for a in self.appends
                   if a["operation_id"] == operation_id)


class FakeRunnerRecord:
    """import_batches stand-in: submitted/completion flags per op id."""

    def __init__(self):
        self.submitted = {}
        self.completion_recorded = {}
        self.status = {}

    def mark_submitted(self, operation_id):
        self.submitted[operation_id] = True

    def mark_completed(self, operation_id, status="ok"):
        self.completion_recorded[operation_id] = True
        self.status[operation_id] = status


def _resume(engine, record, operation_id, payload):
    """Resume one batch: classify, then perform ONLY the mapped action.

    Query-or-rebuild performs zero provider appends (it queries status
    or rebuilds from a prefix — both side-effect-free here); submit
    performs exactly one accept; never-resubmit performs zero.
    """
    state = classify_write(
        submitted=record.submitted.get(operation_id, False),
        completion_recorded=record.completion_recorded.get(operation_id,
                                                           False),
        status=record.status.get(operation_id))
    action = resume_action(state)
    if action == SUBMIT:
        record.mark_submitted(operation_id)
        engine.accept(operation_id, payload)
        record.mark_completed(operation_id)
    elif action == NEVER_RESUBMIT:
        pass
    elif action == QUERY_STATUS_OR_REBUILD:
        # Safe reconciliation path: query op-status / rebuild from a
        # known prefix. Neither re-appends the batch — assert that by
        # doing neither here.
        pass
    else:  # pragma: no cover - resume_action only returns the three above
        raise AssertionError(f"unexpected resume action {action!r}")
    return state, action


def test_crash_at_accept_but_unrecorded_never_blind_appends():
    engine = FakeEngine()
    record = FakeRunnerRecord()
    batch_key = ("pacbench-lifeA-cp1", "2026-03-02T09:00:00+00:00")
    op_id = same_operation_id(batch_key)
    payload = [{"source_id": "m1", "text": "hello"}]

    # Day of the crash: runner submits, provider ACCEPTS, runner dies
    # before recording completion.
    record.mark_submitted(op_id)
    engine.accept(op_id, payload)
    assert op_id not in record.completion_recorded  # the crash

    # Resume: must query-or-rebuild, and the engine must still show
    # exactly one append — no blind second append.
    state, action = _resume(engine, record, op_id, payload)
    assert state == SUBMITTED_UNKNOWN
    assert action == QUERY_STATUS_OR_REBUILD
    assert engine.append_count(op_id) == 1


def test_op_id_reused_on_genuine_uncertainty_retry():
    bank, boundary = "pacbench-lifeB-cp2", "2026-03-03T09:00:00+00:00"
    first = same_operation_id((bank, boundary))
    retry = same_operation_id((bank, boundary))
    assert first == retry  # resume addresses the SAME operation
    # Matches the Hindsight adapter derivation exactly
    # (uuid5(NAMESPACE_URL, f"{bank}:{collected_at}")).
    assert first == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{bank}:{boundary}"))
    # String form agrees with pair form.
    assert same_operation_id(f"{bank}:{boundary}") == first
    # Different batches get different identities.
    assert same_operation_id((bank, "2026-03-04T09:00:00+00:00")) != first
    assert same_operation_id(("pacbench-other", boundary)) != first


def test_empty_success_receives_zero_second_attempts():
    engine = FakeEngine()
    record = FakeRunnerRecord()
    op_id = same_operation_id(
        ("pacbench-lifeC-cp3", "2026-03-04T09:00:00+00:00"))
    payload = [{"source_id": "m9", "text": "low-signal message"}]

    # Provider succeeded but extracted nothing; runner recorded that.
    record.mark_submitted(op_id)
    engine.accept(op_id, payload)
    record.mark_completed(op_id, status="empty-success")

    # Resume must never resubmit — even though PACBench disliked the
    # (empty) result. Zero second semantic attempts.
    state, action = _resume(engine, record, op_id, payload)
    assert state == COMPLETED
    assert action == NEVER_RESUBMIT
    assert engine.append_count(op_id) == 1


def test_failed_batch_retries_under_same_op_id():
    engine = FakeEngine()
    record = FakeRunnerRecord()
    op_id = same_operation_id(
        ("pacbench-lifeD-cp4", "2026-03-05T09:00:00+00:00"))
    payload = [{"source_id": "m2", "text": "retry me"}]

    record.mark_submitted(op_id)
    engine.accept(op_id, payload)
    record.mark_completed(op_id, status="failed")

    state, action = _resume(engine, record, op_id, payload)
    assert state == FAILED
    assert action == SUBMIT
    # Retry reuses the identical operation identity (idempotent retry).
    assert engine.appends[-1]["operation_id"] == op_id
