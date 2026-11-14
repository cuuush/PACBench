# Local memory ingestion providers

Eligible systems have a practical documented way to ingest daily raw text locally. Real message capture qualifies alongside document import. Collect new messages, preserve their metadata and text, submit them through the native interface, and wait for processing. Hermes only receives later questions through its selected provider's normal context and tools.

Mem0 OSS 2.2.1 can recover a failed batch embedding by embedding extracted items
individually. A per-item failure logs `Failed to embed memory text:` at WARNING
(the async path adds `(async)`) and skips that item, potentially returning an
empty or partial successful result. The import observer records these exact
native warnings as `native_per_item_embedding_failed` and refuses readiness;
generic warnings and a successful fallback are not failures. No expected fact
count is imposed, and legitimate empty extraction is still allowed. Preserve
the returned result and warning receipt; do not blindly replay a partially
persisted add. This observes native failure, not extraction accuracy.

Honcho's 100-message bound is per API request, not per conversation. A nightly collector groups messages by their real thread, opens or reuses that thread's native session, enrolls the configured observer before submission, and sends ordered requests of at most 100 messages. A 300-message thread uses three requests in the same session; it does not become three conversations. Native processing decides its own extraction batches. This connector-side pagination needs no server code changes or raised limit. Delivery acknowledgments prove transport, not completed memory processing.

Use the provider's native API and documented configuration. Run-owned stores, local embeddings and gateway transport are setup; processing choices such as Supermemory's `dreaming=instant`, Mem0's reasoning-model flag and Honcho's deriver allowance must remain explicit. None authorizes changing provider prompts, patching extraction, forcing queue completion or splitting out known answer facts. Local Honcho also needs PostgreSQL and its API/deriver services; this is self-hosted integration work, not a claim of a five-minute consumer iMessage connector. The benchmark's synthetic clock is test infrastructure, separate from the production collector.

The [Hermes memory documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory-providers.md) was checked against pinned integrations on 10 October 2026. Memcal is an additional project requirement.

| Provider | Native daily delivery | Local setup / current status |
| --- | --- | --- |
| Mem0 | `Memory.add(raw_transcript, infer=True)` | OSS SDK, local Qdrant, Ollama embeddings and gateway inference. Set the documented `is_reasoning_model: true` flag for Spark and the selected DeepSeek reasoning model; the SDK then chooses its native reasoning-model parameters. No custom output budget or reasoning effort. The earlier legacy-chat classification was a setup error; larger-output trials remain a different configuration. |
| Hindsight | Retain raw text with source time and document ID; wait for consolidation | Documented `local_external` backend and isolated bank; 1.2.1 server/client on the exact selected direct OpenRouter route. New-version native processing and recall require qualification. |
| OpenViking | Upload then `resources(wait=True)`; check semantic/vector completion | Local server/store and Ollama embeddings. Native resource processing is distinct from interactive memory capture. Expose `viking_read`; original-source access earns no retention credit. Check model-health receipts independently of native completion. |
| Supermemory | `documents.add(dreaming="instant")` with raw content, canonical container tags and custom ID; wait for indexing and memory consolidation | Published local server and native SDK, Ollama embeddings. Use the native namespace resolver. Explicit instant mode processes each already-batched daily document instead of default dynamic grouping across documents. The October 23 native canary completed extraction and populated the profile. The mode is recorded in setup and ingestion receipts; earlier dynamic-mode blockers are separate conditions. |
| Honcho | Real source peers, original timestamps and native configured recall observer through `session.add_messages` | Documented pgvector/API/deriver self-hosting, native server 3.6.1 and SDK 2.9.0. Keep native model/output defaults for the selected DeepSeek route; historical Spark overrides are a different condition. Retain native batching and directional observation for the owner and non-speaking AI observer before messages. Require actual work enrollment/completion, including captured assistant-session instructions before fresh-session checks. Original-message fallback earns no retention credit. |
| Memcal | Native Messages SQLite and TLS IMAP readers, separate contacts, normal nightly processing | Provider-independent Apple schema and RFC 5322 fixtures; nightly processing at 03:00 scenario-local time. Version 0.8.2 with product fixes on `main`. |
| ReMe | Publish the complete raw transcript under native `resource/`; default `auto_resource` watcher extracts a source-linked daily card | Isolated local Application/workspace and native BM25 indexing. Observe the actual resource action, generated card and indexed chunk IDs. Hermes exposes automatic prefetch only; no invented query tool. |
| Cognee | Shipped `HttpBackend.remember_permanent`: raw multipart upload to native `/api/v1/remember`, which runs add and cognify | Local 1.6.8 server, isolated dataset and native Ollama embeddings. Wait for actual document extraction and exact raw-source readback. Incremental loads additionally require this new item's cognify completion stamp, not an earlier dataset status. Native first-turn prefetch can be empty because retrieval is queued after a preceding turn. |
| MemOS | Native `/api/v1/import?ownership=target` with the raw document as trace text and summary, empty assistant text | Default raw/lexical condition requires exact API readback; native import leaves vectors null. Separate opt-in `native-raw-document-semantic-ready-v1` runs bounded native embedding repair and per-ID vector verification. Both conditions do no fact extraction; original-document retrieval remains source-assisted. See [condition details](reme-memos-local.md). |
| Holographic | Fact storage and regex extraction | Excluded: no practical general automatic source ingestion. |
| Memobase | Inspected native dispatcher processes chat/summary, not accepted raw-document blocks | Excluded at the inspected pin. Do not fabricate assistant turns to bypass document eligibility. |
| RetainDB | Hosted storage/import | Excluded: Hermes documents no verified local backend. |
| Memori | Documented Hermes plugin uses cloud service | Excluded: no verified local Hermes integration for this condition. |

