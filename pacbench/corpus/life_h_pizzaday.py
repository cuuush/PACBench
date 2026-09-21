"""Pizza day: minimal end-to-end smoke (NOT a benchmark life).

One fictional day (Thursday 2026-09-10). Devon asks Leo to get pizza
Friday. Basically nothing else. Two checkpoints (what / when+who).
Deliberately unregistered: ``corpus/__init__.py`` never imports this
module, so it cannot leak into the frozen corpus, the freeze hash, or
any split. Permanently exposed diagnostic material. Total paid calls
per provider run: single digits.
"""

from __future__ import annotations

from ..life import Life
from ..observation import Observation
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "pizza-day"
_D = "2026-09-10"


def _t(clock: str) -> str:
    return f"{_D}T{clock}:00"


def lives() -> list[Life]:
    obs = (
        Observation(
            source_id=f"{_ID}/text-ask", channel="text",
            written_at=_t("18:00"), arrived_at=_t("18:00"),
            collected_at=_t("18:00"), sender="Devon", thread="devon",
            text="yo, wanna grab pizza friday night?",
        ),
        Observation(
            source_id=f"{_ID}/text-yes", channel="text",
            written_at=_t("18:05"), arrived_at=_t("18:05"),
            collected_at=_t("18:05"), sender="Me", thread="Devon",
            text="yes! friday works, say 7?",
        ),
        Observation(
            source_id=f"{_ID}/text-place", channel="text",
            written_at=_t("18:10"), arrived_at=_t("18:10"),
            collected_at=_t("18:10"), sender="Devon", thread="devon",
            text="7 works. Luigi's on 5th?",
        ),
        Observation(
            source_id=f"{_ID}/text-ok", channel="text",
            written_at=_t("18:12"), arrived_at=_t("18:12"),
            collected_at=_t("18:12"), sender="Me", thread="Devon",
            text="perfect, see you at Luigi's friday 7",
        ),
    )

    what = Checkpoint(
        id=f"{_ID}/what", at=_t("19:00"),
        question="What are the Friday plans?",
        slots=(
            Slot(id="plan", kind=Kind.CURRENT, norm=Norm.TEXT,
                 expected="pizza",
                 supported_by=(f"{_ID}/text-ask", f"{_ID}/text-ok")),
        ),
    )
    whenwho = Checkpoint(
        id=f"{_ID}/when-who", at=_t("19:05"),
        question="When and with whom?",
        mode="complete-plan",
        required_fields=("day", "time", "who"),
        slots=(
            Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                 expected="Friday",
                 supported_by=(f"{_ID}/text-ask", f"{_ID}/text-ok")),
            Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                 expected="7pm", alternatives=("7",),
                 supported_by=(f"{_ID}/text-yes", f"{_ID}/text-ok")),
            Slot(id="who", kind=Kind.CURRENT, norm=Norm.ENTITY,
                 expected="Devon",
                 supported_by=(f"{_ID}/text-ask",)),
        ),
    )
    return [Life(id=_ID, bet="pizza-smoke", role="development",
                 lineage="toy-day",
                 observations=obs, checkpoints=(what, whenwho))]
