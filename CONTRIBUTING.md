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
python3 -m pacbench specs
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

New studies use the public OpenRouter workflow. Run `./scripts/setup` once,
export `OPENROUTER_API_KEY` and choose a budget for the command. To inspect
the local runtime after installation, use `./scripts/setup --check`.
[Run Pizza Week from a fresh clone](README.md#run-pizza-week-with-openrouter)

Setup provisions the shared catalog once. Each trial receives its own Hermes
home, provider data and receipts. `HERMES_RUNTIME_DIR` selects the package
manager tool store used during setup.

Live experiments require substantial setup. Read the
[provider map](docs/document-ingestion-provider-map.md) and [runtime instructions](vendor/README)
before provisioning services. Native servers use separate environments from
Hermes' pinned client environment. Record actual revisions, model settings,
clock conditions and every processing deviation from defaults.

### Existing runs and historical campaigns

For a configured Muse run, use the original shared gateway state. Start its
listener with `python -m pacbench gateway serve --state /workspace/shared/muse-gateway`.
Inspect an existing ledger with `python -m pacbench gateway reconcile --help`.
The original ledger and credentials are required to resume that spending history.

Use the current checkout for offline evidence review:

```sh
python -m pacbench audit prepared runs/pizza-month-local-20261105/preparation
python -m pacbench audit r2
python -m pacbench paired inventory
python -m pacbench regrade --help
python -m pacbench batch --help
```

The dated Pizza Day and Week launchers, direct receipt journal writers, and
runtime-cleanup leases belong to the source revisions that issued them. For
reproduction or restoration, use the original authorization, private state, and
[archived procedures](https://github.com/cuuush/PACBench/blob/6582fd200c5e270b2b68d59175c7e462e95c1a7f/CONTRIBUTING.md#continuing-a-historical-campaign).
For an interrupted OpenViking run, `python -m pacbench resume --help` describes the supported pre-recall recovery.

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

## Inspect receipt costs offline

`python -m pacbench costs` reads retained evidence without opening credentials,
calling providers or changing a ledger. For example, choose a **new** output path:

```sh
python -m pacbench costs \
  --snapshots runs/pizza-month-local-20261105/repeat-1/mem0/model-usage.json \
  --components runs/pizza-month-local-20261105 \
  --output /tmp/pacbench-mem0-receipt-costs.json
```

Use `--ledger <retained-direct-ledger.json>` instead of `--snapshots` for an offline
copy of a direct JSON ledger, optionally with `--since-index`. The command reads
immutable `receipt-metadata/<request-id>/` sidecars alongside that ledger when
available. `--generation-index` accepts a retained generation-observation index;
lookups are joined by original request ID and verified generation ID. No network
lookup is performed. Output must be a new file outside input components.

The v2 report preserves exact decimal USD internally and supplies rounded cents
for display. Only explicit OpenRouter `usage.cost` or identity-bound generation
`total_cost` evidence qualifies as actual. `settled` alone does not: Muse's token
price estimates remain estimates. Historical direct records without retained
explicit provenance remain unknown in this report. Conflicting receipts and
multiple requests claiming the same generation remain visible and are excluded
from actual subtotals. Missing values and empty bins are not authoritative zero.
Conservative accounting amounts are exposure, never an additional bill to add to
actual costs. Detail tokens are subsets of input/output, not extra tokens.

Legacy memory windows identify observed timing only. The report leaves their
causal phase ambiguous; driver projects identify retrieval/answering, and shared
judging is counted separately once. Explicit phase attribution requires a matching
run and arm in request-bound dispatch context. Unattributed setup, native background
work and embeddings without that evidence stay separate. Receipt coverage describes
retained requests, not all work or a complete lifecycle invoice. Saved scores are
read without changing answers, pending grades or the frozen cohort.

The standalone receipt helpers normalize safe generation IDs and exact costs and
can retain atomic, immutable metadata sidecars without rewriting a billing ledger.
They contain no prompts, content, credentials or full response payloads.
Receipt coverage remains limited to the routes with instrumentation.

Route evidence is classified as proven, missing, conflicting or recorded-only.
A model name, endpoint suffix, generation-ID shape or recorded `upstream_origin`
alone cannot establish the billing service. In the historical embedded gateway,
`direct_meta` was inserted even when the server upstream was configured differently;
it is not proof of a Meta route or a token-price estimate. The offline report does
not repair that ledger label or infer the route of historical reservations.
Explicit normalized receipt sources preserve authoritative costs. Contradictory
origin/provider evidence withholds named billing/upstream attribution while leaving
those charges visible; same-generation collisions across known execution routes
remain quarantined with their receipts intact rather than summed twice. Unscoped
generation records cannot borrow a route merely by matching an ID; retained
OpenRouter observation indexes must declare their supported source/version.

Per-provider benchmark request charges are distinct from account-level credit
purchase fees and any separately documented provider invoices or BYOK charges.
This report does not reconstruct those charges or allocate unknown/shared costs
to benchmark arms. They must remain separate evidence rather than assumed zero
or added to request totals without checking their scope.

The checked-in cost audit retains the historical report, source hashes and route
census. Some original Pizza Day snapshots exist only on the historical branch;
portable tests explicitly project its already reviewed receipt records when those
sources are absent. This does not reconstruct a private ledger or claim access to
the revised Pizza Week cohort.

## Validate changes

The runtime bootstrap excludes development dependencies. After bootstrap, install
the pinned test dependencies with
`uv pip install --python vendor/venv-hermes-isolated/bin/python --constraint vendor/runtime-locks/hermes.txt -r requirements-dev.txt`
and activate that environment to run the suite. Node.js is needed for the
JavaScript syntax checks. Run checks appropriate to your change:

```sh
python3 -m pytest -q
node --check explorer/dashboard.js
node --check explorer/timeline.js
python3 -m pacbench audit prepared runs/pizza-month-local-20261105/preparation
```

Native/live tests need their declared optional services. Some harness tests need
loopback networking and a writable temporary directory outside Git;
`TMPDIR=/var/tmp` works in the managed Linux environment. Report skipped checks
and missing prerequisites. Passing offline tests establishes harness integrity,
not provider accuracy.

### Focused Pizza Week checks

```sh
python -m pytest -q tests/test_cohort_regrade.py tests/test_question_judge_batch.py \
  tests/test_workflow_daily_documents.py tests/test_workflow_realism.py \
  tests/test_week_spec_audit.py tests/test_receipt_costs.py tests/test_openrouter_run.py
python -m compileall -q pacbench provider_adapters
python -m pacbench specs
```

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
