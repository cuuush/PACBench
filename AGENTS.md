# PACBench operating guide

## User priorities

Realism comes first. Use practical local providers, their documented setup and native ingestion. No seeded facts, benchmark summaries, fake assistant ingestion turns or provider-specific extraction hints. Fix product bugs only in `/workspace/memcal`; commit them there. Benchmark/gateway fixes belong in their own repositories. Commit coherent changes and push when authorized.

Report work and decisions in the conversation, including limitations and choices the user may want to influence. Do not write standalone results summaries or accumulating progress docs. Maintain concise operating/API instructions and machine-readable run evidence. Development results are disposable: retain the current trial and necessary failure evidence; delete obsolete artifacts/code. Preserve runtime dependencies, credentials, the spending ledger and unrelated user files.

Use Spark Contributor for every writer, verifier, judge, Hermes and provider inference request through `/workspace/muse-gateway`. The hard boundary is **$10 per UTC day, no lifetime cap**. Unknown costs retain their historical bounds on request dates; only active reservations span future days. The user authorized correcting the old perpetual-reservation bug on 2026-10-15. Reclassify abandoned workers only after proving execution ended; retain the full request-day bound. Same-day review frees no budget that day. Never zero unknown costs, change labels or bypass the gateway to get around a refusal. After a daily monetary refusal, stop live work until the next UTC day. Check the live ledger before paid work. Fixed upstream error labels/protocol shape may be logged; prompts, credentials and raw error text may not.

## Source and execution rules

- Generate and verify a corpus once; freeze it before provider execution. Reuse its portable `week.json` without writer/verifier calls. Resume only unchanged verified scenes/contracts. Changed source truth, question scope or judging rules require fresh relevant review.
- Author one JSON with low/medium/high noise, reusable channels, weekday sections, scenes with human-readable times and inline English facts, and assistant turns with public prompts/English expectations. No top-level fact catalog. Use `days` for one week or a `weeks` array of Monday–Sunday maps for several. Channels persist across weeks; `ingestion_days` validates contiguous daily sources and final-only questions. See [authoring](docs/authoring-options.md) and [conversations](docs/flexible-checks.md).
- Source/thread IDs are opaque. Author outcome labels never enter provider data. Preserve sender, membership, channel, thread and written/collection times. Exact citation aliases may simplify draft authoring; normalize them to real IDs and independently review actual evidence. Unknown citations fail.
- Memcal reads a reusable synthetic Apple Messages SQLite database plus separate Contacts, then processes normally. Email uses RFC 5322 messages over a real local TLS IMAP fixture with Inbox/Sent ownership and reply references. No native reader replacements or direct archive insertion. Other providers receive the same lossless raw message records through native import/capture. See [provider setup](docs/document-ingestion-provider-map.md).
- Import chronologically, wait for native processing, and withhold recall after failed readiness. Queue acceptance or a native “done” receipt alone is insufficient. Honcho must witness messages through its configured non-speaking observer and prove enrollment/completion of newly submitted work. Do not fabricate AI source messages or retrospectively attach an observer.
- Use `America/New_York`, including DST, for sources and native processes. Memcal's nightly processing is 03:00 local. Never ask a question against future source/processing state. Align final questions with the host date where native capture uses it. Accelerated backfill does not measure wall-clock memory aging.
- Hermes starts in a genuinely empty directory outside every Git project, without source sessions or built-in memory. In this cloud environment use `TMPDIR=/var/tmp`; `/tmp/.git` can be a sandbox artifact. Do not use `--ignore-rules`, which also disables native memory hooks. Verify the actual saved native system prompt and working directory.
- New trials expose only the selected provider's exact shipped tools, including message/source readers (`provider-memory-with-source-penalty-v3`). OS tools, outside connectors, generic discovery/dispatch, built-in memory and session search stay blocked before execution. A prohibited attempt fails the check. This is a tool boundary, not an OS sandbox.
- Source-assisted answers retain their semantic grade but earn zero retention credit. Carry that source path into dependent turns sharing its context. Memcal's normal event opener, retained pages, generated summaries and attached citations keep normal credit. Unclassified/background source paths without proven attribution remain uncredited pending review. See [tool boundary](docs/provider-tool-boundary.md).
- Stop further paid checks in an arm after three definitive native provider HTTP 400/422 responses. Preserve errors/holds and continue independent arms. Pending calls or successful calls without usage are not definitive HTTP errors. Do not automatically approve ByteRover review requests.

## Judging and cost

