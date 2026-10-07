# Document ingestion provider map

The benchmark now requires a supported raw-document ingestion route. Hermes does not receive source messages as chat turns and does not extract facts during ingestion. A connector transports unmodified source material into the provider; the provider owns extraction, organization and updates. Hermes only answers later evaluation questions through its normal memory integration.

Document/native-source ingestion is the current benchmark path. Development results may be regenerated or removed; earlier chat-handoff results do not constrain this implementation.

## Inventory and evidence

The pinned [Hermes memory documentation](../vendor/hermes-isolated/website/docs/user-guide/features/memory-providers.md) lists eight bundled providers plus separately installed Memori. Memcal is an additional project requirement. Documentation and plugin code were inspected on 6 October 2026; a listed API is evidence of an interface, not proof of a working deployment.

| Provider | Observed interface | Eligibility for document benchmark | Remaining work |
| --- | --- | --- | --- |
| OpenViking | `viking_add_resource` indexes URLs/docs; plugin uses server resource ingestion | Document route present | Validate local server setup, identity scope, processing completion and whether resource indexing extracts the facts needed by ordinary Hermes recall |
| Hindsight | Retain accepts source text, with entity extraction and document tracking | Text-document route present | Use raw transcript as document, preserve source date/document identity, configure local inference through gateway, verify processing and native recall |
| Supermemory | SDK `documents.add(content=..., container_tags=..., custom_id=...)` | Document route present | Verify documented local deployment, extraction/ready state, isolated container and model-cost routing |
| RetainDB | File upload followed by `/v1/files/{id}/ingest`; also session ingestion | Document route present | Account and measurable spending boundary; Hermes docs list $20/month, exceeding the task budget for a new subscription |
| ByteRover | `brv curate -- <content>` invokes provider curation | Candidate; raw-document support must be confirmed | Verify CLI documentation for document/file input, preservation of complete text and gateway-only inference; do not equate fact curation with document import without evidence |
| Mem0 | `add(messages, infer=True)` extracts facts; Hermes `mem0_add` uses `infer=False` and stores verbatim | Raw-text document route confirmed in the installed SDK (`messages: str`, `infer=True`) | OSS Qdrant/local Ollama setup; direct synchronous string import. Record native extraction errors even when the SDK returns an empty result. No fake conversation or verbatim fact writes |
| Honcho | Session/peer messages through `add_messages` | Not eligible on inspected evidence | Native message modeling is not established document ingestion; include only if upstream documentation establishes a document route |
| Memori | Published `hermes-memori` 0.1.8 uses SDK `capture_agent_turn`; completed-turn augmentation | Not eligible on inspected evidence | Verify upstream document import separately; do not fabricate assistant replies, tool traces or completed agent turns to feed messages |
| Holographic | Fact store; optional session-end regex extraction copies matching user text, capped at 400 chars | Excluded | No general document ingestion/extraction; do not add a benchmark-owned extractor |
| Memcal | Native source ingestion archives raw lines and spools them for its normal processing pipeline | Explicit user-authorized native iMessage exception | Real source reader and contacts import, then normal nightly processing. No archive writes by the benchmark or Hermes ingestion |

Sources: each bundled provider's `vendor/hermes-isolated/plugins/memory/<provider>/README.md` and `__init__.py`; Honcho's `session.py`; Memcal's source/archive/dream code and agent guide. Memori evidence comes from the public PyPI package metadata and wheel source, inspected without installation or service calls: https://pypi.org/project/hermes-memori/ .

## Fairness and connector contract

- Same frozen messages and exact source bytes, in chronological batches; retain sender, channel, thread, source ID, written time and collection time. Keep source dates distinct from import time.
- A document can be a lossless transcript with metadata. Format conversion is allowed; summaries, pre-extracted facts, answers, judge criteria and benchmark-owned extraction prompts are not.
- Use the provider's recommended import and update semantics, run-owned scopes and documented readiness signal. A queued import is not successful extraction. Readiness checks must be bounded and recorded consistently, including timeouts and failures.
- Preserve replay/duplicate behavior and revisions. Do not silently truncate transcripts, invent sender/assistant roles or relabel third-party statements as user preferences.
- Hermes evaluation starts fresh with ordinary provider tools/context. No source transcript, catch-up conversation, expected facts or populated builtin memory is passed to Hermes. Later evaluation questions can share that fresh context.
- Record native inputs, import IDs, processing status, calls, duration, provider model usage and later Hermes usage. Unknown hosted costs stay unknown and block paid execution under the $10 ceiling. Every Spark call must use the authenticated gateway; no upstream fallback.
- Distinguish source-document retrieval from extracted-memory retrieval. Preserve the user's retention penalty for original-message/archive fallback. Provider document search must be classified by what it actually returns; do not silently count raw transcript reads as retained facts.

Start with the existing small frozen Pizza Day messages. Validate one actual document route before expanding provider count or message volume. OpenViking, Hindsight and Supermemory are the first confirmed-interface candidates; deployment and cost safety remain to be established. No provider is marked working merely because an API exists.
