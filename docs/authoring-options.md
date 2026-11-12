# Writing a spec

Keep one JSON per scenario in `specs/`; its duration can be a day, a month or longer. [Pizza Day](../specs/pizza_day.json) shows `week_start`, reusable `channels`, and seven `days` sections, from `monday` through `sunday`. [Challenging Pizza Week](../specs/pizza_week_challenging.json) uses `weeks` to place its final questions after seven full source days. Empty days are `{}`. `python -m pacbench.specs` checks the whole directory for structural errors without generating text, creating artifacts or calling a model. Preparation still requires independent semantic checks.

Define each chat once under `channels`, with its source `type`, `participants` and optional group `name`. A scene then needs only `id`, `channel`, a human-readable `time`, a short `scene` description and inline English `facts`. Its channel key also identifies its thread. The same contact name resolves to the same synthetic source identity across DMs and groups; silent group members remain members.

Optionally define recurring people before channels in `cast`, mapping each participant name to a concise description. This private context goes to the writer and checker for scenes involving that person; it is not source evidence or provider metadata. All facts in a scene are checked together in one independent review.

Scene style:

- Use `cast` for stable character context.
- Describe what happens in one short scene sentence.
- Let channel and time metadata establish the thread and sequence.
- Put one checkable outcome or constraint in each fact, including date scope and continuity; avoid narrative instructions.

## Write for the context the writer actually receives

Each scene is a separate writing request, but it is not entirely isolated: the
writer receives earlier generated messages from **that same thread**, the
current scene and its facts, relevant cast descriptions, and channel/time
metadata. It does not receive the whole spec, other threads' transcripts,
future scenes, or the later evaluation questions. The checker receives earlier
source messages across channels; that wider view does not supply missing
context to the writer. Check these boundaries against the generator revision
you will actually use.

Before writing a scene, decide which of these cases applies:

- **Independent arrangement:** describe it on its own. Avoid “separately”,
  “the other plan”, or “before the walk” when the referenced event is absent
  from this thread and is not needed to write the exchange.
- **Same-thread continuation:** rely on the preceding source where it is
  unambiguous. “Same time” or “another day” can be natural when the earlier
  exchange establishes the referent. A long gap or multiple similar events
  may require an ordinary clarification.
- **Cross-thread dependency:** put the minimum established background in the
  scene prose: which event, its current state, who knows it, and what is being
  communicated now. Do not assume the writer has read another scene. A person
  who was absent from the earlier exchange needs a plausible relay before
  responding as though they know its contents.

For example, Jen's first DM does not contain the park-group arrangement.
Instead of “Jen separately arranges a call before the Saturday walk; keep the
appointments distinct”, write:

> Jen asks the user for a short phone call to review her job application.
> They agree on Saturday October 10 at 9 am.

Keep the call's date/time and purpose in its inline facts. The separate
channels and later cancellation-scope question test event separation; the
dialogue need not explain the benchmark's distinction.

By contrast, a group scene that depends on a DM can say: “Maya and the user
agreed in their DM this morning to move the existing Friday lunch from 1 pm
to noon. Leo has not heard about the change. The user relays it to him here,
and he accepts.” This supplies writer context and motivates the relay without
requiring a recap of every unchanged detail. This is prose in the existing
`scene` field, not a new schema field.

Background must describe only established information available before this
scene. Do not introduce future outcomes or assume an earlier intended fact was
successfully generated. Keep background distinct from the facts this scene
must establish. During source review, verify the actual prior messages and
the relay; private authoring prose is never provider evidence.

Email channels use `type: "email"` and a `subject`. Each generated entry becomes an RFC 5322 email body with From/To, Date, Message-ID and reply references. Bodies preserve paragraphs and quoted reply history as part of the actual sender's message. Email exchanges stay at 2–6 messages at every noise level; more email volume comes from authored threads rather than text-chat quotas. Messaging and email addresses are distinct accounts on the same separate Contacts card. Memcal consumes Inbox/Sent through its native secure IMAP reader against a synthetic local mailbox; this is a protocol fixture, not a running Proton Bridge. No native readers or archive writes are substituted. The checker sees preceding source context across channels; the writer sees its own preceding thread and authored requirements.

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

`noise` is `low`, `medium` or `high`. Low is the shortest natural exchange (normally 2–12 messages). Medium targets 20–40 messages per person per conversation per day. High targets 600–800 per person, including unrelated topics, facts and side plans. Multiple scenes in the same chat/day share that target. Each member must meet the minimum; the total exchange stays bounded, while individual speakers may exceed their target share. Larger exchanges use bounded chunks; messages are never padded, copied or truncated to meet a count. Start with low. An optional `until` sets the scene's ending wall time; high otherwise continues through the rest of its day, respecting the next scene in that chat. Authored start times remain unchanged. Within each window, deterministic uneven gaps provide synthetic pacing; they do not claim to reproduce an empirically measured distribution.

For multiple weeks, replace `days` with a `weeks` array of complete Monday–Sunday maps. Channels remain shared, so an ongoing chat keeps its identity across the month. Scene IDs gain a week prefix when compiled; assistant turn and session IDs stay as authored. [Pizza Month](../specs/pizza_month.json) covers 30 consecutive source days, then asks upcoming-plan questions. `ingestion_days: 30` verifies that exact daily coverage and prevents interim questions.

