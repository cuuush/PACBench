# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 10 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings and gateway inference. Set the documented `is_reasoning_model: true` flag for Spark; the SDK then chooses its native reasoning-model parameters. No custom output budget or reasoning effort. The earlier legacy-chat classification was a setup error; larger-output trials remain a different configuration. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank; native API `meta` adapter for Spark, using the gateway base URL. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Native resource processing is distinct from interactive memory capture. Expose `viking_read`; original-source access earns no retention credit. Check model-health receipts independently of native completion. |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json` | Local CLI/context tree, OpenAI-compatible BYOK. Completed curation is readable immediately. Its pending-review log is retrospective: native rejection restores prior files. Leave it unchanged; never auto-approve. Native diagnostic recall confirmed this behavior. |
| Supermemory | `documents.add(dreaming="instant")` with raw content, canonical container tags and custom ID; wait for indexing and memory consolidation | Published local server and native SDK, Ollama embeddings. Use the native namespace resolver. Explicit instant mode processes each already-batched daily document instead of default dynamic grouping across documents. The October 23 native canary completed extraction and populated the profile. The mode is recorded in setup and ingestion receipts; earlier dynamic-mode blockers are separate conditions. |
| Honcho | Real source peers, original timestamps and native configured recall observer through `session.add_messages` | Documented pgvector/API/deriver self-hosting. Native batching/output defaults; current setup defaults to directional observation, applied to the owner and non-speaking AI observer before messages. Require new work enrollment and completion. Allow its native accumulation delay before 15-minute processing/five-minute stall bounds. Report model incompatibilities instead of increasing limits. Host-clock assistant capture and original-message fallback remain visible; fallback earns no retention credit. |
| Memcal | Native Messages SQLite and TLS IMAP readers, separate contacts, normal nightly processing | Provider-independent Apple schema and RFC 5322 fixtures; nightly processing at 03:00 scenario-local time. Version 0.8.2 with product fixes on `main`. |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

Every connector uses the same frozen public message records. Daily document v3 is a readable chronological transcript: conversation/channel, roster and subject headers, written times, senders, ownership and every original body, including email quotes. Different delivery/capture times remain visible. Full canonical records and IDs remain in `messages.json` for audits and native fixtures; repetitive machine IDs and identical metadata do not inflate the LLM's text. v1/v2 JSON documents retain their original audit rules. No messages are selected, summarized or extracted by the connector.

Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture. Nightly worker deadlines allow 15 minutes for a multi-conversation batch; Honcho additionally retains its declared native accumulation delay. All inference remains under the shared daily spending cap.

Memcal email delivery uses a real local TLS IMAP server with Inbox/Sent folders, reply references and separate account addresses linked by standard Contacts cards. This is a text-message fixture implementing the commands used by the native reader, not a Proton Bridge installation or a full mailbox server. No reader methods are replaced. The connector waits for native ingestion and processing before Hermes starts.

Run-owned namespaces and stores prevent cross-trial state. Configure each system from its project instructions; retain version/setup receipts. Failed readiness blocks recall. Internal native tools, queues and cost are captured where available; missing observability stays unknown. Current readiness/recall receipts live under `runs/` and are shown directly in the explorer. Inspect answer, retention and harness issues separately. These development grades are not calibrated rankings.


Honcho's [directional representations](https://docs.honcho.dev/v3/documentation/features/advanced/directional-representations)
require the querying observer to witness messages when they are created. Hermes
normally recalls via its AI peer with the human as target. The connector joins
that configured AI as a non-speaking reader using native `SessionPeerConfig`,
verifies the server's actual flags, then delivers real source messages. No
assistant utterances, manual conclusions or retrospective memory inserts are made.
Honcho [queue status](https://docs.honcho.dev/v3/documentation/features/advanced/queue-status)
is an observability interface, not a production synchronization contract. The
benchmark's bounded barrier covers only its finite, isolated submitted sessions.
Read the queue baseline before submission and require at least one new work unit
per acknowledged source message before accepting an empty queue. Counts are
periodically cleaned by Honcho; if that prevents proving enrollment, withhold
readiness rather than guessing success. Native assistant capture uses host time;
source-message capture explicitly preserves original timestamps.

The native completion counter also includes terminally errored queue items.
The local runner observes the run-owned PostgreSQL queue without modifying it,
stops on submitted-source errors and saves their classes/hashes. A cleared queue
alone must not turn failed extraction into successful ingestion.
