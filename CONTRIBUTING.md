# Contributing to PACBench

PACBench is still developing its scenarios, provider integrations and evaluation
method. Contributions should make experiments easier to inspect and reproduce,
while preserving realistic source ingestion and honest reporting of failures.
The same rules apply to human contributors and coding agents; agents should also
read [AGENTS.md](AGENTS.md).

## Start with the saved evidence

Use Python 3.11 or newer from the repository root:

```sh
python3 serve_explorer.py
python3 -m pacbench.specs
```

The explorer displays the saved runs. The spec command validates all files in
`specs/` without generating messages, writing artifacts or calling a model.
Neither command needs provider services or model credentials.

The reusable month preparation is
[`runs/pizza-month-local-20261105/preparation/week.json`](runs/pizza-month-local-20261105/preparation/week.json).
The preparation contains 2,868 messages across 48 scenes, with 30 source days and 15 final
questions. `week.json` is a hash manifest used for any duration: keep its sibling
files, including shared text, together. Reuse this preparation without new writer
or verifier calls. Its simulated dates stay fixed regardless of the host date.

The [month run](runs/pizza-month-local-20261105) is incomplete. Its failed native
audit, ungraded judgments and ingestion blockers are part of the evidence.
The [Bike Pickup canary](runs/bike-pickup-native-clock-20261105) is a smaller
Honcho clock and persistence diagnostic, not a substitute for the month.
Private provider homes and stores are not included in Git; public receipts alone
cannot reconstruct them or support full native re-auditing.

## Next steps

Work on small, inspectable changes before expanding the benchmark:

1. **Improve judging reliability.** Some month answers remain ungraded after
   malformed judge JSON and the permitted retry. Use controlled correct and wrong
   answers to test changes. Version any new judging condition and preserve the
   original grades and failed attempts.
   Experimental `semantic-judge-compact-v3` keeps the date-scoped grading
   criteria but omits the evidence diagnostic from that request and marks it
   ungraded. It is not the native default. Offline controls verify parsing and
   preservation only; live correct and wrong controls are required before use.
2. **Investigate provider compatibility with small canaries.** Supermemory stopped
   before completing the month; OpenViking has no selected current month arm.
   ByteRover is retired; preserve its historical traces without new execution.
   Establish native processing and recall before trying a full month.
   Prior error labels alone do not establish a cause or a fix.
3. **Keep Honcho's processing failures separate from recall.** Its selected month
   attempt failed structured-output processing before questions. The small clock
   canary completed, but does not resolve the larger workload. Do not repeat the
   failing month or increase output budgets without new evidence and review.
4. **Make evidence easier to inspect and publish.** Improve explorer navigation
   and portable, immutable references without losing exact source, tool or judge
   records. Keep private state and credentials out of published artifacts.
5. **Broaden scenarios after the execution path is reliable.** Review source
   ambiguity and question scope first, then rerun the same frozen month across
   working providers. Expand gradually toward varied personal lives and longer
   histories rather than generating many corpora ahead of validation.

## Run a new experiment

For a fresh environment, run `python3 -m pacbench.bootstrap --install --start-services` from
the repository root, then `python3 -m pacbench.bootstrap --check`. See the
[runtime setup](vendor/README) for prerequisites and a copyable cloud setup
snippet. This provisions pinned local components without paid inference.
Private spending history must be restored separately before any paid run;
installation does not recover the ledger or establish native recall readiness.

Live experiments require substantial setup. Read the
[provider map](docs/document-ingestion-provider-map.md) and [runtime instructions](vendor/README)
before provisioning services. Native servers use separate environments from
Hermes' pinned client environment. Record actual revisions, model settings,
clock conditions and every processing deviation from defaults.

The default writer, verifier, judge, Hermes and provider inference route uses
**Spark Contributor through [muse-gateway](https://github.com/cuuush/muse-gateway)**,
an independent repository normally checked out at `/workspace/muse-gateway`.
The shared limit is **$10 per UTC day**, with no lifetime cap. Check the live
ledger before paid work. A daily budget refusal stops live work until the next
UTC day; authentication failures stop it globally until a successful free model
inventory. Never bypass a refusal or erase unknown cost bounds.

When resuming existing work, recover the actual private gateway state, including
its spending ledger and credentials. Gateway source and public request receipts
do not recover spending history. If that state is missing, paid work remains
blocked; do not initialize a replacement ledger to continue the experiment. With
the actual private state restored, start the in-repository gateway:

```sh
python -m pacbench.gateway --state /workspace/shared/muse-gateway
```

The client uses `PACBENCH_GATEWAY_STATE` when set, falls back to the legacy
`MUSE_GATEWAY_STATE`, and otherwise uses `/workspace/shared/muse-gateway`. The
existing ledger, unknown cost bounds and authentication circuit remain authoritative.

Request evidence retains available input/output, reasoning and cache token
counts, request/generation IDs, model and observed benchmark-stage attribution.
Reported charges, token-price estimates and unconfirmed budget bounds are
separate; reasoning and cached tokens are components of the totals, not extra
tokens to add again. Missing service telemetry stays unknown.

The current inference route is direct Meta. OpenRouter reconciliation applies
only to genuine OpenRouter-origin rows with a generation ID; it cannot supply
charges for direct Meta requests. With an authorized `OPENROUTER_API_KEY`, an
explicit pass against an existing ledger is:

```sh
python -m pacbench.gateway.reconcile_cli --state /workspace/shared/muse-gateway
```

The command records retry status and backoff for unresolved eligible requests.
It does not initialize a ledger or schedule future passes automatically.

An explicit `--inference-route codex-subscription` selects GPT-6 Luna for
preparation or for the native pilot's Hermes and judge calls. Subscription
usage is unpriced and is not represented by gateway receipts. Provider-internal
inference still uses Spark through Muse, so its ledger and access gates remain
required. The native pilot route is experimental and fails before ingestion: each fresh
Hermes profile requires its own native Codex OAuth login, and the pilot does not
yet offer a provision/login/resume handoff. Do not copy the Codex CLI refresh
token into Hermes; the pinned Hermes integration warns that refresh tokens are
single-use. The route does not authorize bypassing a missing provider ledger.
Text-only Codex calls keep SQLite state inside their disposable workspace using
the documented `sqlite_home` setting, without changing the authentication home.
The Codex CLI must still be able to start with its existing native login; a
read-only runtime startup failure is an execution blocker, not a model result.

The experimental OpenRouter planner uses the selected
`deepseek/deepseek-v4.1-flash-1017` model and exact `deepinfra/fp8/flex`
endpoint for Hermes, native extraction and judging. Inspect it without a key,
network access or paid calls:

```sh
python -m pacbench.openrouter_run --plan
# Inspect a selection instead:
python -m pacbench.openrouter_run --plan --providers memcal honcho
```

The current Pizza Day task has a separately approved $20 total OpenRouter budget,
including preparation, diagnostics, all providers and retries. Its persistent
task state is `/workspace/shared/pacbench-openrouter-pizza-day-20261112`.
Preserve that ledger and its uncertain charges across every restart. This is new
authorized OpenRouter accounting; Muse's historical ledger remains missing.

The user retired the local inference proxy. `openrouter_run --plan` reviews
direct HTTPS configuration. Its execution path uses client-side transport hooks
in Python urllib/HTTPX and native JavaScript fetch; no local inference server or
forwarding connection is started. Keep old proxy source and trial evidence for
reproducibility. Prior proxy scores remain provisional.

Each request durably reserves its full-context/output cost bound in the same
task ledger before connecting directly to OpenRouter. Short admission and
settlement locks never cover network work. Interrupted requests retain their
bounds and uncertain outcomes prevent automatic replay. Native clients receive
labelled accounting capabilities; only the transport hooks substitute the real
credential. Requests that bypass those hooks cannot authenticate. Hooks add only
the exact routing/price ceilings and streaming usage reporting, without changing
source processing, prompts or generation limits. Node uses its require preload;
the shipped Supermemory binary loads the hook through native `bunfig.toml`.
A separate key or routing preset is not required by this path.

Run only against audited frozen preparation, with a new output directory:

```sh
python -m pacbench.openrouter_run \
  --prepared artifacts/pizza-day-openrouter-20261112/week.json \
  --output runs/my-direct-pizza-trial --providers memcal --spend-limit-usd 20
```

For the historical Pizza Day archive, prepare fresh review inputs without
changing the sources or copying old approvals:

```sh
python -m pacbench.pizza_day_recovery <historical-preparation-directory>
```

This prints the original messages, a proposed current check and exact contract
and corpus review prompts. Add `--source-only` to emit just the source records
and public question for independent review. The archive's source IDs and timing
differ from the current compiler; the output remains blocked until provenance
compatibility, current reviews and the actual preparation audit are resolved.

Before a Memcal canary, inspect the local prerequisites without inference:

```sh
python3 -m pacbench.preflight \
  --prepared runs/bike-pickup-native-clock-20261105/preparation/week.json \
  --all-providers
```

This checks frozen preparation, the exact Hermes pin and patch, installed client
dependencies, the sibling Memcal checkout, isolated scratch space, driver
configuration and existing gateway files. It does not initialize stores or
recover spending history. The optional all-provider inventory reports installation
paths and additional requirements for all seven integrations; file presence alone
does not establish native readiness or OpenRouter compatibility.
Add `--check-gateway` for a free authenticated health
check after restoring the original state. A passing setup check is not proof of
model access or recall; verify the recovered ledger and free model inventory
before running paid controls. If the configured temporary directory resolves
inside Git, choose a writable `TMPDIR` outside every project; do not disable the
context-isolation check.

Prefer an existing verified preparation. After services and gateway access are
ready, a month trial follows this shape:

```sh
python3 -m pacbench.preparation_audit runs/pizza-month-local-20261105/preparation
python3 -m pacbench.native_pilot \
  --prepared runs/pizza-month-local-20261105/preparation/week.json \
  --providers memcal --max-messages 3000 --output runs/my-month-trial
python3 -m pacbench.native_audit runs/my-month-trial
```

Use a fresh output directory. This example assumes Memcal and its native
dependencies are installed; it is not an installer. Pull current Memcal before
a requested retest and record its actual revision. Do not overwrite frozen
evidence, reingest completed days to probe authentication or blindly reuse a
failed native store. The historical OpenViking pre-recall resume protocol has
strict unchanged-input and successful-prefix checks. Catalog-provider resume
remains blocked until existing-home plugin provenance and recovery are verified;
do not resume an older store under the upgraded integration by relabeling it.

For a new scenario, follow [authoring](docs/authoring-options.md) and
[conversation checks](docs/flexible-checks.md). Validate the spec offline first,
then generate and independently review the corpus once. Freeze questions,
evidence-cited criteria and recovery policy before provider execution. The
current month also requires an independent source-only audit of all 15 semantic
claims. Automated bulk review is advisory, not proof of source quality.

## Preserve the experiment

- Use native import, collection and capture. Do not seed memories, pass answer
  keys to providers, fabricate assistant ingestion turns, patch third-party
  extraction code or force queues to complete.
- Preserve thread membership, sender identity, bodies and source/collection times.
  API chunk limits require ordered delivery within the same conversation.
- Wait for actual native processing. An accepted write or completed counter alone
  may not establish readiness. Failed readiness withholds recall.
- Record documented configuration explicitly. Mem0's reasoning-model flag,
  Supermemory's instant dreaming and Honcho's configured deriver allowance are
  distinct conditions. Preserve native batching and do not silently raise limits.
- Keep semantic grades, retention credit, source provenance, compatibility errors
  and ungraded execution outcomes separate. Preserve original judgments when
  testing a separately versioned corrective review.
- Account for deferred provider work and unknown costs. Removing an obsolete
  development artifact never removes its charges from the gateway ledger.

## Validate changes

Install `pytest` in your development environment to run the Python suite; Node.js
is needed for the JavaScript syntax checks. Run checks appropriate to your change:

```sh
python3 -m pytest -q
node --check explorer/dashboard.js
node --check explorer/timeline.js
python3 -m pacbench.preparation_audit runs/pizza-month-local-20261105/preparation
```

Native/live tests need their declared optional services. Some harness tests need
loopback networking and a writable temporary directory outside Git;
`TMPDIR=/var/tmp` works in the managed Linux environment. Report skipped checks
and missing prerequisites. Passing offline tests establishes harness integrity,
not provider accuracy.

## Submit a change

Explain the problem, resulting behavior and relevant validation in the pull
request. For evaluation changes, identify the affected protocol and demonstrate
plausible correct and incorrect controls. Report limitations and uncertainty
instead of assigning causes that the evidence cannot establish.

Benchmark, explorer and gateway changes belong here. Memcal product fixes belong
in [Memcal](https://github.com/cuuush/memcal). Do not modify third-party provider
code to rescue a benchmark result.

Keep operating instructions in the existing docs and run evidence in
machine-readable artifacts. Include only selected public trials and necessary
failure evidence. Before publishing, inspect staged files for credentials and
private runtime data; preserve unrelated files and the private spending ledger.
