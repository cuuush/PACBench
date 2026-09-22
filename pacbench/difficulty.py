"""Measured difficulty values (§13, contract co-author directive).

Author-declared ``difficulty_tags`` stay as descriptive metadata. Anything
plotted, compared, or claimed uses THESE functions over the frozen corpus +
support graph — e.g. "thread-size-xl" must mean the rendered thread actually
holds the messages, not that the authoring request asked for them.

All functions are offline, deterministic, and provider-blind: they read the
frozen `Life` objects only.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .life import Life
from .rubric import Checkpoint

#: Schema version stamped on every measured-difficulty block so comparative
#: consumers can fail closed on stale/unexpected shapes instead of silently
#: mixing generations.
MEASURED_SCHEMA = "measured-difficulty/1"

_DEPTH_RE = re.compile(r"update-depth-(\d+)")
_CHANNEL_RE = re.compile(r"cross-channel-(\d+)")
_COLLISION_RE = re.compile(r"entity-collision-(\d+)")


def _field(o, name: str):
    """Attribute-or-key read so frozen dict observations work too."""
    return o[name] if isinstance(o, dict) else getattr(o, name)


def _observations(life) -> list:
    return list(getattr(life, "observations", ()) or ())


def _checkpoints(life) -> list:
    return list(getattr(life, "checkpoints", ()) or ())


def _life_tags(life) -> tuple:
    return tuple(getattr(life, "difficulty_tags", ()) or ())


def _cp_tags(checkpoint) -> tuple:
    return tuple(getattr(checkpoint, "difficulty_tags", ()) or ())


def _parse_first(tags, pattern: re.Pattern) -> int | None:
    for tag in tags:
        m = pattern.search(tag)
        if m:
            return int(m.group(1))
    return None


def _by_id(life) -> dict:
    return {_field(o, "source_id"): o for o in _observations(life)}


def thread_sizes(life: Life) -> dict[str, int]:
    """Message count per thread id, as frozen."""
    return dict(Counter(o.thread for o in life.observations))


def required_support_ids(checkpoint: Checkpoint) -> set[str]:
    """Observation ids the rubric requires for this checkpoint."""
    ids: set[str] = set()
    for slot in (*checkpoint.slots, *checkpoint.supported):
        if slot.required:
            ids.update(slot.supported_by)
    return ids


def relevant_thread_size(life: Life, checkpoint: Checkpoint) -> int:
    """Size of the largest thread holding required support evidence.

    The "relevant conversation" for accuracy-vs-thread-size curves: the
    biggest haystack the graded fact hides in.
    """
    sizes = thread_sizes(life)
    by_id = _by_id(life)
    threads = {by_id[i].thread for i in required_support_ids(checkpoint)
               if i in by_id}
    return max((sizes[t] for t in threads), default=0)


def total_exposed(life: Life, until: str) -> int:
    """Observations collected at or before `until`: total history volume."""
    return sum(1 for o in life.observations if o.collected_at <= until)


def required_channels(life: Life, checkpoint: Checkpoint) -> set[str]:
    """Distinct channels carrying required support evidence."""
    by_id = _by_id(life)
    return {by_id[i].channel for i in required_support_ids(checkpoint)
            if i in by_id}


def evidence_age_hours(life: Life, checkpoint: Checkpoint) -> float | None:
    """Hours from the latest required-support collection to checkpoint time.

    How stale the decisive evidence is when asked. None when no required
    support exists.
    """
    by_id = _by_id(life)
    stamps = sorted(by_id[i].collected_at
                    for i in required_support_ids(checkpoint) if i in by_id)
    if not stamps:
        return None
    at = datetime.fromisoformat(checkpoint.at)
    latest = datetime.fromisoformat(stamps[-1])
    return round((at - latest).total_seconds() / 3600, 2)


def support_batches(life: Life, checkpoint: Checkpoint) -> int:
    """Distinct collection instants among required support evidence.

    Proxy for update depth: each batch is one moment the graded state could
    have changed. A move announced, confirmed, and repeated across three
    instants has depth ≥3 whether or not every batch changed the value.
    """
    by_id = _by_id(life)
    return len({_field(by_id[i], "collected_at")
                for i in required_support_ids(checkpoint) if i in by_id})


def requested_depth(checkpoint) -> int | None:
    """Author-requested update depth parsed from a checkpoint's tags.

    Authoring input only — never a measured claim. None when the checkpoint
    carries no ``update-depth-N`` tag.
    """
    return _parse_first(_cp_tags(checkpoint), _DEPTH_RE)


def life_requested_depths(life) -> list[int]:
    """Sorted unique requested depths across life + checkpoint tags."""
    found: set[int] = set()
    for tag in _life_tags(life):
        m = _DEPTH_RE.search(tag)
        if m:
            found.add(int(m.group(1)))
    for cp in _checkpoints(life):
        n = requested_depth(cp)
        if n is not None:
            found.add(n)
    return sorted(found)


def required_senders(life, checkpoint) -> set[str]:
    """Distinct senders carrying required support evidence."""
    by_id = _by_id(life)
    return {_field(by_id[i], "sender") for i in required_support_ids(checkpoint)
            if i in by_id}


def measured_difficulty(life) -> dict:
    """Freeze-time measured difficulty block for one life (§P0-13).

    Every value is computed from the frozen observations + support graph —
    never from author-declared ``difficulty_tags``. Provider-blind, offline,
    deterministic, JSON-serializable. Attach at freeze time; comparative
    curves must consume this block, not the tags.
    """
    sizes = thread_sizes(life)
    obs = _observations(life)
    cps = _checkpoints(life)
    channels = sorted({_field(o, "channel") for o in obs})
    per_cp: dict[str, dict] = {}
    ages: dict[str, float | None] = {}
    measured_depth = 0
    max_req_channels = 0
    collision = 0
    for cp in cps:
        batches = support_batches(life, cp)
        thread = relevant_thread_size(life, cp)
        chans = sorted(required_channels(life, cp))
        senders = sorted(required_senders(life, cp))
        age = evidence_age_hours(life, cp)
        per_cp[cp.id] = {
            "support_batches": batches,
            "relevant_thread_size": thread,
            "required_channels": chans,
            "required_channel_count": len(chans),
            "required_senders": senders,
            "evidence_age_hours": age,
        }
        ages[cp.id] = age
        measured_depth = max(measured_depth, batches)
        max_req_channels = max(max_req_channels, len(chans))
        collision = max(collision, len(senders))
    known_ages = sorted(a for a in ages.values() if a is not None)
    return {
        "schema": MEASURED_SCHEMA,
        "life_id": getattr(life, "id", ""),
        "max_thread_size": max(sizes.values(), default=0),
        "thread_sizes": dict(sorted(sizes.items())),
        "total_exposed": len(obs),
        "channel_count": len(channels),
        "channels": channels,
        "max_required_channels": max_req_channels,
        "measured_update_depth": measured_depth,
        "evidence_age_hours": {
            "per_checkpoint": ages,
            "max": known_ages[-1] if known_ages else None,
            "min": known_ages[0] if known_ages else None,
        },
        # Provider-blind proxy for entity/occurrence collision load: the most
        # distinct voices any single checkpoint's required support forces the
        # assistant to keep apart. 0 when no checkpoint names required support.
        "collision_count": collision,
        "per_checkpoint": per_cp,
    }


def flag_depth_mismatch(life, measured: dict | None = None) -> dict:
    """Requested vs measured update depth, side by side.

    A checkpoint requesting depth 8 but rendering depth 7 is flagged here —
    it must never silently enter the "depth 8" bucket. Per-checkpoint
    entries compare each checkpoint's requested tag against its measured
    support-batch count; the life-level entry compares the max requested
    depth against the max measured depth.
    """
    measured = measured if measured is not None else measured_difficulty(life)
    per = measured.get("per_checkpoint", {})
    mismatches = []
    for cp in _checkpoints(life):
        req = requested_depth(cp)
        if req is None:
            continue
        got = per.get(cp.id, {}).get("support_batches")
        mismatches.append({
            "checkpoint_id": cp.id,
            "requested": req,
            "measured": got,
            "mismatch": got != req,
        })
    requested = life_requested_depths(life)
    requested_max = max(requested) if requested else None
    measured_depth = measured.get("measured_update_depth")
    life_mismatch = (requested_max is not None
                     and requested_max != measured_depth)
    return {
        "life_id": getattr(life, "id", ""),
        "requested_depths": requested,
        "requested_max": requested_max,
        "measured_update_depth": measured_depth,
        "mismatches": mismatches,
        "has_mismatch": life_mismatch or any(m["mismatch"]
                                             for m in mismatches),
    }


def require_measured(measured_by_life: dict, life_id: str) -> dict:
    """Fetch a life's freeze-time measured block, failing closed.

    Comparative/difficulty-curve code must go through here: a missing,
    mislabeled, or wrong-schema block raises instead of falling back to
    author-declared tags.
    """
    try:
        measured = measured_by_life[life_id]
    except (KeyError, TypeError):
        raise ValueError(
            f"no measured difficulty block for life {life_id!r}: "
            "comparative curves must not consume author-declared tags")
    if not isinstance(measured, dict) or measured.get("schema") != MEASURED_SCHEMA:
        raise ValueError(
            f"stale/invalid measured difficulty block for life {life_id!r}: "
            f"expected schema {MEASURED_SCHEMA!r}")
    if measured.get("life_id", life_id) != life_id:
        raise ValueError(
            f"measured difficulty block life mismatch: block is for "
            f"{measured.get('life_id')!r}, asked {life_id!r}")
    return measured


def measured_tags_for_checkpoint(life, checkpoint,
                                 measured: dict | None = None) -> tuple[str, ...]:
    """Comparative-bucket tags derived from the measured block only.

    ``measured`` is required: calling without the freeze-time block raises
    rather than silently substituting the checkpoint's author tags. The
    depth entry uses the rendered support-batch count, so a depth-8
    request rendering depth 7 buckets as ``measured-depth-7``.
    """
    if measured is None:
        raise ValueError(
            "measured difficulty block required: pass the freeze-time "
            "measured_difficulty() output; author-declared difficulty_tags "
            "must not feed comparative buckets")
    life_id = getattr(life, "id", "")
    if measured.get("life_id", life_id) != life_id:
        raise ValueError(
            f"measured block is for {measured.get('life_id')!r}, "
            f"checkpoint belongs to {life_id!r}")
    try:
        detail = measured["per_checkpoint"][checkpoint.id]
    except (KeyError, TypeError):
        raise ValueError(
            f"no measured entry for checkpoint {checkpoint.id!r} "
            f"(life {life_id!r}): freeze-time block is stale or incomplete")
    return (f"measured-depth-{detail['support_batches']}",
            f"measured-thread-{detail['relevant_thread_size']}",
            f"measured-channels-{detail['required_channel_count']}")
