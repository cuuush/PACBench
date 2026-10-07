# PACBench agent guide

## Priorities and budget

Realism is the first priority. Benchmark practical local memory backends with authentic daily document or message ingestion. Hermes only answers later questions using the selected provider's normal context and exact shipped tools. Start with small Pizza Day, then corrections over two days, two independent plans over three days, roughly 100 extra messages, and only later 1000. Never pad dialogue or generate 100k lines.

Use **Spark Contributor** through `/workspace/muse-gateway` for every live authoring, judging, Hermes and provider inference call. The shared task cap is **$10 total**, with **$5 per UTC day**. Durable ledger and credentials live in `/workspace/shared/muse-gateway`, outside Git. Check the cost boundary before live work. Unknown charges retain reservations; never fall back upstream or call an unmetered hosted service.

Development results are disposable. Keep only the most recent Pizza Day trial and necessary source/authoring/audit evidence. Delete old runs, caches, snapshot reports, dead execution paths and compatibility branches. Do not add historical invalidity UI. Preserve current failure evidence while diagnosing it; never relabel failure as success. Preserve installed runtime dependencies, the spending ledger and unrelated user files.

## Authentic ingestion and recall

- Use native documented document import or real source-message capture. Preserve raw text, source ID, sender, thread, channel, written time and collection time. No summaries, seeded facts, invented assistant turns, extraction prompts or Hermes ingestion turns.
- Memcal uses its native iMessage reader and processing against a provider-independent Apple Messages SQLite fixture and separate contacts source. Preserve realistic GUIDs, Apple timestamps, service fields, sender handles, chat membership and joins. Future provider connectors must be able to reuse the fixture unchanged.
- Configure providers from project recommendations, using isolated run-owned identities/stores. Queue acceptance is not completed ingestion. Record bounded readiness checks and fail before recall when processing is incomplete. Exclude cloud-only or impractical ingestion backends; see [provider map](docs/document-ingestion-provider-map.md).
- Set native process `TZ` to the frozen scenario's named timezone; Eastern means `America/New_York`, with seasonal offsets. Memcal's native nightly clock is 03:00 local. Never evaluate earlier questions against future source or processing state.
- Recall begins without prior Hermes source conversations or populated built-in memory. Run the normal CLI in an empty directory outside every Git project. Do not use `--ignore-rules`: it also disables external memory hooks. Audit Hermes's actual saved system prompt and working directory; benchmark project context must never reach the answering agent.
- `provider-only-v1` exposes and permits only exact names from the selected native provider schemas. Disable built-in memory, session search, discovery/dispatch, filesystem, shell, browser and other tools. Block attempts before execution and fail the trial even if its answer is correct. This is a tool boundary, not an OS sandbox; normal provider internals remain available.
- Fix Memcal product bugs directly in `/workspace/memcal`; commit there and push when requested. Do not hide native bugs with provider-specific benchmark workarounds. Benchmark changes belong in PACBench. Commit coherent checkpoints and final code; never commit credentials, private homes, databases or encrypted artifacts.

## Authoring, questions and judgment

