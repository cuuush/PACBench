# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 10 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings, gateway inference. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank; native API `meta` adapter for Spark, using the gateway base URL. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Native resource processing is distinct from interactive memory capture. Expose `viking_read`; original-source access earns no retention credit. Check model-health receipts independently of native completion. |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json` | Local CLI/context tree, OpenAI-compatible BYOK. Current curation left a native pending review; recall was withheld. |
| Supermemory | `documents.add` with raw content, canonical container tags and custom ID; poll status | Published local server and native SDK, Ollama embeddings. Native namespace resolver. Current document status reported done despite repeated provider-model HTTP 400s; run stopped. |
| Honcho | Real source peers, original timestamps and native configured recall observer through `session.add_messages` | Documented pgvector/API/deriver self-hosting. Hermes's observer joins before messages; retrospective attachment does not backfill. Require newly acknowledged work to enroll and finish, with a 15-minute processing/five-minute stall bound. Spark needs native 16384-token allowance; 2500 truncated extraction. Preserve the integration's resolved observation mode. Host-clock assistant capture and backend original-message fallback still qualify recall; fallback earns no retention credit. |
| Memcal | Native Messages SQLite and TLS IMAP readers, separate contacts, normal nightly processing | Provider-independent Apple schema and RFC 5322 fixtures; nightly processing at 03:00 scenario-local time. Version 0.8.2 with product fixes on `main`. |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

Every connector gets the same frozen public message records; daily documents are lossless JSON lines. Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture.

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
