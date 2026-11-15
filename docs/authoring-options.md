# Writing a spec

Keep one JSON per scenario in `specs/`. Pin `week_start` and `timezone`, then use
`days` (seven weekday sections) or `weeks` (a list of those sections). Empty days
are `{}`. `python -m pacbench.specs` compiles and validates offline: it does not
write scenes, call a model or run providers. The revised
[Pizza Week assistant draft](../specs/pizza_week_assistant.json) is for review,
not a generated or verified corpus.

## Threads and participants

Give every scene a stable `thread` key. The first scene creates that thread;
later scenes with that key continue it. The default source `type` is `imessage`.
For email, set `type: "email"` and `subject` on its first scene. Use different
keys for separate groups or email subjects, even if the people are identical.
Source type, name and subject cannot change within a thread.

Exact scenes infer each new speaker's membership **at that message**, not at the
start of the scene. Optional scene-local `participants` establish known silent
members at the scene start. Generated scenes need local `participants` on the
first scene: the writer never establishes membership or invents speakers. Later
generated scenes can omit the list to retain the existing roster, or list new
members to add them at that scene's start. Membership persists; omission never
removes a person. No explicit join event is required. `Me` is the owner label.
An outgoing-only first scene needs explicit recipients: unknown future speakers
cannot supply recipients retroactively. Membership is separate from an event's
attendance/RSVP roster.

Public source records carry the roster available at **each message**. Stable
opaque thread IDs do not depend on roster, and adding a speaker does not mutate
older observations. The document transport emits a new roster header when it
changes, under the same conversation ID. Email From/To headers use that message's
roster and keep reply references on the same thread.

**Apple Messages limitation:** the current synthetic reader schema has a single
`chat_handle_join` roster with no historical membership clock. Updating it would
make old messages appear to have future members. Its fixture therefore rejects
membership changes, both within a batch and across appends; it neither backfills
the old chat nor creates another chat. Shared native source preparation currently
builds this fixture, so a changing iMessage group cannot yet pass that preparation
path, including Memcal's native Messages reader. Offline public records/document
exports demonstrate the join faithfully; they do not establish Apple fixture or
provider coverage. Earlier completed snapshot files remain unchanged. There is
no silent fixture fallback.

Legacy top-level `channels` with scene `channel` references remain supported as
fixed rosters. Do not mix the legacy and scene-local formats for the same thread.
Optional `cast` is private backstory, never membership authority; every cast name
must independently occur in source membership. New examples need no preamble.

## Exact messages and readable timing

An exact scene normally needs only `sender` and `text` per message. The first
message uses the scene start; each subsequent omitted clock adds one elapsed
minute to the previous message. An optional `time`, such as `"6:03 pm"` or
`"6:03:07 pm"`, overrides that message on the scene's local calendar day.
Legacy offset-aware ISO `at` is also accepted and preserved; do not supply both.
Use an override only when the gap or exact clock matters to the story or a test.
An override resets the base for the next default. Text, whitespace, Unicode and
speaker identity are preserved. No owner reply is invented.

Clocks must be strictly increasing and inside the scene window (`time` through
`until`, or 20 minutes). Relative defaults advance correctly over midnight and
DST. Readable times on the scene's day cannot denote the next day: use another
scene or a legacy ISO override for that explicit clock. Nonexistent or ambiguous
local overrides fail; use a valid offset-aware ISO instant for a repeated DST
hour. Offsets must agree with the specified IANA timezone. Date-specific ISO
values do not move with `--week-start`. Unknown fields, empty text, duplicate
bodies, invalid speakers and generated quotas on exact scenes fail offline.

**Omit scene `facts` for new exact source.** It bypasses both writer and scene
checker. Receipts say `author-source-v1`, `state: "unjudged"`, and record a
hash-bound deterministic integrity check—not a fabricated semantic pass.
Prepare, imported-dialogue validation, cache/reuse and freeze audits enforce
exact bodies, timestamps, speakers and public metadata. A forged model-verdict
or checker receipt is rejected. Question/answer grading and independent
source-supported question/criterion audits remain separate and still apply.
Corpus review `source-realism-v11` uses all actual source for answerability while
exempting author-owned source from writing/naturalness/scene-fact judging.
It does not endorse an authored expectation merely because its source was exact.

For compatibility, explicitly supplied legacy scene `facts` (including `[]`)
retain the original model-checking contract. Old compiled scenes, checker
receipts and frozen artifacts keep their protocol. Remove that field only when
authoring a **new** intentionally unjudged scene; nonempty facts cannot accompany
`source_review: "author-source-v1"`. Historical facts are never silently discarded. Resuming a legacy reviewed scene
as unjudged source is refused before files change; use a fresh preparation for
that deliberate change.

## Generated scenes and a mixed example

Say what people are doing or discussing in that moment. A simple scene can be
`"Maya and I discuss lunch plans."` Put factual outcomes and constraints in
`facts`, not writer directions or a checkpoint recap. The writer sees prior
messages in this thread; the checker sees preceding cross-thread source context.
Generated source retains normal independent scene review, bound to actual text,
facts and context. Neither generated source nor exact source reaches providers
with private author directions, facts or answer keys.

This complete minimal example mixes exact and generated source, infers Sam's
join on the second Tuesday message, and retains him silently on Wednesday. It
compiles offline. Because of the Apple fixture limitation above, it is **not** a
claim of native Messages transport support.

```json
{
  "id": "small-mixed-example",
  "week_start": "2026-10-05",
  "timezone": "America/New_York",
  "days": {
    "monday": {
      "scenes": [{"id": "invite", "thread": "lunch", "time": "6 pm",
        "messages": [{"sender": "Maya", "text": "Lunch Friday?"}]}]
    },
    "tuesday": {
      "scenes": [{"id": "new-speaker", "thread": "lunch", "time": "noon",
        "messages": [{"sender": "Maya", "text": "The café has new chairs."},
                     {"sender": "Sam", "text": "Hi both!"}]}]
    },
    "wednesday": {
      "scenes": [{"id": "plans", "thread": "lunch", "time": "6 pm",
        "scene": "Maya and I discuss lunch plans.",
        "facts": ["Maya and the user agree to lunch Friday at noon at Rosa's."]}]
    },
    "thursday": {"conversations": [{"id": "recall", "time": "9 am",
      "turns": [{"id": "lunch-plan", "prompt": "What lunch plans do I have?",
        "expect": "Report the agreed Friday lunch at noon at Rosa's."}]}]},
    "friday": {}, "saturday": {}, "sunday": {}
  }
}
```

For a generated first scene, add `participants: ["Me", "Maya"]`; for a new email
thread also add `type: "email", subject: "Lunch"`. Silent members may remain
silent; participants do not imply attendance. Cast is optional backstory and is
omitted here.

For an update scene, describe what changes. Use “same time” or “venue unchanged” when people rely on prior context; listing the old restaurant as a new scene fact encourages an unnatural recap. Inspect generated dialogue for added reminders that would weaken the intended test. Expectations should judge facts the question naturally invites, rather than requiring unrelated details in a yes/no answer.

Omitting `noise` (or explicitly setting `noise: "authored"`) lets the scene descriptions control topics and length, without automatic message quotas, per-person targets or generic noise instructions. For new specs, leave out `noise` and numeric message-count ranges. If length matters, describe it in the scene, such as “a quick check-in” or “a long, rambling group chat.” The writer chooses the count. Pizza Day remains the unchanged explicit low-noise smoke test. Legacy specs with explicit `message_range` bounds remain supported for reproducibility. Previously, omission selected `low`. Existing frozen manifests retain their resolved policy and are not rewritten. When resuming an older legacy preparation, specify its old `noise` mode explicitly; omission is rejected before any files change. To deliberately migrate it, specify `noise: "authored"`; changed generation fingerprints require new scene generation and verification. An unchanged authored preparation resumes with the same fingerprints whether the field is omitted or explicit.

