"""Development Life F: long-horizon noisy recall (race-week).

Bet: long-horizon-noisy-recall. The owner trains for the Harbor 10k,
plans a nephew's birthday party, and repairs a car across one scored
week plus August history — ~1,600 observations, most of them mundane.
The assistant must hold nine-update training state, a four-update venue
chain, superseded times, a durable allergy, time-bounded deadlines, and
very-short-lived states, while ignoring declined/expired/wrong-person
traps and a storm-cancellation rumor the organizer denies.

Generated-life proof (proceed ruling step 3): this module is the
authoring plan — structured data only. Beats carry plain drafts plus atomic
must_include tokens; :mod:`pacbench.render` will rewrite surface
language through Spark with entailment validation, or drafts ship
verbatim (deterministic, offline). Filler is programmatic from timeless
templates (seeded) plus :mod:`pacbench.gen_qa` screens: no weekday/time
tokens, no commitments, no slot-forbidden words, no negate cues — so
the contradiction scan stays disposition-clean by construction except
for deliberate update trails.

Noise discipline: filler carries no dates, times, plans, or names from
the scored stories. A friend's lunch is topical hay with a different
organizer, never the race, the party, or the car.
"""

from __future__ import annotations

import random

from ..authoring import BeatPlan, LifePlan, Person, ThreadPlan, build_life
from ..life import Life, TruthFact
from ..manifest import SupportManifest
from ..rubric import Checkpoint, Kind, Norm, Slot

_ID = "race-week"
_WEEK_START = "2026-09-14"
_LINEAGE = "harbor-10k"


def _t(day: int, clock: str) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(_WEEK_START) + timedelta(days=day)
    return f"{d.isoformat()}T{clock}:00"


def _people() -> tuple[Person, ...]:
    return (
        Person(name="Maya", relation="running buddy"),
        Person(name="Coach Diaz", relation="Harbor 10k organizer"),
        Person(name="Ana", relation="sister, Leo's mom"),
        Person(name="Leo", relation="nephew, turning 7"),
        Person(name="Gus", relation="mechanic at Harbor Auto"),
        Person(name="Priya", relation="friend"),
        Person(name="Rae", relation="work manager"),
        Person(name="Sam", relation="coworker"),
    )


def _threads() -> tuple[ThreadPlan, ...]:
    return (
        ThreadPlan(id="hermes", channel="hermes", participants=("me",)),
        ThreadPlan(id="wa-maya", channel="whatsapp", participants=("Maya",)),
        ThreadPlan(id="grp-race", channel="groupme",
                   participants=("Maya", "Coach Diaz", "Jess")),
        ThreadPlan(id="mail-coach", channel="email",
                   participants=("Coach Diaz",)),
        ThreadPlan(id="im-ana", channel="imessage", participants=("Ana",)),
        ThreadPlan(id="tx-gus", channel="text", participants=("Gus",)),
        ThreadPlan(id="wa-priya", channel="whatsapp", participants=("Priya",)),
        ThreadPlan(id="slack-work", channel="slack",
                   participants=("Rae", "Sam")),
        ThreadPlan(id="wa-sam", channel="whatsapp", participants=("Sam",)),
        ThreadPlan(id="mail-news", channel="email",
                   participants=("Harbor Gazette",)),
    )


