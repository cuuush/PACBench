"""Provider adapter contract (seam for later stages — not implemented in 0.1).

Every memory provider evaluated under PACBench implements this contract so the
runner can treat systems uniformly without knowing their internals:

- ``reset`` — clear all state for one isolated (life, configuration, repetition).
- ``ingest`` — accept one batch of *public* observations at a collection boundary.
- ``maintenance`` — run scheduled background work (e.g. nightly consolidation).
- ``readiness`` — report whether background work has completed; the runner waits on
  declared real-time deadlines and records timeouts rather than waiting without
  limit (waiting without limit would flatter slow systems).
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

v0.1 ships the contract only. There is deliberately no concrete adapter and no
recorded provider result: Hermes isolated runtime, provider adapters, and live
comparisons are later stages.
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
