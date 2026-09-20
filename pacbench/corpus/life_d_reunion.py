"""Development Life D: thread-size difficulty (reunion picnic).

Bet: accuracy survives when the key fact is buried in a large conversation.
Nadia's riverside picnic moves once (Saturday 12pm -> Sunday 1pm, Pavilion 3)
inside a ~150-message WhatsApp group chat full of reactions, side plans, and
topically similar hay (other weekend events on overlapping days, a hearsay
cancellation claim Theo floats and Nadia denies, one stale repeat of the old
time). The correct current answer is Sunday 1pm; Saturday stays recoverable
as the original; the picnic was never canceled.

Matched control: a work picnic (Friday 12pm, office roof) discussed in a
small Slack thread — both systems should answer it; thread size is the only
difference.

Noise discipline (§10.4): pure filler carries no weekday/time tokens, no
commitments, no trips, no appointments, no addresses. Topical hay names other
events with other organizers on distinct times; only Nadia speaks
authoritatively about Nadia's picnic. Nothing here invents durable facts
(allergies, relationships, trips) outside the scored story.

World truth vs evidence vs expected behavior: truth in ``Life.truth``,
messages in ``Life.observations``, hidden rubrics on the checkpoints.
"""

from __future__ import annotations

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "reunion-thread"
_WEEK_START = "2026-09-07"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _plan() -> LifePlan:
    beats: list[BeatPlan] = [
        # Older history: this group chat existed last month. Explicitly last
        # year's event so it cannot compete with the scored picnic.
        BeatPlan(local_id="wa-old-1", thread="wa-reunion", sender="Nadia Reyes",
                 day=-24, clock="19:02",
                 draft=("Throwback to last August's picnic, that potato salad "
                        "was elite."),
                 register="casual"),
        BeatPlan(local_id="wa-old-2", thread="wa-reunion", sender="Theo Marsh",
                 day=-24, clock="19:05",
                 draft="we ate SO much, I was useless for days lol",
                 register="casual"),
        # The scored week: email establishes the original plan + venue.
        BeatPlan(local_id="email-orig", thread="email-nadia",
                 sender="Nadia Reyes", day=0, clock="09:00",
                 draft=("Picnic this Saturday at 12pm, Riverside Park, "
                        "Pavilion 3. See you there!"),
                 fact_id="picnic-created",
                 must_include=("Saturday", "12pm", "Pavilion 3"),
                 register="formal"),
        BeatPlan(local_id="hermes-ack", thread="hermes", sender="me",
                 day=1, clock="18:30",
                 draft="Sunday 1pm works for Nadia's picnic. I'm still going.",
                 fact_id="picnic-ack", must_include=("Sunday",),
                 from_owner=True),
        # The move, in-thread, mid-week.
        BeatPlan(local_id="wa-move", thread="wa-reunion", sender="Nadia Reyes",
                 day=2, clock="12:10",
                 draft=("Heads up: picnic moves to Sunday at 1pm, same "
                        "Pavilion 3. Saturday's forecast is awful."),
                 fact_id="picnic-move",
                 must_include=("Sunday", "1pm", "Pavilion 3"),
                 register="casual"),
        # Cross-channel confirmation with the full current state.
        BeatPlan(local_id="email-confirm", thread="email-nadia",
                 sender="Nadia Reyes", day=3, clock="10:00",
                 draft=("Confirmed: Nadia's picnic is Sunday at 1pm, "
                        "Pavilion 3, Riverside Park. Rain or shine."),
                 fact_id="picnic-final",
                 must_include=("Sunday", "1pm", "Pavilion 3"),
                 register="formal"),
        # Hearsay fake-out: Theo floats a cancellation, Nadia denies it.
        BeatPlan(local_id="wa-hearsay", thread="wa-reunion", sender="Theo Marsh",
                 day=4, clock="09:20",
                 draft="wait didn't Nadia cancel the picnic?? swear I heard that",
                 fact_id="cancel-hearsay", must_include=("cancel",),
                 register="casual"),
        BeatPlan(local_id="wa-deny", thread="wa-reunion", sender="Nadia Reyes",
                 day=4, clock="09:35",
                 draft=("Not canceled, still on for Sunday 1pm! No idea where "
                        "that rumor started lol"),
                 fact_id="cancel-denied",
                 must_include=("Not canceled", "still on", "Sunday"),
                 register="casual"),
        # Stale repeat after the move: must not override the current state.
        BeatPlan(local_id="wa-stale", thread="wa-reunion", sender="Alex Kim",
                 day=5, clock="08:15",
                 draft="see everyone at the picnic Saturday noon!! bringing dessert",
                 fact_id="stale-repeat", must_include=("Saturday",),
                 register="casual"),
        # Matched control: small-thread work picnic that never moves.
        BeatPlan(local_id="slack-work", thread="slack-team", sender="Devon Park",
                 day=1, clock="11:00",
                 draft="Work picnic Friday at 12pm, office roof. Veggie options sorted.",
                 fact_id="work-stable", must_include=("Friday", "12pm"),
                 register="terse"),
    ]
    beats.extend(_topical_hay())
    beats.extend(_wa_filler())
    beats.extend(_email_filler())
    beats.extend(_slack_filler())

    return LifePlan(
        id=_ID,
        title="Reunion picnic in a noisy thread",
        bet="thread-size",
        role="development",
        week_start=_WEEK_START,
        difficulty_tags=("thread-size-xl", "cross-channel-3",
                         "source-authority", "history-reconstruction",
                         "control-stable"),
        people=(
            Person(name="Nadia Reyes", relation="picnic organizer"),
            Person(name="Theo Marsh", relation="group chat regular"),
            Person(name="June Park", relation="group chat regular"),
            Person(name="Alex Kim", relation="group chat regular"),
            Person(name="Robin Diaz", relation="group chat regular"),
            Person(name="Casey Lee", relation="group chat regular"),
            Person(name="Mo Hassan", relation="group chat regular"),
            Person(name="Devon Park", relation="coworker"),
            Person(name="me", relation="the owner"),
        ),
        threads=(
            ThreadPlan(id="wa-reunion", channel="whatsapp",
                       participants=("Nadia Reyes", "Theo Marsh", "June Park",
                                     "Alex Kim", "Robin Diaz", "Casey Lee",
                                     "Mo Hassan", "me")),
            ThreadPlan(id="email-nadia", channel="email",
                       participants=("Nadia Reyes", "me")),
            ThreadPlan(id="email-personal", channel="email",
                       participants=("me",)),
            ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
            ThreadPlan(id="slack-team", channel="slack",
                       participants=("Devon Park", "Priya Nair", "me")),
        ),
        beats=tuple(beats),
        noise_budget=190,
        checkpoints=(
            Checkpoint(
                id=f"{_ID}/now", at=_t(5, "12:00"),
                question="Where and when is Nadia's picnic now?",
                mode="complete-plan", required_fields=("day", "time", "place"),
                family="current-state",
                difficulty_tags=("thread-size-xl", "cross-channel-3"),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Sunday", forbidden=("Saturday",),
                         supported_by=(f"{_ID}/email-confirm", f"{_ID}/wa-move"),
                         error="stale"),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="1pm", alternatives=("1:00",),
                         forbidden=("12pm", "noon"),
                         supported_by=(f"{_ID}/email-confirm", f"{_ID}/wa-move"),
                         error="stale"),
                    Slot(id="place", kind=Kind.CURRENT, norm=Norm.ENTITY,
                         expected="Pavilion 3",
                         alternatives=("pavilion three",),
                         forbidden=("office roof",),
                         supported_by=(f"{_ID}/email-confirm",),
                         error="wrong-occurrence"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/original", at=_t(5, "12:05"),
                question="What day was Nadia's picnic originally supposed to be?",
                family="history",
                difficulty_tags=("thread-size-xl", "history-reconstruction"),
                slots=(
                    Slot(id="day", kind=Kind.HISTORICAL, norm=Norm.DATE,
                         expected="Saturday", forbidden=("Sunday",),
                         supported_by=(f"{_ID}/email-orig",),
                         error="stale"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/canceled", at=_t(5, "12:10"),
                question="Was Nadia's picnic ever canceled?",
                family="source-authority",
                difficulty_tags=("thread-size-xl", "source-authority"),
                slots=(
                    Slot(id="canceled", kind=Kind.HISTORICAL, norm=Norm.YESNO,
                         expected="no", forbidden=("yes",),
                         supported_by=(f"{_ID}/wa-hearsay", f"{_ID}/wa-deny"),
                         error="invented"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/work-now", at=_t(4, "12:30"),
                question="When is the work picnic?",
                family="current-state",
                difficulty_tags=("control-stable",),
                slots=(
                    Slot(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Friday",
                         forbidden=("Saturday", "Sunday"),
                         supported_by=(f"{_ID}/slack-work",),
                         error="stale"),
                    Slot(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                         expected="12pm", alternatives=("noon",),
                         forbidden=("1pm",),
                         supported_by=(f"{_ID}/slack-work",),
                         error="stale"),
                ),
            ),
        ),
        truth=(
            TruthFact(id="picnic-timeline",
                      statement=("Nadia's picnic was Saturday 9/12 12pm at "
                                 "Pavilion 3; moved to Sunday 9/13 1pm, same "
                                 "pavilion. Theo's cancellation rumor was "
                                 "denied by Nadia; Alex repeated the stale "
                                 "Saturday time after the move."),
                      established_by=(f"{_ID}/email-orig", f"{_ID}/wa-move",
                                      f"{_ID}/email-confirm",
                                      f"{_ID}/wa-hearsay", f"{_ID}/wa-deny",
                                      f"{_ID}/wa-stale")),
            TruthFact(id="work-stable",
                      statement="Work picnic Friday 9/11 12pm, office roof, never moved.",
                      established_by=(f"{_ID}/slack-work",)),
        ),
    )


def _topical_hay() -> list[BeatPlan]:
    """Other weekend events, other organizers, distinct times.

    Same-thread hay: shares days with the scored picnic but never the
    (organizer, event) pair, so a resolver must use identity, not keywords.
    """
    return [
        BeatPlan(local_id="wa-hay-game", thread="wa-reunion", sender="Theo Marsh",
                 day=0, clock="20:12",
                 draft="game night at my place Friday, 8pm start, bring nothing lol",
                 register="casual"),
        BeatPlan(local_id="wa-hay-game2", thread="wa-reunion", sender="June Park",
                 day=1, clock="10:02",
                 draft="Friday game night is still on right? need a headcount for snacks",
                 register="casual"),
        BeatPlan(local_id="wa-hay-hike", thread="wa-reunion", sender="June Park",
                 day=1, clock="18:44",
                 draft="early hike Sunday, trailhead 8am, back before lunch",
                 register="casual"),
        BeatPlan(local_id="wa-hay-shower", thread="wa-reunion", sender="Robin Diaz",
                 day=2, clock="09:15",
                 draft="my sister's baby shower is Sunday afternoon, so I'll be late to everything else",
                 register="casual"),
        BeatPlan(local_id="wa-hay-book", thread="wa-reunion", sender="Casey Lee",
                 day=2, clock="21:30",
                 draft="book club Thursday, we're doing the fish book, don't ask",
                 register="casual"),
        BeatPlan(local_id="wa-hay-brunch", thread="wa-reunion", sender="Mo Hassan",
                 day=3, clock="11:05",
                 draft="brunch crew Saturday 10am? the diner with the pancakes",
                 register="casual"),
        BeatPlan(local_id="wa-hay-brunch2", thread="wa-reunion", sender="Alex Kim",
                 day=3, clock="11:20",
                 draft="Saturday brunch yes. pancakes > everything",
                 register="casual"),
        BeatPlan(local_id="wa-hay-tix", thread="wa-reunion", sender="Theo Marsh",
                 day=3, clock="19:58",
                 draft="anyone want my spare movie ticket for Friday night? can't go",
                 register="casual"),
        BeatPlan(local_id="wa-hay-hike2", thread="wa-reunion", sender="June Park",
                 day=4, clock="07:50",
                 draft="Sunday hike crew: 8am sharp, I am leaving slow people behind",
                 register="casual"),
        BeatPlan(local_id="wa-hay-shower2", thread="wa-reunion", sender="Nadia Reyes",
                 day=4, clock="12:00",
                 draft="Robin tell your sister good luck Sunday! we'll save you picnic leftovers",
                 register="casual"),
        BeatPlan(local_id="wa-hay-game3", thread="wa-reunion", sender="Casey Lee",
                 day=4, clock="20:15",
                 draft="Friday game night ran long last time, I'm out by midnight this time",
                 register="casual"),
        BeatPlan(local_id="wa-hay-yoga", thread="wa-reunion", sender="Robin Diaz",
                 day=5, clock="09:02",
                 draft="yoga in the park Saturday morning was packed, never again",
                 register="casual"),
        BeatPlan(local_id="wa-hay-tix2", thread="wa-reunion", sender="Mo Hassan",
                 day=5, clock="14:40",
                 draft="I'll take the Friday ticket Theo",
                 register="casual"),
        BeatPlan(local_id="wa-hay-book2", thread="wa-reunion", sender="June Park",
                 day=5, clock="18:22",
                 draft="finished the fish book. it was fine. that's my whole review",
                 register="casual"),
        BeatPlan(local_id="wa-hay-brunch3", thread="wa-reunion", sender="Theo Marsh",
                 day=6, clock="10:30",
                 draft="brunch aftermath: that diner never misses",
                 register="casual"),
        BeatPlan(local_id="wa-hay-hike3", thread="wa-reunion", sender="Alex Kim",
                 day=6, clock="12:05",
                 draft="hike kicked my butt. worth it",
                 register="casual"),
    ]


_WA_SENDERS = ("Theo Marsh", "June Park", "Alex Kim", "Robin Diaz",
               "Casey Lee", "Mo Hassan", "Nadia Reyes")

_WA_FILLER = (
    "lol", "hahaha", "lolol", "+1", "this", "omg", "same", "mood",
    "facts", "dead", "ayy", "no way", "wait what", "oh nice", "yup",
    "nah", "hmm", "say less", "bet", "fr", "anyway", "classic",
    "of course", "timing", "crying", "sob", "pics or it didn't happen",
    "sending the video", "that cat lol", "that dog video lol",
    "meme dump incoming", "sending memes instead of working",
    "64 unread messages yall", "backread the chat lol", "muting this chat",
    "unmuting, drama?", "who asked", "tacos??", "pizza > everything",
    "leftover pasta supremacy", "is cereal dinner? asking for me",
    "that new place slaps", "coffee first, opinions later",
    "deploy is green", "shipping the fix now", "review pls", "lgtm",
    "ci is red again", "who broke main", "reverting, sorry",
    "standup notes posted", "retro moved, wins welcome",
    "posted the release notes", "nothing urgent",
)

_EMAIL_SENDERS = ("City Museum", "Inbox Deals", "Cloud Photos", "Power Bill",
                  "Library Alerts", "Sneaker Drop", "Transit Authority")

_EMAIL_FILLER = (
    "Members' preview plus a gift-shop sale, see you there.",
    "Your weekly deals roundup is here. Unsubscribe anytime.",
    "Your photo backup finished. You're running low on space.",
    "Your bill is ready to view. Paperless saves paper.",
    "Two books you reserved are ready for pickup.",
    "New arrivals just dropped. Members shop first.",
    "Weekend track work: check before you travel.",
    "Your gift-shop order shipped. Track it in the app.",
    "Reminder: your library card renews soon.",
    "The exhibit closes this month. Don't miss it.",
    "Your storage is almost full. Make room for memories.",
    "Flash sale ends soon. No rainchecks.",
    "Your statement is available. Go paperless today.",
    "New routes start next month. Plan ahead.",
    "The book sale is back. Bags provided.",
    "Your order arrived. Rate your experience.",
    "Museum late nights are back. Friends welcome.",
    "Your subscription renews shortly. Manage it online.",
    "Service alert: delays on the crosstown line.",
    "Photos from this day last year. What a throwback.",
    "Your receipt is attached. Thanks for shopping.",
    "Early access starts now for members.",
)

_SLACK_FILLER = (
    "deploy is green, shipping the fix now",
    "review pls, small diff",
    "lgtm, merging",
    "ci is red again, looking",
    "who broke main (it was me, fixing)",
    "reverting, sorry all",
    "standup notes posted in the doc",
    "retro moved, bring your wins",
    "release notes are up, nothing urgent",
    "oncall handoff done, quiet week",
    "dashboard looks normal again",
    "flaky test quarantined, moving on",
    "docs PR is up if anyone's bored",
    "cache cleared, try now",
    "staging is frozen till noon",
    "added metrics to the rollout",
    "rollback finished, all clear",
    "new runbook for the pager, skim it",
    "lint is clean, miracle",
    "dependency bump, no behavior change",
    "incident review moved to next week",
    "feature flag flipped, watching",
    "logs look boring, as they should",
    "deploy train left the station",
)


def _spread(count: int, day_lo: int, day_hi: int, start_min: int = 9 * 60,
            step_min: int = 47) -> list[tuple[int, str]]:
    """Deterministic timestamps: no randomness, freeze-stable."""
    out: list[tuple[int, str]] = []
    minute = start_min
    day = day_lo
    for _ in range(count):
        while minute >= 23 * 60:
            minute -= 14 * 60
            day += 1
            if day > day_hi:
                day = day_lo
        out.append((day, f"{minute // 60:02d}:{minute % 60:02d}"))
        minute += step_min
        if minute >= 23 * 60:
            minute = start_min
            day += 1
            if day > day_hi:
                day = day_lo
    return out


def _wa_filler() -> list[BeatPlan]:
    stamps = _spread(124, 0, 6)
    beats: list[BeatPlan] = []
    for i, ((day, clock), text) in enumerate(zip(stamps, (_WA_FILLER * 4)[:124])):
        beats.append(BeatPlan(
            local_id=f"wa-f{i:03d}", thread="wa-reunion",
            sender=_WA_SENDERS[i % len(_WA_SENDERS)],
            day=day, clock=clock, draft=text, register="casual"))
    return beats


def _email_filler() -> list[BeatPlan]:
    stamps = _spread(22, 0, 6, start_min=8 * 60, step_min=191)
    beats: list[BeatPlan] = []
    for i, ((day, clock), text) in enumerate(zip(stamps, _EMAIL_FILLER)):
        beats.append(BeatPlan(
            local_id=f"em-f{i:02d}", thread="email-personal",
            sender=_EMAIL_SENDERS[i % len(_EMAIL_SENDERS)],
            day=day, clock=clock, draft=text, register="formal"))
    return beats


def _slack_filler() -> list[BeatPlan]:
    stamps = _spread(24, 0, 5, start_min=9 * 60 + 30, step_min=137)
    beats: list[BeatPlan] = []
    for i, ((day, clock), text) in enumerate(zip(stamps, _SLACK_FILLER)):
        sender = "Priya Nair" if i % 3 == 2 else (
            "Devon Park" if i % 3 == 0 else "me")
        beats.append(BeatPlan(
            local_id=f"sl-f{i:02d}", thread="slack-team",
            sender=sender, day=day, clock=clock, draft=text,
            from_owner=(sender == "me"), register="terse"))
    return beats


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST
