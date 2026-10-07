# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 7 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings, gateway inference. Current clean Pizza Day: 5/5 with one search. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank. Connector implemented; clean recall not rerun. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Connector implemented; original-document access must fail retention. |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json` | Local CLI/context tree, OpenAI-compatible BYOK. Native Hermes query timeout remains a practical blocker. |
| Supermemory | `documents.add` with raw content, canonical container tags and custom ID; poll status | Published local server and native SDK, Ollama embeddings. Connector shares Hermes's namespace resolver; clean recall not rerun. |
| Honcho | Real source peers and original timestamps through `session.add_messages` | Documented pgvector/API/deriver self-hosting. Accepted source messages, but native processing did not drain within the bounded deadline. |
| Memcal | Native Messages SQLite reader, separate contacts, normal nightly processing | Provider-independent Apple schema fixture; nightly processing at 03:00 scenario-local time. Connector implemented; clean recall not rerun. |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

Every connector gets the same frozen public message records; daily documents are lossless JSON lines. Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture.

Run-owned namespaces and stores prevent cross-trial state. Configure each system from its project instructions; retain version/setup receipts. Failed readiness blocks recall. Internal native tools, queues and cost are captured where available; missing observability stays unknown. Only the current clean Mem0 test is retained as recall evidence; no old scores imply other providers passed this condition.
