# Qualifying Cognee, Memobase and Holographic locally

Generate a **setup plan**, not a successful benchmark receipt:

```sh
python -m pacbench.cognee_memobase_setup cognee --root /var/tmp/pacbench-trial --run-id pizza-day-1
python -m pacbench.cognee_memobase_setup memobase --root /var/tmp/pacbench-trial --run-id pizza-day-1
python -m pacbench.cognee_memobase_setup holographic --root /var/tmp/pacbench-trial --run-id pizza-day-1
```

The plan writes nothing and makes no network/model calls. It records pinned
upstream revisions, isolated paths and scope identifiers, configuration,
and the evidence still required. Loopback memory-service URLs are mandatory.
This verifies the memory-service destination only: independently inspect its
extraction, embedding, reranking and telemetry settings. A local server can still
send inference to a hosted service. The benchmark's chosen agent/judge inference
route is a separate declared condition.

## Inclusion cutoff

The benchmark imports emails/message threads as text documents. A provider's
supported raw-document ingestion is sufficient; it does not also need a native
chat importer. Conversely, installing a plugin, storing an inert blob or accepting
agent-written facts is insufficient. Exclude a provider that cannot process these
documents rather than creating fake conversation turns or seeding extracted facts.
The planner marks Cognee as a document candidate and Holographic/Memobase as
excluded under the inspected versions. This is an ingestion-capability decision,
not a zero score or claim about their other use cases.

## Cognee

Source: [official Hermes plugin](https://github.com/topoteretes/cognee-integrations/tree/a176248914fdb32a78354854b300174203b15a5c/integrations/hermes-agent).
The inspected plugin requires `cognee==1.6.6` in an isolated runtime because its
requirements conflict with Hermes's. Follow that version's local setup, not the
default remote wizard choice. Pin a unique dataset, data root, system root and
server port. Clear inherited remote URL/key settings; select the memory provider,
not merely plugin enablement. Record actual loaded plugin and backend revisions.

The native plugin captures typed session entries, then promotes them into the
knowledge graph at session end. A detached worker can outlive Hermes. `remember`
returning `running` means accepted, not graph-ready. For raw nightly documents,
the shipped `HttpBackend.remember_permanent` sends the unmodified UTF-8 document
as a content-hash-named multipart upload to `/api/v1/remember`, with
`run_in_background=true`. Use this native path rather than extracting and seeding
facts. Before recall, witness the **cognify** pipeline for the isolated dataset,
verify stored raw document bytes, and capture actual injected context from saved
Hermes input. A different dataset's completion cannot qualify the arm. Do not
substitute `code_graph_pipeline` status for document processing.

Keep native prompts/tools, including its memory-steering policy, disclosed.
Do not silently turn off steering to make it resemble other providers. The
backend's default automatic dataset routing must not permit another trial's data.

## Memobase

Sources: [native plugin](https://github.com/fred0m/hermes-memobase-provider/tree/4e8e447966acefaea372395e2db96ca0d0127f18),
[self-hosted server](https://github.com/memodb-io/memobase).
Provision isolated PostgreSQL/pgvector, Redis and API services with private data
volumes. The plugin's `MEMOBASE_BASE_URL` includes `/api/v1`; its bearer token is
for the local service, not a required cloud subscription. Use an isolated
`MEMOBASE_USER_NAME` (the plugin derives a stable UUID from it), Eastern timezone,
and verify every inference endpoint. Do not export credentials in run artifacts.

Native `sync_turn` writes a **paired user/assistant chat** to the buffer. It
truncates each side to 16,000 characters, waits at most five seconds for a prior
writer, and may skip a turn if that writer is still busy. These are native
behaviors that must be observed, not hidden by a successful process exit.
`on_session_end` flushes the chat buffer with `wait_process=true`; verify its
result and all relevant source IDs before fresh-session recall. Tiny Pizza Day
may never reach the automatic 1,024-token threshold.

**Raw document ingestion is not yet qualified.** The inspected server models
accept `doc`, but the processing dispatcher supports only `chat` and `summary`.
An accepted doc therefore does not prove profile/event extraction. Do not create
fake assistant replies, convert authored expectations into summaries, or silently
relabel all third-party speakers as the owner to satisfy the paired-turn plugin.
Under the document-ingestion cutoff, exclude this inspected version rather than
creating a special chat-import condition merely to keep it on the scorecard. A
future supported document extraction path could qualify after independent review.

## Holographic

Source: [maintained plugin](https://github.com/itpartypattaya/hermes-plugin-holographic/tree/05e890b461dc3e6390982cb21c5b471a6ed3d8b4).
This is a local SQLite fact store with native agent tools. Embeddings and
reranking are optional and off by default. Keep them off for the local baseline.
`auto_extract` is also **off by default**, not a document-ingestion engine.
The opt-in extractor selects user sentences matching preference/decision rules;
it is not a general multi-person raw-transcript importer.

The historical Hermes pin in PACBench bundles an older provider named
`holographic`; bundled discovery takes precedence over a same-named standalone
plugin. Installing the maintained plugin does not prove it is the loaded one.
Require its actual module path and revision, and choose an appropriate declared
Hermes version rather than patching discovery or vendor code.

**Do not seed `fact_store` with authored facts to manufacture Pizza Day support.**
The current document benchmark correctly blocks this provider until a faithful
native source-ingestion path exists. Exclude it from this raw-document benchmark. A separate conversational-exposure
experiment is outside the current goal and must not substitute for document
ingestion. Empty memory under default capture is not evidence
that its retrieval algorithm failed.

## Qualification gate

For each provider retain source hashes, actual loaded versions, local storage
scope, all inference endpoints, accepted-write IDs and native processing
completion, fresh-session saved model inputs, exact tool calls/results,
failures, timing and deferred work. An installed plugin, HTTP 200 or correct answer
alone does not prove faithful integration. Keep unsupported ingestion, execution
failures, source-assisted answers and memory-retention results distinct.

`provider_adapters.cognee_local.qualify_document` provides a narrow canary using
the pinned plugin's actual `HttpBackend` interface. It requires a genuinely fresh
isolated dataset, uploads source once through `remember_permanent`, observes the
native cognify completion state and reads back the exact raw source. The active
runner also supports `qualify_incremental_document`: it verifies every prior
source ID/hash, submits the new raw document once, and reads the native
`datasets/{id}/processing-status?pipeline=cognify_pipeline` endpoint until the
new item's own completion stamp is present. An older dataset-level completion
alone is insufficient. It does not
retry uncertain writes and does not call an accepted write completed. A successful
receipt explicitly says fresh-session recall is **not run** and the provider is
**not qualified** until its native Hermes input/tool trace is checked. This helper
is wired into the native direct runner; eligibility still requires a separately
audited fresh-session context/tool trace. The standalone single-document canary
retains its original protocol.