def _evidence() -> list[BeatPlan]:
    B = BeatPlan
    return [
        # -- August history ------------------------------------------------
        B(local_id="h-reg", thread="mail-coach", sender="Harbor 10k",
          day=-25, clock="09:00", register="formal",
          draft="You're registered for the Harbor 10k on October 4. Early-bird entry $45.",
          fact_id="reg", must_include=("Harbor 10k", "October 4")),
        B(local_id="h-shoes-old", thread="hermes", sender="me",
          day=-20, clock="21:10", from_owner=True,
          draft="Retiring the old Pegasus, 400 miles on them.",
          fact_id="shoes-old", must_include=("Pegasus",)),
        B(local_id="h-run-sat", thread="wa-maya", sender="Maya",
          day=-6, clock="08:00",
          draft="Long run Saturday 7am as usual?",
          fact_id="run-sat", must_include=("Saturday", "7am")),
        # -- registration deadline (time-bounded) --------------------------
        B(local_id="b-rb-early", thread="mail-coach", sender="Harbor 10k",
          day=0, clock="09:12", register="formal",
          draft="Early-bird pricing ends Thursday at midnight, then the fee goes from $45 to $60.",
          fact_id="reg-deadline", must_include=("Thursday", "$60")),
        # -- party setup ----------------------------------------------------
        B(local_id="b-party0", thread="im-ana", sender="Ana",
          day=0, clock="18:20",
          draft="Leo's birthday party is Saturday at 2pm, community hall is booked.",
          fact_id="venue0", must_include=("Saturday", "2pm", "community hall")),
        B(local_id="b-allergy0", thread="im-ana", sender="Ana",
          day=0, clock="18:24",
          draft="Leo is allergic to peanuts, so no peanut snacks at the party please.",
          fact_id="allergy", must_include=("allergic to peanuts",)),
        # -- car -------------------------------------------------------------
        B(local_id="b-car0", thread="tx-gus", sender="Gus",
          day=1, clock="10:05",
          draft="Brakes are $420 all-in, car will be ready Thursday.",
          fact_id="car", must_include=("$420", "Thursday")),
        B(local_id="b-rsvp", thread="im-ana", sender="Ana",
          day=1, clock="19:40",
          draft="Please RSVP for the party by Wednesday so I have a headcount.",
          fact_id="rsvp", must_include=("Wednesday",)),
        # -- injury -----------------------------------------------------------
        B(local_id="b-injury", thread="hermes", sender="me",
          day=1, clock="20:15", from_owner=True,
          draft="Shin splints flaring up on the left leg again.",
          fact_id="injury", must_include=("Shin splints",)),
        B(local_id="b-physio", thread="tx-gus", sender="Physio Plus",
          day=2, clock="09:30",
          draft="Rest 3 days, ice 15 minutes twice daily.",
          fact_id="injury", must_include=("Rest 3 days",)),
        # -- long run moves (depth chain) --------------------------------------
        B(local_id="b-washout", thread="grp-race", sender="Coach Diaz",
          day=2, clock="07:15",
          draft="Saturday is a washout — long run moves to Sunday 7am.",
          fact_id="run-sun", must_include=("Sunday", "7am")),
        B(local_id="b-maya-confirm", thread="wa-maya", sender="Maya",
          day=2, clock="07:40",
          draft="Sunday 7am works, see you at the pier.",
          fact_id="run-sun", must_include=("Sunday", "7am")),
        # -- declined trap ------------------------------------------------------
        B(local_id="b-hw-invite", thread="wa-priya", sender="Priya",
          day=2, clock="12:10",
          draft="Housewarming Saturday at 4pm, you in?",
          fact_id="hw-invite", must_include=("Housewarming",)),
        B(local_id="b-hw-decline", thread="hermes", sender="me",
          day=2, clock="12:30", from_owner=True,
          draft="Told Priya I can't make the housewarming, party day.",
          fact_id="hw-declined", must_include=("can't make", "housewarming")),
        # -- shoes + expired coupon ----------------------------------------------
        B(local_id="b-shoes", thread="tx-gus", sender="Fleet Feet",
          day=2, clock="14:00",
          draft="Nimbus 26 size 9, $140. The 20%-off coupon expired Sunday.",
          fact_id="shoes", must_include=("Nimbus 26", "expired Sunday")),
        # -- very-short-lived state -----------------------------------------------
        B(local_id="b-busy", thread="slack-work", sender="Rae",
          day=2, clock="13:05", register="terse",
          draft="Heads-down until 3pm, ping me after.",
          fact_id="busy3", must_include=("until 3pm",)),
        # -- training support ------------------------------------------------------
        B(local_id="b-gels", thread="wa-maya", sender="Maya",
          day=3, clock="08:10",
          draft="Bringing citrus gels for mile 4.",
          fact_id="gels", must_include=("citrus", "mile 4")),
        B(local_id="b-bib", thread="mail-coach", sender="Harbor 10k",
          day=3, clock="09:00", register="formal",
          draft="Bib pickup Friday 5 to 8pm at the expo hall.",
          fact_id="bib", must_include=("Friday", "8pm", "expo hall")),
        # -- wrong-person trap -------------------------------------------------------
        B(local_id="b-sister-ankle", thread="wa-maya", sender="Maya",
          day=3, clock="12:20",
          draft="My sister twisted her ankle, she's out for Sunday.",
          fact_id="other-injury", must_include=("sister", "ankle")),
        # -- venue chain (depth-4) -----------------------------------------------------
        B(local_id="b-gym0", thread="im-ana", sender="Ana",
          day=3, clock="17:30",
          draft="Bad news — the community hall double-booked us, moving Leo's birthday party to the school gym.",
          fact_id="venue1", must_include=("school gym",)),
        B(local_id="b-gym-permit", thread="im-ana", sender="Ana",
          day=3, clock="17:35",
          draft="The gym needs a permit for Saturday events.",
          fact_id="permit0", must_include=("permit",)),
        B(local_id="b-cake", thread="im-ana", sender="Ana",
          day=3, clock="18:00",
          draft="Cake ordered: chocolate, says LEO 7, pickup Friday at noon.",
          fact_id="cake", must_include=("LEO 7", "noon")),
        B(local_id="b-rsvp-count", thread="im-ana", sender="Ana",
          day=3, clock="19:00",
          draft="Headcount is 12 so far.",
          fact_id="rsvp", must_include=("12",)),
        # -- rumor vs organizer ------------------------------------------------------------
        B(local_id="b-rumor", thread="grp-race", sender="Jess",
          day=4, clock="08:00",
          draft="Heard the 10k might be canceled with the storm coming?!",
          fact_id="rumor", must_include=("canceled",)),
        B(local_id="b-coach-on", thread="mail-coach", sender="Coach Diaz",
          day=4, clock="08:25", register="formal",
          draft="The Harbor 10k is ON, rain or shine. See you Sunday.",
          fact_id="race-on", must_include=("ON",)),
        B(local_id="b-coach-echo", thread="hermes", sender="me",
          day=4, clock="08:40", from_owner=True,
          draft="Coach says the race is still on despite the storm talk.",
          fact_id="race-on", must_include=("still on",)),
        # -- dinner moves ----------------------------------------------------------------------
        B(local_id="b-dinner0", thread="wa-maya", sender="Maya",
          day=4, clock="18:00",
          draft="Carb-load dinner Saturday 6pm at my place.",
          fact_id="dinner0", must_include=("Saturday", "6pm")),
        B(local_id="b-ven-permit-ok", thread="im-ana", sender="Ana",
          day=4, clock="10:15",
          draft="Permit approved Thursday morning, gym is ours Saturday.",
          fact_id="venue-ok", must_include=("Permit approved",)),
        B(local_id="b-car-late", thread="tx-gus", sender="Gus",
          day=4, clock="11:00",
          draft="Parts are late — brakes ready Friday at 3pm now.",
          fact_id="car1", must_include=("Friday", "3pm")),
        B(local_id="b-car-ok", thread="tx-gus", sender="Gus",
          day=4, clock="16:00",
          draft="Car is ready, pickup Friday 3pm confirmed.",
          fact_id="car1", must_include=("Friday", "3pm")),
        B(local_id="b-gift", thread="hermes", sender="me",
          day=4, clock="20:00", from_owner=True,
          draft="Got the Lego set for Leo, $35, wrapped.",
          fact_id="gift", must_include=("Lego",)),
        # -- race eve -------------------------------------------------------------------------------
        B(local_id="b-dinner1", thread="wa-maya", sender="Maya",
          day=5, clock="09:30",
          draft="Carb-load dinner moved to 6:30, same place.",
          fact_id="dinner1", must_include=("6:30",)),
        B(local_id="b-bib-remind", thread="hermes", sender="me",
          day=5, clock="08:00", from_owner=True,
          draft="Bib pickup today 5pm, don't forget ID.",
          fact_id="bib", must_include=("5pm",)),
        B(local_id="b-cake-done", thread="im-ana", sender="Ana",
          day=5, clock="12:30",
          draft="Cake picked up, LEO 7 looks great.",
          fact_id="cake", must_include=("LEO 7",)),
        B(local_id="b-allergy1", thread="im-ana", sender="Ana",
          day=5, clock="13:00",
          draft="Reminder: nut-free table only, Leo's allergy.",
          fact_id="allergy", must_include=("nut-free",)),
        B(local_id="b-run-sun2", thread="grp-race", sender="Maya",
          day=5, clock="14:00",
          draft="See everyone Sunday 7am at the pier, don't be late.",
          fact_id="run-sun", must_include=("Sunday", "7am")),
        B(local_id="b-venue-ok2", thread="im-ana", sender="Ana",
          day=5, clock="14:30",
          draft="Gym setup starts 1pm Saturday, Ana has the key.",
          fact_id="venue-ok", must_include=("Gym",)),
        B(local_id="b-meter", thread="hermes", sender="me",
          day=5, clock="15:05", from_owner=True,
          draft="Meter paid until 4:30, back by then.",
          fact_id="meter", must_include=("until 4:30",)),
        B(local_id="b-raceday", thread="mail-coach", sender="Harbor 10k",
          day=5, clock="17:00", register="formal",
          draft="Gates open 5:30 Sunday, Pier Lot closes at 7am.",
          fact_id="raceday", must_include=("7am", "Pier Lot")),
        B(local_id="b-alarm", thread="hermes", sender="me",
          day=5, clock="21:00", from_owner=True,
          draft="Alarm set 5:45am for race day.",
          fact_id="raceday", must_include=("5:45am",)),
        B(local_id="b-brunch-maybe", thread="wa-maya", sender="Maya",
          day=5, clock="20:00",
          draft="Might do brunch after the race, TBD.",
          fact_id="brunch-tbd", must_include=("TBD",)),
        B(local_id="b-gels-packed", thread="hermes", sender="me",
          day=5, clock="21:20", from_owner=True,
          draft="Citrus gels packed for mile 4.",
          fact_id="gels", must_include=("Citrus", "mile 4")),
    ]