For compatibility, `noise` also accepts `low`, `medium` or `high`. Low is the shortest natural exchange (normally 2–12 messages). Medium targets 20–40 messages per person per conversation per day. High targets 600–800 per person, including unrelated topics, facts and side plans. Multiple scenes in the same chat/day share that target. Each member must meet the minimum; the total exchange stays bounded, while individual speakers may exceed their target share. Larger exchanges use bounded chunks; messages are never padded, copied or truncated to meet a count. Prefer scene-authored chatter for new specs; explicit legacy modes remain available for reproducibility. An optional `until` sets the scene's ending wall time; high otherwise continues through the rest of its day, respecting the next scene in that chat. Authored start times remain unchanged. Within each window, deterministic uneven gaps provide synthetic pacing; they do not claim to reproduce an empirically measured distribution.

## Pizza Week topic experiment

Pizza Week adds office-party and movie discussion around Maya's invitation,
video-game discussion around Devon's invitation, and a longer movie/game debate
in the existing Saturday chatter scene. Its dates, facts and questions stay the
same. Topics and desired pacing live in each `scene`; no global verbosity target
controls this experiment. Maya's invitation explicitly orders office-party chatter,
the pizza agreement in the middle, then movie discussion, ending on movies.
Fact position is an authored condition: pizza appearing first in a pizza-first
scene is instruction-following, not a generation failure. Other scenes can
remain brief when their context calls for it. The party has no invented schedule or attendance commitments; games and
movies are discussion, not extra outings.

The following is an **illustrative, hand-authored draft** of Tuesday's Maya scene
for Chris to review. It is not output from the configured writer, a verified
corpus, or evidence that generation succeeds:

```text
Maya: the office party decorations are apparently going to involve a balloon arch
Me: apparently?
Maya: someone sent a mockup. it has its own lighting
Me: the arch has a bigger budget than the snacks
Maya: honestly that would explain a lot
Maya: anyway want to get pizza Friday? 1 at Rosa's on Oak Street?
Me: yeah that works
Me: please tell me the arch isn't company colours
Maya: you already know the answer
Me: tragic
Maya: also why do movie trailers show the entire ending now
Me: because nobody trusts us to watch a film without knowing every single thing
Maya: I watched one where the big reveal was literally in the thumbnail
Me: sometimes I like knowing the vibe first though
Maya: vibe yes. murderer no
Me: fair distinction
```

Before running providers on a newly generated Pizza Week, review the actual
writer output and record findings with scene IDs and quoted source evidence:

- **Writing quality / naturalness:** Chris reviews dialogue flow, distinct voices,
  plausible pacing, and whether topic changes feel forced or padded. Compare the
  illustrative draft above with the actual writer output; the draft is not a pass.
- **Consequential facts:** inspect extra dates, invitations, attendance promises,
  availability constraints, cancellations and venue changes, even when omitted
  from `facts`. Ungraded text can still change a correct answer.
- **Cross-scene conflicts:** read the complete week across DMs and groups; check
  new chatter against every existing plan and later reference. A per-scene check
  alone is insufficient.
- **Coverage / judging:** confirm the existing facts remain supported and the
  existing questions have the same answers; don't grade incidental movie/game
  opinions or mistake a changed source fact for harmless chatter.
- **Volume experiment:** record actual message and word counts per scene. With
  no quota, longer output is not guaranteed. Compare retention only after source
  review; verbosity by itself is not proof of a harder or better test.

Offline compilation and prompt tests establish wiring and compatibility only.
Human writing review, fresh generated-scene verification and independent question/corpus review remain
pending until actual preparation; author-owned exact scenes remain explicitly unjudged. Use fresh preparation for changed scenes and
review bindings; do not relabel the earlier frozen corpus or its scores.

