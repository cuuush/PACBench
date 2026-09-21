"""Contradiction scan (contract §12.3).

For every required slot with forbidden values, find non-support
observations whose current-tense text asserts a forbidden value under the
grader's own matching rules (no judge). Each hit is a *candidate*
contradiction to a protected benchmark fact: it may be deliberate
difficulty (stale repeats, hearsay, superseded trails), same-word
different-entity noise, or a genuine corpus accident.

Every candidate must carry a human disposition in KNOWN. The enforcing
test fails on any undispositioned hit, so new corpus text that
accidentally contradicts a protected fact breaks the suite until a human
looks at it and either dispositions it (deliberate) or fixes the corpus
(accident). The scan itself is deterministic and provider-blind: frozen
corpus in, candidate list out.

Known scan limitations (recorded, not hidden):
- YESNO polarity is entity- and proposition-blind: an affirm cue about
  Eric's game matches a "yes" forbidden on John's slot. Such hits are
  dispositioned `scan-limitation-yesno` only after verifying the text is
  about a different entity or consistent with the expected value.
  Proposition-scoped polarity is judge/semantic territory (§33).
- Single-character TEXT values match by substring ("2" inside gate code
  "8821"). Dispositioned `scan-limitation-substring` after verifying
  different meaning. (The parallel TIME issue — "noon" inside
  "afternoon" — was a live false-conviction path and was fixed in the
  grader instead, with a regression test.)
"""

from __future__ import annotations

from dataclasses import dataclass

from .grade import _asserts, _current_text
from .life import Life

#: Closed disposition vocabulary. `accidental` is intentionally absent: an
#: accidental contradiction must be fixed in the corpus, never allowlisted.
REASONS = (
    "trail-superseded",       # earlier/later state in the same update trail
    "deliberate-stale",       # authored stale repeat (difficulty)
    "deliberate-hearsay",     # authored misleading claim (difficulty)
    "different-entity",       # same word, different person/procedure
    "different-procedure",    # same domain, different procedure (exam vs cleaning)
    "different-event",        # same weekday/time word, different event
    "different-topic",        # same weekday word, unrelated topic
    "ambient-coincidence",    # filler-chatter word coincidence
    "trigger-turn",           # checkpoint's own prompt-side user turn
    "scan-limitation-yesno",  # entity-blind polarity match, verified benign
    "scan-limitation-substring",  # substring match, verified different meaning
)


@dataclass(frozen=True)
class Candidate:
    life_id: str
    obs_id: str
    checkpoint_id: str  # short id (after the "/")
    slot_id: str
    matched: tuple[str, ...]
    expected: str
    channel: str
    collected_at: str

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.life_id, self.obs_id, self.checkpoint_id,
                self.slot_id, "|".join(self.matched))


def scan_contradictions(life: Life) -> list[Candidate]:
    """All (required slot, non-support observation) forbidden assertions."""
    out: list[Candidate] = []
    for cp in life.checkpoints:
        short = cp.id.split("/")[-1]
        for slot in (*cp.slots, *cp.supported):
            if not (slot.required and slot.forbidden):
                continue
            for o in life.observations:
                if o.source_id in slot.supported_by:
                    continue
                cur = _current_text(o.text)
                vals = sorted(
                    f for f in slot.forbidden
                    if f and f.strip() and _asserts(
                        cur, f, slot.norm, None, for_forbidden=True))
                if vals:
                    out.append(Candidate(
                        life_id=life.id, obs_id=o.source_id,
                        checkpoint_id=short, slot_id=slot.id,
                        matched=tuple(vals), expected=slot.expected,
                        channel=o.channel, collected_at=o.collected_at))
    return out


#: (life, obs, checkpoint-short, slot, matched-joined) -> reason.
#: Reviewed 2026-09-20 against full observation text: zero accidental
#: contradictions; every hit below is deliberate difficulty, a different
#: entity/event/topic, filler coincidence, prompt-side context, or a
#: verified-benign scan limitation.
KNOWN: dict[tuple[str, str, str, str, str], str] = {
    ("poker-move", "poker-move/email-oldaddr", "where", "loc",
     "12 Ash|12 Ash Street|Sam's"): "trail-superseded",
    ("poker-move", "poker-move/wa-stale", "where", "loc",
     "Sam's"): "deliberate-stale",
    ("poker-move", "poker-move/gm-replan", "where", "day",
     "Friday"): "trail-superseded",
    ("poker-move", "poker-move/wa-stale", "where", "day",
     "Friday"): "deliberate-stale",
    ("weekend-plans", "weekend-plans/email-memberday", "dinner-day",
     "dana-day", "Saturday"): "ambient-coincidence",
    ("weekend-plans", "weekend-plans/im-family", "dinner-day",
     "dana-day", "Sunday"): "different-event",
    ("dentist-depth", "dentist-depth/email-orig", "now", "day",
     "Friday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/hermes-task", "now", "day",
     "Tuesday"): "different-topic",
    ("dentist-depth", "dentist-depth/sms-move1", "now", "day",
     "Tuesday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/hermes-ack", "now", "day",
     "Tuesday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-move2", "now", "day",
     "Wednesday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-eye", "now", "day",
     "Wednesday"): "different-procedure",
    ("dentist-depth", "dentist-depth/sms-cancel", "now", "day",
     "Wednesday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-stale", "now", "day",
     "Friday"): "deliberate-stale",
    ("dentist-depth", "dentist-depth/slack-retro", "now", "day",
     "Friday"): "different-topic",
    ("dentist-depth", "dentist-depth/email-orig", "now", "time",
     "2pm"): "trail-superseded",
    ("dentist-depth", "dentist-depth/sms-move1", "now", "time",
     "3pm"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-move2", "now", "time",
     "11am"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-eye", "now", "time",
     "10am"): "different-procedure",
    ("dentist-depth", "dentist-depth/sms-cancel", "now", "time",
     "11am"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-stale", "now", "time",
     "2pm"): "deliberate-stale",
    ("dentist-depth", "dentist-depth/email-news", "original", "day",
     "Thursday"): "different-topic",
    ("dentist-depth", "dentist-depth/email-rebook", "original", "day",
     "Thursday"): "trail-superseded",
    ("dentist-depth", "dentist-depth/email-orig", "eye-exam", "day",
     "Friday"): "different-procedure",
    ("dentist-depth", "dentist-depth/email-news", "eye-exam", "day",
     "Thursday"): "different-topic",
    ("dentist-depth", "dentist-depth/email-rebook", "eye-exam", "day",
     "Thursday"): "different-procedure",
    ("dentist-depth", "dentist-depth/email-stale", "eye-exam", "day",
     "Friday"): "different-procedure",
    ("dentist-depth", "dentist-depth/slack-retro", "eye-exam", "day",
     "Friday"): "different-topic",
    ("dentist-depth", "dentist-depth/email-move2", "eye-exam", "time",
     "11am"): "different-procedure",
    ("dentist-depth", "dentist-depth/sms-cancel", "eye-exam", "time",
     "11am"): "different-procedure",
    ("dentist-depth", "dentist-depth/email-rebook", "eye-exam", "time",
     "4:30pm"): "different-procedure",
    ("poker-collision", "poker-collision/wa-priya-claim", "eric-now",
     "status", "canceled"): "deliberate-hearsay",
    ("poker-collision", "poker-collision/email-lineup", "eric-now",
     "day", "Friday"): "different-entity",
    ("poker-collision", "poker-collision/gm-john-plan", "eric-now",
     "day", "Friday"): "different-entity",
    ("poker-collision", "poker-collision/email-john-cancel", "eric-now",
     "day", "Friday"): "different-entity",
    ("poker-collision", "poker-collision/gm-john-plan", "eric-now",
     "time", "7pm"): "different-entity",
    ("poker-collision", "poker-collision/gm-eric-plan", "eric-now",
     "time", "7pm"): "trail-superseded",
    ("poker-collision", "poker-collision/gm-eric-stale", "eric-now",
     "time", "7pm"): "deliberate-stale",
    ("poker-collision", "poker-collision/email-lineup", "eric-now",
     "loc", "John's"): "different-entity",
    ("poker-collision", "poker-collision/gm-john-plan", "eric-now",
     "loc", "78 Cedar"): "different-entity",
    ("poker-collision", "poker-collision/im-eric-correct", "john-now",
     "status", "yes"): "scan-limitation-yesno",
    ("poker-collision", "poker-collision/hermes-commit", "john-now",
     "status", "yes"): "scan-limitation-yesno",
    ("poker-collision", "poker-collision/hermes-commit", "eric-canceled",
     "status", "yes"): "scan-limitation-yesno",
    ("poker-collision", "poker-collision/email-allhands", "lunch",
     "day", "Thursday"): "different-event",
    ("coat-weekend", "coat-weekend/hermes-sams", "coat-reminder",
     "reminder", "Sam's"): "trigger-turn",
    ("coat-weekend", "coat-weekend/im-coat-norush", "brunch-commitment",
     "going", "no"): "scan-limitation-yesno",
    ("coat-weekend", "coat-weekend/email-concert", "brunch-commitment",
     "going", "no"): "scan-limitation-yesno",
    ("coat-weekend", "coat-weekend/hermes-brunch-yes", "concert-rsvp",
     "rsvp", "yes"): "scan-limitation-yesno",
    ("coat-weekend", "coat-weekend/im-brunch", "concert-rsvp",
     "date", "Sunday"): "different-event",
    ("coat-weekend", "coat-weekend/hermes-brunch-yes", "concert-rsvp",
     "date", "Sunday"): "different-event",
    ("coat-weekend", "coat-weekend/gm-rooftop", "concert-rsvp",
     "date", "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/email-orig", "now", "day",
     "Saturday"): "trail-superseded",
    ("reunion-thread", "reunion-thread/wa-stale", "now", "day",
     "Saturday"): "deliberate-stale",
    ("reunion-thread", "reunion-thread/wa-hay-brunch", "now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-brunch2", "now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/email-orig", "now", "time",
     "12pm"): "trail-superseded",
    ("reunion-thread", "reunion-thread/wa-stale", "now", "time",
     "noon"): "deliberate-stale",
    ("reunion-thread", "reunion-thread/slack-work", "now", "time",
     "12pm"): "different-event",
    ("reunion-thread", "reunion-thread/sl-f14", "now", "time",
     "noon"): "ambient-coincidence",
    ("reunion-thread", "reunion-thread/slack-work", "now", "place",
     "office roof"): "different-event",
    ("reunion-thread", "reunion-thread/hermes-ack", "original", "day",
     "Sunday"): "trail-superseded",
    ("reunion-thread", "reunion-thread/wa-move", "original", "day",
     "Sunday"): "trail-superseded",
    ("reunion-thread", "reunion-thread/email-confirm", "original", "day",
     "Sunday"): "trail-superseded",
    ("reunion-thread", "reunion-thread/wa-deny", "original", "day",
     "Sunday"): "trail-superseded",
    ("reunion-thread", "reunion-thread/wa-hay-shower", "original", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-hike2", "original", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-shower2", "original", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/hermes-ack", "canceled",
     "canceled", "yes"): "scan-limitation-yesno",
    ("reunion-thread", "reunion-thread/email-confirm", "canceled",
     "canceled", "yes"): "scan-limitation-yesno",
    ("reunion-thread", "reunion-thread/wa-hay-game2", "canceled",
     "canceled", "yes"): "scan-limitation-yesno",
    ("reunion-thread", "reunion-thread/wa-hay-brunch2", "canceled",
     "canceled", "yes"): "scan-limitation-yesno",
    ("reunion-thread", "reunion-thread/em-f02", "canceled",
     "canceled", "yes"): "scan-limitation-yesno",
    ("reunion-thread", "reunion-thread/email-orig", "work-now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/hermes-ack", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-move", "work-now", "day",
     "Saturday|Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/email-confirm", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-deny", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-stale", "work-now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-shower", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-brunch", "work-now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-brunch2", "work-now", "day",
     "Saturday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-hike2", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/wa-hay-shower2", "work-now", "day",
     "Sunday"): "different-event",
    ("reunion-thread", "reunion-thread/hermes-ack", "work-now", "time",
     "1pm"): "different-event",
    ("reunion-thread", "reunion-thread/wa-move", "work-now", "time",
     "1pm"): "different-event",
    ("reunion-thread", "reunion-thread/email-confirm", "work-now", "time",
     "1pm"): "different-event",
    ("reunion-thread", "reunion-thread/wa-deny", "work-now", "time",
     "1pm"): "different-event",
    ("revoke-week", "revoke-week/email-booked", "dinner-now", "day",
     "Friday"): "trail-superseded",
    ("revoke-week", "revoke-week/hermes-cancel", "dinner-now", "day",
     "Friday"): "trail-superseded",
    ("revoke-week", "revoke-week/email-cancelconf", "dinner-now", "day",
     "Friday"): "trail-superseded",
    ("revoke-week", "revoke-week/wa-drinks", "dinner-now", "day",
     "Friday"): "different-event",
    ("revoke-week", "revoke-week/wa-drinks2", "dinner-now", "day",
     "Friday"): "different-event",
    ("revoke-week", "revoke-week/email-booked", "dinner-count", "count",
     "two"): "trail-superseded",
    ("revoke-week", "revoke-week/sms-gate", "dinner-count", "count",
     "2"): "scan-limitation-substring",
    ("revoke-week", "revoke-week/em-f01", "dinner-count", "count",
     "two"): "ambient-coincidence",
    ("revoke-week", "revoke-week/wa-f05", "dinner-canceled", "canceled",
     "no"): "ambient-coincidence",
    # race-week (Life F): deliberate update trails + different-event
    # weekday mentions + entity-blind YESNO polarity on the allergy note.
    ("race-week", "race-week/h-run-sat", "long-run", "day",
     "Saturday"): "trail-superseded",
    ("race-week", "race-week/b-party0", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-hw-invite", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-gym-permit", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-dinner0", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-ven-permit-ok", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-venue-ok2", "long-run", "day",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-party0", "party-venue", "venue",
     "community hall"): "trail-superseded",
    ("race-week", "race-week/b-rb-early", "car-pickup", "day",
     "Thursday"): "different-event",
    ("race-week", "race-week/b-car0", "car-pickup", "day",
     "Thursday"): "trail-superseded",
    ("race-week", "race-week/b-ven-permit-ok", "car-pickup", "day",
     "Thursday"): "different-event",
    ("race-week", "race-week/b-allergy0", "race-on", "on",
     "no"): "scan-limitation-yesno",
    ("race-week", "race-week/b-allergy0", "earlybird", "open",
     "no"): "scan-limitation-yesno",
    ("race-week", "race-week/b-allergy0", "rae-free", "free",
     "no"): "scan-limitation-yesno",
    ("race-week", "race-week/h-run-sat", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-party0", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-washout", "afterparty", "brunch",
     "Saturday|Sunday"): "different-event",
    ("race-week", "race-week/b-maya-confirm", "afterparty", "brunch",
     "Sunday"): "different-event",
    ("race-week", "race-week/b-hw-invite", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-shoes", "afterparty", "brunch",
     "Sunday"): "different-event",
    ("race-week", "race-week/b-sister-ankle", "afterparty", "brunch",
     "Sunday"): "different-event",
    ("race-week", "race-week/b-gym-permit", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-coach-on", "afterparty", "brunch",
     "Sunday"): "different-event",
    ("race-week", "race-week/b-dinner0", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-ven-permit-ok", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-run-sun2", "afterparty", "brunch",
     "Sunday"): "different-event",
    ("race-week", "race-week/b-venue-ok2", "afterparty", "brunch",
     "Saturday"): "different-event",
    ("race-week", "race-week/b-raceday", "afterparty", "brunch",
     "Sunday"): "different-event",
}


def undispositioned(lives: list[Life]) -> list[Candidate]:
    """Scan hits with no human disposition — the suite must see none."""
    out: list[Candidate] = []
    for life in lives:
        for cand in scan_contradictions(life):
            if cand.key() not in KNOWN:
                out.append(cand)
    return out
