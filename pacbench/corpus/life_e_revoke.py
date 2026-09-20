"""Development Life E: cancellation-reversal, deletion, and completion (revoke).

Bet: invalidation is a first-class state change. The owner books a dinner,
cancels it, then rebooks a new night — the assistant must hold exactly one
active booking, not zero and not two. The owner explicitly tells the
assistant to forget a superseded gate code — the old code must stop shaping
current answers while staying recoverable as history (deleting current
relevance is not destroying context). A completed plant-watering task must
not resurface the next time the owner walks in the door.

Matched controls are internal: the history checkpoints (was it canceled?
what was the old code?) verify the invalidated state is preserved, not
erased — the mirror image of the current-state probes.

Noise discipline (§10.4): filler carries no weekday/time tokens, no
commitments, no trips, no appointments, no addresses. A friend's Friday
drinks plan is topical hay with a different organizer, never Bistro Nova.

World truth vs evidence vs expected behavior: truth in ``Life.truth``,
messages in ``Life.observations``, hidden rubrics on the checkpoints.
"""

from __future__ import annotations

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "revoke-week"
_WEEK_START = "2026-09-07"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _plan() -> LifePlan:
    return LifePlan(
        id=_ID,
        title="Reversal, deletion, and completion",
        bet="invalidation",
        role="development",
        week_start=_WEEK_START,
        difficulty_tags=("cancel-reversal", "deletion", "task-completion",
                         "negative-control"),
        people=(
            Person(name="Bistro Nova", relation="restaurant"),
            Person(name="Ray", relation="building super"),
            Person(name="Theo Marsh", relation="friend with separate plans"),
            Person(name="June Park", relation="group chat regular"),
            Person(name="Priya Nair", relation="coworker"),
            Person(name="me", relation="the owner"),
        ),
        threads=(
            ThreadPlan(id="email-bistro", channel="email",
                       participants=("Bistro Nova", "me")),
            ThreadPlan(id="sms-super", channel="text",
                       participants=("Ray", "me")),
            ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
            ThreadPlan(id="wa-friends", channel="whatsapp",
                       participants=("Theo Marsh", "June Park", "me")),
            ThreadPlan(id="email-personal", channel="email",
                       participants=("me",)),
            ThreadPlan(id="slack-team", channel="slack",
                       participants=("Priya Nair", "me")),
        ),
        beats=tuple([
            # Older history: last month's dinner, explicitly past.
            BeatPlan(local_id="email-oldvisit", thread="email-bistro",
                     sender="Bistro Nova", day=-21, clock="20:00",
                     draft=("Thanks for dining with us last month. "
                            "We hope to see you again soon."),
                     register="formal"),
            # The scored dinner: booked Friday...
            BeatPlan(local_id="email-booked", thread="email-bistro",
                     sender="Bistro Nova", day=0, clock="09:00",
                     draft=("Confirmed: dinner Friday at 7pm, Bistro Nova, "
                            "table for two."),
                     fact_id="dinner-booked",
                     must_include=("Friday", "7pm"), register="formal"),
            # ...canceled by the owner through Hermes...
            BeatPlan(local_id="hermes-cancel", thread="hermes", sender="me",
                     day=1, clock="18:00",
                     draft="Cancel my Friday dinner at Bistro Nova, something came up.",
                     fact_id="dinner-canceled",
                     must_include=("Cancel", "Friday"),
                     from_owner=True),
            # ...confirmed canceled by the restaurant...
            BeatPlan(local_id="email-cancelconf", thread="email-bistro",
                     sender="Bistro Nova", day=2, clock="10:00",
                     draft=("Cancellation confirmed for your Friday dinner. "
                            "Hope to see you another night."),
                     fact_id="dinner-cancel-conf",
                     must_include=("Cancellation", "Friday"),
                     register="formal"),
            # ...then rebooked Saturday. Exactly one active booking.
            BeatPlan(local_id="email-rebook", thread="email-bistro",
                     sender="Bistro Nova", day=3, clock="12:00",
                     draft=("Rebooked: dinner Saturday at 7pm, Bistro Nova, "
                            "table for two. This replaces the canceled booking "
                            "— you now hold a single active reservation."),
                     fact_id="dinner-final",
                     must_include=("Saturday", "7pm", "single"),
                     register="formal"),
            # Gate code rotation + explicit forget instruction.
            BeatPlan(local_id="sms-gate", thread="sms-super", sender="Ray",
                     day=0, clock="08:00",
                     draft=("Building notice: gate code changed to 8821. "
                            "Disregard the old 4410."),
                     fact_id="gate-new", must_include=("8821", "4410"),
                     register="terse"),
            BeatPlan(local_id="hermes-forget", thread="hermes", sender="me",
                     day=4, clock="09:00",
                     draft="Forget the old gate code 4410, only 8821 works now.",
                     fact_id="gate-forget",
                     must_include=("Forget", "4410", "8821"),
                     from_owner=True),
            # Plant task: assigned, then completed before the probe.
            BeatPlan(local_id="hermes-task", thread="hermes", sender="me",
                     day=1, clock="08:00",
                     draft="Remind me to water the plants tonight.",
                     fact_id="task-plants",
                     must_include=("water", "plants"),
                     from_owner=True),
            BeatPlan(local_id="hermes-done", thread="hermes", sender="me",
                     day=1, clock="21:00",
                     draft="Watered the plants, done.",
                     fact_id="task-done",
                     must_include=("Watered", "done"),
                     from_owner=True),
            # Topical hay: someone else's Friday plan, different organizer.
            BeatPlan(local_id="wa-drinks", thread="wa-friends",
                     sender="Theo Marsh", day=2, clock="19:20",
                     draft="drinks at my place Friday, 9pm, small crew",
                     register="casual"),
            BeatPlan(local_id="wa-drinks2", thread="wa-friends",
                     sender="June Park", day=3, clock="10:15",
                     draft="Friday drinks still on? bringing nothing as usual lol",
                     register="casual"),
            *_wa_filler(),
            *_email_filler(),
            *_slack_filler(),
        ]),
        noise_budget=35,
        checkpoints=(
            Checkpoint(
                id=f"{_ID}/dinner-now", at=_t(4, "12:00"),
                question="When is my Bistro Nova dinner now?",
                mode="complete-plan", required_fields=("day", "time"),
                family="current-state",
                difficulty_tags=("cancel-reversal",),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Saturday", forbidden=("Friday",),
                         supported_by=(f"{_ID}/email-rebook",)),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="7pm", alternatives=("7:00",),
                         forbidden=("8pm",),
                         supported_by=(f"{_ID}/email-rebook",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/dinner-count", at=_t(4, "12:05"),
                question="How many active dinner bookings do I have at Bistro Nova?",
                family="invalidation",
                difficulty_tags=("cancel-reversal",),
                slots=(
                    Slot(id="count", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="one",
                         alternatives=("1", "single", "just one",
                                       "one booking"),
                         forbidden=("two", "2", "both"),
                         supported_by=(f"{_ID}/email-rebook",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/dinner-canceled", at=_t(4, "12:10"),
                question="Was my Bistro Nova dinner ever canceled?",
                family="history",
                difficulty_tags=("cancel-reversal",),
                slots=(
                    Slot(id="canceled", kind=Kind.HISTORICAL, norm=Norm.YESNO,
                         expected="yes", forbidden=("no",),
                         supported_by=(f"{_ID}/hermes-cancel",
                                       f"{_ID}/email-cancelconf")),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/gate-now", at=_t(5, "12:00"),
                question="What's the gate code?",
                family="current-state",
                difficulty_tags=("deletion",),
                slots=(
                    Slot(id="code", kind=Kind.CURRENT, norm=Norm.ENTITY,
                         expected="8821", forbidden=("4410",),
                         supported_by=(f"{_ID}/sms-gate",
                                       f"{_ID}/hermes-forget")),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/gate-old", at=_t(5, "12:05"),
                question="What was the old gate code before it changed?",
                family="history",
                difficulty_tags=("deletion",),
                slots=(
                    Slot(id="code", kind=Kind.HISTORICAL, norm=Norm.ENTITY,
                         expected="4410", forbidden=("8821",),
                         supported_by=(f"{_ID}/sms-gate",)),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/plants-silent", at=_t(2, "12:00"),
                question="I'm home.",
                mode="open-ended",
                family="proactivity",
                difficulty_tags=("task-completion", "negative-control"),
                forbidden_options=(
                    Slot(id="trap-water", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="water", alternatives=("watering",)),
                    Slot(id="trap-plants", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="plants"),
                ),
            ),
        ),
        truth=(
            TruthFact(id="dinner-timeline",
                      statement=("Bistro Nova dinner booked Friday 9/11 7pm, "
                                 "canceled 9/8, rebooked Saturday 9/12 7pm. "
                                 "Exactly one active booking."),
                      established_by=(f"{_ID}/email-booked",
                                      f"{_ID}/hermes-cancel",
                                      f"{_ID}/email-cancelconf",
                                      f"{_ID}/email-rebook")),
            TruthFact(id="gate-codes",
                      statement=("Gate code rotated 4410 -> 8821; owner "
                                 "explicitly told the assistant to forget "
                                 "4410. 8821 is current; 4410 is history."),
                      established_by=(f"{_ID}/sms-gate",
                                      f"{_ID}/hermes-forget")),
            TruthFact(id="plants-task",
                      statement=("Plant-watering task assigned Monday morning, "
                                 "completed Monday night; nothing outstanding."),
                      established_by=(f"{_ID}/hermes-task",
                                      f"{_ID}/hermes-done")),
        ),
    )


def _wa_filler() -> list[BeatPlan]:
    texts = (
        "lol", "hahaha", "+1", "omg", "same", "no way", "oh nice", "say less",
        "that dog video lol", "meme dump incoming", "muting this chat",
        "tacos??", "leftover pasta supremacy", "coffee first, opinions later",
        "that new place slaps", "weekend already?", "finally",
        "sending the video", "classic", "of course",
    )
    senders = ("Theo Marsh", "June Park")
    beats: list[BeatPlan] = []
    minute = 9 * 60
    day = 0
    for i, text in enumerate(texts):
        beats.append(BeatPlan(
            local_id=f"wa-f{i:02d}", thread="wa-friends",
            sender=senders[i % 2], day=day,
            clock=f"{minute // 60:02d}:{minute % 60:02d}",
            draft=text, register="casual"))
        minute += 211
        while minute >= 23 * 60:
            minute -= 14 * 60
            day += 1
            if day > 6:
                day = 0
    return beats


def _email_filler() -> list[BeatPlan]:
    texts = (
        ("Cloud Photos", "Your photo backup finished. Space is running low."),
        ("Library Alerts", "Two books you reserved are ready for pickup."),
        ("Power Bill", "Your bill is ready to view. Paperless saves paper."),
        ("City Museum", "The exhibit closes this month. Don't miss it."),
        ("Inbox Deals", "Your weekly deals roundup is here."),
        ("Transit Authority", "Weekend track work: check before you travel."),
        ("Sneaker Drop", "New arrivals just dropped. Members shop first."),
        ("Cloud Photos", "Photos from this day last year. What a throwback."),
    )
    beats: list[BeatPlan] = []
    for i, (sender, text) in enumerate(texts):
        beats.append(BeatPlan(
            local_id=f"em-f{i:02d}", thread="email-personal", sender=sender,
            day=i % 7, clock=f"{8 + i % 10:02d}:{(i * 17) % 60:02d}",
            draft=text, register="formal"))
    return beats


def _slack_filler() -> list[BeatPlan]:
    texts = (
        "deploy is green, shipping the fix now", "review pls, small diff",
        "lgtm, merging", "ci is red again, looking",
        "standup notes posted in the doc", "release notes are up",
        "oncall handoff done, quiet week", "flaky test quarantined",
    )
    beats: list[BeatPlan] = []
    for i, text in enumerate(texts):
        sender = "Priya Nair" if i % 2 else "me"
        beats.append(BeatPlan(
            local_id=f"sl-f{i:02d}", thread="slack-team", sender=sender,
            day=i % 6, clock=f"{9 + i % 8:02d}:{(i * 23) % 60:02d}",
            draft=text, from_owner=(sender == "me"), register="terse"))
    return beats


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST
