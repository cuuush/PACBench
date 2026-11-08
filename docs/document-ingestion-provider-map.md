# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

Honcho's 100-message bound is per API request, not per conversation. A nightly collector groups messages by their real thread, opens or reuses that thread's native session, enrolls the configured observer before submission, and sends ordered requests of at most 100 messages. A 300-message thread uses three requests in the same session; it does not become three conversations. Native processing decides its own extraction batches. This connector-side pagination needs no server code changes or raised limit. Delivery acknowledgments prove transport, not completed memory processing.

Use the provider's native API and documented configuration. Run-owned stores, local embeddings and gateway transport are setup; processing choices such as Supermemory's `dreaming=instant`, Mem0's reasoning-model flag and Honcho's deriver allowance must remain explicit. None authorizes changing provider prompts, patching extraction, forcing queue completion or splitting out known answer facts. Local Honcho also needs PostgreSQL and its API/deriver services; this is self-hosted integration work, not a claim of a five-minute consumer iMessage connector. The benchmark's synthetic clock is test infrastructure, separate from the production collector.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 10 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings and gateway inference. Set the documented `is_reasoning_model: true` flag for Spark; the SDK then chooses its native reasoning-model parameters. No custom output budget or reasoning effort. The earlier legacy-chat classification was a setup error; larger-output trials remain a different configuration. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank; 1.1.0 server/client and native API `meta` adapter for Spark, using the gateway base URL. The updated pair passed the two-day raw-message/email and fresh-session persistence canary. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Native resource processing is distinct from interactive memory capture. Expose `viking_read`; original-source access earns no retention credit. Check model-health receipts independently of native completion. |
| ByteRover | `brv curate --files imports/<day>/messages.txt --format json` | Local CLI/context tree, OpenAI-compatible BYOK. Completed curation is readable immediately. Its pending-review log is retrospective: native rejection restores prior files. Leave it unchanged; never auto-approve. Native diagnostic recall confirmed this behavior. |
| Supermemory | `documents.add(dreaming="instant")` with raw content, canonical container tags and custom ID; wait for indexing and memory consolidation | Published local server and native SDK, Ollama embeddings. Use the native namespace resolver. Explicit instant mode processes each already-batched daily document instead of default dynamic grouping across documents. The October 23 native canary completed extraction and populated the profile. The mode is recorded in setup and ingestion receipts; earlier dynamic-mode blockers are separate conditions. |
| Honcho | Real source peers, original timestamps and native configured recall observer through `session.add_messages` | Documented pgvector/API/deriver self-hosting, native server 3.4.1. The default 2,500-token deriver limit exhausted Spark's allowance without JSON. The user-authorized documented 8,192-token setting passed the source/recall canary; record it as a configured condition. Retain native batching and directional observation for the owner and non-speaking AI observer before messages. Require actual work enrollment/completion, including captured assistant-session instructions before fresh-session checks. Original-message fallback earns no retention credit. |
| Memcal | Native Messages SQLite and TLS IMAP readers, separate contacts, normal nightly processing | Provider-independent Apple schema and RFC 5322 fixtures; nightly processing at 03:00 scenario-local time. Version 0.8.2 with product fixes on `main`. |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

Every connector uses the same frozen public message records. Daily document v3 is a readable chronological transcript: conversation/channel, roster and subject headers, written times, senders, ownership and every original body, including email quotes. Different delivery/capture times remain visible. Full canonical records and IDs remain in `messages.json` for audits and native fixtures; repetitive machine IDs and identical metadata do not inflate the LLM's text. v1/v2 JSON documents retain their original audit rules. No messages are selected, summarized or extracted by the connector.

Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture. Nightly worker deadlines allow 15 minutes for a multi-conversation batch. All inference remains under the shared daily spending cap.

Honcho's explicit `--honcho-clock simulated` profile advances its actual PostgreSQL, API, deriver and Hermes wall clocks between daily bulk imports. Install the pinned Linux x86_64 runtime with `vendor/libfaketime/install-runtime.sh`. Native batching settings remain unchanged; pending messages become eligible when the isolated clock reaches the native age threshold. The harness still witnesses their actual processing. Gateway budgets, operational deadlines and monotonic timers remain real. Clock steps, native observations and message witnesses are audited; future checkpoint exposure is refused. The older real-clock profile retains its native waiting time and is a separate condition.

The shipped Honcho SDK has a 100-message request limit. A daily thread with more messages uses consecutive native requests in its existing observed session, followed by one processing barrier for the complete import. This is transport batching; no text is summarized or extracted by the connector. Atomic, hash-bound chunk receipts retain verified native IDs and mark uncertain delivery without automatic replay.

