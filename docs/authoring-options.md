# Writing a spec

Keep one JSON per scenario in `specs/`; its duration can be a day, a month or longer. [Pizza Week](../specs/pizza_week.json) shows the format: `week_start`, reusable `channels`, and seven `days` sections, from `monday` through `sunday`. Empty days are `{}`. `python -m pacbench.specs` checks the whole directory for structural errors without generating text, creating artifacts or calling a model. Preparation still requires independent semantic checks.

Define each chat once under `channels`, with its source `type`, `participants` and optional group `name`. A scene then needs only `id`, `channel`, a human-readable `time`, a short `scene` description (or exact `messages`, below) and inline English `facts`. Its channel key also identifies its thread. The same contact name resolves to the same synthetic source identity across DMs and groups; silent group members remain members.

Optionally define recurring people before channels in `cast`, mapping each participant name to a concise description. This private context goes to the writer and checker for scenes involving that person; it is not source evidence or provider metadata. All facts in a scene are checked together in one independent review.

Scene style:

- Use `cast` for stable character context.
- Describe what happens in one short scene sentence.
- Let channel and time metadata establish the thread and sequence.
- Put one checkable outcome or constraint in each fact, including date scope and continuity; avoid narrative instructions.

## Supply exact messages for a simple scene

Use `messages` to provide the source directly instead of asking the writer to
compose it. Generated and supplied scenes can coexist in the same spec and
thread. For example, this scene fragment belongs to a week pinned to
`2026-10-05`, with a `sam` channel containing `Me` and `Sam`:

```json
{
  "id": "pizza-proposal",
  "channel": "sam",
  "time": "6 pm",
  "messages": [
    {"sender": "Sam", "text": "Pizza Friday at six?", "at": "2026-10-05T18:00:02-04:00"}
  ],
  "facts": ["Sam proposes pizza Friday at 6 pm."]
}
```

For a short exchange, add message objects in order:

```json
"messages": [
  {"sender": "Sam", "text": "Pizza Friday at six?", "at": "2026-10-05T18:00:02-04:00"},
  {"sender": "Me", "text": "Yes, that works.", "at": "2026-10-05T18:00:17-04:00"},
  {"sender": "Sam", "text": "Great!", "at": "2026-10-05T18:03:04-04:00"}
]
```

Each object accepts exactly `sender`, `text` and `at`. The source preserves their
values, including body whitespace, newlines and Unicode; it never invents an
owner reply or spreads supplied timestamps. The channel and membership come
from the normal channel definition. A single incoming or outgoing message is
valid, even when other channel members are silent. A `scene` description is
optional for supplied source and, if provided, remains private author context.
Keep the usual `facts` list accurate for the actual text; a proposal is not an
accepted plan, and ungraded chatter can use `facts: []`.

Timestamps must include UTC offsets matching the spec's time zone, be strictly
increasing and lie inside the scene window (`time` through `until`, or the
default 20 minutes). Empty messages, unknown senders, undeclared object fields,
duplicate message bodies and conflicting clocks fail offline validation. For
a next-day reply, add another scene in that day's section on the same channel,
with its own exact timestamp; prior thread messages remain available as context.
Absolute message timestamps do not shift with `--week-start`: update them when
changing the simulated calendar, or validation will reject the mismatch.

Supplied source ignores global noise quotas and cannot declare `message_range`
or `messages_per_person`. Only generated scenes share automatic chat-volume
targets. This bypasses the writer alone: the independent scene checker still
reviews all facts together against the exact source and preceding context,
including each supplied message's timestamp. A rejection preserves its receipt
and blocks that thread; the writer never rewrites the source, and a failed
cached recheck is not rerolled for approval. Edit the spec and resume when needed.

The normal contract review, corpus review, hash-bound freeze audit and provider
ingestion paths apply. Saved provenance marks the scene as `supplied`, with zero
writer attempts and a required checker receipt. Body or timestamp edits invalidate
scene reuse. Providers receive only normal public source records with opaque
IDs, never `facts`, scene descriptions, writer provenance or the answer key.
Offline validation needs no model; preparing supplied scenes still requires
the normal verification and review calls.

