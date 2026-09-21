"""Provider adapter contract (seam every evaluated provider implements).

Every memory provider evaluated under PACBench implements this contract so the
runner can treat systems uniformly without knowing their internals:

- ``reset`` — clear all state for one isolated (life, configuration, repetition).
- ``ingest`` — accept one batch of *public* observations at a collection boundary.
- ``maintenance`` — run scheduled background work (e.g. nightly consolidation).
  Scheduled maintenance is OPTIONAL (clarification 2026-09-21): a provider
  with no native background work sets ``maintenance_applicable = False``;
  the runner then records not-applicable and the run stays valid. Do not
  invent nightly work for another provider or penalize it for lacking
  Memcal's lifecycle.
- ``readiness`` — report whether background work has completed; the runner waits on
  declared real-time deadlines and records timeouts rather than waiting without
  limit (waiting without limit would flatter slow systems). Readiness means
  ready according to that provider's supported native lifecycle — not "has
  completed something analogous to Memcal dream".
- ``cleanup`` — delete only resources this run owns.
- ``usage`` — report observable cost/usage; unknown cost stays unknown, never zero.

Fairness rules (binding on later implementations, stated here so adapters are
written against them from the start):

- Adapters receive only :func:`pacbench.manifest.public_observations` output at
  the declared collection times. Never future observations, never the rubric,
  never the support manifest, never the answer key.
- Adapters contain no scenario-specific interpretation, identity fixes, or
  answer-key-derived behavior.
- Each provider's *native* lifecycle is exercised (ingestion, consolidation,
  retrieval, reasoning as that product does them); the harness must not repair
  one provider with hidden benchmark logic, nor reduce a product to vector search
  when its normal workflow uses more.
- Harness modifications are allowed only when fairness-neutral, shared in intent
  across providers, unrelated to specific answer keys, and necessary for an
  isolated controlled experiment.

The contract lives here; a concrete adapter now exists outside the scored
package at ``provider_adapters/memcal_adapter.py`` (scratch MEMCAL_HOME,
user-turn redirect, native retrieval seam). The runner treats every system
uniformly through this contract without knowing provider internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    """Observable cost/usage. Unknown fields stay ``None`` (unknown, not zero)."""

    model_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None
    cost_usd: float | None = None
    notes: tuple[str, ...] = ()


@dataclass
class Readiness:
    ready: bool
    pending: tuple[str, ...] = ()
    detail: str = ""


class ProviderAdapter(ABC):
    """Common contract every evaluated memory provider implements (later stage)."""

    name: str = "unnamed"
    #: False when the provider has no native scheduled background work
    #: (clarification 2026-09-21): the runner records not-applicable and
    #: the run stays valid. True (default) means a scheduled pass is part
    #: of the native lifecycle and a skip leaves the provider not exercised.
    maintenance_applicable: bool = True

    @abstractmethod
    def reset(self, namespace: str) -> None:
        """Clear all state for one isolated run namespace. NOT-IN-0.1."""
        raise NotImplementedError("NOT-IN-0.1: provider adapters")

    @abstractmethod
    def ingest(self, observations: list[dict], collected_at: str) -> Usage:
        """Ingest one batch of public observations. NOT-IN-0.1."""
        raise NotImplementedError("NOT-IN-0.1: provider adapters")

    @abstractmethod
    def maintenance(self, at: str) -> Usage:
        """Run scheduled background work at simulated time `at`. NOT-IN-0.1."""
        raise NotImplementedError("NOT-IN-0.1: provider adapters")

    @abstractmethod
    def readiness(self) -> Readiness:
        """Report background-work completion. NOT-IN-0.1."""
        raise NotImplementedError("NOT-IN-0.1: provider adapters")

    @abstractmethod
    def cleanup(self, namespace: str) -> None:
        """Delete only resources owned by this run. NOT-IN-0.1."""
        raise NotImplementedError("NOT-IN-0.1: provider adapters")