Clock v3 suspends the native runtime during benchmark judging only at an observed idle boundary, excluding that harness overhead from provider wall time. Database/queue and gateway snapshots cannot prove arbitrary in-memory quiescence; monotonic deadlines still elapse. Active observed work proceeds normally. A separate real-clock watchdog stops services if the owner is killed during suspension. No frozen absolute-time mode is used: its native sleep compatibility probe failed. Native trace dates on the simulated clock cannot establish timing against the gateway's real timestamps, so uncertain source paths remain uncredited.

Memcal email delivery uses a real local TLS IMAP server with Inbox/Sent folders, reply references and separate account addresses linked by standard Contacts cards. This is a text-message fixture implementing the commands used by the native reader, not a Proton Bridge installation or a full mailbox server. No reader methods are replaced. The connector waits for native ingestion and processing before Hermes starts.

Run-owned namespaces and stores prevent cross-trial state. Configure each system from its project instructions; retain version/setup receipts. Failed readiness blocks recall. Internal native tools, queues and cost are captured where available; missing observability stays unknown. Current readiness/recall receipts live under `runs/` and are shown directly in the explorer. Inspect answer, retention and harness issues separately. These development grades are not calibrated rankings.

Cross-session write barriers use native status without forcing work. Explicit
write IDs take precedence; unrelated capture failures cannot replace their
outcome. When only Supermemory conversation capture supplies the write, v2 also
verifies every actual user instruction in the captured user-role text. A matching
session ID alone can refer to an older turn. v1 trials retain their frozen rules.
Supermemory can expose an extracted memory while its document still reports
`dreaming`; a read-only diagnostic can demonstrate that discrepancy, but it does
not retroactively complete the original availability-gated test.

For Honcho, v2 can instead verify a synchronous explicit `conclude` write through native conclusion records. Match the actual accepted tool text, originating session, observer and target, covering every required input and accepted write. That path needs no conversation-capture queue wait. The captured-input/queue barrier remains the fallback; a saved acknowledgement alone never proves persistence. No conclusion is created by this observer.


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


For Spark on Honcho we explicitly set the native per-model deriver allowance:

```sh
DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS=8192 python -m pacbench.native_pilot \
  --prepared runs/<prepared>/week.json --providers honcho --output runs/<new-run>
```

This is Honcho's standard environment configuration, equivalent to
`max_output_tokens = 8192` under `[deriver.model_config]` in its
[shipped configuration example](https://github.com/plastic-labs/honcho/blob/5314d0a8635f02375b8ad73c89eb80f86d7d1cb4/config.toml.example#L123).
We run server 3.4.1 and Hermes' pinned Python SDK 2.2.0, configure the OpenAI-compatible transport
through the shared Spark gateway, and use local Ollama embeddings. Directional
observation, source peers and timestamps are unchanged. Fresh Hermes homes explicitly
set `sessionStrategy=per-session`, matching the current native setup wizard;
omission otherwise selects the legacy per-directory fallback. The 512-token work-unit
target, 1,024-token input batch target, 1,800-second accumulation window and
`FLUSH_ENABLED=false` remain native defaults. We do not set reasoning effort,
replace prompts, or repair extraction results. The global 2,500-token default
also remains unchanged; this override applies to the deriver model only.

The larger allowance provides room for Spark's reasoning and its JSON answer;
it does not force longer answers. A controlled native-format check of the same
source and prompt stopped normally at 3,802 output tokens instead of exhausting
the old cap without JSON. Native imports still have to complete before recall.
`native-processing-settings.json` records the resolved allowance and verifies
batching defaults. Such runs are explicitly configured trials, separate from
the earlier default-limit failures. Model usage is charged as observed through
the unchanged $10/day gateway boundary.


The October 25 configured canary completed native processing for all nine raw
messages and Hermes recalled both initial arrangements through shipped Honcho
tools. Observed agent/provider inference cost was $0.006632; native batching made
ingestion take about 32 minutes. This is a small compatibility result, not a
month score. The reply repeated a vague Saturday item, and message-search paths
were attempted and logged; it does not establish clean retention credit.
That earlier canary used the legacy per-directory fallback; it does not prove
fresh-session persistence under the current per-session setup.
Historical evidence is under `artifacts/retired-public-bike-compatibility-20261026/provider-compatibility/honcho-8192-20261025/`.
The current isolated-clock canary is `runs/bike-pickup-native-clock-20261105`.
