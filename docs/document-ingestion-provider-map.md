# Daily source ingestion provider map

The benchmark covers **local memory backends with practical automatic ingestion of daily raw text**. Supported document import and real message capture both qualify. A source connector exports messages and submits them to the provider; the provider does extraction and organization. Hermes receives only later questions, its normal provider context and that provider's shipped tools.

For a real daily import, collect new messages after the last successful checkpoint, preserve their original text and metadata, submit a lossless transcript or message batch, wait for native processing, and record the acknowledgement. Do not send the transcript through Hermes, invent assistant replies, extract facts ourselves or customize extraction prompts. Failed processing remains a failed import.

## Inventory

The [Hermes documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) lists nine integrations, including the separately installed Memori plugin. The public documentation was checked again on 7 October 2026 against the pinned local plugins. Memcal is an additional project requirement.

| Provider | Authentic daily delivery | Local setup used | Small-test finding |
| --- | --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)`; text contains actual speakers and timestamps | OSS SDK, local Qdrant, Ollama embeddings, gateway inference | Imported and recalled 5/5 facts |
| Hindsight | Native retain API with raw text, source time and document ID; wait for consolidation | Documented `local_external` backend, run-owned bank, gateway inference | Imported; recalled 4/5, still missed the street after its one eligible follow-up |
| OpenViking | Native temporary upload then `resources(wait=True)`; inspect semantic/vector completion | Local server/storage, Ollama embeddings, gateway inference | Imported; answered 5/5 by opening the full source document, so retention fails |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json`, without extra instructions | Local CLI/context tree, documented OpenAI-compatible BYOK, no cloud login | Curation completed. First recall answered 5/5; repeat timed out. Native Hermes query timeout is 10 seconds; retrieval classification remains under review |
| Supermemory | `documents.add(content=raw_transcript, container_tags=..., custom_id=...)`, then poll `documents.get` | Published local server 0.0.8, native Hermes SDK 3.50.0, Ollama embeddings, gateway inference | Imported and recalled 5/5 after fixing connector namespace binding |
| Honcho | Native session messages for actual source peers, with original timestamps and metadata; `session.add_messages` | Documented manual self-hosting: pgvector, native API/deriver, Ollama embeddings, gateway inference | Accepted all 25 messages, but processing did not finish within three minutes. No recall score; currently blocked |
| Holographic | Fact storage and optional regex extraction, without a usable general source-ingestion pipeline | Excluded | Would require benchmark-owned extraction; do not adapt artificially |
| RetainDB | Hosted document upload/ingest | Excluded | Hermes documents hosted storage; no verified local backend |
| Memori | Hermes plugin uses Memori Cloud completed-agent-turn capture | Excluded | No verified local source-ingestion integration; do not fabricate agent turns |
| Memcal | Real iMessage reader and separate contacts import, then native nightly processing | Local Memcal with a reusable synthetic Apple Messages database | Imported and recalled 5/5 with one retained-event open |

Working interfaces do not establish provider quality. These are a small diagnostic and targeted repetitions, not calibrated rankings. Exact run paths are in [the local diagnostic report](local-provider-diagnostic-2026-10-07.md).

## Setup evidence and practical limits

- Mem0: use the bundled plugin's manual native configuration route, synchronous `infer=True` ingestion and default extraction prompt. `mem0_add` uses `infer=False` and cannot substitute for raw-document extraction. Capture parser/extraction errors even if the SDK swallows them.
- Hindsight: use the documented external local backend route. Wait for native retain and observation consolidation. Do not force observations or supply a fact-oriented mission.
- OpenViking: use the native resource pipeline. Resource indexing is useful document retrieval, but distinguish it from retained memory. No source answer is injected into its memory store.
- ByteRover: [CLI documentation](https://docs.byterover.dev/) supports file curation and local BYOK. Preserve native progress, completion and pending-review status. Do not approve pending changes automatically. Record provider-internal task journals and nested tools separately from Hermes calls. Preserve its hard-coded Hermes query timeout as an integration failure; do not extend it specially to obtain a pass.
- Supermemory: published local-server documentation supports OpenAI-compatible inference and embeddings. Use the SDK shipped with the pinned Hermes plugin, not an incompatible latest SDK. Verify release bytes against published SHA256. Resolve ingestion tags using Hermes's native resolver, otherwise the plugin can search a different namespace. Server 0.0.8 binds all interfaces inside this private execution environment; no public port is published.
- Honcho: [server repository](https://github.com/plastic-labs/honcho) documents manual self-hosting, native migrations and embedding-schema setup. Message capture models real participants without invented replies. Its default deriver batches small inputs until an age threshold; the repeat explicitly used the documented CLI advanced option `DERIVER_FLUSH_ENABLED=true` ("snappy local deriver"). Even then the queue did not drain within three minutes. Keep it blocked; do not patch prompts or seed representations. Official tokenizer bytes already bundled in the installed runtime are cached with native hash verification when this environment blocks their download.
- Memcal: use a provider-independent Apple Messages fixture with GUIDs, Apple timestamps, handles, service, membership and joins. Display names belong in a separate contacts source. Import through the actual reader and process at its ordinary nightly checkpoint, 03:00 Eastern. Do not insert facts, archive rows or Memcal-specific database fields.

Native setup commands and receipts live in each arm's private artifacts. Adapters are `provider_adapters/*_local.py` and `provider_adapters/document_connector.py`. Runtime binaries, credentials, homes and stores stay outside Git.

## Fairness contract

- Every arm gets the same frozen messages in chronological daily batches. Preserve source ID, sender, channel, thread, written time and collection time. Documents are lossless JSON lines; Honcho gets the same records as native messages, and Memcal gets them through Apple Messages tables.
- Format conversion is allowed. Summaries, pre-extracted facts, hidden answers, judging criteria and benchmark-owned extraction instructions are not.
- Use documented import, namespace and processing semantics. Queue acknowledgement is not completed ingestion. Record bounded readiness checks and failures; do not ask Hermes against incomplete state.
- Evaluation starts without source chat history or populated built-in memory. Later planned questions may share that evaluation session. Hermes may invoke only exact tool names shipped by its selected provider; a forbidden attempt fails the trial.
- Log arguments, results, retries, latency and available provider-internal traces. Separate Hermes usage, provider inference and benchmark overhead. Native traces do not establish causal token attribution per tool; unknown values remain unknown.
- All Spark traffic goes through the shared spending gateway: $5 per UTC day and $10 total. Paid hosted-only backends are excluded. Unknown requests retain reservations.
- Original-source/archive/message fallback fails retention even if correct. Unclassified operations get no retention credit until reviewed. Do not equate document search with memory extraction.

Stay at small Pizza Day until these failures are understood. Next test corrections and two independent plans across three days, then roughly 100 additional ordinary messages. Do not scale volume to conceal small-case problems.
