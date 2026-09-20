"""A `Life`: one person's week (or a slice of it) plus the questions asked of it.

`bet` names the architectural claim the family exercises; `role` is its corpus split
(`development` for debugging, `calibration` for reviewing the grader, `held-out` for the
comparative measurement). A `control_of` life removes exactly the one element the bet
depends on, so a win on the bet family paired with parity on its control is a capability
claim rather than a corpus authored to favour one product.

Three layers stay distinct on every life:

- ``truth`` — what is actually true in the fictional person's life (world truth);
- ``observations`` — the messages and records that make that truth knowable (evidence);
- ``checkpoints`` — the assistant situations scored against hidden rubrics (expected
  behavior).

`difficulty_tags` annotate the difficulty dimensions a life exercises
(`update-depth-4`, `entity-collision-2`, `cross-channel-3`, ...). They are plain
descriptive labels, never scoring logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .observation import Observation
from .rubric import Checkpoint


@dataclass(frozen=True)
class TruthFact:
    """One world-truth statement about the fictional life.

    This is what is *actually true*, independent of which messages state it and of
    what the assistant is expected to say. Authoring aid and review surface only:
    never delivered to a provider or Hermes during a scored run.
    """

    id: str
    statement: str
    # Observation.source_id values that establish this fact (subset of the support
    # manifest, kept here so reviewers can trace truth -> evidence per fact).
    established_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Life:
    id: str
    bet: str
    role: str
    observations: tuple[Observation, ...]
    checkpoints: tuple[Checkpoint, ...]
    control_of: str | None = None
    title: str = ""
    #: Number of consecutive simulated days the scored horizon covers (7 in v0.1).
    horizon_days: int = 7
    #: Difficulty dimensions this life exercises, e.g. ("update-depth-4", ...).
    difficulty_tags: tuple[str, ...] = ()
    #: World truth, kept distinct from evidence (observations) and expected
    #: assistant behavior (checkpoints).
    truth: tuple[TruthFact, ...] = ()
