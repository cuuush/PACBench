"""Development Life B: entity collision and source authority (poker).

Bet: similar simultaneous entities stay distinct, and who said something matters.
Two poker games run side by side — John's Friday game (canceled by John himself)
and Eric's Saturday game (moved 7pm -> 8pm by Eric himself). A non-organizer
hearsay claim ("I think Eric's game is canceled too?") must not override the
organizer's correction, and a later stale repeat of the old 7pm time must not
override the move. A weak system merges the games, cancels both, moves the wrong
one, or attaches Eric's time to John's game.

Matched control: a team lunch with no competing occurrence — both systems should
answer it, and the collision advantage does not apply.

World truth vs evidence vs expected behavior:

- truth: John's game canceled; Eric's game active Saturday 8pm at 15 Harbor Rd
  (originally 7pm); the hearsay claim is not fact; the lunch is Wednesday noon;
- evidence: two GroupMe threads, a cancellation email, a WhatsApp claim, an
  organizer iMessage correction, a Hermes commitment, a stale repeat;
- expected behavior: per-game current state, original-time history, the
  hearsay-vs-organizer distinction, and the single-occurrence control.
"""

from __future__ import annotations

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "poker-collision"
_WEEK_START = "2026-09-07"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _plan() -> LifePlan:
    return LifePlan(
        id=_ID,
        title="Poker collision and source authority",
        bet="entity-collision+source-authority",
        role="development",
        week_start=_WEEK_START,
        difficulty_tags=("entity-collision-2", "source-authority", "cross-channel-3",
                         "conflict-stale", "history-reconstruction", "control-single",
                         "evidence-age-month"),
        people=(
            Person(name="John Park", relation="organizer of John's Friday game"),
            Person(name="Eric Vance", relation="organizer of Eric's Saturday game"),
            Person(name="Priya Nair", relation="participant in both threads"),
            Person(name="Devon Park", relation="participant in Eric's thread"),
            Person(name="Maya Chen", relation="coworker"),
            Person(name="me", relation="the owner"),
        ),
        threads=(
            ThreadPlan(id="gm-john", channel="groupme",
                       participants=("John Park", "Priya Nair", "me")),
            ThreadPlan(id="gm-eric", channel="groupme",
                       participants=("Eric Vance", "Priya Nair", "Devon Park", "me")),
            ThreadPlan(id="email-john", channel="email",
                       participants=("John Park", "me")),
            ThreadPlan(id="wa-crew", channel="whatsapp",
                       participants=("Priya Nair", "Devon Park", "me")),
            ThreadPlan(id="im-eric", channel="imessage",
                       participants=("Eric Vance", "me")),
            ThreadPlan(id="email-work", channel="email",
                       participants=("Maya Chen", "me")),
            ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
        ),
        beats=(
            # Older history: both games exist side by side before the scored week.
            BeatPlan(local_id="email-lineup", thread="email-work",
                     sender="Maya Chen", day=-21, clock="10:00",
                     draft=("Fall lineup: John's Friday game and Eric's Saturday "
                            "game are both back. Don't mix them up!"),
                     fact_id="games-lineup",
                     must_include=("John's", "Eric's"), register="formal"),
            BeatPlan(local_id="gm-john-plan", thread="gm-john",
                     sender="John Park", day=0, clock="10:00",
                     draft=("Poker at my place Friday 7pm, 78 Cedar St. "
                            "Who's in?"),
                     fact_id="john-planned",
                     must_include=("Friday", "7pm", "78 Cedar")),
            BeatPlan(local_id="gm-eric-plan", thread="gm-eric",
                     sender="Eric Vance", day=0, clock="11:00",
                     draft=("My game Saturday 7pm at mine, 15 Harbor Rd. "
                            "Bring your own buy-in."),
                     fact_id="eric-planned",
                     must_include=("Saturday", "7pm", "15 Harbor")),
            BeatPlan(local_id="email-lunch", thread="email-work",
                     sender="Maya Chen", day=1, clock="12:00",
                     draft="Team lunch Wednesday at noon, Verde. See you there.",
                     fact_id="lunch-planned",
                     must_include=("Wednesday", "noon"), register="formal"),
            # John cancels his own game by email: authoritative for John's game.
            BeatPlan(local_id="email-john-cancel", thread="email-john",
                     sender="John Park", day=2, clock="09:00",
                     draft=("Canceling my Friday game — work trip came up. "
                            "Sorry all, next week."),
                     fact_id="john-canceled",
                     must_include=("Canceling", "Friday"), register="formal"),
            # Hearsay about the *other* game, from a non-organizer, tentative.
            BeatPlan(local_id="wa-priya-claim", thread="wa-crew",
                     sender="Priya Nair", day=2, clock="18:00",
                     draft="hmm, I think Eric's game is canceled too?",
                     fact_id="misleading-claim", must_include=("canceled",)),
            # The organizer corrects it directly and moves his game.
            BeatPlan(local_id="im-eric-correct", thread="im-eric",
                     sender="Eric Vance", day=2, clock="19:00",
                     draft=("Priya's wrong — this is Eric, my game's still on. "
                            "Moving to 8pm Saturday, 15 Harbor Rd."),
                     fact_id="eric-correction",
                     must_include=("still on", "8pm")),
            BeatPlan(local_id="hermes-commit", thread="hermes", sender="me",
                     day=2, clock="20:00",
                     draft="Still going to Eric's Saturday. I'll bring chips.",
                     fact_id="user-commitment",
                     must_include=("Eric's", "chips"), from_owner=True),
            # Stale repeat of the superseded time in Eric's own thread.
            BeatPlan(local_id="gm-eric-stale", thread="gm-eric",
                     sender="Devon Park", day=3, clock="10:00",
                     draft="see everyone 7pm Saturday",
                     fact_id="stale-repeat", must_include=("7pm",),
                     register="terse"),
            # Late-week noise so the scored week runs the full seven days.
            BeatPlan(local_id="wa-meme", thread="wa-crew",
                     sender="Devon Park", day=4, clock="20:00",
                     draft="that cat video again",
                     register="terse"),
            BeatPlan(local_id="email-allhands", thread="email-work",
                     sender="Maya Chen", day=5, clock="09:00",
                     draft="All-hands moved to next Thursday, slides due Wednesday.",
                     register="formal"),
            BeatPlan(local_id="gm-ice", thread="gm-eric",
                     sender="Priya Nair", day=6, clock="12:00",
                     draft="someone bring ice this time",
                     register="terse"),
        ),
        checkpoints=(
            Checkpoint(
                id=f"{_ID}/eric-now", at=_t(3, "12:00"),
                question="What's happening with Eric's poker game?",
                mode="complete-plan",
                required_fields=("status", "day", "time", "loc"),
                family="current-state",
                difficulty_tags=("entity-collision-2", "source-authority",
                                 "cross-channel-3", "conflict-stale"),
                slots=(
                    Slot(id="status", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="still on",
                         alternatives=("still happening", "still going"),
                         forbidden=("canceled", "cancelled", "called off"),
                         supported_by=(f"{_ID}/im-eric-correct",
                                       f"{_ID}/hermes-commit")),
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Saturday", forbidden=("Friday",),
                         supported_by=(f"{_ID}/im-eric-correct",)),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="8pm", alternatives=("8 pm", "8:00pm"),
                         forbidden=("7pm",),
                         supported_by=(f"{_ID}/im-eric-correct",)),
                    Slot(id="loc", kind=Kind.CURRENT, norm=Norm.ENTITY,
                         expected="Eric's",
                         alternatives=("15 Harbor", "15 Harbor Rd"),
                         forbidden=("John's", "78 Cedar"),
                         supported_by=(f"{_ID}/gm-eric-plan",
                                       f"{_ID}/im-eric-correct",
                                       f"{_ID}/hermes-commit")),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/john-now", at=_t(3, "12:05"),
                question="Is John's poker game still on?",
                family="current-state",
                difficulty_tags=("entity-collision-2",),
                slots=(
                    Slot(id="status", kind=Kind.CURRENT, norm=Norm.YESNO,
                         expected="no",
                         supported_by=(f"{_ID}/email-john-cancel",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/eric-original", at=_t(3, "12:10"),
                question="What time was Eric's game originally?",
                family="history",
                difficulty_tags=("entity-collision-2", "history-reconstruction"),
                slots=(
                    Slot(id="time", kind=Kind.HISTORICAL, norm=Norm.TIME,
                         expected="7pm", alternatives=("7 pm",),
                         supported_by=(f"{_ID}/gm-eric-plan",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/eric-canceled", at=_t(3, "12:15"),
                question="Was Eric's game canceled?",
                family="source-authority",
                difficulty_tags=("source-authority",),
                slots=(
                    Slot(id="status", kind=Kind.CURRENT, norm=Norm.YESNO,
                         expected="no",
                         supported_by=(f"{_ID}/im-eric-correct",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/lunch", at=_t(2, "12:30"),
                question="When is the team lunch?",
                family="current-state",
                difficulty_tags=("control-single",),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Wednesday", forbidden=("Thursday",),
                         supported_by=(f"{_ID}/email-lunch",)),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="noon", alternatives=("12pm", "12:00"),
                         supported_by=(f"{_ID}/email-lunch",)),
                ),
            ),
        ),
        truth=(
            TruthFact(id="john-canceled",
                      statement="John's Friday 7pm game at 78 Cedar St is canceled.",
                      established_by=(f"{_ID}/gm-john-plan",
                                      f"{_ID}/email-john-cancel")),
            TruthFact(id="eric-active",
                      statement=("Eric's Saturday game at 15 Harbor Rd is still on, "
                                 "moved from 7pm to 8pm."),
                      established_by=(f"{_ID}/gm-eric-plan",
                                      f"{_ID}/im-eric-correct")),
            TruthFact(id="priya-hearsay",
                      statement=("Priya tentatively claimed Eric's game was canceled; "
                                 "Eric corrected it. The claim is not fact."),
                      established_by=(f"{_ID}/wa-priya-claim",
                                      f"{_ID}/im-eric-correct")),
            TruthFact(id="user-commitment",
                      statement=("The owner is still going to Eric's game and "
                                 "will bring chips."),
                      established_by=(f"{_ID}/hermes-commit",)),
            TruthFact(id="lunch",
                      statement="Team lunch is Wednesday at noon at Verde.",
                      established_by=(f"{_ID}/email-lunch",)),
        ),
    )


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST
