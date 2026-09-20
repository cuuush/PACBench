"""Measured difficulty values (§13, contract co-author directive).

Author-declared ``difficulty_tags`` stay as descriptive metadata. Anything
plotted, compared, or claimed uses THESE functions over the frozen corpus +
support graph — e.g. "thread-size-xl" must mean the rendered thread actually
holds the messages, not that the authoring request asked for them.

All functions are offline, deterministic, and provider-blind: they read the
frozen `Life` objects only.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .life import Life
from .rubric import Checkpoint


def _by_id(life: Life) -> dict:
    return {o.source_id: o for o in life.observations}


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
    return len({by_id[i].collected_at
                for i in required_support_ids(checkpoint) if i in by_id})
