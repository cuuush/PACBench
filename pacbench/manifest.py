"""The support manifest: a generation-time authoring aid, never scored-run visible.

For each benchmark-relevant fact or state transition, the manifest records which
messages were *intended* to establish it. Validation checks that the cited
observations actually carry the required literal values; grading never consults the
manifest — the rubric's ``supported_by`` ids and the deterministic grader decide.

The manifest (like the rubric) must never be exposed to a memory provider or Hermes
during a scored run. Only :func:`public_observations` output crosses that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from .life import Life


@dataclass(frozen=True)
class SupportEntry:
    """One fact and the messages intended to establish it."""

    fact_id: str
    observation_ids: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class SupportManifest:
    """Authoring aid for one life. Kept out of scored runs by construction."""

    life_id: str
    entries: tuple[SupportEntry, ...] = ()

    def observations_for(self, fact_id: str) -> tuple[str, ...]:
        for entry in self.entries:
            if entry.fact_id == fact_id:
                return entry.observation_ids
        return ()


def public_observations(life: Life) -> list[dict]:
    """The only observation payload an adapter may see: public fields, no manifest,
    no rubric, no truth. Structurally incapable of leaking the answer key, because
    none of those objects are reachable from the returned dicts."""
    return [o.public() for o in life.observations]