ByteRover is retired from active execution at the user's direction. Its saved
CLI curation, retrieval and timeout traces remain historical evidence; adapter
and audit code is retained only to interpret those records. No new ByteRover
trials or hosted alternative are part of the lineup.

Every connector uses the same frozen public message records. Daily document v3 is a readable chronological transcript: conversation/channel, roster and subject headers, written times, senders, ownership and every original body, including email quotes. Different delivery/capture times remain visible. Full canonical records and IDs remain in `messages.json` for audits and native fixtures; repetitive machine IDs and identical metadata do not inflate the LLM's text. v1/v2 JSON documents retain their original audit rules. No messages are selected, summarized or extracted by the connector.

New direct OpenRouter runs require `native-question-v2`: send the authored
question verbatim and add no PACBench text to Hermes's user or system prompts.
The scenario date comes from Hermes's native clock seam. An HTTP-boundary
observer records actual system/user inputs and verifies the native date line,
unchanged query and injected context. A pinned native session-title request is
auxiliary and charged, and cannot prove answer-context delivery. The semantic
judge separately retains the explicit scenario date. Historical
`user-clock-prefix-v1` and `ephemeral-system-clock-v2` trials retain their original
conditions and cannot execute or resume as current trials. Never silently
regrade those trials as native-question runs.

Direct harness authoring and judging use a real 180-second total HTTP deadline,
including non-streaming whitespace keepalives; an interrupted outcome retains
its cost bound and the identical request is not automatically repeated. A
different question can proceed only if its full reservation still fits alongside
every unknown hold and the external allocation. An older unknown outcome without
a request fingerprint blocks that entire project. The source writer explicitly
uses low reasoning. `--judge-reasoning-effort low` is an independently recorded
benchmark-judge condition, bound by the native audit to actual request records.
It changes neither Hermes nor memory-provider model settings. Scoring sanity
controls must cover correct and wrong answers plus valid evidence diagnostics;
passing fact grades alone is insufficient calibration.

Memcal's source database preserves realistic GUIDs, Apple-epoch timestamps, sender handles, service fields, chat membership and joins. Names live in a separate contacts fixture. No provider-specific facts or extraction hints are inserted. Future iMessage connectors can use that same source fixture. Nightly worker deadlines normally allow 15 minutes for a multi-conversation batch. Hindsight keeps the 15-minute retain/consolidation ceiling, plus 30 seconds to save its final processing receipt. For the selected DeepSeek route its documented `HINDSIGHT_API_LLM_TIMEOUT` is 300 seconds: the native 120-second request deadline cancelled active generations and triggered repeated retries. This changes only the per-request wait; native consolidation, output limits and retry count remain unchanged. Actual successful consolidation is still mandatory. All inference remains under the shared task spending cap.

Honcho's explicit `--honcho-clock simulated` profile advances PostgreSQL,
API, deriver and Hermes wall clocks between daily bulk imports. The upgraded
`honcho-wall-clock-v5` condition retains the split clock: PostgreSQL alone uses
the reproducible runtime built by `vendor/libfaketime/install-runtime-v4.sh`;
Python services and the native Hermes gateway/CLI use a pinned shared-offset
`time-machine` runtime. OS certificate validation, operational deadlines and
monotonic timers remain real. A free direct HTTPS check passed; applying the
preload library to the HTTPS client instead failed certificate validity checks.
Actual gateway and CLI adoption is bound to distinct PIDs, process start ticks
and a per-turn nonce. Its declared `forward-only-natural-startup-prelude-v1`
condition stages at most 60 seconds before the authored checkpoint, then leaves
the offset unchanged while the native gateway and CLI run. Native startup hooks
and caches remain intact. The client waits naturally until the checkpoint;
actual client submit, gateway receipt, in-transaction admission and committed
acceptance must fit the original five-second window and precede the next
checkpoint. A delayed committed acceptance is retained as an execution failure,
never relabeled as an unsent request. Natural recovery uses its actual acceptance
time, with the original local-date and next-checkpoint restrictions. Gateway
lifecycle exclusion and surviving-consumer checks refuse clock mutations while
Hermes is alive. These are transport checks; provider readiness and live
recall still require validation. Older v1–v4 receipts keep their original runtime
identity and cannot resume as v5. Native batching defaults remain unchanged;
actual processing witnesses and future-checkpoint guards remain required.

The shipped Honcho SDK has a 100-message request limit. A daily thread with more messages uses consecutive native requests in its existing observed session, followed by one processing barrier for the complete import. This is transport batching; no text is summarized or extracted by the connector. Atomic, hash-bound chunk receipts retain verified native IDs and mark uncertain delivery without automatic replay.

Clocks v3–v5 attempt to suspend the native runtime during benchmark judging only at an observed idle boundary, excluding that harness overhead from provider wall time. Database/queue and gateway snapshots cannot prove arbitrary in-memory quiescence; monotonic deadlines still elapse. Active observed work proceeds normally. A separate real-clock watchdog stops services if the owner is killed during suspension. No frozen absolute-time mode is used: its native sleep compatibility probe failed. Native trace dates on the simulated clock cannot establish timing against the gateway's real timestamps, so uncertain source paths remain uncredited.

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
If a document-status request fails, retain its accepted ID and earlier status
observations, withhold readiness and do not resubmit. Submission failures without
a definitive rejection remain uncertain. Public failure receipts keep safe error
classes, status codes and hashes, not native exception bodies or worker streams.

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

New Honcho document runs declare `honcho-chronological-membership-v2` separately
from their processing-clock profile. Native perspective search filters messages
by the session peer's actual `joined_at`/`left_at`, so joining just before a bulk
upload does not establish visibility of older message timestamps. The adapter
requires an explicit, static source roster including the owner, enrolls those
members and the configured silent Hermes observer through `Session.add_peers`,
and reads actual membership windows before advancing to nightly import. It
rechecks those windows immediately before source submission. It never backdates
membership rows, substitutes collection time for message time, or inserts facts. Version 2
uses the shipped native peer-ID encoding convention for names containing spaces
or punctuation (for example, `Alex Reed` becomes `Alex-Reed`). Original source
sender and roster metadata remain unchanged. Enrollment records the mapping;
collisions between distinct names, including across batches, stop before native
writes. Historical v1 receipts keep their original raw-ID audit semantics.

The simulated profile now starts before the earliest source event so this
enrollment can happen chronologically. Missing membership after that event,
roster transitions, changed source bindings and left/rejoined peers stop source
delivery. Existing active memberships can be reused only while their native
windows still cover the source. Real-clock historical bulk imports into a fresh
store cannot establish those windows with the inspected native API and remain
unsupported under this new qualification. Older saved trials retain their
original condition and findings.

Historical clock v4 separates the Python wall clock from the OS clock. PostgreSQL retains
the pinned libfaketime runtime; the API, deriver and Hermes use the pinned
`time-machine` runtime installed with `vendor/python-clock/install-runtime.py`.
Each Python time read uses the shared atomic offset file. OpenSSL certificate
validation and monotonic timers retain real time. Package hashes and actual
service/turn PID adoption are audited. Older whole-process clock trials retain
their HTTPS failures; TLS verification remains enabled in every condition.

The fresh `runs/pizza-day-honcho-clock4-embedding-ready-20261112` development
trial completed native extraction and injected the pizza plan into Hermes's
actual model input. Both native tool calls succeeded and all five claims graded
correct. Native source search was also used, so retention credit remains zero
under the frozen source penalty. Its explicit deriver thinking effort is `none`;
the native 2,500-token output allowance and batching defaults are unchanged.
This establishes this small workload's integration, not calibrated accuracy.

Some managed Docker runtimes lack `cgroup.freeze`. The split-clock v4 and v5 protocols allow an
explicitly recorded fallback only after the native pause reports unsupported
freezing and an independent inspect confirms the container is running and
unpaused. Resume the apps and leave the shared offset unchanged. Record real,
provider and DB timestamps plus the unchanged offset hash in
`provider-clock-running-overhead.jsonl`. This is unexcluded judging time, not a
suspension. Other pause failures remain fatal. The scripted checkpoint guard
still refuses future-time exposure, so long judging can make later checks
unavailable; this condition cannot establish equivalent wall-time aging.

A saved answer that failed before its first judge request can receive a
separately versioned first-judgment review. Bind the original source, contract,
clock, answer, visible evidence, tool provenance and actual judge receipt.
Preserve the original ungraded execution and do not claim recovery ran. This
does not authorize replaying a judge request with an unknown outcome.

An earlier v4 trial found the local embedding service unavailable and stopped
without recall after 900 real seconds. Honcho's immediate-embedding lease has a
native ten-minute retry backoff, checked on a five-minute reconciler cadence.
Its reconciliation was enqueued shortly before the harness deadline; the failed
trial is preserved. New Honcho setup checks an actual local embedding before
starting model work. The successful fresh trial did not reuse that failed store.

The native completion counter also includes terminally errored queue items.
The local runner observes the run-owned PostgreSQL queue without modifying it,
stops on submitted-source errors and saves their classes/hashes. A cleared queue
alone must not turn failed extraction into successful ingestion. Errors without
session attribution also block readiness. Known unrelated errors remain separate
in the evidence; an empty list of submitted sessions never proves completion.


For Spark on Honcho we explicitly set the native per-model deriver allowance:

```sh
DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS=8192 python -m pacbench.native_pilot \
  --prepared runs/<prepared>/week.json --providers honcho --output runs/<new-run>
```

This is Honcho's standard environment configuration, equivalent to
`max_output_tokens = 8192` under `[deriver.model_config]` in its
[shipped configuration example](https://github.com/plastic-labs/honcho/blob/5314d0a8635f02375b8ad73c89eb80f86d7d1cb4/config.toml.example#L123).
That historical Spark trial ran server 3.4.1 and Python SDK 2.2.0, configured the OpenAI-compatible transport
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


For the current Supermemory v5 actor integration, published local server-v0.0.9
omits consolidation status from its v5 document read. PACBench observes the
native v3 read endpoint for the same public/native document ID, namespace, exact
content and metadata. A source-bound `supermemory-native-v3-completion-observer-v1`
receipt is required before crediting completed dreaming. This is read-only API
compatibility instrumentation; the v5 source write, shipped plugin, native
processing and current SDK remain unchanged.

The Honcho overhead check reads accounting without waiting while native apps are
suspended. A busy accounting lock means the apps resume and native time keeps
running during judging, using the existing recorded fallback. It never waits on
a lock whose owner it suspended or treats unavailable accounting as idle.