_FILLER_POOL: tuple[tuple[str, str, str], ...] = (
    # (channel, sender, draft) — timeless, dateless, planless, digit-free.
    ("imessage", "Ana", "Leftover soup is in the fridge if you want some"),
    ("imessage", "Ana", "The basil plant is somehow still alive"),
    ("imessage", "Ana", "Did you leave your jacket here again"),
    ("text", "Gus", "Shop coffee is terrible as always"),
    ("text", "Gus", "Rain got the floors all muddy"),
    ("whatsapp", "Maya", "That sunset on the run was unreal"),
    ("whatsapp", "Maya", "My knees are complaining about the hills"),
    ("whatsapp", "Maya", "Found a great smoothie place near the park"),
    ("whatsapp", "Priya", "This book is so good I stayed up late"),
    ("whatsapp", "Priya", "Have you watched that baking show yet"),
    ("whatsapp", "Priya", "My sourdough finally rose properly"),
    ("whatsapp", "Sam", "Anyone want my extra concert poster"),
    ("whatsapp", "Sam", "The new cafe downtown has oat milk"),
    ("groupme", "Maya", "Who ate the last granola bar"),
    ("groupme", "Jess", "Sharing the trail map link again"),
    ("groupme", "Coach Diaz", "Hydrate, people"),
    ("slack", "Rae", "standup notes posted in the doc"),
    ("slack", "Sam", "review pls, small diff"),
    ("slack", "Rae", "lgtm, merging"),
    ("slack", "Sam", "ci is red again, looking"),
    ("slack", "Rae", "deploy is green, shipping now"),
    ("slack", "Sam", "flaky test quarantined"),
    ("email", "Harbor Gazette", "Harbor Gazette: neighborhood cleanup recap"),
    ("email", "Harbor Gazette", "Harbor Gazette: library fundraiser results"),
    ("email", "GreenCart", "GreenCart: seasonal produce guide"),
    ("imessage", "Ana", "Mom called twice, call her back"),
    ("imessage", "Ana", "The cat knocked over the plant again"),
    ("text", "Gus", "Waiting on parts, slow day"),
    ("whatsapp", "Maya", "Stretching is the worst part honestly"),
    ("whatsapp", "Priya", "Made too much pasta, come help"),
    ("whatsapp", "Sam", "Lost my water bottle again"),
    ("groupme", "Jess", "Good luck everyone this weekend"),
    ("slack", "Rae", "release notes are up"),
    ("slack", "Sam", "oncall handoff done, quiet week"),
    ("email", "GreenCart", "GreenCart: five-ingredient recipe collection"),
    ("imessage", "Ana", "Leo lost another tooth"),
    ("imessage", "Ana", "The porch light is flickering"),
    ("text", "Gus", "Somebody left headlights on in the lot"),
    ("whatsapp", "Maya", "Foam roller is my best friend now"),
    ("whatsapp", "Priya", "The farmers documentary was lovely"),
    ("whatsapp", "Sam", "My headphones finally died"),
    ("groupme", "Maya", "Rest day done right with pizza"),
    ("slack", "Rae", "docs sprint starts soon, volunteers welcome"),
    ("email", "Harbor Gazette", "Harbor Gazette: park concert series photos"),
    ("imessage", "Ana", "Borrowed your umbrella, sorry"),
    ("whatsapp", "Maya", "Easy miles only, legs are heavy"),
    ("whatsapp", "Priya", "Tea or coffee person lately"),
    ("whatsapp", "Sam", "The bus was packed this morning"),
    ("slack", "Sam", "pairing session was super useful"),
    ("email", "GreenCart", "GreenCart: pantry staples checklist"),
    ("imessage", "Ana", "Fridge is making a weird noise"),
    ("text", "Gus", "Lunch truck is back on the corner"),
    ("whatsapp", "Maya", "Hill repeats never get easier"),
    ("whatsapp", "Priya", "Rewatching the old sitcom again"),
    ("groupme", "Jess", "Anyone have sunscreen to share"),
    ("slack", "Rae", "retro moved to the usual room"),
    ("email", "Harbor Gazette", "Harbor Gazette: volunteer spotlight"),
    ("imessage", "Ana", "Leo drew a rocket ship"),
    ("whatsapp", "Maya", "New playlist is carrying the long run"),
    ("whatsapp", "Sam", "Power went out for a minute here"),
    ("slack", "Sam", "stuck on the auth bug, any ideas"),
    ("email", "GreenCart", "GreenCart: herb garden starter tips"),
    ("imessage", "Ana", "The wifi dropped during my call"),
    ("text", "Gus", "Oil stain finally came off the driveway"),
    ("whatsapp", "Priya", "Soup season is officially here"),
    ("whatsapp", "Maya", "Blister situation under control"),
    ("groupme", "Maya", "Carpool playlist nominations open"),
    ("slack", "Rae", "demo went well, nice work all"),
    ("email", "Harbor Gazette", "Harbor Gazette: mural project update"),
    ("imessage", "Ana", "Packages keep arriving for Leo"),
    ("whatsapp", "Sam", "The bakery line was out the door"),
    ("slack", "Sam", "migrated the last endpoint, done"),
    ("email", "GreenCart", "GreenCart: composting basics"),
    ("imessage", "Ana", "Sidewalk chalk everywhere outside"),
    ("whatsapp", "Maya", "Tempo run felt strong for once"),
    ("whatsapp", "Priya", "Knitting a scarf, very slowly"),
    ("groupme", "Jess", "Thanks for the route tips all"),
    ("slack", "Rae", "incident review notes shared"),
    ("email", "Harbor Gazette", "Harbor Gazette: farmers column"),
    ("imessage", "Ana", "The smoke alarm beeped at midnight"),
    ("whatsapp", "Maya", "Ice bath, zero regrets after"),
    ("whatsapp", "Sam", "Found a dollar on the sidewalk"),
    ("slack", "Sam", "tests are green across the board"),
    ("email", "GreenCart", "GreenCart: lunchbox ideas"),
    ("text", "Gus", "Classic car show in the lot was fun"),
    ("whatsapp", "Priya", "Plants need watering, oops"),
    ("groupme", "Maya", "Who is bringing the speaker"),
    ("slack", "Rae", "hiring loop debrief posted"),
    ("email", "Harbor Gazette", "Harbor Gazette: tide schedule note"),
    ("imessage", "Ana", "Leo hid the remote again"),
    ("whatsapp", "Maya", "Recovery shake tastes like chalk"),
    ("whatsapp", "Sam", "Elevator is out, taking stairs"),
    ("slack", "Sam", "refactored the helper, much cleaner"),
    ("email", "GreenCart", "GreenCart: soup night menu"),
    ("imessage", "Ana", "The mailbox was full of flyers"),
    ("text", "Gus", "Air compressor is fixed now"),
    ("whatsapp", "Priya", "Sunsets have been gorgeous lately"),
    ("groupme", "Jess", "Stretch circle after, join us"),
    ("slack", "Rae", "perf review season is here"),
    ("email", "Harbor Gazette", "Harbor Gazette: bookmobile stops"),
    ("imessage", "Ana", "Leo wants pancakes shaped like dinosaurs"),
    ("whatsapp", "Maya", "Chafing is the enemy of mankind"),
    ("whatsapp", "Sam", "Crosswalk light is blinking red"),
    ("slack", "Sam", "can someone approve the migration"),
    ("email", "GreenCart", "GreenCart: farmers interview"),
    ("text", "Gus", "Torque wrench is missing again"),
    ("whatsapp", "Priya", "Hot chocolate weather is coming"),
    ("groupme", "Maya", "Post-run bagels are sacred"),
    ("slack", "Rae", "all-hands notes are posted"),
    ("email", "Harbor Gazette", "Harbor Gazette: garden tour recap"),
    ("imessage", "Ana", "The dog next door barks at squirrels"),
    ("whatsapp", "Maya", "Sleep is the real performance enhancer"),
    ("whatsapp", "Sam", "Vending machine ate my dollar"),
    ("slack", "Sam", "schema change is backward compatible"),
    ("email", "GreenCart", "GreenCart: grain bowl guide"),
    ("imessage", "Ana", "Couch cushions are all over the floor"),
    ("text", "Gus", "Battery tester says all good"),
    ("whatsapp", "Priya", "Puzzle night was a success"),
    ("groupme", "Jess", "First aid kit is in my car"),
    ("slack", "Rae", "travel policy updated, read when free"),
    ("email", "Harbor Gazette", "Harbor Gazette: pier cleanup day"),
    ("imessage", "Ana", "Leo learned to whistle sort of"),
    ("whatsapp", "Maya", "Spectating is underrated"),
    ("whatsapp", "Sam", "Laundry day finally arrived"),
    ("slack", "Sam", "load test passed with room to spare"),
    ("email", "GreenCart", "GreenCart: pickling primer"),
    ("text", "Gus", "Shop dog learned a new trick"),
    ("whatsapp", "Priya", "Movie night picks are slim"),
    ("groupme", "Maya", "Early bedtime club checking in"),
    ("slack", "Rae", "security training due soon"),
    ("email", "Harbor Gazette", "Harbor Gazette: lighthouse history piece"),
)