For multiple weeks, replace `days` with a `weeks` array of complete Monday–Sunday maps. Thread keys remain shared, so an ongoing chat keeps its identity across the month; legacy channel definitions retain fixed rosters. Scene IDs gain a week prefix when compiled; assistant turn and session IDs stay as authored. [Pizza Month](../specs/pizza_month.json) covers 30 consecutive source days, then asks upcoming-plan questions. `ingestion_days: 30` verifies that exact daily coverage and prevents interim questions.

Concrete dates are assigned during preparation from the spec's `week_start` Monday. `--week-start` explicitly overrides it; update any prose containing concrete dates when moving a scenario. Legacy unpinned inputs can still prepare relative to the host week, but the inventory requires a fixed date. Eastern defaults to `America/New_York`, including daylight saving time. Dates and offsets are frozen in the compiled corpus.

```sh
python -m pacbench.specs
python -m pacbench.prepare specs/pizza_week.json --output artifacts/corpora/pizza-week
python -m pacbench.preparation_audit artifacts/corpora/pizza-week
python -m pacbench.native_pilot --prepared artifacts/corpora/pizza-week/week.json --providers memcal --output runs/pizza-current
```

Preparation writes a small plain-JSON `week.json` manifest with hashes of the generated messages, verification receipts, questions and evidence-cited judging contracts beside it. Keep the prepared directory together to reuse it across providers and runs; the manifest references these files rather than embedding a second copy. Provider execution checks every referenced file before importing it, without authoring or verifying messages again. Older self-contained JSON bundles remain readable. `--resume` reuses unchanged verified scenes and accepted contracts when fixing an interrupted preparation. Changed sources, questions, expectations or clocks require fresh contract review; a changed scene requires fresh source verification. Failed scenes are recorded, dependent source continuations skip, and independent scenes continue. Incomplete preparation cannot become a runnable frozen corpus.

The minimal [Pizza Day](../specs/pizza_day.json) uses the same format for a one-day source smoke test. Preparation always runs separately from Hermes and native provider ingestion. All paid work goes through the shared gateway.

### Scene review output

`scene-judge-v11` uses one complete response schema: independent whole-scene
`supported`, `evidence`, and `problem`, plus an ordered `facts` array (empty when
no facts are authored). Individual fact approvals never imply global approval.
All Boolean, index, citation and required-field types are validated before the
semantic verdict. Missing fields remain ungraded; explicit negative judgments
remain rejected, including negatives inside malformed responses.

The checker requires commitments only where the authored scenario or facts do.
Legacy supplied chatter with an explicit facts field keeps that checker contract; an empty facts array does not automatically approve it. New author-owned exact scenes omit facts and bypass this scene checker, as described above.
This version replaces v10's split output instructions, which sometimes elicited
only per-fact judgments. It does not permit automatic format retries or rerolls.
Changed checker prompts require new bound checks, reusing the exact source text;
prior failed and ungraded receipts remain separate evidence.
# Source review and uncertainty

New preparations use `source-realism-v10` for the independent source-blind corpus
review. It distinguishes determinate answers, grounded unknown attributes,
references needing clarification, and invalid questions. Mixed questions retain
their known attributes. An invitation, booking, promise or old quotation does
not establish an accepted invitation, attendance, completed task or current
preference.

Grounded uncertainty must be responsive to the public question and justified
against the complete eligible history. The request and receipt bind each
checkpoint's evidence set; citations preserve source identities, exact text,
written and collection times, and ordered conditional public inputs. A question's
presupposition is not a factual report. Source coherence, naturalness, and the
separate independent verification of every frozen criterion remain required.
Unresolved contradictory current source cannot pass as mere uncertainty.

Historical v9 reviews keep their original standard and verdicts. A prospective
v10 review does not turn a v9 rejection into a pass; retain the predecessor and
the changed review condition. Qualify the condition with positive and negative
controls before live use.
