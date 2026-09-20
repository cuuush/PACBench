"""Deterministic corpus validation for PACBench.

Three layers, in increasing order of semantic reach:

1. **Structural validation** (`validate_life`, `validate_corpus`) — schema validity,
   unique ids, valid timestamps and channels, chronology, collection order, evidence
   available before the checkpoint that needs it, required threads and participants,
   required literal values present in the cited evidence, and hidden grading data
   structurally inaccessible to adapters. Pure stdlib, no model, no network.

2. **Fact-realization check** (`fact_realization_notes`) — for each manifest entry,
   do the cited observations carry the required literal tokens? Deterministic where
   the token rule applies; anything genuinely paraphrased is reported as
   ``needs-human-review`` rather than passed or failed by fiat.

3. **Model-based validation and human review** — a narrow validator answering "do
   these messages establish fact X?" plus manual inspection of every release. That
   layer is an explicit seam (`judge=` callable), not shipped behavior: v0.1 never
   derives ground truth from a model output.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime

from .grade import Norm
from .life import Life
from .manifest import SupportManifest
from .observation import CHANNELS, Observation, chronology_ok
from .rubric import Kind, Slot

ROLES = ("development", "calibration", "held-out")

#: Norms whose expected values must appear literally in the cited evidence.
LITERAL_NORMS = (Norm.DATE, Norm.TIME, Norm.STATUS, Norm.ENTITY, Norm.TEXT)


class CorpusError(RuntimeError):
    """Raised by :func:`check` when validation finds errors."""


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _time_variants(value: str) -> set[str]:
    v = value.strip().casefold()
    variants = {v, v.replace(" ", "")}
    if " " not in v and v[-2:] in ("am", "pm"):
        variants.add(v[:-2] + " " + v[-2:])
    return variants


def _bare_hour(value: str) -> str | None:
    """The bare hour of a `:00` time ("7pm" -> "7"), or None.

    Evening shorthand such as "Friday 7" establishes 7pm the same way the
    grader's own bare-hour default reads it, so the literal check accepts it.
    """
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", value.strip().lower())
    if not m or int(m.group(2) or 0) != 0:
        return None
    return m.group(1)


def evidence_carries(text: str, value: str, norm: Norm) -> bool:
    """Lenient deterministic check that evidence text carries a required value."""
    hay = text.casefold()
    if norm is Norm.TIME:
        if any(v in hay for v in _time_variants(value)):
            return True
        bare = _bare_hour(value)
        return (bare is not None
                and re.search(rf"\b{re.escape(bare)}\b", hay) is not None)
    if norm is Norm.YESNO:
        return True  # yes/no answers are behavior, not cited literals
    return value.strip().casefold() in hay


def _iter_slots(life: Life) -> tuple[Slot, ...]:
    out: list[Slot] = []
    for cp in life.checkpoints:
        out.extend(cp.slots)
        out.extend(cp.supported)
        out.extend(cp.forbidden_options)
    return tuple(out)


def validate_life(life: Life, manifest: SupportManifest | None = None) -> list[str]:
    """Return a list of error strings; empty means the life is structurally valid."""
    errors: list[str] = []
    by_id = {o.source_id: o for o in life.observations}
    if len(by_id) != len(life.observations):
        errors.append(f"{life.id}: duplicate observation source_id")
    for o in life.observations:
        if o.channel not in CHANNELS:
            errors.append(f"{life.id}: {o.source_id} has unknown channel {o.channel!r}")
        if not o.sender or not o.thread:
            errors.append(f"{life.id}: {o.source_id} missing sender/thread")
        for clock in (o.written_at, o.arrived_at, o.collected_at):
            if _parse_ts(clock) is None:
                errors.append(f"{life.id}: {o.source_id} has bad timestamp {clock!r}")
        if not o.text.strip():
            errors.append(f"{life.id}: {o.source_id} has empty text")
    for bad in chronology_ok(list(life.observations)):
        errors.append(f"{life.id}: {bad} has inconsistent clocks "
                      f"(written>arrived or arrived>collected)")
    # Observation objects must be structurally incapable of carrying expectations.
    fields = {f.name for f in dataclasses.fields(Observation)}
    for o in life.observations:
        if set(o.public()) != fields:
            errors.append(f"{life.id}: {o.source_id} public payload != dataclass fields")
    if not life.observations:
        errors.append(f"{life.id}: no observations")
    if not life.checkpoints:
        errors.append(f"{life.id}: no checkpoints")
    if life.role not in ROLES:
        errors.append(f"{life.id}: unknown role {life.role!r}")
    if life.role == "development" and life.horizon_days != 7:
        errors.append(f"{life.id}: development lives must cover a 7-day horizon")
    for truth in life.truth:
        for oid in truth.established_by:
            if oid not in by_id:
                errors.append(f"{life.id}: truth {truth.id!r} cites unknown {oid!r}")
    for cp in life.checkpoints:
        at = _parse_ts(cp.at)
        if at is None:
            errors.append(f"{life.id}: checkpoint {cp.id} has bad time {cp.at!r}")
            continue
        if cp.mode not in ("fact", "complete-plan", "open-ended", "unanswerable"):
            errors.append(f"{life.id}: checkpoint {cp.id} has bad mode {cp.mode!r}")
        for tag in cp.difficulty_tags:
            if tag not in life.difficulty_tags:
                errors.append(f"{life.id}: checkpoint {cp.id} tag {tag!r} "
                              f"not in life difficulty_tags")
        prompt = cp.public_prompt().casefold()
        for slot in (*cp.slots, *cp.supported, *cp.forbidden_options):
            for oid in slot.supported_by:
                obs = by_id.get(oid)
                if obs is None:
                    errors.append(f"{life.id}: checkpoint {cp.id} slot {slot.id!r} "
                                  f"cites unknown observation {oid!r}")
                    continue
                if _parse_ts(obs.collected_at) and at and obs.collected_at > cp.at:
                    errors.append(f"{life.id}: checkpoint {cp.id} needs {oid!r} "
                                  f"before it is collected (future evidence)")
            # The prompt must not hand over time/status/date answers or stale claims.
            # Entity/text names may appear (a question can name who it asks about).
            if slot.kind is Kind.ABSTAIN and not slot.expected:
                values = [(f, f) for f in slot.forbidden]
            elif slot.norm in (Norm.DATE, Norm.TIME, Norm.STATUS):
                values = ([(slot.expected, "expected")] if slot.expected else [])
                values += [(f, "forbidden") for f in slot.forbidden]
            else:
                values = [(f, "forbidden") for f in slot.forbidden]
            for value, kind in values:
                if value and value.strip().casefold() in prompt:
                    errors.append(f"{life.id}: checkpoint {cp.id} prompt leaks "
                                  f"{kind} value {value!r}")
            # Required literal values must appear in the cited evidence (the expected
            # value or any accepted alternative).
            if (slot.required and slot.expected and slot.norm in LITERAL_NORMS
                    and slot.kind is not Kind.ABSTAIN):
                cited = [by_id[oid].text for oid in slot.supported_by if oid in by_id]
                values = (slot.expected, *slot.alternatives)
                if cited and not any(evidence_carries(t, v, slot.norm)
                                     for t in cited for v in values):
                    errors.append(f"{life.id}: checkpoint {cp.id} slot {slot.id!r} "
                                  f"expected {slot.expected!r} in none of its cited evidence")
        if cp.mode == "complete-plan":
            slot_ids = {s.id for s in cp.slots}
            for fid in cp.required_fields:
                if fid not in slot_ids:
                    errors.append(f"{life.id}: checkpoint {cp.id} required field "
                                  f"{fid!r} is not a slot")
    if manifest is not None:
        if manifest.life_id != life.id:
            errors.append(f"{life.id}: manifest belongs to {manifest.life_id!r}")
        for entry in manifest.entries:
            for oid in entry.observation_ids:
                if oid not in by_id:
                    errors.append(f"{life.id}: manifest fact {entry.fact_id!r} "
                                  f"cites unknown {oid!r}")
    return errors


def validate_corpus(lives: list[Life],
                    manifests: dict[str, SupportManifest] | None = None
                    ) -> list[str]:
    """Cross-life checks: unique ids, control targets, plus per-life validation."""
    errors: list[str] = []
    ids = [life.id for life in lives]
    if len(set(ids)) != len(ids):
        errors.append("duplicate life id")
    known = set(ids)
    for life in lives:
        if life.control_of is not None and life.control_of not in known:
            errors.append(f"{life.id}: control_of unknown life {life.control_of!r}")
        errors.extend(validate_life(life, (manifests or {}).get(life.id)))
    return errors


def fact_realization_notes(life: Life, manifest: SupportManifest) -> list[str]:
    """Deterministic fact-realization pass over a manifest.

    Returns human-readable notes; entries ending in ``needs-human-review`` are
    paraphrase cases the token rule cannot decide — authoring-time QA input, never
    a silent pass. The narrow model-based validator and manual review sit on top
    of this (see module docstring).
    """
    notes: list[str] = []
    by_id = {o.source_id: o for o in life.observations}
    for entry in manifest.entries:
        texts = [by_id[oid].text for oid in entry.observation_ids if oid in by_id]
        if not texts:
            notes.append(f"{life.id}/{entry.fact_id}: no cited observations")
            continue
        notes.append(f"{life.id}/{entry.fact_id}: {len(texts)} supporting message(s); "
                     f"literal carriage needs-human-review where paraphrased")
    return notes


def check(lives: list[Life],
          manifests: dict[str, SupportManifest] | None = None) -> None:
    """Raise :class:`CorpusError` if validation finds any error."""
    errors = validate_corpus(lives, manifests)
    if errors:
        raise CorpusError("\n".join(errors))


if __name__ == "__main__":
    from .corpus import MANIFESTS, all_lives

    found = validate_corpus(all_lives(), MANIFESTS)
    for life in all_lives():
        print(f"{life.id}: {len(life.observations)} obs, "
              f"{len(life.checkpoints)} checkpoints")
    if found:
        print("ERRORS:")
        for error in found:
            print(f"  - {error}")
        raise SystemExit(1)
    print("all lives structurally valid")