Freeze questions, evidence-cited criteria and recovery policy before arms run. Judge only facts a natural prompt reasonably invites: event identity, current state and change history. No preferred assistant style/tool choice, forced clarification, redundant wording requirement or assumption that an elapsed appointment happened. Acknowledgement proves neither persistence nor an external action; check later in a fresh session if that behavior is being tested.

Keep original model grades. Missing ground truth, malformed judgments and execution errors are ungraded with causes. Initial pass/fail/ungraded counts are disjoint; recovery authoring errors do not erase valid initial grades. Continue independent conversations and skip only unavailable dependencies. Prior replies resolve references but cannot supply facts absent from the current answer.

One combined follow-up is allowed only for silent implicit omissions when at least one fact was correct. Wrong values, admitted retrieval failure, missed explicit requests or zero recall get none. Spark writes a short question from the original prompt and missing facts, with one independent coverage/leakage review; one rejected draft may be revised once before delivery. Preserve event/date scope. Initial facts earn one point and recovered facts half a point; source penalties are separate. See [recovery](docs/followups.md).

Record calls/arguments/results, retries/failures, latency and observed tokens separately for Hermes, provider internals and benchmark overhead. Separate ingestion, initial questions and follow-ups. Provider work after answering can be capture/consolidation; deferred work is not free. Per-tool causal prices and missing token counts remain unknown. Measure whether Memcal is cheaper without assuming it wins or choosing only favorable cost windows.

## State and lessons before Pizza Month

Current evidence is `runs/pizza-week-natural-messages-all-20261012-r1`: 80 messages, 19 scenes, 17 final questions. Inspect `development-review.json`, `failure-review.json`, `cost-comparison.json`, native/component audits and actual source/answer/judge traces through the explorer. This run used the earlier strict tool policy; the new source-permission policy has offline coverage but no matched live rerun yet. Do not relabel old permissions/results.

Memcal, Mem0, Hindsight and Honcho completed six daily source batches. Honcho still has a timed-out turn, source fallback and a recovery-review error. OpenViking completed imports then stopped on native model errors; Supermemory's done receipts masked repeated errors; ByteRover required review. These are distinct from wrong remembered facts. Incompatible model routes and pending native review do not prove extraction incapacity. Safe gateway diagnostics cannot reconstruct error text already discarded.

Before expanding, fix two harness problems for every provider: the redundant lunch-label criterion penalized a correct noon-pizza answer, and “first agreed outing” was an ambiguous recovery anchor. Use stable event/date scope and semantic equivalence, then freeze/review new contracts. Never patch Memcal or change source truth to make those answers pass. Memcal 0.8.2 revision `33878cb` includes source-preview and relative-day display fixes; missing visible context previously was not proof of extraction loss.

Pizza Month means **30 chronological daily imports, no questions during ingestion, and upcoming-plan questions only afterward**. The user chose upcoming plans only; old plans/changes are competing source context, not historical-recall questions. Keep the first month low noise; grow repeated-date/event confusion before adding high volume. Most plans should stay stable, with occasional natural changes. Freeze one corpus and share it across eligible providers. Validate the revised judge/recovery and source permissions on small canaries before committing to 30 batches on unhealthy backends.

## Verification and explorer

Relevant code under `pacbench/`: `prepare.py`, `authoring.py`, `semantic_contract.py`, `evaluation.py`, `document_ingestion.py`, `native_pilot.py`, `harness.py`, `native_checks.py`, `followups.py`, `source_policy.py`, `tool_policy.py` and `explorer_data.py`; native imports live in `provider_adapters/document_connector.py`.

```sh
python -m pytest -q
python -m pacbench.preparation_audit runs/<current>/preparation
python -m pacbench.native_audit runs/<current>
node --check explorer/dashboard.js
node --check explorer/timeline.js
python serve_explorer.py
```

Full native re-auditing needs private homes/stores; saved audit receipts and preparation are portable. Offline tests/trace integrity do not establish accuracy. Last verified: 285 harness and 26 gateway tests, desktop/mobile and clean-checkout explorer. Repeat checks when changes justify it, not for documentation-only cleanup.

The explorer reads `runs/` directly, showing sources, daily ingestion, Hermes turns, criteria, judges, tool paths and costs. Track public evidence; ignore private homes/databases/caches and encrypted replay. Never export a dashboard snapshot or duplicate results report. Keep only the latest public run, with relative preparation paths; inspect staged artifacts for secrets before pushing. Cloud localhost is separate from the user's Mac: pull the branch and run `python3 serve_explorer.py` locally.
