# ReMe and MemOS local qualification

These are **not yet qualified Pizza Day arms**. Configuration, loader and health
checks do not establish extraction, completed persistence, or recall quality.
No benchmark inference was run while adding this setup support.

## Pinned sources

| Provider | Repository | Revision |
| --- | --- | --- |
| ReMe | `https://github.com/agentscope-ai/ReMe.git` | `36c23674ad8c599dae20b0687af40acd8ecb990e` |
| MemOS local plugin | official npm `@memtensor/memos-local-plugin` | `2.0.21`; verified archive in `vendor/MEMOS_LOCAL_PIN.json` |

ReMe's plugin manifest declares Hermes `>=0.21`. Verify the selected Hermes
runtime rather than changing the benchmark's pin silently. Both source plugins
loaded through the current pinned Hermes loader during qualification.

## Preparing isolated local settings

After installing the official sources and dependencies into isolated runtimes:

```sh
python -m pacbench.reme_memos_setup reme /absolute/new-run-root /path/to/ReMe \
  --endpoint http://127.0.0.1:11434/v1 --model your-installed-local-model
python -m pacbench.reme_memos_setup memos /absolute/another-new-run-root /path/to/memos-local-plugin/package \
  --endpoint http://127.0.0.1:11434/v1 --model your-installed-local-model
```

The root must not already exist. The helper verifies the ReMe source commit or official MemOS archive and shipped files, links the
native plugin into a run-owned Hermes home, and writes private configuration.
`runtime.json` contains process environment settings, not credentials. Apply them
to a clean process environment; do not inherit unrelated provider configuration
or credentials. The helper permits only literal loopback/localhost endpoints.
It neither installs software nor makes a model request.

ReMe uses embedded mode and the default native pipeline, with a local
OpenAI-compatible extraction endpoint. Default BM25 search needs no embedding
service. It still needs `reme-ai[core]` dependencies and a real local model.
The placeholder key is a constant, not an account credential.

MemOS uses `openai_compatible` with a local endpoint and empty API key.
**`local_only` is not a local model backend:** it always raises `LLM_UNAVAILABLE`.
Host fallback, cloud hub and telemetry are disabled. Embeddings remain the native
local transformers implementation, set offline after its model assets have been
downloaded and verified. A `core.health` result with `available: true` but
`lastOkAt: null` is not evidence that either model actually ran.

The MemOS Node bridge must retain its package tree: the Python adapter resolves
`bridge.mts`, dependencies and assets relative to its original package. Do not
copy only its Python files into a new directory. Native Node addons may require
an explicit build. For restricted writable homes, set npm's cache and node-gyp
development directory to run-owned writable locations; do not change privileges.

## Ingestion and lifecycle requirements

The active ReMe benchmark uses the default native `auto_resource` watcher:
publish the exact transcript under `resource/<source-day>/`, then observe the
native action's source-linked generated card and actual indexed chunks. It does
not seed Markdown summaries, manually invoke extraction, or use `reindex` to
discover new files. Direct `auto_memory` below remains a separate standalone
qualification path.

ReMe's HTTP/native `auto_memory` action accepts a message list and stable request
ID. Use complete raw chronological source with original authors/times, without
fact hints, extracted summaries or invented assistant replies. Preserve native
default extraction; verify successful completion and durable workspace files.
Its Hermes adapter queues actual completed agent turns in a FIFO in memory.
Orderly shutdown drains that queue within its default budget; crashes can lose
queued work. Its automatic `prefetch` uses a five-second native foreground limit
and returns no model-visible search tool. A failed lookup can be empty context.

MemOS maps `prefetch` to `turn.start`, `sync_turn` to `turn.end`, and session end
to `session.close` on one shared Node bridge. Its native tools also expose memory
search/timeline. Verify synchronous trace persistence and any ensuing extraction
before dependent recall; `sync_turn` itself catches errors and can return after a
warning, so a hook invocation alone is not readiness.

MemOS's documented `POST /api/v1/import` can accept raw document traces. Its
native OpenClaw importer similarly copies original text into a trace's `summary`
field without model extraction. Chris explicitly approved raw text document
ingestion. The connector uses this public endpoint, preserves the full frozen
daily document verbatim in `userText` and `summary`, and leaves `agentText` empty.
It does not fabricate an assistant turn or precompute scored facts. This is a
separate `native-raw-document-import-v1` condition, not extracted-memory ingestion.
Any retrieval of these raw documents is source-assisted and earns no retention
credit under the current frozen source policy. Do not merge these results with a
normal agent-capture condition or claim extraction ran.

The standalone native document connector can be invoked after starting the
official service and preparing a frozen source directory:

