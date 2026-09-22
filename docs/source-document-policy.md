# Source-document policy (plan.md P0-1)

Mapping version: `1.0` (see `SOURCE_DOCUMENT_MAPPING_VERSION` in
`pacbench/source_documents.py`). Written against bundle `0.2.0`, corpus `0.3.0`.

Rule: **natural source boundaries, never benchmark-story boundaries.**
Document identity is derived solely from public source provenance. It must
never contain a life ID, checkpoint ID, story/family name, grading metadata,
or batching/collection timestamps.

Current provider note: `provider_adapters/hindsight_adapter.py` still ingests
per-message `document_id = source_id`. That scheme is SUPERSEDED by this
policy and will be replaced under P0-2 (stable public source identity); this
file is the target semantics, not a description of the current adapter.

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
   cannot express one (see ambiguities doc), so singleton-per-message is the
   v1 rule without exception.
8. **Hermes conversations** — one Hermes session/conversation is one logical
   conversation. Key: `hermes:{thread}`. The historical `agent` channel alias
   normalizes to `hermes` before keying, so both labels address the same
   document.

Beyond the eight: **SMS (`text` channel)** is conversational by the same
reasoning as iMessage — one conversation is one document (`text:{thread}`).
It is not named in the P0-1 table but needs a row so ingestion fails closed
on nothing; recorded here explicitly rather than smuggled in.

## Negative rules (apply to every channel)

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
