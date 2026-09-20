"""Development Life C: proactive tasks and broad context (coat + weekend).

Bet: a useful assistant surfaces relevant stored context without being asked, and
knows the difference between an opportunity, a commitment, and a declined plan.
The owner borrows Eric's green coat, commits to returning it, and days later says
they are heading to Eric's — the assistant should proactively surface the coat.
A later visit to Sam's, with no outstanding task attached, is the matched
negative control: no reminder should fire.

The same week carries several weekend opportunities (concert, farmers market,
family brunch), one declined invitation (waterfront festival), one tentative
invitation (rooftop movie), and one real commitment (brunch). "Anything fun this
weekend?" must surface the live options without promoting the declined, expired,
or tentative ones into plans, and attendance questions must not turn an
unconfirmed invitation into a commitment.

World truth vs evidence vs expected behavior:

- truth: the coat is outstanding; the concert/market are uncommitted
  opportunities; brunch is a commitment; the festival is declined; the rooftop
  plan is tentative; the Glassworks deal expired Wednesday;
- evidence: Hermes turns, texts, WhatsApp, group chat, email;
- expected behavior: a proactive reminder, a silent negative control, broad
  opportunity recall, commitment precision, and abstention on the unresolved
  concert.
"""

from __future__ import annotations

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "coat-weekend"
_WEEK_START = "2026-09-07"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _plan() -> LifePlan:
    return LifePlan(
        id=_ID,
        title="Coat return and weekend opportunities",
        bet="proactive+opportunity",
        role="development",
        lineage="weekend-opportunities",
        week_start=_WEEK_START,
        difficulty_tags=("proactive-context", "negative-control",
                         "opportunity-coverage", "commitment-precision",
                         "uncertainty-abstain", "cross-channel-2",
                         "evidence-age-week"),
        people=(
            Person(name="Eric Vance", relation="coat owner"),
            Person(name="Sam Delgado", relation="no outstanding task"),
            Person(name="Maya Chen", relation="concert inviter, dinner recommender"),
            Person(name="Priya Nair", relation="farmers-market organizer"),
            Person(name="Mom", relation="brunch host"),
            Person(name="Devon Park", relation="festival inviter (declined)"),
            Person(name="me", relation="the owner"),
        ),
        threads=(
            ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
            ThreadPlan(id="im-eric", channel="imessage",
                       participants=("Eric Vance", "me")),
            ThreadPlan(id="im-maya", channel="imessage",
                       participants=("Maya Chen", "me")),
            ThreadPlan(id="im-mom", channel="imessage",
                       participants=("Mom", "me")),
            ThreadPlan(id="wa-friends", channel="whatsapp",
                       participants=("Eric Vance", "Priya Nair", "me")),
            ThreadPlan(id="gm-crew", channel="groupme",
                       participants=("Priya Nair", "Devon Park", "Sam Delgado",
                                     "me")),
            ThreadPlan(id="email-tix", channel="email",
                       participants=("Riverside Concerts", "Glassworks", "me")),
        ),
        beats=(
            # The coat: borrowed before the scored week, still outstanding.
            BeatPlan(local_id="hermes-borrow", thread="hermes", sender="me",
                     day=-3, clock="22:15",
                     draft=("Borrowed Eric's green coat after the party. "
                            "Need to return it next time I see him."),
                     fact_id="coat-task", must_include=("coat", "Eric's"),
                     from_owner=True),
            BeatPlan(local_id="im-coat-norush", thread="im-eric",
                     sender="Eric Vance", day=0, clock="09:00",
                     draft="no rush on the green coat, just bring it whenever you're over",
                     fact_id="coat-confirm", must_include=("coat",)),
            # Weekend opportunities and traps.
            BeatPlan(local_id="email-concert", thread="email-tix",
                     sender="Riverside Concerts", day=1, clock="10:00",
                     draft=("Maya invited you: Lantern Show concert Saturday 8pm. "
                            "Reply to join — no RSVP from you yet."),
                     fact_id="opp-concert", must_include=("concert", "Saturday"),
                     register="formal"),
            BeatPlan(local_id="hermes-decline", thread="hermes", sender="me",
                     day=1, clock="15:00",
                     draft=("Told Devon I can't make the waterfront festival "
                            "Saturday — too crowded."),
                     fact_id="declined-festival",
                     must_include=("waterfront festival",), from_owner=True),
            BeatPlan(local_id="im-maya-dinner", thread="im-maya",
                     sender="Maya Chen", day=2, clock="12:00",
                     draft="Verde on 5th is great for dinner if you need a spot",
                     fact_id="rec-dinner", must_include=("Verde",)),
            BeatPlan(local_id="gm-market", thread="gm-crew",
                     sender="Priya Nair", day=2, clock="18:00",
                     draft="farmers market Saturday morning, who's in?",
                     fact_id="opp-market",
                     must_include=("farmers market", "Saturday")),
            BeatPlan(local_id="email-glassworks", thread="email-tix",
                     sender="Glassworks", day=0, clock="10:00",
                     draft=("Glassworks exhibit flash sale ends Wednesday — "
                            "members only after that."),
                     fact_id="expired-deal", must_include=("Glassworks",),
                     register="formal"),
            BeatPlan(local_id="im-brunch", thread="im-mom",
                     sender="Mom", day=3, clock="09:00",
                     draft="Family brunch Sunday at ours. Let me know!",
                     fact_id="opp-brunch", must_include=("brunch", "Sunday")),
            BeatPlan(local_id="hermes-brunch-yes", thread="hermes", sender="me",
                     day=3, clock="10:00",
                     draft="Yeah, we're coming to brunch Sunday. I'll bring juice.",
                     fact_id="commit-brunch", must_include=("brunch", "Sunday"),
                     from_owner=True),
            BeatPlan(local_id="gm-rooftop", thread="gm-crew",
                     sender="Sam Delgado", day=3, clock="14:00",
                     draft="rooftop movie night maybe Sunday? still figuring it out",
                     fact_id="tentative-rooftop", must_include=("rooftop",)),
            # Proactive trigger and its negative control.
            BeatPlan(local_id="hermes-erics", thread="hermes", sender="me",
                     day=5, clock="17:00",
                     draft="I'm heading over to Eric's around six.",
                     fact_id="trigger-eric", must_include=("Eric's",),
                     from_owner=True, register="terse"),
            BeatPlan(local_id="hermes-sams", thread="hermes", sender="me",
                     day=6, clock="11:00",
                     draft="I'm heading over to Sam's around noon.",
                     fact_id="trigger-sam", must_include=("Sam's",),
                     from_owner=True, register="terse"),
        ),
        checkpoints=(
            Checkpoint(
                id=f"{_ID}/coat-reminder", at=_t(5, "17:05"),
                question="I'm heading over to Eric's around six.",
                family="proactivity",
                difficulty_tags=("proactive-context",),
                slots=(
                    Slot(id="reminder", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="coat",
                         alternatives=("green coat", "his coat", "jacket"),
                         forbidden=("Sam's", "Jordan's"),
                         supported_by=(f"{_ID}/hermes-borrow",
                                       f"{_ID}/im-coat-norush"),
                         error="wrong-person"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/sam-visit", at=_t(6, "11:05"),
                question="I'm heading over to Sam's around noon.",
                mode="open-ended",
                family="proactivity",
                difficulty_tags=("proactive-context", "negative-control"),
                forbidden_options=(
                    Slot(id="trap-coat", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="coat", alternatives=("jacket",),
                         error="wrong-person"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/anything-fun", at=_t(4, "19:00"),
                question="Anything fun I can do this weekend?",
                mode="open-ended",
                family="opportunity",
                difficulty_tags=("opportunity-coverage",),
                supported=(
                    Slot(id="opt-concert", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="concert", alternatives=("Lantern Show",),
                         supported_by=(f"{_ID}/email-concert",)),
                    Slot(id="opt-market", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="farmers market", alternatives=("market",),
                         supported_by=(f"{_ID}/gm-market",)),
                    Slot(id="opt-brunch", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="brunch", alternatives=("family brunch",),
                         supported_by=(f"{_ID}/hermes-brunch-yes",)),
                ),
                forbidden_options=(
                    Slot(id="trap-festival", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="waterfront",
                         alternatives=("waterfront festival",),
                         error="expired"),
                    Slot(id="trap-glassworks", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="Glassworks", alternatives=("exhibit",),
                         error="expired"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/brunch-commitment", at=_t(4, "19:05"),
                question="Are you going to the family brunch Sunday?",
                family="commitment",
                difficulty_tags=("commitment-precision",),
                slots=(
                    Slot(id="going", kind=Kind.CURRENT, norm=Norm.YESNO,
                         expected="yes", forbidden=("no",),
                         supported_by=(f"{_ID}/hermes-brunch-yes",),
                         error="invented"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/concert-rsvp", at=_t(4, "19:06"),
                question="Are you going to the Lantern Show concert?",
                family="uncertainty",
                difficulty_tags=("commitment-precision", "uncertainty-abstain"),
                slots=(
                    # Commitment status, not a date: the evidence establishes
                    # an uncommitted opportunity (no RSVP), never a missing
                    # date to recall. An empty-DATE sentinel here would muddle
                    # exactly the opportunity-vs-commitment distinction this
                    # family measures.
                    Slot(id="rsvp", kind=Kind.CURRENT, norm=Norm.YESNO,
                         expected="no", forbidden=("yes",),
                         supported_by=(f"{_ID}/email-concert",),
                         error="invented"),
                    Slot(id="date", kind=Kind.CURRENT, norm=Norm.DATE,
                         expected="Saturday", forbidden=("Sunday",),
                         supported_by=(f"{_ID}/email-concert",),
                         error="stale"),
                ),
            ),
            Checkpoint(
                id=f"{_ID}/dinner-rec", at=_t(4, "19:10"),
                question="What did Maya recommend for dinner?",
                family="recommendation",
                difficulty_tags=("cross-channel-2",),
                slots=(
                    Slot(id="spot", kind=Kind.CURRENT, norm=Norm.TEXT,
                         expected="Verde", alternatives=("Verde on 5th",),
                         supported_by=(f"{_ID}/im-maya-dinner",)),
                ),
            ),
        ),
        truth=(
            TruthFact(id="coat-outstanding",
                      statement=("The owner borrowed Eric's green coat and must "
                                 "return it; it is still outstanding."),
                      established_by=(f"{_ID}/hermes-borrow",
                                      f"{_ID}/im-coat-norush")),
            TruthFact(id="concert-opportunity",
                      statement=("Maya invited the owner to the Saturday concert; "
                                 "the owner never RSVP'd."),
                      established_by=(f"{_ID}/email-concert",)),
            TruthFact(id="market-opportunity",
                      statement="The Saturday farmers market is an open invitation.",
                      established_by=(f"{_ID}/gm-market",)),
            TruthFact(id="brunch-commitment",
                      statement="The owner committed to Sunday family brunch.",
                      established_by=(f"{_ID}/im-brunch",
                                      f"{_ID}/hermes-brunch-yes")),
            TruthFact(id="festival-declined",
                      statement="The owner declined the waterfront festival.",
                      established_by=(f"{_ID}/hermes-decline",)),
            TruthFact(id="rooftop-tentative",
                      statement="The rooftop movie is tentative, not a plan.",
                      established_by=(f"{_ID}/gm-rooftop",)),
            TruthFact(id="deal-expired",
                      statement="The Glassworks sale ended Wednesday.",
                      established_by=(f"{_ID}/email-glassworks",)),
        ),
    )


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST
