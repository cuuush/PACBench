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
``boundary`` is the batch's ``collected_at`` boundary (the canonical
``collected_at`` of the batch — :func:`ingest_boundary`'s chronological
max over the batch's stamps, full ISO, exact-instant clock — never
the transport day key, never wall-clock ``ingested_at``). Because the
id is derived deterministically from the batch key, any retry of the
same batch reuses the same identity automatically, which is what makes
operational retry idempotent.

Coverage: Hindsight ingest only. The boundary assert and this
deterministic op-id cover the Hindsight ``ingest`` path (adapter +
``live.py`` ``import_batches`` wiring, all three sides via
:func:`ingest_boundary` — one shared derivation, no drift).
Deliberately OUT of scope (declared, not hidden): memcal/lexical
``ingest`` perform no boundary assert and mint no operation id, and
the Hindsight ``maintenance`` pass mints a free-form
``f"pacbench-{at}"`` id outside :func:`same_operation_id`. None of
those paths participates in crash-resume identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

#: Operation-id namespace. Must stay ``uuid.NAMESPACE_URL`` to match the
#: Hindsight adapter's ``ingest`` derivation (which calls
#: :func:`same_operation_id` directly — one shared helper, no drift).
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


def _parse_instant(stamp) -> datetime:
    """Parse one ``collected_at`` stamp to an aware UTC instant.

    Naive stamps are source-discovery times on the simulated benchmark
    clock, which runs UTC by convention (cf. ``authoring._stamp``) —
    so a missing offset means UTC, never host-local wall-clock.
    Raises :exc:`ValueError` on anything unparsable (fail-closed:
    garbage never becomes a boundary).
    """
    text = stamp.strip() if isinstance(stamp, str) else str(stamp).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"refusing unparsable collected_at {stamp!r}: not ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_ts(stamp) -> str:
    """Canonical instant string for one ``collected_at`` stamp.

    Strip → :meth:`datetime.fromisoformat` → naive means UTC →
    re-emit as naive UTC ISO (``YYYY-MM-DDTHH:MM:SS[.ffffff]``, the
    corpus stamp convention from ``authoring._stamp``). Same instant
    yields the same string regardless of input spelling (``+00:00`` /
    ``Z`` / non-zero offset / naive / surrounding whitespace), so
    mixed-format batches can never fork resume identity; already
    canonical stamps pass through verbatim (idempotent), so uniform
    corpus-format batches keep their existing op-ids byte for byte.
    Raises :exc:`ValueError` on garbage.
    """
    return _parse_instant(stamp).replace(tzinfo=None).isoformat()


def ingest_boundary(observations) -> str:
    """Canonical ingest boundary for one batch: chronological max ``collected_at``.

    Derived from the actual supplied observations: every
    ``collected_at`` is mapped through :func:`_normalize_ts` (so
    ``...+00:00``, naive, ``Z``/offset, and padded spellings of one
    instant all collapse to one canonical string) and the max is taken
    over instants, not raw strings — a lexicographic max picks the
    chronologically-wrong stamp under mixed offsets. The Hindsight
    adapter derives its boundary through this helper and asserts the
    caller-supplied ``collected_at`` agrees after the SAME
    normalization, and ``live.py`` derives its ``import_batches``
    boundary through this helper too, so all three sides share one
    derivation and can never drift.

    Fail-closed: an empty batch, a non-mapping item, any item with a
    missing/blank ``collected_at``, or any unparsable stamp raises
    :exc:`ValueError` (malformed boundary — never guess one).
    """
    try:
        items = list(observations)
    except TypeError:
        raise ValueError(
            "refusing ingest batch without observations: "
            "no collected_at boundary derivable") from None
    if not items:
        raise ValueError(
            "refusing empty ingest batch: "
            "no collected_at boundary derivable")
    missing: list[str] = []
    for o in items:
        try:
            stamp = o.get("collected_at")
        except AttributeError:
            stamp = None
        if not stamp or not str(stamp).strip():
            try:
                sid = o.get("source_id", "?")
            except AttributeError:
                sid = "?"
            missing.append(str(sid))
    if missing:
        shown = ", ".join(missing[:5])
        suffix = ", ..." if len(missing) > 5 else ""
        raise ValueError(
            f"refusing batch with missing collected_at on "
            f"{len(missing)} observation(s) ({shown}{suffix}): "
            f"boundary must derive from batch contents")
    ranked: list[tuple[datetime, object]] = []
    for o in items:
        raw = o.get("collected_at")  # type: ignore[union-attr]
        try:
            ranked.append((_parse_instant(raw), raw))
        except ValueError as exc:
            try:
                sid = o.get("source_id", "?")  # type: ignore[union-attr]
            except AttributeError:
                sid = "?"
            raise ValueError(
                f"refusing batch with unparsable collected_at {raw!r} "
                f"on observation {sid!r}: boundary must derive from "
                f"real instants") from exc
    # Chronological max over instants; the winner is re-emitted through
    # the single canonicalizer, so the format lives in exactly one place.
    return _normalize_ts(max(ranked, key=lambda pair: pair[0])[1])


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