_FILLER_N = 1550
_FILLER_SEED = 20260914

_THREAD_OF = {
    "hermes": "hermes", "whatsapp": "wa-maya", "groupme": "grp-race",
    "slack": "slack-work", "email": "mail-news", "imessage": "im-ana",
    "text": "tx-gus",
}


def _filler_beats() -> list[BeatPlan]:
    """Programmatic timeless filler: seeded, deterministic, planless."""
    rng = random.Random(_FILLER_SEED)
    pool = list(_FILLER_POOL)
    beats: list[BeatPlan] = []
    for i in range(_FILLER_N):
        channel, sender, draft = pool[i % len(pool)]
        # Spread: 15% August history, 85% across the scored week.
        if rng.random() < 0.15:
            day = rng.randint(-30, -1)
        else:
            day = rng.randint(0, 6)
        clock = f"{rng.randint(7, 22):02d}:{rng.choice((5, 15, 25, 35, 45, 55)):02d}"
        thread = _THREAD_OF[channel]
        if channel == "whatsapp" and sender in ("Priya", "Sam"):
            thread = "wa-priya" if sender == "Priya" else "wa-sam"
        beats.append(BeatPlan(
            local_id=f"f{i:04d}", thread=thread, sender=sender,
            day=day, clock=clock, draft=draft,
            register="formal" if channel == "email" else (
                "terse" if channel == "slack" else "casual")))
    return beats


