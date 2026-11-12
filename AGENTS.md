# PACBench operating guide

Read [README.md](README.md) for scope and [CONTRIBUTING.md](CONTRIBUTING.md)
for setup, current limitations and next steps. Keep detailed procedures in the
existing docs and run evidence in machine-readable artifacts.

## Working rules

- Use realistic sources and native provider ingestion. No seeded memories,
  answer hints or fabricated assistant ingestion turns. Do not change third-party
  provider code to rescue a benchmark. Record documented configuration changes
  and preserve native defaults.
- Keep Memcal product fixes in its repository, gateway fixes in muse-gateway,
  and benchmark fixes here. Preserve unrelated files, credentials, runtime
  dependencies, private stores and spending history. Push only when authorized.
- Report progress and limitations in conversation. Do not accumulate handoff or
  results documents. Historical runs and troubleshooting notes belong in evidence
  and Git history, not this file.

## Paid execution

- Follow the user's selected model and route. Existing Muse trials use the
  shared gateway at `/workspace/muse-gateway` with a $10-per-UTC-day limit and
  no lifetime cap. A new paid route requires a concrete approved spending limit.
- The current OpenRouter Pizza Day task has a separately authorized **$20 total**
  budget across all providers and retries. A $2 exclusive Pizza Week allocation
  leaves at most $18 for this audit, including all historical costs and uncertain
  bounds. Count the allocation once; do not append separate Week spending against
  it while the full allocation remains held. Use the persistent task ledger and
  exact DeepSeek/DeepInfra route. Muse's missing historical ledger does not block
  this authorized route; never claim its history was recovered or reset it.
- Check actual gateway state before paid work. Private state lives at
  `/workspace/shared/muse-gateway`; source code and public receipts do not
  recover its historical ledger. If that ledger is missing, keep Muse execution
  blocked. Never create a blank replacement or reconstruct incomplete history.
- Preserve unknown cost bounds, pending reservations and deferred provider costs.
  Never bypass a refusal or relabel costs to free budget. A daily monetary refusal
  stops live work until the next UTC day.
- HTTP 401/403 or `upstream_auth_blocked` stops live work globally until a
  successful free model inventory. Three definitive provider HTTP 400/422 errors,
  or three completed requests without usable token usage, stop that provider arm.
  Keep execution, access, cost and recall failures distinct.

## Sources and evaluation

- Validate specs with `python -m pacbench.specs`. Use the existing compiler.
  Generate and review once, then freeze and reuse `week.json` with all siblings.
  Preserve bodies, identities, membership, threads and written/collection times.
- Scenes may supply exact messages instead of using the writer. Keep the normal
  independent checks and freeze audit; never rewrite rejected supplied source.
- Require current source, contract and corpus reviews bound to actual inputs
  and receipts. Bulk review is advisory; retain required independent source
  audits. Never relabel stale reviews, reroll a rejected verdict for approval,
  or silently migrate historical evidence to a new protocol.
- Freeze natural questions, evidence-cited criteria and recovery rules before
  execution. Grade only facts reasonably invited by the question. Preserve
  original grades; corrective reviews need a separately versioned overlay.
  Missing or malformed judgments remain ungraded.
- Follow [authoring](docs/authoring-options.md),
  [conversation checks](docs/flexible-checks.md) and
  [judging/recovery](docs/followups.md). Keep semantic grades, retention credit,
  compatibility failures and unknown costs separate.

## Native execution

- Use local-capable memory backends. Keep Mem0 OSS and hosted Mem0 separate;
  hosted BYOK is reserved for the final full benchmark, never development.
  The selected OpenRouter inference route remains unchanged.
- Follow the [provider map](docs/document-ingestion-provider-map.md) and
  [runtime setup](vendor/README). Memcal uses native Messages/Contacts and TLS
  IMAP fixtures through its collection lifecycle; other providers use native
  import/capture. Preserve source chronology and within-thread chunk order.
- Wait for actual processing and consolidation before recall. Accepted writes,
  queue counters and indexing completion alone are insufficient. Preserve
  native errors and submitted-work witnesses; never repair queues, force flushes
  or raise output limits to manufacture readiness. ByteRover is retired from
  active execution; preserve its historical traces and audit support.
- Keep the scenario's fixed calendar and America/New_York timezone. Disclose
  actual provider clock conditions. Simulated time does not prove processing
  completion; spending and harness deadlines use real time.
- Start Hermes in an empty directory outside Git, with only the selected
  provider's shipped tools. Disable built-in memory, session search, OS tools
  and outside connectors. Do not use `--ignore-rules`, which disables native
  memory hooks. Verify the actual saved prompt and working directory.
- Send the authored public question verbatim and add no PACBench instructions
  to Hermes user or system prompts. Historical date-prefix and ephemeral-system
  clock trials are separate conditions. New execution and supported resumes
  require the verified `native-question-v2` condition.
- Apply the [tool and source boundary](docs/provider-tool-boundary.md).
  Source-assisted answers keep semantic grades but receive zero retention
  credit; uncertain provenance remains uncredited pending review.
- Resume only through a verified native recovery protocol. OpenViking's
  `python -m pacbench.native_resume <run>` supports a successful pre-recall
  prefix; do not reingest completed days or blindly reuse failed stores.

## Validation and publication

Run checks appropriate to the change:

```sh
python -m pytest -q
python -m pacbench.preparation_audit runs/<current>/preparation
python -m pacbench.native_audit runs/<current>
node --check explorer/dashboard.js
node --check explorer/timeline.js
```

Harness isolation tests need loopback and `TMPDIR=/var/tmp` outside Git;
the managed sandbox can supply synthetic Git ancestors. Native re-audits need
private stores. Offline tests establish harness integrity, not provider accuracy.

Preserve exact source/judge evidence and shared-text hashes. Publish only audited
frozen inputs and selected public trials; exclude credentials and private stores.
Verify the explorer on desktop/mobile and from a clean checkout before publishing.
Keep its record endpoint protected against private paths, traversal and symlinks.
