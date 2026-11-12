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
2. **Investigate provider compatibility with small canaries.** Supermemory stopped
   before completing the month; OpenViking and ByteRover have no selected current
   month arm. Establish native processing and recall before trying a full month.
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

Live experiments require substantial setup. Read the
[provider map](docs/document-ingestion-provider-map.md) and [runtime instructions](vendor/README)
before provisioning services. Native servers use separate environments from
Hermes' pinned client environment. Record actual revisions, model settings,
clock conditions and every processing deviation from defaults.

All writer, verifier, judge, Hermes and provider inference for this project uses
**Spark Contributor through PACBench's budget gateway** in `pacbench/gateway`.
The shared limit is **$10 per UTC day**, with no lifetime cap. Check the live
ledger before paid work. A daily budget refusal stops live work until the next
UTC day; authentication failures stop it globally until a successful free model
inventory. Never bypass a refusal or erase unknown cost bounds.

When resuming existing work, recover the actual private gateway state, including
its spending ledger and credentials. Gateway source and public request receipts
do not recover spending history. If that state is missing, paid work remains
blocked; do not initialize a replacement ledger to continue the experiment. With
the actual private state restored, start and reconcile the in-repository gateway:

```sh
python -m pacbench.gateway --state /workspace/shared/muse-gateway
python -m pacbench.gateway.reconcile_cli --state /workspace/shared/muse-gateway
```

The client uses `PACBENCH_GATEWAY_STATE` when set, falls back to the legacy
`MUSE_GATEWAY_STATE`, and otherwise uses `/workspace/shared/muse-gateway`. The
existing ledger, unknown cost bounds and authentication circuit remain authoritative.

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
failed native store. Only OpenViking currently has a dedicated pre-recall resume
protocol (`python3 -m pacbench.native_resume <run>`), with strict unchanged-input
and successful-prefix checks.

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
