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
from .rubric import ERROR_CLASSES, Checkpoint, Kind, Slot

#: Corpus split roles (§39 + ruling 2026-09-21 Q7). ``exposed_after_review``
#: marks a life whose detailed inspection (evidence, rubric, output,
#: trace, failure) could have informed implementation, prompts, grader,
#: or provider config: its results are preserved as regression, but it
#: is never fresh held-out again. Aggregate-only review (F1, latency,
#: error counts, curves) leaves a life held out. Every life carrying
#: this role must appear in docs/exposure-log.md (enforced by test).
ROLES = ("development", "calibration", "held-out", "exposed_after_review")

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
    if not life.lineage or not life.lineage.strip():
        errors.append(f"{life.id}: missing §39 story lineage")
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
            # Leak matching is word-boundaried: substring matching overfires on
            # short values ("no" lives inside "Nova", "not", "another").
            if slot.kind is Kind.ABSTAIN and not slot.expected:
                values = [(f, f) for f in slot.forbidden]
            elif slot.norm in (Norm.DATE, Norm.TIME, Norm.STATUS):
                values = ([(slot.expected, "expected")] if slot.expected else [])
                values += [(f, "forbidden") for f in slot.forbidden]
            else:
                values = [(f, "forbidden") for f in slot.forbidden]
            for value, kind in values:
                if not value or not value.strip():
                    continue
                if re.search(rf"\b{re.escape(value.strip().casefold())}\b", prompt):
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
            # §36 error taxonomy: a required slot naming forbidden values must
            # name the KIND of falsehood they represent, so misrepresentations
            # break out by class instead of conflating.
            if slot.required and slot.forbidden and slot.error not in ERROR_CLASSES:
                errors.append(f"{life.id}: checkpoint {cp.id} slot {slot.id!r} "
                              f"has forbidden values but no valid error class "
                              f"(got {slot.error!r})")
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


# ---------------------------------------------------------------------------
# P0-12: support-graph ablation (additive; no existing behavior changed).
#
# ``Slot.supported_by`` stays cited-only (see docs/support-graph.md): it
# records which observations are cited as relevant, never sufficiency or
# necessity. The helpers below prove stronger claims explicitly by removing
# one claimed-essential source and recomputing knowability under the same
# literal-carry rule structural validation uses.
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class AblationResult:
    """Outcome of removing one source/channel and recomputing knowability."""

    checkpoint_id: str
    removed_ids: tuple[str, ...]
    answer_changed_or_uncertain: bool
    affected_slot_ids: tuple[str, ...] = ()
    detail: str = ""


def _slot_values(slot: Slot) -> tuple[str, ...]:
    return tuple(v for v in (slot.expected, *slot.alternatives) if v and v.strip())


def _slot_carried(slot: Slot, texts: list[str]) -> bool | None:
    """Literal-carry knowability of one slot over the given evidence texts.

    Returns True/False for required literal slots with an expected value;
    None when the slot is outside the literal rule (YESNO, no expected
    value, or ABSTAIN) and ablation cannot decide it.
    """
    if (not slot.required or not slot.expected
            or slot.norm not in LITERAL_NORMS or slot.kind is Kind.ABSTAIN):
        return None
    values = _slot_values(slot)
    return any(evidence_carries(t, v, slot.norm) for t in texts for v in values)


def ablation_check(life: Life, checkpoint: Checkpoint,
                   channel_or_source: str) -> AblationResult:
    """Remove one claimed-essential source and recompute knowability.

    ``channel_or_source`` is either a channel name (removes every
    observation on that channel) or one observation ``source_id`` (removes
    just it). Only evidence collected at or before the checkpoint counts,
    mirroring the future-evidence rule in :func:`validate_life`.

    ``answer_changed_or_uncertain`` is True iff at least one required
    literal slot is carried by the full cited evidence but uncarried after
    the removal. Unknown channel/source names raise ``ValueError``.
    """
    by_id = {o.source_id: o for o in life.observations}
    if channel_or_source in CHANNELS:
        removed = tuple(o.source_id for o in life.observations
                        if o.channel == channel_or_source)
    elif channel_or_source in by_id:
        removed = (channel_or_source,)
    else:
        raise ValueError(f"unknown channel or source_id {channel_or_source!r}")
    removed_set = set(removed)

    affected: list[str] = []
    undecided: list[str] = []
    baseline_unknowable: list[str] = []
    for slot in (*checkpoint.slots, *checkpoint.supported):
        full_texts = [by_id[oid].text for oid in slot.supported_by
                      if oid in by_id and by_id[oid].collected_at <= checkpoint.at]
        kept_texts = [by_id[oid].text for oid in slot.supported_by
                      if oid in by_id and oid not in removed_set
                      and by_id[oid].collected_at <= checkpoint.at]
        before = _slot_carried(slot, full_texts)
        if before is None:
            undecided.append(slot.id)
            continue
        after = _slot_carried(slot, kept_texts)
        if before and not after:
            affected.append(slot.id)
        elif not before:
            baseline_unknowable.append(slot.id)

    parts = [f"removed {len(removed)} observation(s)"]
    if affected:
        parts.append(f"affected: {', '.join(affected)}")
    if baseline_unknowable:
        parts.append(f"uncarried even before removal: "
                     f"{', '.join(baseline_unknowable)}")
    if undecided:
        parts.append(f"outside the literal rule (needs-human-review): "
                     f"{', '.join(undecided)}")
    return AblationResult(
        checkpoint_id=checkpoint.id,
        removed_ids=removed,
        answer_changed_or_uncertain=bool(affected),
        affected_slot_ids=tuple(affected),
        detail="; ".join(parts),
    )


def multi_source_dependency(life: Life) -> list[str]:
    """Ids of checkpoints whose *cited* required support spans >1 channel.

    Candidacy list only: citation span is not proof of dependence (see
    docs/support-graph.md). Prove each entry with :func:`ablation_check`.
    """
    by_id = {o.source_id: o for o in life.observations}
    out: list[str] = []
    for cp in life.checkpoints:
        channels = set()
        for slot in (*cp.slots, *cp.supported):
            if not slot.required:
                continue
            for oid in slot.supported_by:
                obs = by_id.get(oid)
                if obs is not None:
                    channels.add(obs.channel)
        if len(channels) > 1:
            out.append(cp.id)
    return out


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
