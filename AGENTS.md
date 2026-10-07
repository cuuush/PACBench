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

- `scene` plus short prose `facts` defines source intent. The writer chooses natural dialogue within optional 2–50 message bounds, with recent same-thread history for continuity. `at` anchors relative dates; timezone defaults to Eastern. IDs/windows derive when omitted. `scene` plus `test_intent` is also supported. No fixed pizza/date/time slots.
- Verify generated dialogue and freeze an evidence-cited contract and question before provider execution. Supplied fact wording and public prompts are preserved. Each prompt must fairly elicit its expected facts. Unsupported or unfair criteria block preparation. Private scenes/criteria never enter Hermes questions or provider imports.
- The semantic judge decides meaning. Code validates IDs, citations, shapes and provenance, never lexical answer matches. Missing or malformed output leaves a turn ungraded. Current artifacts must match their actual source hashes.
- Flexible `checks: [{id,prompt,facts,at?}]` supports arbitrary topics. `evaluation_context: shared` continues one real fresh evaluation session; `isolated` starts one per check. Previous answers resolve references but cannot earn facts missing from the current answer. Shared source fallback affects later retention.
- Freeze recovery policy before arms run. Zero initial correct facts, wrong values, admitted lookup failure or a missed explicit request gets no follow-up. Otherwise Spark writes ONE short message asking for ALL silent implicit omissions, given only original question and those facts, with one independent coverage/leakage review. Reuse frozen wording across matching missing-fact sets. No per-fact questions upfront; no second attempt.
- Keep initial grades unchanged; recovery earns half credit separately. Retained event/page detail may recover a fact. Explicit archive/original-source/message access fails retention; unknown operations get no retention credit pending review. Log calls, arguments/results, failures/repeats, latency, actual token deltas and gateway windows. Unknown provider work or tool cost stays unknown.

## Current code and evidence

Preparation: `pacbench/prepare.py`; frozen dialogue/claims: `frozen.py`, `semantic_contract.py`; direct ingestion: `document_ingestion.py`, `provider_adapters/document_connector.py`; native execution: `native_pilot.py`, `harness.py`; saved native context: `context_isolation.py`; judgment/recovery: `semantic_grade.py`, `followups.py`, `native_checks.py`. The old SDK runner and Hermes chat ingestion route are removed.

Current private trial: `runs/pizza-clean-context-mem0-20261007`, with source preparation nested in `preparation/`. The frozen 25-message input yielded 5/5 initial facts and 5/5 retention through one native Mem0 search, no follow-up. Its actual saved prompt contains no project context. Other provider connectors are implemented but no clean recall score is asserted for them. Honcho processing and ByteRover's native query timeout remain practical blockers. [PIZZA_DAY.md](PIZZA_DAY.md) records current evidence.

The cumulative accounted upper bound was **$3.2266252**, including reservations. Re-read the ledger before any new live work. Cleanup and offline checks need no paid traffic. Do not rerun every provider once one works without a concrete fix or unresolved concern. Advance complexity gradually and inspect actual traces; no comparative ranking without human-reviewed calibration.

## Verification and explorer

```sh
python -m pytest -q
python -m pacbench.preparation_audit runs/<current>/preparation
python -m pacbench.native_audit runs/<current>
node --check explorer/dashboard.js
node --check explorer/timeline.js
python scripts/build_explorer_data.py
```

Offline harness tests do not establish provider accuracy; a native integrity audit does not establish quality. The explorer shows source messages, native daily ingestion, actual Hermes turns, expected criteria and per-answer judge decisions. Show provider-internal tools separately when observable. Redact credentials/encrypted reasoning and never index private provider homes. Use `python scripts/build_explorer_data.py --snapshot` to keep `explorer/latest-run.json` updated with only the latest browser-ready result when pushing results; fresh checkouts use this portable snapshot without private run directories. Verify relevant changes in desktop/mobile Chromium. Keep current docs concise; do not append historical reports.

Current verification: 150 offline tests pass; preparation and native trace audits pass. The one-run explorer was checked in Chromium on desktop and mobile, including source ingestion, memory calls, prompt isolation and per-answer judges.
