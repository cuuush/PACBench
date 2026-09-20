"""Development Life A: update depth and history (dentist).

Bet: recency is decided at write time. A dental cleaning moves four times in three
days (Friday 2pm -> Tuesday 3pm -> Wednesday 11am -> canceled -> Thursday 4:30pm),
with a stale automated reminder arriving after the final rebook. A retrieve-at-read
stack is expected to surface the original Friday confirmation or the stale repeat;
the correct current answer is Thursday 4:30pm, while Friday stays recoverable as
the original date and the cancellation stays answerable as history.

Matched control: an eye exam (Wednesday 10am) that never moves — both systems
should answer it, and the write-time advantage does not apply.

World truth vs evidence vs expected behavior:

- truth (``Life.truth``): the appointment timeline, the stable eye exam, the
  insurance task, the BBQ opportunity, the durable dentist name;
- evidence (``Life.observations``): emails, texts, Hermes user turns, work noise;
- expected behavior (``Life.checkpoints``): current-state, history, durable-fact,
  task, and control questions graded by the hidden atomic rubric.
"""

from __future__ import annotations

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "dentist-depth"
_WEEK_START = "2026-09-07"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _plan() -> LifePlan:
    return LifePlan(
        id=_ID,
        title="Dentist depth and history",
        bet="update-depth",
        role="development",
        week_start=_WEEK_START,
        difficulty_tags=("update-depth-4", "cross-channel-3", "evidence-age-month",
                         "history-reconstruction", "conflict-stale", "control-stable",
                         "task-tracking", "durable-fact"),
        people=(
            Person(name="Bright Smile Dental", relation="dental office (Dr. Patel)"),
            Person(name="Pearl Vision", relation="eye clinic"),
            Person(name="me", relation="the owner"),
            Person(name="Priya Nair", relation="coworker"),
        ),
        threads=(
            ThreadPlan(id="email-dentist", channel="email",
                       participants=("Bright Smile Dental", "me")),
            ThreadPlan(id="sms-dentist", channel="text",
                       participants=("Bright Smile Dental", "me")),
            ThreadPlan(id="email-eye", channel="email",
                       participants=("Pearl Vision", "me")),
            ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
            ThreadPlan(id="slack-eng", channel="slack",
                       participants=("Priya Nair", "Devon Park", "me")),
            ThreadPlan(id="email-personal", channel="email",
                       participants=("City Museum", "me")),
        ),
        beats=(
            # Older history: durable fact (the dentist's name) from a month ago.
            BeatPlan(local_id="email-due", thread="email-dentist",
                     sender="Bright Smile Dental", day=-28, clock="09:00",
                     draft=("You're due for a cleaning — reply to book. "
                            "Your dentist is Dr. Patel."),
                     fact_id="durable-dentist", must_include=("Dr. Patel",),
                     register="formal"),
            # The scored week.
            BeatPlan(local_id="email-orig", thread="email-dentist",
                     sender="Bright Smile Dental", day=0, clock="08:00",
                     draft=("Confirmed: dental cleaning Friday at 2pm "
                            "with Dr. Patel."),
                     fact_id="appt-created", must_include=("Friday", "2pm"),
                     register="formal"),
            BeatPlan(local_id="hermes-task", thread="hermes", sender="me",
                     day=0, clock="09:00",
                     draft="I need to send the insurance form by Tuesday.",
                     fact_id="task-form", must_include=("insurance form", "Tuesday"),
                     from_owner=True, register="terse"),
            BeatPlan(local_id="sms-move1", thread="sms-dentist",
                     sender="Bright Smile Dental", day=0, clock="12:00",
                     draft=("Update: your cleaning moved to Tuesday at 3pm. "
                            "Sorry for the shuffle!"),
                     fact_id="appt-move-1", must_include=("Tuesday", "3pm"),
                     register="terse"),
            BeatPlan(local_id="hermes-ack", thread="hermes", sender="me",
                     day=0, clock="18:30",
                     draft="Tuesday at 3 works for the dentist. Don't let me forget.",
                     fact_id="appt-ack", must_include=("Tuesday",),
                     from_owner=True),
            BeatPlan(local_id="slack-deploy", thread="slack-eng",
                     sender="Priya Nair", day=1, clock="09:15",
                     draft="deploy is green, shipping the fix now",
                     register="terse"),
            BeatPlan(local_id="email-move2", thread="email-dentist",
                     sender="Bright Smile Dental", day=1, clock="10:00",
                     draft=("Schedule change: your cleaning moved again, "
                            "now Wednesday at 11am."),
                     fact_id="appt-move-2", must_include=("Wednesday", "11am"),
                     register="formal"),
            BeatPlan(local_id="email-eye", thread="email-eye",
                     sender="Pearl Vision", day=1, clock="14:00",
                     draft="Confirmed: eye exam Wednesday at 10am. See you then.",
                     fact_id="eye-stable", must_include=("Wednesday", "10am"),
                     register="formal"),
            BeatPlan(local_id="slack-bbq", thread="slack-eng",
                     sender="Devon Park", day=1, clock="16:00",
                     draft="BBQ at my place Saturday, swing by if you're free!",
                     fact_id="opp-bbq", must_include=("Saturday", "BBQ")),
            BeatPlan(local_id="email-news", thread="email-personal",
                     sender="City Museum", day=1, clock="18:00",
                     draft="Members' preview Thursday evening, plus a gift-shop sale."),
            BeatPlan(local_id="sms-cancel", thread="sms-dentist",
                     sender="Bright Smile Dental", day=2, clock="08:00",
                     draft=("Your Wednesday 11am cleaning is canceled — the office "
                            "is closing early. We'll be in touch to rebook."),
                     fact_id="appt-canceled", must_include=("canceled",),
                     register="terse"),
            BeatPlan(local_id="email-rebook", thread="email-dentist",
                     sender="Bright Smile Dental", day=2, clock="16:00",
                     draft=("Rebooked: dental cleaning Thursday at 4:30pm "
                            "with Dr. Patel. See you then."),
                     fact_id="appt-final", must_include=("Thursday", "4:30pm"),
                     register="formal"),
            # Stale automation sent after the rebook: must not override it.
            BeatPlan(local_id="email-stale", thread="email-dentist",
                     sender="Bright Smile Dental", day=3, clock="08:00",
                     draft=("Reminder: your cleaning is Friday at 2pm tomorrow. "
                            "Reply to reschedule."),
                     fact_id="stale-auto", must_include=("Friday", "2pm"),
                     register="formal"),
            # Late-week noise so the scored week runs the full seven days.
            BeatPlan(local_id="slack-retro", thread="slack-eng",
                     sender="Priya Nair", day=4, clock="09:00",
                     draft="retro moved to Friday, bring your wins",
                     register="terse"),
            BeatPlan(local_id="email-receipt", thread="email-personal",
                     sender="City Museum", day=5, clock="10:00",
                     draft="Your gift-shop order shipped, arrives Monday.",
                     register="formal"),
            BeatPlan(local_id="slack-notes", thread="slack-eng",
                     sender="Devon Park", day=6, clock="18:00",
                     draft="posted the weekend deploy notes, nothing urgent",
                     register="terse"),
        ),
        checkpoints=(
            Checkpoint(
                id=f"{_ID}/now", at=_t(3, "12:00"),
                question="When is my dentist appointment now?",
                mode="complete-plan", required_fields=("day", "time"),
                family="current-state",
                difficulty_tags=("update-depth-4", "conflict-stale",
                                 "cross-channel-3"),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Thursday",
                         forbidden=("Friday", "Tuesday", "Wednesday"),
                         supported_by=(f"{_ID}/email-rebook",)),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="4:30pm", alternatives=("4:30",),
                         forbidden=("2pm", "3pm", "11am", "10am"),
                         supported_by=(f"{_ID}/email-rebook",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/original", at=_t(4, "08:00"),
                question="What was the original day of my dentist appointment before it moved?",
                family="history",
                difficulty_tags=("update-depth-4", "history-reconstruction"),
                slots=(
                    Slot(id="day", kind=Kind.HISTORICAL, norm=Norm.DATE,
                         expected="Friday", forbidden=("Thursday",),
                         supported_by=(f"{_ID}/email-orig",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/canceled", at=_t(4, "08:05"),
                question="Was my dentist appointment ever canceled?",
                family="history",
                difficulty_tags=("history-reconstruction",),
                slots=(
                    Slot(id="canceled", kind=Kind.HISTORICAL, norm=Norm.YESNO,
                         expected="yes", forbidden=("no",),
                         supported_by=(f"{_ID}/sms-cancel",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/eye-exam", at=_t(3, "12:05"),
                question="When is my eye exam?",
                family="current-state",
                difficulty_tags=("control-stable",),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Wednesday", forbidden=("Thursday", "Friday"),
                         supported_by=(f"{_ID}/email-eye",)),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="10am", alternatives=("10:00",),
                         forbidden=("11am", "4:30pm"),
                         supported_by=(f"{_ID}/email-eye",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/dentist-name", at=_t(4, "08:10"),
                question="Who is my dentist?",
                family="durable-facts",
                difficulty_tags=("evidence-age-month", "durable-fact"),
                slots=(
                    Slot(id="who", kind=Kind.CURRENT, norm=Norm.ENTITY,
                         expected="Patel",
                         alternatives=("Dr. Patel", "Dr Patel"),
                         supported_by=(f"{_ID}/email-due", f"{_ID}/email-orig")),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/insurance-task", at=_t(1, "18:00"),
                question="What did I need to send by Tuesday?",
                family="tasks",
                difficulty_tags=("task-tracking",),
                slots=(
                    Slot(id="item", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="insurance form",
                         alternatives=("insurance paperwork",),
                         supported_by=(f"{_ID}/hermes-task",)),
                ),
            ),
        ),
        truth=(
            TruthFact(id="appt-timeline",
                      statement=("Cleaning was Friday 9/11 2pm; moved to Tuesday "
                                 "9/8 3pm, then Wednesday 9/9 11am, then canceled, "
                                 "then rebooked Thursday 9/10 4:30pm."),
                      established_by=(f"{_ID}/email-orig", f"{_ID}/sms-move1",
                                      f"{_ID}/email-move2", f"{_ID}/sms-cancel",
                                      f"{_ID}/email-rebook")),
            TruthFact(id="eye-stable",
                      statement="Eye exam Wednesday 9/9 10am, never moved.",
                      established_by=(f"{_ID}/email-eye",)),
            TruthFact(id="durable-dentist", statement="The dentist is Dr. Patel.",
                      established_by=(f"{_ID}/email-due",)),
            TruthFact(id="task-form",
                      statement="The owner must send the insurance form by Tuesday.",
                      established_by=(f"{_ID}/hermes-task",)),
            TruthFact(id="opp-bbq",
                      statement=("Devon invited the owner to a Saturday BBQ; "
                                 "the owner never committed."),
                      established_by=(f"{_ID}/slack-bbq",)),
        ),
    )


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST
