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
- Preserve provider processing defaults. The user rejected Honcho's eager-flush override: new runs use native batching/output limits and the current setup's directional observation default, including owner membership. Honor the declared accumulation delay before a stall cutoff. Gateway/model/embedding transport configuration remains explicit. Do not silently raise output budgets or flush thresholds to rescue a model incompatibility.
- Use `America/New_York`, including DST, for sources and native processes. New daily ingestion v2 schedules every provider at 03:00 after the collection day; v1 receipts retain their original condition. Native backend host clocks are not universally simulated; preserve explicit source timestamps. Never ask against future source/processing state. Align final questions with the host date where native capture uses it. Accelerated backfill does not measure wall-clock memory aging.
- Hermes starts in a genuinely empty directory outside every Git project, without source sessions or built-in memory. In this cloud environment use `TMPDIR=/var/tmp`; `/tmp/.git` can be a sandbox artifact. Do not use `--ignore-rules`, which also disables native memory hooks. Verify the actual saved native system prompt and working directory.
- New trials expose only the selected provider's exact shipped tools, including message/source readers (`provider-memory-with-source-penalty-v3`). OS tools, outside connectors, generic discovery/dispatch, built-in memory and session search stay blocked before execution. A prohibited attempt fails the check. This is a tool boundary, not an OS sandbox.
- Source-assisted answers retain their semantic grade but earn zero retention credit. Carry that source path into dependent turns sharing its context. Memcal's normal event opener, retained pages, generated summaries and attached citations keep normal credit. Unclassified/background source paths without proven attribution remain uncredited pending review. See [tool boundary](docs/provider-tool-boundary.md).
- Stop further paid checks in an arm after three definitive native provider HTTP 400/422 responses. Preserve errors/holds and continue independent arms. Pending calls or successful calls without usage are not definitive HTTP errors. Do not automatically approve ByteRover review requests.

## Judging and cost

Freeze questions, evidence-cited criteria and recovery policy before arms run. Judge only facts a natural prompt reasonably invites: event identity, current state and change history. No preferred assistant style/tool choice, forced clarification, redundant wording requirement or assumption that an elapsed appointment happened. Acknowledgement proves neither persistence nor an external action; check later in a fresh session if that behavior is being tested.

Keep original model grades. Missing ground truth, malformed judgments and execution errors are ungraded with causes. Initial pass/fail/ungraded counts are disjoint; recovery authoring errors do not erase valid initial grades. Continue independent conversations and skip only unavailable dependencies. Prior replies resolve references but cannot supply facts absent from the current answer.

One combined follow-up is allowed only for silent implicit omissions when at least one fact was correct. Wrong values, admitted retrieval failure, missed explicit requests or zero recall get none. Spark writes a short question from the original prompt and missing facts, with one independent coverage/leakage review; one rejected draft may be revised once before delivery. Preserve event/date scope. Initial facts earn one point and recovered facts half a point; source penalties are separate. See [recovery](docs/followups.md).

Record calls/arguments/results, retries/failures, latency and observed tokens separately for Hermes, provider internals and benchmark overhead. Separate ingestion, initial questions and follow-ups. Provider work after answering can be capture/consolidation; deferred work is not free. Per-tool causal prices and missing token counts remain unknown. Measure whether Memcal is cheaper without assuming it wins or choosing only favorable cost windows.

## Current trial and lessons

The author input `weeks/pizza_month.json` now defines medium noise, 45 scenes and 14 final checks. Generate and review its new frozen corpus before comparing providers; the published low-noise cohort below used different input.

Current evidence is `runs/pizza-month-all-20261015`: 165 messages, 40 scenes, **30 chronological daily imports, no interim questions, and 10 upcoming-plan questions** afterward. The low-noise corpus includes repeated Fridays, quotations, hypothetical/declined invitations, third-party plans, DM-to-group changes, cross-channel rescheduling and an unnamed group cancellation. Sources, criteria and recovery policy were frozen before execution; every selected arm uses the same source bytes and fixture hashes. Inspect `development-review.json`, `failure-review.json`, `cost-comparison.json`, component audits and actual traces in the explorer. Source permission/retention policy v3 was exercised live.

Memcal, Mem0 and Honcho each have 9 initial passes/1 failure; Hindsight has 7/3. They completed the month. OpenViking's fresh attempt completed all 30 imports, then stopped after three native HTTP 400 errors: 5 passes, 3 recall failures, 2 ungraded questions. Its first attempt was interrupted after 20 imports. Supermemory's native canary returned repeated HTTP 400 errors despite done receipts; ByteRover required native curation review. Those two have no month memory grade. Replaced attempts retain separate traces and costs. Native/model execution failures are distinct from wrong remembered facts.

Memcal 0.8.2 development revision `a7044d0` makes MCP, Hermes and brief guidance consistently prefer opening retained records before archive search. Its fresh month rerun verified delivery of those exact shipped instructions. The initial pond answer then used event opens; its follow-up still searched sources. Keep that retention penalty and do not infer the change caused every difference between independent ingestions. Normal opening remains full credit; no benchmark hint or grade override was added.

Do not repeat old harness mistakes: semantic equivalents need no redundant lunch label; recovery anchors must identify the event/date; a past appointment does not prove attendance; cancellation tests must not require a particular clarification style. Hindsight readiness must paginate operations. Provider work after an answer cannot causally penalize it, but its cost still counts. Deferred capture/consolidation is unknown future cost, not free work. Preserve Honcho's resolved native observation mode and observer enrollment; do not tune it after seeing answers.

Accelerated daily backfill does not test real-time aging. Low-noise scenes remain short and somewhat stylized. Review this cohort before adding moderate noise; do not jump to thousands of messages. Resolve native Spark incompatibilities or review blockers through supported configuration before scaling affected providers. Upcoming plans only remains the user's selected question scope.

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

Full native re-auditing needs private homes/stores; saved audit receipts and preparation are portable. Publication re-exports audited `preparation/week.json` for corpus reuse and rejects preparation differing from the executed frozen files. Offline tests/trace integrity do not establish accuracy. Last verified: 326 harness tests, 29 gateway tests, 2482 Memcal unit tests (39 skipped) under Eastern time, and 400 temporal checks. Verify desktop/mobile and a staged clean-checkout explorer before publishing. Repeat checks when changes justify it, not for documentation-only cleanup.

The explorer reads `runs/` directly, showing sources, daily ingestion, Hermes turns, criteria, judges, tool paths and costs. Track public evidence; ignore private homes/databases/caches and encrypted replay. Never export a dashboard snapshot or duplicate results report. Keep only the latest public run, with relative preparation paths; inspect staged artifacts for secrets before pushing. Cloud localhost is separate from the user's Mac: pull the branch and run `python3 serve_explorer.py` locally.
