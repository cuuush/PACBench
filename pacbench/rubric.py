"""Hidden expectations: the atomic rubric a checkpoint grades against.

A checkpoint poses one natural user question at a scenario time and carries a rubric the
assistant never sees. The rubric is a set of fact *slots*, not a paragraph. Grading is
therefore N small judgements — "is this slot right, wrong, or missing" — instead of one
holistic verdict on prose, which is what makes every reported point traceable to a
single fact and a single piece of evidence.

Per slot the verdict is exactly one of:

- ``CORRECT``        — the expected value (or an accepted alternative) is asserted.
- ``MISSED``         — nothing is asserted for the slot.
- ``MISREPRESENTED`` — a forbidden value is asserted (a stale time, the wrong person, an
  invented commitment), *or* both the current and a superseded value are asserted as
  current. A slot is never both missed and misrepresented; the three exhaust the space
  and sum to 100% over the answerable slots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    CURRENT = "current"        # the present truth at the checkpoint's moment
    HISTORICAL = "historical"  # a prior value that must stay recoverable as history
    ABSTAIN = "abstain"        # evidence is genuinely insufficient; asking/abstaining wins


class Norm(str, Enum):
    """How a slot's value is matched. Everything but TEXT is deterministic; TEXT is the
    only kind that may fall through to the semantic judge."""

    DATE = "date"
    TIME = "time"
    STATUS = "status"
    YESNO = "yesno"
    ENTITY = "entity"
    TEXT = "text"


class Verdict(str, Enum):
    CORRECT = "correct"
    MISSED = "missed"
    MISREPRESENTED = "misrepresented"


#: Closed vocabulary for the §36 error class of a slot's forbidden values —
#: what KIND of falsehood it is when one of them is asserted. Every required
#: slot that names forbidden values must name its class (enforced by
#: validation), so the report can break MISREPRESENTED out instead of
#: conflating stale resurrections with invented commitments.
ERROR_CLASSES = frozenset({
    # A value from the wrong point in the timeline (superseded time/place
    # asserted as current, or the current value intruding into history).
    "stale",
    # Right fact, wrong person (coat reminder fired for Sam).
    "wrong-person",
    # Right kind of thing, wrong instance (John's game for Eric's).
    "wrong-occurrence",
    # Asserted with no supporting evidence: a cancellation that never
    # happened, an uncommitted plan presented as committed, a real
    # commitment/cancellation falsely denied.
    "invented",
    # Lapsed, declined, or completed matter surfaced as live (expired deal,
    # declined festival, finished task resurfacing).
    "expired",
    # One commitment counted twice (two active bookings after a rebook).
    "duplicate",
})


@dataclass(frozen=True)
class Slot:
    """One answerable fact.

    ``forbidden`` are values that must not be asserted *as current* — most often the
    stale value this slot's evidence superseded. Naming them per slot is what lets the
    grader tell a wrong answer (misrepresented) from a silent one (missed).

    ``error`` names the §36 error class of the forbidden values (one of
    :data:`ERROR_CLASSES`): the KIND of falsehood an assertion of one of them
    would be. Required slots with forbidden values must set it; slots without
    forbidden values leave it empty (no misrepresentation is representable).
    """

    id: str
    kind: Kind
    norm: Norm
    expected: str
    alternatives: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    supported_by: tuple[str, ...] = ()   # Observation.source_id values
    required: bool = True
    error: str = ""


@dataclass(frozen=True)
class Checkpoint:
    """A question asked of the assistant, plus the hidden rubric it is graded against.

    ``mode``:
    - ``fact``          — score each slot independently.
    - ``complete-plan`` — the occasion is correct only if every id in ``required_fields``
      is CORRECT and nothing is misrepresented; predeclare those fields.
    - ``open-ended``    — score recall over ``supported`` options and precision against
      ``forbidden_options``; never grade prose style.
    - ``unanswerable``  — a single ABSTAIN slot; abstention accuracy is its own axis.
    """

    id: str
    at: str                       # scenario time the question is posed
    question: str                 # the natural user message sent to the assistant
    slots: tuple[Slot, ...] = ()
    mode: str = "fact"
    required_fields: tuple[str, ...] = ()
    supported: tuple[Slot, ...] = ()
    forbidden_options: tuple[Slot, ...] = ()
    #: Capability family under test, e.g. "current-state", "history", "proactivity".
    family: str = ""
    #: Difficulty dimensions this checkpoint exercises, e.g. ("update-depth-4",).
    difficulty_tags: tuple[str, ...] = ()

    def public_prompt(self) -> str:
        """The only thing the assistant receives. The rubric stays behind."""
        return self.question
