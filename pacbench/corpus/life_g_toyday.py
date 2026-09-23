"""Toy day: end-to-end plumbing diagnostic (NOT a benchmark life).

One fictional day (Thursday 2026-09-10), three events, three
checkpoints, one update trail (plumber 9am → 2pm). Deliberately
unregistered: ``corpus/__init__.py`` never imports this module, so it
cannot leak into the frozen corpus, the freeze hash, or any split. It
exists to prove the canonical path (freeze → bundle → sequential
episode → grade) on both providers for pocket change before any real
spend. Permanently exposed diagnostic material.
"""

from __future__ import annotations

from ..life import Life
from ..observation import Observation
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "toy-day"
_D = "2026-09-10"


def _t(clock: str) -> str:
    return f"{_D}T{clock}:00"


def lives() -> list[Life]:
    obs = (
        # -- event 1: dinner with Maya ----------------------------------
        Observation(
            source_id=f"{_ID}/text-dinner", channel="text",
            written_at=_t("08:00"), arrived_at=_t("08:00"),
            collected_at=_t("08:00"), sender="Maya", thread="maya",
            text="Dinner tomorrow? Friday 7pm at Casa Luna, I booked.",
        ),
        Observation(
            source_id=f"{_ID}/im-dinner-ok", channel="imessage",
            written_at=_t("08:05"), arrived_at=_t("08:05"),
            collected_at=_t("08:05"), sender="Me", thread="maya",
            text="Friday 7 at Casa Luna, see you there",
        ),
        Observation(
            source_id=f"{_ID}/wa-dinner-side", channel="whatsapp",
            written_at=_t("08:20"), arrived_at=_t("08:20"),
            collected_at=_t("08:20"), sender="Priya Nair",
            thread="weekend-plans",
            text="have fun at dinner friday!!",
        ),
        # -- event 2: plumber moved 9am → 2pm ----------------------------
        Observation(
            source_id=f"{_ID}/email-plumb-first", channel="email",
            written_at=_t("09:00"), arrived_at=_t("09:00"),
            collected_at=_t("09:00"), sender="Gus",
            thread="plumbing",
            text="Coming by this morning at 9 to fix the sink.",
        ),
        Observation(
            source_id=f"{_ID}/email-plumb-move", channel="email",
            written_at=_t("10:30"), arrived_at=_t("10:30"),
            collected_at=_t("10:30"), sender="Gus",
            thread="plumbing",
            text="Running late — make it 2pm instead, sorry.",
        ),
        Observation(
            source_id=f"{_ID}/text-plumb-ok", channel="text",
            written_at=_t("11:00"), arrived_at=_t("11:00"),
            collected_at=_t("11:00"), sender="Me", thread="gus",
            text="2pm works, see you then",
        ),
        # -- event 3: package locker code --------------------------------
        Observation(
            source_id=f"{_ID}/email-locker", channel="email",
            written_at=_t("12:00"), arrived_at=_t("12:00"),
            collected_at=_t("12:00"), sender="Carrier",
            thread="deliveries",
            text="Parcel delivered to locker 7. Pickup code 4821.",
        ),
        Observation(
            source_id=f"{_ID}/text-locker", channel="text",
            written_at=_t("12:10"), arrived_at=_t("12:10"),
            collected_at=_t("12:10"), sender="Building",
            thread="deliveries",
            text="Locker 7 code is 4821. Expires Sunday.",
        ),
    )

    dinner = Checkpoint(
        id=f"{_ID}/dinner", at=_t("13:00"),
        question="When and where is dinner with Maya?",
        mode="complete-plan",
        required_fields=("day", "time", "loc"),
        slots=(
            Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                 expected="Friday",
                 supported_by=(f"{_ID}/text-dinner",)),
            Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                 expected="7pm",
                 supported_by=(f"{_ID}/text-dinner",
                               f"{_ID}/im-dinner-ok")),
            Slot(id="loc", kind=Kind.CURRENT, norm=Norm.ENTITY,
                 expected="Casa Luna",
                 supported_by=(f"{_ID}/text-dinner",
                               f"{_ID}/im-dinner-ok")),
        ),
    )
    plumber = Checkpoint(
        id=f"{_ID}/plumber", at=_t("13:05"),
        question="What time is the plumber coming?",
        slots=(
            Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                 expected="2pm", forbidden=("9am",),
                 supported_by=(f"{_ID}/email-plumb-move",
                               f"{_ID}/text-plumb-ok"),
                 error="stale"),
        ),
    )
    package = Checkpoint(
        id=f"{_ID}/package", at=_t("13:10"),
        question="What is the locker pickup code?",
        slots=(
            Slot(id="code", kind=Kind.CURRENT, norm=Norm.TEXT,
                 expected="4821",
                 supported_by=(f"{_ID}/email-locker",
                               f"{_ID}/text-locker")),
        ),
    )
    return [Life(id=_ID, bet="plumbing-diagnostic", role="development",
                 lineage="toy-day",
                 observations=obs, checkpoints=(dinner, plumber, package))]
