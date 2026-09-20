"""Runner skeleton: deterministic exposure and scoring seams.

Implemented in 0.1 (real, tested, offline):

- :func:`exposed_observations` — the observations a system may have seen by a
  simulated time (collection clock, never the source clock).
- :func:`grade_answer` — deterministic grading of one answer at one checkpoint.
- :class:`RunManifest` — the immutable identity of a run (versions, hashes,
  selections); :func:`manifest_hash` pins it.

Explicit skeleton seam for offline use (raises, never fakes a result):

- :func:`run_checkpoint` — needs a live Hermes + provider; raises
  ``NotImplementedError`` instead of returning a fabricated grade. The live
  path now lives in :mod:`pacbench.live` (:func:`run_checkpoint_live`,
  :func:`run_life`, :func:`run_pilot`); this seam remains for offline use.
  A setup defect must create an invalid run,
  never a passing score, so there is no fallback path that returns grades
  without executing the experiment.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from .adapters import ProviderAdapter
from .freeze import digest
from .grade import Grade, Judge, grade
from .hermes import IsolatedHermes
from .life import Life
from .observation import Observation, delivery_order
from .rubric import Checkpoint


@dataclasses.dataclass(frozen=True)
class RunManifest:
    """Immutable identity of one run. Saved with every scored run (later stage)."""

    corpus_version: str
    corpus_hash: str
    lives: tuple[str, ...]
    provider: str
    provider_version: str
    hermes_version: str
    driver_model: str
    grader_version: str
    seed: int = 0
    repetition: int = 0


def manifest_hash(manifest: RunManifest) -> str:
    """Effective-manifest hash: configurations cannot overwrite one another."""
    raw = json.dumps(dataclasses.asdict(manifest), sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def exposed_observations(life: Life, until: str) -> list[Observation]:
    """Observations collected at or before simulated time `until`, in the order
    the systems learn them. Expectations follow this clock: before collection, a
    system cannot be expected to know the update."""
    return delivery_order([o for o in life.observations if o.collected_at <= until])


def public_exposure(life: Life, until: str) -> list[dict]:
    """Adapter-visible form of :func:`exposed_observations` (public fields only)."""
    return [o.public() for o in exposed_observations(life, until)]


def grade_answer(checkpoint: Checkpoint, answer: str,
                 judge: Judge | None = None) -> Grade:
    """Deterministic grading of one answer (judge only resolves TEXT residuals)."""
    return grade(checkpoint, answer, judge=judge)


def run_checkpoint(life: Life, checkpoint: Checkpoint, hermes: IsolatedHermes,
                   adapter: ProviderAdapter, corpus_hash: str) -> Grade:
    """Execute one checkpoint through Hermes + provider and grade it.

    Offline skeleton seam: raises instead of returning a fabricated grade.
    The live implementation is :func:`pacbench.live.run_checkpoint_live`
    (full episodes: :func:`pacbench.live.run_life`,
    :func:`pacbench.live.run_pilot`).
    """
    _ = (life, checkpoint, hermes, adapter, corpus_hash, digest)
    raise NotImplementedError(
        "NOT-IN-0.1: Hermes isolated runtime and provider adapters — "
        "no scored answers are collected in 0.1")