def _checkpoints() -> tuple[Checkpoint, ...]:
    S = Slot
    return (
        Checkpoint(
            id=f"{_ID}/long-run", at=_t(4, "09:00"),
            question="When is the long run now?",
            family="current-state", difficulty_tags=("update-depth-8",),
            slots=(
                S(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                  expected="Sunday", alternatives=("Sun",),
                  forbidden=("Saturday",), supported_by=(
                      f"{_ID}/b-washout", f"{_ID}/b-maya-confirm"),
                  error="stale"),
                S(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                  expected="7am", alternatives=("7 am",),
                  supported_by=(f"{_ID}/b-washout",)),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/party-venue", at=_t(5, "10:00"),
            question="Where is Leo's birthday party?",
            family="current-state", difficulty_tags=("update-depth-4",),
            slots=(
                S(id="venue", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="school gym", alternatives=("gym",),
                  forbidden=("community hall",), supported_by=(
                      f"{_ID}/b-gym0", f"{_ID}/b-ven-permit-ok"),
                  error="stale"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/car-pickup", at=_t(4, "17:00"),
            question="When can I pick up the car?",
            family="current-state", difficulty_tags=("update-depth-2",),
            slots=(
                S(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                  expected="Friday", alternatives=("Fri",),
                  forbidden=("Thursday",), supported_by=(
                      f"{_ID}/b-car-late", f"{_ID}/b-car-ok"),
                  error="stale"),
                S(id="time", kind=Kind.CURRENT, norm=Norm.TIME,
                  expected="3pm", alternatives=("3 pm",),
                  supported_by=(f"{_ID}/b-car-late",)),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/race-on", at=_t(4, "18:30"),
            question="Is the 10k still on despite the storm talk?",
            family="current-state", difficulty_tags=("hearsay-vs-organizer",),
            slots=(
                S(id="on", kind=Kind.CURRENT, norm=Norm.YESNO,
                  expected="yes", forbidden=("no",),
                  supported_by=(f"{_ID}/b-coach-on", f"{_ID}/b-coach-echo"),
                  error="invented"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/party-snacks", at=_t(5, "14:00"),
            question="What snacks should I bring for Leo's party?",
            family="current-state", difficulty_tags=("durable-preference",),
            slots=(
                S(id="safe", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="nut-free", alternatives=("no peanuts", "peanut-free"),
                  forbidden=("peanut snacks",), supported_by=(
                      f"{_ID}/b-allergy0", f"{_ID}/b-allergy1"),
                  error="invented"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/rsvp-by", at=_t(2, "12:00"),
            question="When do I need to RSVP for Leo's party?",
            family="current-state", difficulty_tags=("time-bounded",),
            slots=(
                S(id="day", kind=Kind.CURRENT, norm=Norm.DATE,
                  expected="Wednesday", alternatives=("Wed",),
                  supported_by=(f"{_ID}/b-rsvp",)),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/earlybird", at=_t(2, "12:30"),
            question="Is early-bird registration still open?",
            family="current-state", difficulty_tags=("time-bounded",),
            slots=(
                S(id="open", kind=Kind.CURRENT, norm=Norm.YESNO,
                  expected="yes", forbidden=("no",),
                  supported_by=(f"{_ID}/b-rb-early",),
                  error="invented"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/rae-free", at=_t(2, "15:30"),
            question="Is Rae free now?",
            family="current-state", difficulty_tags=("very-short-lived",),
            slots=(
                S(id="free", kind=Kind.CURRENT, norm=Norm.YESNO,
                  expected="yes", forbidden=("no",),
                  supported_by=(f"{_ID}/b-busy",),
                  error="invented"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/first-venue", at=_t(5, "16:00"),
            question="Where was Leo's party first booked?",
            family="history", difficulty_tags=("update-depth-4",),
            slots=(
                S(id="venue", kind=Kind.HISTORICAL, norm=Norm.TEXT,
                  expected="community hall", alternatives=("the hall",),
                  supported_by=(f"{_ID}/b-party0",)),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/weekend-fun", at=_t(5, "18:00"),
            question="Anything fun this weekend?",
            mode="open-ended",
            family="current-state", difficulty_tags=("cross-channel",),
            supported=(
                S(id="opt-race", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="Harbor 10k", alternatives=("10k", "race"),
                  supported_by=(f"{_ID}/b-coach-on",)),
                S(id="opt-party", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="birthday party", alternatives=("party",),
                  supported_by=(f"{_ID}/b-gym0",)),
                S(id="opt-dinner", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="carb-load dinner", alternatives=("dinner",),
                  supported_by=(f"{_ID}/b-dinner1",)),
            ),
            forbidden_options=(
                S(id="trap-housewarming", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="housewarming", alternatives=("Priya's party",),
                  error="expired"),
                S(id="trap-coupon", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="coupon", alternatives=("20%-off",),
                  error="expired"),
                S(id="trap-satrun", kind=Kind.CURRENT, norm=Norm.TEXT,
                  expected="Saturday", alternatives=("Saturday morning",),
                  error="stale"),
            ),
        ),
        Checkpoint(
            id=f"{_ID}/afterparty", at=_t(5, "21:30"),
            question="Is there a post-race brunch plan?",
            mode="unanswerable",
            family="abstention", difficulty_tags=("negative-control",),
            slots=(
                S(id="brunch", kind=Kind.ABSTAIN, norm=Norm.TEXT,
                  expected="", forbidden=("Saturday", "Sunday"),
                  supported_by=(f"{_ID}/b-brunch-maybe",),
                  error="invented"),
            ),
        ),
    )


def _truth() -> tuple[TruthFact, ...]:
    T = TruthFact
    return (
        TruthFact(id="T01", statement="The owner is registered for the Harbor 10k on October 4.",
                  established_by=(f"{_ID}/h-reg",)),
        TruthFact(id="T02", statement="Early-bird pricing ends Thursday; then $60.",
                  established_by=(f"{_ID}/b-rb-early",)),
        TruthFact(id="T03", statement="The long run is Sunday 7am (moved from Saturday by rain).",
                  established_by=(f"{_ID}/h-run-sat", f"{_ID}/b-washout",
                                  f"{_ID}/b-maya-confirm", f"{_ID}/b-run-sun2")),
        TruthFact(id="T04", statement="Left leg shin splints: rest 3 days, ice twice daily.",
                  established_by=(f"{_ID}/b-injury", f"{_ID}/b-physio")),
        TruthFact(id="T05", statement="Race shoes are Nimbus 26 size 9; the 20%-off coupon expired Sunday.",
                  established_by=(f"{_ID}/b-shoes",)),
        TruthFact(id="T06", statement="Citrus gels for mile 4.",
                  established_by=(f"{_ID}/b-gels", f"{_ID}/b-gels-packed")),
        TruthFact(id="T07", statement="Bib pickup Friday 5 to 8pm at the expo hall.",
                  established_by=(f"{_ID}/b-bib", f"{_ID}/b-bib-remind")),
        TruthFact(id="T08", statement="Leo's party is Saturday 2pm at the school gym (permit approved); first booked at the community hall.",
                  established_by=(f"{_ID}/b-party0", f"{_ID}/b-gym0",
                                  f"{_ID}/b-gym-permit", f"{_ID}/b-ven-permit-ok",
                                  f"{_ID}/b-venue-ok2")),
        TruthFact(id="T09", statement="Leo's party needs an RSVP by Wednesday; headcount 12.",
                  established_by=(f"{_ID}/b-rsvp", f"{_ID}/b-rsvp-count")),
        TruthFact(id="T10", statement="The Lego gift for Leo is bought and wrapped.",
                  established_by=(f"{_ID}/b-gift",)),
        TruthFact(id="T11", statement="Leo is allergic to peanuts; the party table is nut-free.",
                  established_by=(f"{_ID}/b-allergy0", f"{_ID}/b-allergy1")),
        TruthFact(id="T12", statement="Brakes cost $420; the car is ready for Friday 3pm pickup.",
                  established_by=(f"{_ID}/b-car0", f"{_ID}/b-car-late",
                                  f"{_ID}/b-car-ok")),
        TruthFact(id="T13", statement="Priya's housewarming was declined for party day.",
                  established_by=(f"{_ID}/b-hw-invite", f"{_ID}/b-hw-decline")),
        TruthFact(id="T14", statement="Maya's sister (not the owner) hurt her ankle; the owner's issue is shin splints.",
                  established_by=(f"{_ID}/b-sister-ankle", f"{_ID}/b-injury")),
        TruthFact(id="T15", statement="Rae was heads-down until 3pm Tuesday.",
                  established_by=(f"{_ID}/b-busy",)),
        TruthFact(id="T16", statement="The parking meter was paid until 4:30 Saturday.",
                  established_by=(f"{_ID}/b-meter",)),
        TruthFact(id="T17", statement="A post-race brunch is only a TBD maybe.",
                  established_by=(f"{_ID}/b-brunch-maybe",)),
        TruthFact(id="T18", statement="Gates open 5:30 race day; Pier Lot closes 7am; alarm 5:45am.",
                  established_by=(f"{_ID}/b-raceday", f"{_ID}/b-alarm")),
        TruthFact(id="T19", statement="The Harbor 10k is ON despite storm rumors.",
                  established_by=(f"{_ID}/b-rumor", f"{_ID}/b-coach-on",
                                  f"{_ID}/b-coach-echo")),
        TruthFact(id="T20", statement="Carb-load dinner moved from Saturday 6pm to 6:30.",
                  established_by=(f"{_ID}/b-dinner0", f"{_ID}/b-dinner1")),
        TruthFact(id="T21", statement="Chocolate LEO 7 cake, picked up Friday noon.",
                  established_by=(f"{_ID}/b-cake", f"{_ID}/b-cake-done")),
    )


def _plan() -> LifePlan:
    return LifePlan(
        id=_ID,
        title="Harbor 10k week",
        bet="long-horizon-noisy-recall",
        role="development",
        lineage=_LINEAGE,
        week_start=_WEEK_START,
        difficulty_tags=("update-depth-8", "update-depth-4", "update-depth-2",
                         "time-bounded", "durable-preference",
                         "very-short-lived", "hearsay-vs-organizer",
                         "cross-channel", "negative-control"),
        people=_people(),
        threads=_threads(),
        beats=tuple(_evidence() + _filler_beats()),
        checkpoints=_checkpoints(),
        truth=_truth(),
        noise_budget=_FILLER_N,
    )


_LIFE, _MANIFEST = build_life(_plan())


def lives() -> list[Life]:
    return [_LIFE]


def manifest() -> SupportManifest:
    return _MANIFEST