- Author weeks as one JSON file with `noise`, reusable `channels` (source type, participants, optional group name), and `days` keyed `monday` through `sunday`. Scenes use a channel reference, human-readable `time`, short `scene` and inline English `facts`. Assistant turns use public `prompt` and an English `expect`; no top-level fact catalog or fact-ID matching. `--week-start` assigns concrete Monday dates during preparation. Preserve authored times and freeze timezone offsets.
- Noise is only `low | medium | high`. Low stays minimal; medium targets ordinary chatter; high targets 600–800 messages per person per conversation per day. Never pad, copy or truncate dialogue. Use bounded chunks for large scenes, but test low first. No separate noise file or generated noise conversations.
- Verify generated dialogue and freeze evidence-cited criteria and exact questions before provider execution. The semantic judge derives independent criteria from English expectations; code checks shapes, IDs, citations and hashes. Unsupported/unfair criteria prevent a runnable corpus. Private authoring material never enters Hermes questions or provider imports.
- Preparation emits one portable `week.json`; provider execution reuses it without writer/verifier calls. Resume only unchanged verified scenes when repairing preparation. Import daily source batches chronologically: a question cannot see future sources or future native processing.
- Named conversation IDs resume real Hermes sessions; different IDs start fresh while provider memory persists. Planned public user turns use normal provider capture. Earlier answers resolve references but cannot earn facts missing from the current answer. Scripted clarification replies run only when requested. Bind dependent checks to delivered replies; never assume an undelivered update occurred.
- Grade observable behavior pass/fail. Acknowledgement alone proves neither persistence nor an external action; check state later in a fresh session. Missing script truth, malformed judges and execution errors are ungraded with reasons. Continue independent conversations; skip unavailable dependencies with a cause. Never let one failure erase a week. Review passes and failures during development.
- Freeze recovery policy before arms run. Zero initial correct facts, wrong values, admitted lookup failure or a missed explicit request gets no follow-up. Otherwise Spark writes ONE short message asking for ALL silent implicit omissions, given only original question and those facts, with one independent coverage/leakage review. Reuse frozen wording across matching missing-fact sets. No per-fact questions upfront; no second attempt.
- Keep initial grades unchanged; recovery earns half credit separately. Retained event/page detail may recover a fact. Explicit archive/original-source/message access fails retention; unknown operations get no retention credit pending review. Log calls, arguments/results, failures/repeats, latency, actual token deltas and gateway windows. Unknown provider work or tool cost stays unknown.

## Current code and evidence

Preparation: `pacbench/prepare.py`; frozen dialogue/claims: `frozen.py`, `semantic_contract.py`; direct ingestion: `document_ingestion.py`, `provider_adapters/document_connector.py`; native execution: `native_pilot.py`, `harness.py`; direct explorer reader: `explorer_data.py`; saved native context: `context_isolation.py`; judgment/recovery: `semantic_grade.py`, `followups.py`, `native_checks.py`. The old SDK runner and Hermes chat ingestion route are removed.

Current tracked trial: `runs/pizza-clean-context-mem0-20261007`, with source preparation nested in `preparation/`. The frozen 25-message input yielded 5/5 initial facts and 5/5 retention through one native Mem0 search, no follow-up. Its actual saved prompt contains no project context. Other provider connectors are implemented but no clean recall score is asserted for them. Honcho processing and ByteRover's native query timeout remain practical blockers. [PIZZA_DAY.md](PIZZA_DAY.md) records current evidence.

The cumulative accounted upper bound was **$3.2486826**, including reservations. Re-read the ledger before any new live work. Cleanup and offline checks need no paid traffic. Do not rerun every provider once one works without a concrete fix or unresolved concern. Advance complexity gradually and inspect actual traces; no comparative ranking without human-reviewed calibration.

## Verification and explorer

```sh
python -m pytest -q
python -m pacbench.preparation_audit runs/<current>/preparation
python -m pacbench.native_audit runs/<current>
node --check explorer/dashboard.js
node --check explorer/timeline.js
python serve_explorer.py
```

Offline harness tests do not establish provider accuracy; a native integrity audit does not establish quality. The explorer shows source messages, native daily ingestion, actual Hermes turns, expected criteria and per-answer judge decisions. Show provider-internal tools separately when observable. Redact credentials/encrypted reasoning and never index private provider homes. The Web UI reads `runs/` directly through the local server on every refresh. Track public source, model-call, judge and audit artifacts there; keep private provider homes, databases, caches and encrypted replay files ignored. Save encrypted writer reasoning under `model-calls/private/`, with readable sanitized records beside it. Use relative preparation paths so checkouts are portable. Do not introduce a dashboard export or separate result snapshot. Authoring inputs live in `weeks/`; obsolete examples and calibration code are removed. Verify relevant changes in desktop/mobile Chromium. Keep current docs concise; do not append historical reports.

Current verification: 168 offline tests pass; preparation and native trace audits pass locally. The direct-run explorer was checked in Chromium on desktop and mobile, including source ingestion, memory calls, prompt isolation and per-answer judges. A fresh checkout displays the tracked run without private runtime files or a dashboard snapshot.

The weekday Pizza Week implementation is covered offline. Live authoring was stopped during the requested schema simplification; no new Memcal week accuracy is asserted. The latest clean Mem0 result is tracked under `runs/`.
