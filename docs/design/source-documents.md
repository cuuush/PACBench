# Source documents

**Current rule: natural source boundaries, never benchmark-story boundaries.**
Document identity derives solely from public source provenance. It never
contains a life ID, checkpoint ID, story/family name, grading metadata,
or batching/collection timestamps.

## Channel mappings

1. **iMessage** — one conversation/chat is one evolving logical document.
   Key: `imessage:{thread}`.
2. **WhatsApp** — one conversation/chat is one evolving logical document.
   Key: `whatsapp:{thread}`.
3. **GroupMe** — one group/thread is one evolving logical document.
   Key: `groupme:{thread}`.
4. **Slack DM** — one DM conversation is one evolving logical document.
   Key: `slack:{thread}` (the DM's thread/conversation id).
5. **Slack threaded conversation** — one actual Slack thread is one logical
   document. Key: `slack:{thread}` where `{thread}` is the thread's id,
   distinct from the parent channel's id. Replies in different threads are
   different documents even when they share a parent channel.
6. **Slack channel messages (no thread)** — retain normal channel/conversation
   grouping: all non-threaded messages in a channel share that channel's
   document (`slack:{channel-thread-id}`). They are never artificially grouped
   around PACBench stories, and thread replies never collapse into the
   channel document.
7. **Email** — preserve actual email structure. Individual emails remain
   individual documents: `email:{message-local-id}` (the `source_id` with the
   life prefix stripped). Do NOT group all mail from the same sender. If the
   corpus ever explicitly models a real email thread (message-ID references),
   that thread relationship is preserved instead — the current corpus schema
   cannot express one (see below), so singleton-per-message is the
   v1 rule without exception.
8. **Hermes conversations** — one Hermes session/conversation is one logical
   conversation. Key: `hermes:{thread}`. The historical `agent` channel alias
   normalizes to `hermes` before keying, so both labels address the same
   document.

Beyond the eight: **SMS (`text` channel)** is conversational by the same
reasoning as iMessage — one conversation is one document (`text:{thread}`).
It is not named in the P0-1 table but needs a row so ingestion fails closed
on nothing; recorded here explicitly rather than smuggled in.

## Negative rules (every channel)

- No `life_id` / plan id (including the `{plan.id}/` prefix baked into every
  `source_id` by authoring — strip it, never propagate it).
- No checkpoint IDs, expected entity IDs, or grading/rubric metadata.
- No story/family names, lineage strings, or difficulty tags.
- No timestamps used as batching boundaries (`collected_at`, ingestion time,
  collection-cycle dates). `written_at` is content provenance and rides
  *inside* the document; it is not identity either.
- No sender-based grouping for email: same sender ≠ same document.
- No benchmark-story grouping for Slack channel traffic: same story ≠ same
  document.

## Stability contract

Keys are stable across collection cycles, resumes, provider repetitions, and
checkpoint execution, because every key input (channel label, source thread
id, message-local id) is fixed at authoring time and contains no
run-dependent component. Any change to the table or key format bumps
`SOURCE_DOCUMENT_MAPPING_VERSION`, and runs ingested under different mapping
versions must not be silently aggregated (P0-2 digest).

## v1 conventions under schema ambiguity

The corpus schema cannot express every distinction above, so v1 proceeds by
stated convention wherever the schema is silent. Each gap gets a verdict —
**ambiguous** (schema cannot express the distinction) or **explicitly
unambiguous** (schema already carries what the mapping needs).

### Slack thread-vs-channel identity — AMBIGUOUS

`Observation.thread` is a single opaque string with no parent pointer, no
channel-vs-thread flag, and no reply-to reference. Nothing in the schema
distinguishes "top-level message in #general" from "reply in a thread rooted
in #general" except the authoring convention of giving them different thread
ids. The current corpus never exercises the distinction (one `slack-team`
thread per life; no reply-parent encoding anywhere).

V1 rule: key on `(channel, thread)` exactly as authored. Thread replies are
distinct documents *if and only if* authoring gives them distinct thread ids;
channel traffic groups by the channel's thread id. If a future corpus life
models real Slack threads, authoring MUST mint a distinct thread id per real
thread — the mapping will then do the right thing with no code change, but
nothing today verifies that convention. A corpus-side check (distinct thread
ids for asserted distinct threads) is future work, not assumed.

### Email-thread identity — AMBIGUOUS

The schema carries no threading metadata: no message IDs, no
In-Reply-To/References, no Subject header. Corpus `ThreadPlan`s named like
email threads (e.g. `email-bistro` carrying four beats, `email-nadia`
carrying two) are authoring buckets, not asserted real threads — treating
them as real threads would invent structure the source never had.

V1 rule: every email is a singleton document (`email:{message-local-id}`),
no exceptions, no same-sender grouping. If the corpus ever gains explicit
thread references, the policy already reserves the escape hatch (preserve the
real thread relationship); until then the ambiguous case defaults to
singleton, because wrongly merging distinct emails destroys evidence while
wrongly splitting a thread only costs one extra document.

### Hermes session bounds — AMBIGUOUS

The schema has no session identifier and no session-start/end markers; there
is one `hermes` thread per life in the current corpus, but nothing stops two
real sessions from sharing the thread id `hermes`, and nothing records where
one session ends and the next begins.

V1 rule: one `(channel, thread)` pair is one conversation. Distinct real
sessions MUST be authored as distinct thread ids; sessions sharing a thread
id are (by convention) one document.

### Explicitly unambiguous: the `agent` → `hermes` alias

`observation.py` documents `agent` as a historical alias for `hermes`
"so older corpus modules keep loading", with `HERMES_CHANNEL = "hermes"`.
Both labels name the same source, so normalizing `agent` → `hermes` before
keying is schema-grounded, not invented. Two observations differing only in
this label address the same document; this is covered by test.

### Explicitly unambiguous: authoring-side data is out of reach

`ThreadPlan.participants`, `LifePlan` lineage, checkpoints, and grading
metadata structurally never enter an `Observation` (which carries only public
fields), so the mapping cannot — even by accident — key on them. The v1 key
reads `channel`, `thread`, and `source_id` only; sender is content, not
identity, for conversational channels. No gap here, recorded so a future
"enrich the key with participants" proposal has to justify crossing the
public/authoring boundary first.

## History

Plan.md P0-1, written against bundle `0.2.0`, corpus `0.3.0`. Merged from
`docs/source-document-policy.md` (the target semantics) and
`docs/source-document-ambiguities.md` (the schema-gap verdicts). The
per-message `document_id = source_id` ingestion in
`provider_adapters/hindsight_adapter.py` predates this policy and is
superseded by it (replaced under P0-2 stable public source identity).
