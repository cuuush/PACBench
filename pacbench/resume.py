"""Resume/idempotency protocol (plan.md P0-8): pure helpers, no I/O.

Every provider write passes through exactly one dangerous point: the
provider has accepted the batch but the runner has not yet recorded
completion. A crash there leaves the write in genuine operational
uncertainty — it may or may not have landed. These helpers classify
that uncertainty and map each class to its one legal resume action.

They are pure (offline-testable) by design: no engine imports, no
clock reads, no filesystem access. The live wiring (recording the
submitted/completed flags in ``import_batches``, querying Hindsight
op-status, rebuilding from a prefix) belongs to the runner/adapter
layer, not here.

Operation identity convention (must match
``provider_adapters/hindsight_adapter.py`` ``ingest`` and
``pacbench/live.py`` ``import_batches``)::

    operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{bank}:{boundary}"))

where ``bank`` is the provider bank (``f"pacbench-{namespace}"``) and
``boundary`` is the batch's ``collected_at`` boundary (the max
``collected_at`` of the batch, full ISO, exact-instant clock — never
the transport day key, never wall-clock ``ingested_at``). Because the
id is derived deterministically from the batch key, any retry of the
same batch reuses the same identity automatically, which is what makes
operational retry idempotent.
"""

from __future__ import annotations

import uuid

#: Operation-id namespace. Must stay ``uuid.NAMESPACE_URL`` to match the
#: Hindsight adapter's ``ingest`` derivation.
OPERATION_ID_NAMESPACE = uuid.NAMESPACE_URL

# -- write states ---------------------------------------------------------

#: The provider never received the batch. Safe to submit.
NOT_SUBMITTED = "not-submitted"
#: The provider accepted the batch but the runner died before recording
#: completion. Genuine uncertainty: the write may or may not have
#: landed. Never blindly re-append.
SUBMITTED_UNKNOWN = "submitted-unknown"
#: The provider reported success — including success that semantically
#: extracted nothing ("empty-success"). Never resubmit.
COMPLETED = "completed"
#: The provider reported failure before completion was recorded.
#: Safe to submit again under the same operation identity.
FAILED = "failed"

WRITE_STATES = (NOT_SUBMITTED, SUBMITTED_UNKNOWN, COMPLETED, FAILED)

# -- resume actions --------------------------------------------------------

#: Fresh submit under the deterministic operation id (states:
#: not-submitted, failed).
SUBMIT = "submit"
#: Query provider op-status where supported; if safe reconciliation is
#: impossible, rebuild an isolated realization from a known prefix
#: (state: submitted-unknown). Never a blind re-append.
QUERY_STATUS_OR_REBUILD = "query-status-or-rebuild-from-prefix"
#: Idempotent no-op: the write already landed (state: completed,
#: including empty-success).
NEVER_RESUBMIT = "never-resubmit"

RESUME_ACTIONS = (SUBMIT, QUERY_STATUS_OR_REBUILD, NEVER_RESUBMIT)

#: Provider-reported failure markers that classify a recorded write as
#: ``failed`` rather than ``completed``. Matched case-insensitively
#: against the ``status`` argument of :func:`classify_write`.
FAILURE_STATUSES = frozenset({"failed", "failure", "error", "errored"})

#: Success-with-nothing-extracted markers. These are still ``completed``
#: writes: the operation succeeded, the provider recorded it, and a
#: second semantic attempt is forbidden even though PACBench may
#: dislike the (empty) result.
EMPTY_SUCCESS_STATUSES = frozenset({"empty", "empty-success", "no-ids"})


def classify_write(submitted: bool, completion_recorded: bool,
                   status: str | None) -> str:
    """Classify one provider write into its resume state.

    ``submitted``: the runner sent the batch to the provider.
    ``completion_recorded``: the runner recorded the provider's
    completion reply before any crash (i.e. the write is *not* sitting
    on the dangerous accept-but-unrecorded point).
    ``status``: provider-reported outcome where known (``None`` when
    the runner died before hearing back). Failure markers (case
    insensitive, see :data:`FAILURE_STATUSES`) mean ``failed``;
    anything else recorded — including empty-success markers (see
    :data:`EMPTY_SUCCESS_STATUSES`) — means ``completed``.

    Rules:

    * not submitted → ``not-submitted`` (nothing could have landed);
    * submitted but completion unrecorded → ``submitted-unknown``,
      regardless of any guessed status — a guess is not a reply;
    * submitted and recorded, status failed → ``failed``;
    * submitted and recorded, otherwise → ``completed`` (empty-success
      included: success is success).
    """
    if not submitted:
        return NOT_SUBMITTED
    if not completion_recorded:
        return SUBMITTED_UNKNOWN
    if isinstance(status, str) and status.strip().lower() in FAILURE_STATUSES:
        return FAILED
    return COMPLETED


def resume_action(state: str) -> str:
    """Map a write state to its one legal resume action.

    * ``not-submitted`` → ``submit`` (nothing was sent);
    * ``submitted-unknown`` → ``query-status-or-rebuild-from-prefix``
      (query provider op-status where supported, else rebuild an
      isolated realization from a known prefix — never blind re-append);
    * ``completed`` → ``never-resubmit`` (includes empty-success: zero
      second semantic attempts);
    * ``failed`` → ``submit`` (retry under the same deterministic
      operation identity).

    Raises :exc:`ValueError` on unknown states: an unrecognized state
    must fail closed, never default to a submit.
    """
    if state == NOT_SUBMITTED:
        return SUBMIT
    if state == SUBMITTED_UNKNOWN:
        return QUERY_STATUS_OR_REBUILD
    if state == COMPLETED:
        return NEVER_RESUBMIT
    if state == FAILED:
        return SUBMIT
    raise ValueError(f"unknown write state {state!r}: refusing to guess a resume action")


def _canonical_batch_key(batch_key) -> str:
    """Render ``batch_key`` as the ``"{bank}:{boundary}"`` op-id seed.

    Accepts a ``(bank, collected_at_boundary)`` pair (tuple/list) or an
    already-joined ``"bank:boundary"`` string (used verbatim — never
    re-split, since ISO boundaries themselves contain colons).
    """
    if isinstance(batch_key, (tuple, list)):
        if len(batch_key) != 2:
            raise ValueError(
                f"batch_key pair must be (bank, collected_at_boundary), "
                f"got {batch_key!r}")
        bank, boundary = batch_key
        return f"{bank}:{boundary}"
    if isinstance(batch_key, str):
        if ":" not in batch_key:
            raise ValueError(
                f"batch_key string must already be 'bank:boundary', "
                f"got {batch_key!r}")
        return batch_key
    raise TypeError(
        f"batch_key must be a (bank, boundary) pair or 'bank:boundary' "
        f"string, got {type(batch_key).__name__}")


def same_operation_id(batch_key) -> str:
    """Deterministic operation id for one ingest batch.

    ``uuid5(NAMESPACE_URL, "bank:collected_at-boundary")`` — the same
    derivation the Hindsight adapter uses in ``ingest`` (``bank`` +
    ``collected_at`` boundary, i.e. the batch's max ``collected_at``,
    full ISO). Calling it again for the same batch — including on
    resume after a crash — yields the identical id, so operational
    retry is idempotent by construction. Different batches (different
    bank or boundary) yield different ids.
    """
    return str(uuid.uuid5(OPERATION_ID_NAMESPACE,
                          _canonical_batch_key(batch_key)))
