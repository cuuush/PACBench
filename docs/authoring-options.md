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
members at the scene start. Generated first scenes need outside speaker IDs
in local `participants` or `joins` before writing: the writer never establishes
membership or invents speakers. Later
generated scenes can omit the list to retain the existing roster, or list new
members to add them at that scene's start. Membership persists; omission never
removes a person. Alternatively, `joins: ["Nora"]` explicitly adds a member
at the start of a scene, even if silent, without repeating the roster. This is
additions only: names must be distinct, nonempty new contacts, excluding Me and
existing members. Use scene-local threads for joins; legacy channels stay fixed.
No explicit join event is required. `Me` is the owner label.
An outgoing-only first scene needs explicit recipients: unknown future speakers
cannot supply recipients retroactively. Membership is separate from an event's
attendance/RSVP roster.

Public source records carry the roster available at **each message**. Stable
opaque thread IDs do not depend on roster, and adding a speaker does not mutate
older observations. The document transport emits a new roster header when it
changes, under the same conversation ID. Email From/To headers use that message's
roster and keep reply references on the same thread.

**Apple Messages default limitation:** the legacy synthetic reader fixture has a single
`chat_handle_join` roster with no historical membership clock. Updating it would
make old messages appear to have future members. Its fixture therefore rejects
membership changes, both within a batch and across appends; it neither backfills
the old chat nor creates another chat. Shared native source preparation currently
builds this fixture, so a changing iMessage group cannot yet pass that preparation
path, including Memcal's native Messages reader. Offline public records/document
exports demonstrate the join faithfully; they do not establish Apple fixture or
provider coverage. Earlier completed snapshot files remain unchanged. There is
no silent fixture fallback.

An explicitly selected `PACBENCH_IMESSAGE_MEMBERSHIP_CONDITION=apple-messages-additive-v1`
builds checkpoint-local rosters and dated, bodyless participant-added rows for
additions to an existing group. It keeps the same chat identifier and never
rewrites earlier snapshot files. The notice precedes the first affected text by
row order at its boundary clock; the source does not identify an adder, so the
notice has an unspecified actor, not an invented speaker. `other_handle` names
the new member and differs from the actor, avoiding the native phone-number-change
encoding. The independent reader mapping is pinned to imessage-exporter
`0d444ab3220f5940d4632c05da4628009c785a02`; the existing target DDL is unchanged.
Removals, reordered/stale rosters and direct-to-group conversion fail closed.
This is synthetic schema/reader conformance, not an Apple-created database or a
claim that MemCal learns silent membership: the pinned reader skips bodyless
notices. A later snapshot's global roster must not be treated as historical
per-message membership; use the earlier snapshot and dated notices. An
interrupted multi-file install fails its bound artifact hashes and requires the
previous verified source checkpoint rather than blind replay into later state.

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

Use `test_scene_id` to select a scene explicitly, including exact source with
independent assistant checks. Use the compiled ID, such as `w1-invite` for a
scene in a `weeks` array. Unknown IDs fail validation. Assistant checks still
define their own expectations.

For compatibility, explicitly supplied legacy scene `facts` (including `[]`)
retain the original model-checking contract. Old compiled scenes, checker
receipts and frozen artifacts keep their protocol. Remove that field only when
authoring a **new** intentionally unjudged scene; nonempty facts cannot accompany
`source_review: "author-source-v1"`. Historical facts are never silently discarded. Resuming a legacy reviewed scene
as unjudged source is refused before files change; use a fresh preparation for
that deliberate change.

## Generated scenes and a mixed example

Describe the conversation in third person: who is talking, what they are
discussing, and the tone or format if it matters. For example,
`"Two friends decide whether to get pizza."` is enough. Avoid first-person
summaries such as “Devon and I get pizza.” Put the outcome, date, and other
required details in `facts`, not in a recap of what happens outside the chat. The writer sees prior
messages in this thread; the checker sees preceding cross-thread source context.
Generated source retains normal independent scene review, bound to actual text,
facts and context. Neither generated source nor exact source reaches providers
with private author directions, facts or answer keys.

When a scene depends on another thread, give the writer only the previously
established context it needs in the scene description. Show how a participant
who missed the earlier exchange learns about it. Check that relay against the
actual earlier messages and keep scene facts focused on what this exchange
establishes.