## Challenging Pizza Week

[Challenging Pizza Week](../specs/pizza_week_challenging.json) has 24 scenes on October 5–11, 2026,
and 16 independent questions on Monday October 12, after the final nightly
collection boundary at 03:00 Monday. `ingestion_days: 7` requires seven consecutive source days;
the second week map contains questions only. Every question starts a fresh
assistant session so earlier answers cannot supply the next answer.

The spec omits global `noise` and per-person quotas. Its film and recipe
discussions have explicit topics and forbid new commitments; a separate,
authored third-party gathering supplies a controlled event distractor. On the
legacy compiler, omitted noise still resolves to `low`, with 2–12 messages per
chat scene and 2–6 per email scene (48–276 messages total). This is a seven-day
continuity pilot, not a large-volume stress test or proof of quota-free generation.

| Scenes / question IDs | Challenge |
| --- | --- |
| `lunch-invite-unanswered`, `leo-next-day-reply` / `leo-monday-status` | Leo stays silent Monday and first accepts Tuesday. Membership and silence are not acceptance; later acceptance must not rewrite Monday's state. |
| `lunch-time-only`, `relay-time-and-change-venue` / `past-friday-final`, `lunch-changes` | A DM changes time; an explicit relay informs the group before the venue changes. Preserve event identity and unchanged attributes across threads. |
| `leo-withdraws` / `leo-final-status` | One attendee withdraws without cancelling the other attendees' lunch. |
| `devon-moves-day` / `devon-final`, `past-friday-final` | Move a separate Friday outing to Sunday without creating a duplicate or retaining it in Friday's count. |
| `ben-email-reschedule`, `ben-declined-reversion` / `ben-final`, `ben-old-options` | Quoted Thursday history does not undo Saturday's agreement; a later Friday proposal in a DM is declined. |
| `park-postponed` / `park-status`, `separate-call` | Resolve an unnamed cancellation from its own thread; do not cancel Jen's separate DM appointment. No replacement date is invented. |
| `family-pizza-distractor` / `third-party-party` | A third-party gathering shares the owner's Sunday pizza time and venue, but has a different attendee set and is not the owner's invitation. |
| `jen-next-week-call` / `jen-upcoming` | Distinguish a new interview-preparation call from an earlier application call with the same person. |
| `key-pickup`, `key-no-longer-needed` / `key-status`, `key-original` | Keep a favour separate from a meal at the same venue; cancel the obligation while retaining its historical details and non-attending beneficiary. |
| `leo-tentative-next-friday`, `maya-next-friday`, `relay-new-plan` / `next-friday-current`, `leo-next-friday` | Separate next week's similar outing from last week's, and confirmed attendees from a still-unconfirmed invitee. |
| `maya-dairy`, `dairy-still-undecided` / `food-date-scope` | A temporary request expires without establishing its opposite. Next Friday's food requirement remains undecided, not definitely dairy-free or definitely unrestricted. |

Scenes gain the `w1-` prefix when compiled. All past-event criteria concern
agreements, not proof a meal or call actually happened. Compound event details
should remain independently assessable when contracts are frozen. Speaker
silence is a scene requirement, not an enforced speaker whitelist: inspect the
generated Monday transcript for any Leo message. The delayed reply uses an
existing three-person group, so it does not require unsupported one-sided DM
generation. The writer receives relevant cross-thread background in scene
prose; this does not substitute for checking the actual source continuity.

Before paid preparation, validate this spec with the actual generator revision.
After generation, inspect silence, cross-thread relays, faithful email quotes,
incidental commitments and all answer criteria against the source. Structural
validation cannot establish any of these semantic properties.

Concrete dates are assigned during preparation from the spec's `week_start` Monday. `--week-start` explicitly overrides it; update any prose containing concrete dates when moving a scenario. Legacy unpinned inputs can still prepare relative to the host week, but the inventory requires a fixed date. Eastern defaults to `America/New_York`, including daylight saving time. Dates and offsets are frozen in the compiled corpus.

```sh
python -m pacbench.specs
python -m pacbench.prepare specs/pizza_week.json --output artifacts/corpora/pizza-week
python -m pacbench.preparation_audit artifacts/corpora/pizza-week
python -m pacbench.native_pilot --prepared artifacts/corpora/pizza-week/week.json --providers memcal --output runs/pizza-current
```

Preparation writes a small plain-JSON `week.json` manifest with hashes of the generated messages, verification receipts, questions and evidence-cited judging contracts beside it. Keep the prepared directory together to reuse it across providers and runs; the manifest references these files rather than embedding a second copy. Provider execution checks every referenced file before importing it, without authoring or verifying messages again. Older self-contained JSON bundles remain readable. `--resume` reuses unchanged verified scenes and accepted contracts when fixing an interrupted preparation. Changed sources, questions, expectations or clocks require fresh contract review; a changed scene requires fresh source verification. Failed scenes are recorded, dependent source continuations skip, and independent scenes continue. Incomplete preparation cannot become a runnable frozen corpus.

The minimal [Pizza Day](../specs/pizza_day.json) uses the same format for a one-day source smoke test. Preparation always runs separately from Hermes and native provider ingestion. All paid work goes through the shared gateway.