```sh
python -m provider_adapters.reme_memos_documents reme /path/to/sources/YYYY-MM-DD \
  --endpoint http://127.0.0.1:3456
python -m provider_adapters.reme_memos_documents memos /path/to/sources/YYYY-MM-DD \
  --endpoint http://127.0.0.1:18800
```

It verifies `messages.txt` against `source.json` before any write. ReMe uses a
stable native request ID and `auto_memory` to extract from one complete raw
document. MemOS requires a native import acknowledgment followed by exact
API readback. Neither connector blindly retries a possibly executed request.

The default `native-raw-document-import-v1` remains the original raw/lexical
condition. MemOS 2.0.21's importer writes **null** embedding columns; merely
configuring a local embedder does not embed imported rows. Preserve those
historical results and stores. A separate, opt-in
`native-raw-document-semantic-ready-v1` condition uses the pinned package's
documented `POST /api/v1/embeddings/rebuild` with `{"mode":"repair","limit":100}`.
It is bounded by 50 batches and 120 seconds by default, rejects no-progress,
errors and a misleading `done` with remaining repair slots, and verifies fresh
maintenance counters, unchanged trace identity, and finite, nonzero,
dimension-correct vector BLOBs for the exact submitted trace ID through a
read-only native SQLite observation. A timeout preserves an uncertain receipt;
it never reimports or blindly repeats a timed-out write.

For a fresh scored arm, explicitly set
`PACBENCH_MEMOS_INGEST_CONDITION=native-raw-document-semantic-ready-v1` before
using the reviewed cohort launcher. Setup and cohort receipts record it;
retained-store resume rejects changing the condition. The standalone equivalent
adds these arguments to the MemOS command above:

```sh
--memos-condition native-raw-document-semantic-ready-v1 \
  --memos-database /path/to/run/memos-home/data/memos.db
```

Repair fills `vec_summary` from the **raw document**, and `vec_action` from the
native `"(empty)"` action placeholder for these non-lightweight imports with no
assistant text/tools. It can also repair vectors of existing policies, world
models and skills; it creates none of those records or extracted facts. Very
short traces excluded by native embedding eligibility cannot earn this
semantic-ready claim. Source-assisted retrieval still receives zero retention
credit. Keep semantic-ready results separate from raw-only results, including
their local embedding time and any other explicitly selected inference settings.

The opt-in native integration regression runs with
`PACBENCH_TEST_NATIVE_MEMOS=1`: it uses a disposable store, cached local MiniLM,
an allowlisted environment without account credentials, and a rejecting local
LLM endpoint. It checks both vector columns for the exact ID, unchanged record
counts/text and zero LLM requests.

The active runner registers both providers and records the actually loaded
native plugin source hash. Every arm still needs a fresh-session Hermes input
and tool audit before it can be called working. ReMe's prefetch-only native
provider has an intentionally empty model tool allowlist.

## Checks already run and remaining gate

On isolated synthetic homes, with no paid requests:

- ReMe's upstream Hermes integration suite: **36 passed**.
- MemOS's upstream Python adapter suite: **143 passed, 13 subtests passed**.
- Both providers loaded through Hermes's real plugin loader.
- A real ReMe Application started and its health action reported healthy.
- A real MemOS Node bridge created its SQLite store and answered `core.health`.
- A real MemOS HTTP server accepted one synthetic raw document through its import
  API, returned it exactly through trace readback, and returned its text in native
  search `injectedContext`. Native embedding failed because offline model assets
  were absent; the observed result used keyword fallback. This is not a successful
  embedding test or a Pizza Day score. Native import's neutral `value`, `alpha`
  and `priority` of `0.5` are preserved: leaving them at generic import defaults
  of zero caused the initial trace to be filtered out of retrieval.
- PACBench setup helper tests verify isolation, pin refusal, no overwrite,
  loopback-only configuration and disabled cloud fallback. Twelve setup/connector
  checks pass, including raw-source preservation, native readback and no retries.

Still required: real configured extraction/embedding calls, native source delivery,
completion witnesses, fresh-session prefetch/model-input capture, reasonable tool
traces, and unchanged Pizza Day grading. Pin all dependencies, retain failures and
timings, and distinguish integration failure from memory quality. The current
executor has no running local model or verified cached embedding assets. Perform
the live canary in the selected benchmark runtime after budget coordination.

Sources: [ReMe Hermes docs](https://reme.agentscope.io/en/integrations/hermes),
[MemOS adapter](https://github.com/MemTensor/MemOS/blob/3a16e5fe3048a209f722806f4123fa2e533588fd/apps/memos-local-plugin/adapters/hermes/README.md),
[MemOS LLM layer](https://github.com/MemTensor/MemOS/blob/3a16e5fe3048a209f722806f4123fa2e533588fd/apps/memos-local-plugin/core/llm/README.md).
