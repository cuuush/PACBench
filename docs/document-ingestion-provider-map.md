# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 10 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings, gateway inference. Latest six-question week: answers correct; one judge-induced incomplete check, reviewed separately. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank. Latest week: 6/6 answer passes. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Latest week: 6/6 answer passes; original-document access fails retention. |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json` | Local CLI/context tree, OpenAI-compatible BYOK. Latest week: 6/6 answer passes; first query timed out, second succeeded. |
| Supermemory | `documents.add` with raw content, canonical container tags and custom ID; poll status | Published local server and native SDK, Ollama embeddings. Native namespace resolver; latest week: 5/6, missed Devon in updated count. |
| Honcho | Real source peers and original timestamps through `session.add_messages` | Documented pgvector/API/deriver self-hosting. Latest week: native processing completed 9/18 units before the bounded deadline; recall blocked. |
| Memcal | Native Messages SQLite reader, separate contacts, normal nightly processing | Provider-independent Apple schema fixture; nightly processing at 03:00 scenario-local time. Version 0.8.2; latest week: 5/6, initial outings merged; source fallback needed for details. |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

Every connector gets the same frozen public message records; daily documents are lossless JSON lines. Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture.

Run-owned namespaces and stores prevent cross-trial state. Configure each system from its project instructions; retain version/setup receipts. Failed readiness blocks recall. Internal native tools, queues and cost are captured where available; missing observability stays unknown. Current evidence is `runs/pizza-week-recall-all-20261010`; inspect answer, retention and harness issues separately. These development grades are not calibrated rankings.
