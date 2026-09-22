"""Per-life lifecycle cost rollup (plan.md P0-15 — observability only, no gates).

One rollup per evaluated life with exactly seven rows: ingestion,
maintenance/background, question-time provider work, Hermes driver work,
retries, opaque/unpriced work, and the known total.

Rules (binding):

* Unknown stays unknown (``None`` with an ``"unknown"`` status) — never zero.
* NEVER compare cost per API call across providers: provider call shapes
  intentionally differ, so per-call ratios are meaningless. Compare
  per-life lifecycle totals only. The :data:`CostProhibition` note states
  this and is embedded in the rollup output itself.
* This module is pure observability: no thresholds, no gates, no verdicts.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: Prohibition note (P0-15): per-API-call cost comparison is meaningless
#: because provider call shapes intentionally differ. Embedded in rollup
#: output so every consumer sees it next to the numbers.
CostProhibition = (
    "PROHIBITED: do not compare cost per API call across providers — "
    "provider call shapes intentionally differ, so per-call ratios are "
    "meaningless. Compare per-life lifecycle totals only (P0-15)."
)

INGESTION = "ingestion"
MAINTENANCE = "maintenance-background"
QUESTION_TIME = "question-time-provider"
DRIVER = "hermes-driver"
RETRIES = "retries"
OPAQUE = "opaque-unpriced"
TOTAL = "total-known"

#: The rollup always contains exactly these seven rows, in this order.
ROW_ORDER = (INGESTION, MAINTENANCE, QUESTION_TIME, DRIVER, RETRIES, OPAQUE, TOTAL)

TOTAL_LABEL = "total-known-excluding-unknown"

_LABELS = {
    INGESTION: "ingestion",
    MAINTENANCE: "maintenance / background processing",
    QUESTION_TIME: "question-time provider work",
    DRIVER: "Hermes driver work",
    RETRIES: "retries",
    OPAQUE: "opaque / unpriced work",
    TOTAL: TOTAL_LABEL,
}

UNKNOWN = "unknown"
PARTIAL = "partial"
KNOWN = "known"

# Fallback field names accepted when reading token counts, grounded in the
# runner's manifest vocabulary (pacbench/live.py turn/session records use
# turn_input_tokens / session_input_tokens rather than bare input_tokens).
_IN_KEYS = ("input_tokens", "turn_input_tokens", "session_input_tokens")
_OUT_KEYS = ("output_tokens", "turn_output_tokens", "session_output_tokens")


def _field(item: Any, *names: str) -> Any:
    """Read the first present field from a Usage-like object or mapping."""
    if item is None:
        return None
    for name in names:
        if isinstance(item, Mapping):
            if name in item:
                return item[name]
        else:
            value = getattr(item, name, None)
            if value is not None:
                return value
    return None


def _num(value: Any) -> int | float | None:
    """Pass through a numeric metric; anything non-numeric is unknown."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _sum_known(values: Iterable[Any]) -> int | float | None:
    """Sum known values; ``None`` when nothing is known (never zero)."""
    known = [_num(v) for v in values]
    known = [v for v in known if v is not None]
    if not known:
        return None
    return sum(known)


def _usage_triple(item: Any) -> tuple[Any, Any, Any]:
    """Extract (input_tokens, output_tokens, cost_usd) from one entry."""
    if item is None:
        return (None, None, None)
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return (item[0], item[1], None)
    return (
        _field(item, *_IN_KEYS),
        _field(item, *_OUT_KEYS),
        _field(item, "cost_usd"),
    )


def _sum_usages(usages: Iterable[Any] | None) -> tuple[Any, Any, Any, list[str]]:
    """Sum one lifecycle phase's entries; unknown stays unknown, never zero."""
    ins: list[Any] = []
    outs: list[Any] = []
    costs: list[Any] = []
    notes: list[str] = []
    for item in usages or ():
        in_tok, out_tok, cost = _usage_triple(item)
        ins.append(in_tok)
        outs.append(out_tok)
        costs.append(cost)
        note = _field(item, "notes", "note", "detail")
        if note is None:
            continue
        if isinstance(note, (tuple, list)):
            notes.extend(str(n) for n in note)
        else:
            notes.append(str(note))
    return (_sum_known(ins), _sum_known(outs), _sum_known(costs), notes)


def _status(in_tok: Any, out_tok: Any, cost: Any) -> str:
    """Row completeness: known / partial / unknown (unknown is a string)."""
    missing = sum(v is None for v in (in_tok, out_tok, cost))
    if missing == 3:
        return UNKNOWN
    if missing:
        return PARTIAL
    return KNOWN


def _row(label: str, in_tok: Any, out_tok: Any, cost: Any,
         detail: str = "") -> dict:
    return {
        "label": label,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost,
        "status": _status(in_tok, out_tok, cost),
        "detail": detail,
    }


def _retries_row(retries: Any) -> dict:
    """Normalize the retries input: a count, priced entries, or both."""
    if retries is None:
        row = _row(_LABELS[RETRIES], None, None, None,
                   "no retry information recorded; unknown, never zero")
        row["attempts"] = None
        return row
    if isinstance(retries, bool):
        raise TypeError("retries must be a count, entries, or mapping, not bool")
    if isinstance(retries, int):
        row = _row(
            _LABELS[RETRIES], None, None, None,
            f"{retries} retry attempt(s); tokens/cost unknown, never zero",
        )
        row["attempts"] = retries
        return row
    if isinstance(retries, Mapping) and "attempts" in retries:
        in_tok, out_tok, cost = _usage_triple(retries)
        attempts = _num(retries["attempts"])
        row = _row(_LABELS[RETRIES], _num(in_tok), _num(out_tok), _num(cost),
                   str(retries.get("detail", "retries with priced entries")))
        row["attempts"] = attempts
        return row
    in_tok, out_tok, cost, notes = _sum_usages(retries)
    entries = list(retries or ())
    detail = "; ".join(notes) if notes else (
        f"{len(entries)} priced retry attempt(s)")
    row = _row(_LABELS[RETRIES], in_tok, out_tok, cost, detail)
    row["attempts"] = len(entries)
    return row