This complete minimal example mixes exact and generated source. Three existing
iMessage members (Me, Maya and Leo) gain a fourth, Sam, at Tuesday’s scene start.
Sam can remain silent; his membership persists on Wednesday. An exact sender
not declared by participants or joins still joins only at their first message. It
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
        "participants": ["Me", "Maya", "Leo"],
        "messages": [{"sender": "Maya", "text": "Lunch Friday?"}]}]
    },
    "tuesday": {
      "scenes": [{"id": "new-speaker", "thread": "lunch", "time": "noon", "joins": ["Sam"],
        "messages": [{"sender": "Maya", "text": "The café has new chairs."},
                     {"sender": "Sam", "text": "Hi both!"}]}]
    },
    "wednesday": {
      "scenes": [{"id": "plans", "thread": "lunch", "time": "6 pm",
        "scene": "Two friends discuss lunch plans.",
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
python -m pacbench generate specs/pizza_week.json --output artifacts/corpora/pizza-week
python -m pacbench run artifacts/corpora/pizza-week --output runs/pizza-current
```

Preparation writes a small plain-JSON `week.json` manifest with hashes of the generated messages, verification receipts, questions and evidence-cited judging contracts beside it. Keep the prepared directory together to reuse it across providers and runs. `run` accepts the directory or its `week.json`, verifies every referenced file, and imports the corpus without new authoring or model reviews. Older self-contained manifests remain readable. `generate --resume` reuses unchanged verified scenes and accepted contracts when fixing an interrupted preparation. Changed sources, questions, expectations or clocks require fresh contract review. A changed scene requires fresh source verification. Failed scenes are recorded, dependent source continuations skip, and independent scenes continue. Incomplete preparation cannot become a runnable frozen corpus.

`run` uses the full frozen corpus and selects all nine active providers by default. `--providers memcal mem0` selects a subset. `--plan` checks the corpus and writes its full source schedule and frozen question and criterion counts without model calls or provider startup. Conditional follow-ups are included in the frozen counts and can depend on what the provider answers during a run. Native execution requires the [runtime and model route setup](../vendor/README). Its summary reports unavailable arms and retains outcomes separately from semantic grades and retention evidence.

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

## Visible names across platforms

The compiler does not link different visible names across platforms. For example,
Alex on text and Alice in an email remain separate source identities even if
Alice writes “Alex is my nickname.” That sentence is observable evidence for a
memory system to interpret, not a hidden compiler identity mapping. No global
alias or identity-link field is introduced. Legacy identical labels still use
the same synthetic contact identity across messaging and email; different
account addresses can belong to that same-label contact.

## Source-only render

`python -m pacbench.render_sources --output runs/<fresh-render>` renders only
this reviewed assistant spec, with ordinary generated-scene checks. It compiles
exact author source without writer/checker calls and writes a chronological
`transcript.txt`, logical messages, scene receipts and status. It never calls
corpus/question judging, the native Apple fixture, or providers, and does not
create a native-ready `week.json`. A failed scene stops dependent continuation.

This entry point requires explicit `PACBENCH_EXECUTION_SCOPE=source-render-v1`,
`PACBENCH_OPENROUTER_PROFILE=luna-bedrock-global`, and an existing active
`PACBENCH_FUNDED_RUN_ID` on the original authorized ledger. It admits only
`pacbench-authoring` and `pacbench-scene-verifier`, including under split routing;
scored, provider and unknown projects are denied. Existing import/scored scopes
remain restricted, and unknown ledger/execution scopes fail closed. Each call
still reserves and settles against that same prepaid guard with fresh credit
checks and recorded Auto Recharge safety. Output ceilings and reservation policy
must be explicit and retained in the private usage receipt; truncation fails.
No top-up, replacement ledger or account setting changes are supported.

Revised document-only trials may explicitly set
`PACBENCH_LOGICAL_DOCUMENTS_ONLY=1` for non-Memcal arms. Their daily public text
and JSON preserve each message's actual roster; manifests record
`logical-document-metadata-v1`. This omits unused native source fixtures, not
messages or participants. Memcal retains its native fixture gate and cannot use
this condition to claim changing-membership coverage.

`PACBENCH_STRICT_MEMORY_ONLY=1` records `strict-memory-only-v1` decisions per
question before judge dispatch. Actual source reads and mixed raw conversation
injection are ungraded; ambiguous provenance is also ungraded. The original
planned denominator remains in the report. Saved decisions are independently
recomputed by the audit, including inherited session context, and source-assisted
answers never receive a fabricated grade. These are distinct experimental
conditions; historical semantic scores are unchanged.

A broad assistant query may specify both `expect` and `facts`: `expect` defines
its scope (including optional or tentative details), while `facts` supplies the
atomic required criteria. Neither substitutes for the independent source and
criterion reviews. Frozen legacy contracts retain their original protocol.

`source-realism-v12-checkpoint-isolated` independently reviews each question
against its complete eligible source and public-input history. Every receipt
is bound to the exact checkpoint, request and model output; a failed receipt
cannot be resumed for another approval. This avoids mixing source identities
across a large multi-question review. Earlier failed whole-corpus receipts stay
in their original preparation directory. This protocol changes preparation
review granularity, not benchmark source, questions or grading criteria.

The separately labeled `source-realism-v13-checkpoint-locator-bound` protocol
can retain a positive semantic output with an invalid source locator only via
`missing-final-source-id-character-v1`. It requires an absent ID with exactly
its final character omitted and a unique eligible source matching the exact
quote and written time. Existing wrong IDs, ambiguous matches, changed quotes,
public/source category changes and negative semantic verdicts fail closed.
The original failed receipt and raw model response remain unchanged; an overlay
records both hashes, the canonical source identity and eligible-history hash.
Audit recomputes the binding and validates the derived view independently.