## Conversation context and generation

Email channels use `type: "email"` and a `subject`. Each generated entry becomes an RFC 5322 email body with From/To, Date, Message-ID and reply references. Bodies preserve paragraphs and quoted reply history as part of the actual sender's message. Legacy low/medium/high email exchanges stay at 2–6 messages; more email volume comes from authored threads rather than text-chat quotas. Messaging and email addresses are distinct accounts on the same separate Contacts card. Memcal consumes Inbox/Sent through its native secure IMAP reader against a synthetic local mailbox; this is a protocol fixture, not a running Proton Bridge. No native readers or archive writes are substituted. The checker sees preceding source context across channels; the writer sees its own preceding thread and authored requirements.

Every scene checker sees preceding cross-channel source context. Its cache binds the actual checked transcript and context, not just the scene description. A changed checker/context can reverify cached text without rewriting it; only a rejected realization needs a new draft. The private scene cache preserves unvisited renders through interrupted revisions. Frozen-source audits compare message bodies and metadata with the saved passing checker input and model receipt.

```json
{
  "week_start": "2026-09-07",
  "cast": {"Devon": "The user's friend from work."},
  "channels": {
    "devon": {"type": "imessage", "participants": ["Me", "Devon"]}
  },
  "days": {
    "monday": {
      "scenes": [
        {"id": "invite", "channel": "devon", "time": "7:30 am",
         "scene": "A conversation about getting pizza.",
         "facts": ["They agree to get pizza on Friday.",
                   "They agree on Domino's as the venue.",
                   "Maria is coming too."]}
      ]
    }
  }
}
```

This fragment belongs in a complete seven-day week. Facts stay next to the scene; there is no top-level fact catalog or fact-ID matching. Assistant turns use an English `expect` description or a list of inline English `facts` for recall; see [conversations](flexible-checks.md).

For an update scene, describe what changes. Use “same time” or “venue unchanged” when people rely on prior context; listing the old restaurant as a new scene fact encourages an unnatural recap. Inspect generated dialogue for added reminders that would weaken the intended test. Expectations should judge facts the question naturally invites, rather than requiring unrelated details in a yes/no answer.

Omitting `noise` (or explicitly setting `noise: "authored"`) lets the scene descriptions control topics and length, without automatic message quotas, per-person targets or generic noise instructions. New specs should omit this field and describe chatter in each scene. Pizza Day remains the unchanged explicit low-noise smoke test. Explicit scene `message_range` bounds, when supplied, still apply. Previously, omission selected `low`. Existing frozen manifests retain their resolved policy and are not rewritten. When resuming an older legacy preparation, specify its old `noise` mode explicitly; omission is rejected before any files change. To deliberately migrate it, specify `noise: "authored"`; changed generation fingerprints require new scene generation and verification. An unchanged authored preparation resumes with the same fingerprints whether the field is omitted or explicit.

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
Human writing review, fresh scene verification and corpus-wide review remain
pending until actual generation. Use fresh preparation for changed scenes and
review bindings; do not relabel the earlier frozen corpus or its scores.

For multiple weeks, replace `days` with a `weeks` array of complete Monday–Sunday maps. Channels remain shared, so an ongoing chat keeps its identity across the month. Scene IDs gain a week prefix when compiled; assistant turn and session IDs stay as authored. [Pizza Month](../specs/pizza_month.json) covers 30 consecutive source days, then asks upcoming-plan questions. `ingestion_days: 30` verifies that exact daily coverage and prevents interim questions.

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
Supplied chatter without either is checked for coherence, identities, chronology
and relevant continuity; an empty facts array does not automatically approve it.
This version replaces v10's split output instructions, which sometimes elicited
only per-fact judgments. It does not permit automatic format retries or rerolls.
Changed checker prompts require new bound checks, reusing the exact source text;
prior failed and ungraded receipts remain separate evidence.