def _opaque_row(opaque_notes: Any) -> dict:
    """Opaque/unpriced work is always unknown — never zero, never estimated."""
    if opaque_notes is None:
        notes: list[str] = []
    elif isinstance(opaque_notes, str):
        notes = [opaque_notes]
    else:
        notes = [str(n) for n in opaque_notes]
    detail = "; ".join(notes) if notes else (
        "no opaque/unpriced work recorded; unknown, never zero")
    return _row(_LABELS[OPAQUE], None, None, None, detail)


def rollup_lifecycle(
    ingest_usages: Iterable[Any] | None = (),
    maintenance_usages: Iterable[Any] | None = (),
    question_turn_tokens: Iterable[Any] | None = None,
    driver_tokens: Any = None,
    retries: Any = None,
    opaque_notes: Any = (),
) -> dict:
    """Roll one life's lifecycle cost into exactly seven rows.

    ``ingest_usages`` / ``maintenance_usages`` take adapter ``Usage`` objects
    (or mappings) as returned by ``adapter.ingest`` / ``adapter.maintenance``
    and the ``usage()`` reporters (unknown fields stay ``None``).
    ``question_turn_tokens`` takes per-turn token entries — ``(in, out)``
    pairs, mappings, or Usage-like objects (``turn_input_tokens`` /
    ``turn_output_tokens`` spellings accepted). ``driver_tokens`` takes the
    Hermes driver session totals in the same shapes (``session_input_tokens``
    accepted) or a single entry. ``retries`` takes a retry-attempt count, an
    iterable of priced retry entries, or a mapping with ``attempts`` plus
    token fields. ``opaque_notes`` takes free-text notes on opaque/unpriced
    work, which is always reported unknown.

    Returns a dict with exactly the seven :data:`ROW_ORDER` rows. Each row
    carries ``input_tokens`` / ``output_tokens`` / ``cost_usd`` (``None``
    when unknown — never zero), a ``status`` of ``"known"`` / ``"partial"`` /
    ``"unknown"``, and a ``detail`` string. The ``"total-known"`` row sums
    only the five priced rows (opaque work is excluded by definition), is
    labeled ``"total-known-excluding-unknown"``, lists the unknown
    contributors under ``unknown_components``, and carries the
    :data:`CostProhibition` note. No per-API-call column is produced —
    provider call shapes intentionally differ, so such a ratio would be
    meaningless.
    """
    in_ing, out_ing, cost_ing, notes_ing = _sum_usages(ingest_usages)
    in_mnt, out_mnt, cost_mnt, notes_mnt = _sum_usages(maintenance_usages)

    if question_turn_tokens is None:
        in_q, out_q, cost_q, notes_q = (None, None, None, [])
    elif isinstance(question_turn_tokens, Mapping) or (
            isinstance(question_turn_tokens, (tuple, list))
            and len(question_turn_tokens) == 2
            and all(isinstance(v, (int, float)) or v is None
                    for v in question_turn_tokens)):
        in_q, out_q, cost_q = _usage_triple(question_turn_tokens)
        notes_q = []
    else:
        in_q, out_q, cost_q, notes_q = _sum_usages(question_turn_tokens)

    if driver_tokens is None:
        in_drv, out_drv, cost_drv, notes_drv = (None, None, None, [])
    elif isinstance(driver_tokens, Mapping) or (
            isinstance(driver_tokens, (tuple, list))
            and len(driver_tokens) == 2
            and all(isinstance(v, (int, float)) or v is None
                    for v in driver_tokens)):
        in_drv, out_drv, cost_drv = _usage_triple(driver_tokens)
        notes_drv = []
    else:
        in_drv, out_drv, cost_drv, notes_drv = _sum_usages(driver_tokens)

    rows: dict = {
        INGESTION: _row(_LABELS[INGESTION], in_ing, out_ing, cost_ing,
                       "; ".join(notes_ing)),
        MAINTENANCE: _row(_LABELS[MAINTENANCE], in_mnt, out_mnt, cost_mnt,
                         "; ".join(notes_mnt)),
        QUESTION_TIME: _row(_LABELS[QUESTION_TIME], in_q, out_q, cost_q,
                           "; ".join(notes_q)),
        DRIVER: _row(_LABELS[DRIVER], in_drv, out_drv, cost_drv,
                    "; ".join(notes_drv)),
        RETRIES: _retries_row(retries),
        OPAQUE: _opaque_row(opaque_notes),
    }

    priced = [INGESTION, MAINTENANCE, QUESTION_TIME, DRIVER, RETRIES]
    total_in = _sum_known(rows[r]["input_tokens"] for r in priced)
    total_out = _sum_known(rows[r]["output_tokens"] for r in priced)
    total_cost = _sum_known(rows[r]["cost_usd"] for r in priced)
    unknown_components = [r for r in priced if rows[r]["status"] == UNKNOWN]

    total = _row(_LABELS[TOTAL], total_in, total_out, total_cost,
                 CostProhibition)
    if unknown_components and total["status"] == KNOWN:
        # Every aggregate is a number, yet some priced rows contributed
        # nothing known: the total is partial by construction, never whole.
        total["status"] = PARTIAL
    total["unknown_components"] = unknown_components
    total["note"] = CostProhibition
    rows[TOTAL] = total

    return {key: rows[key] for key in ROW_ORDER}
