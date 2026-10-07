# PACBench agent guide

## Goal and limits

Evaluate every bundled memory provider in the pinned Hermes checkout, plus Memcal,
through normal native Hermes behavior. Pizza Day is a wiring diagnostic, not the
benchmark's destination. Add eligible source-ingestion providers incrementally. Holographic is excluded from new
trials. Never substitute fake retrieval, seed expected facts,
or silently skip a broken provider.

The user set a **$10 total ceiling for this task**, with a shared **$5 per UTC
day** gateway cap across projects. Use **Spark Contributor** only. The separate
`/workspace/muse-gateway` Git repository owns the durable ledger and local
credential; service state lives outside Git in `/workspace/shared/muse-gateway`.
All live authoring, judging, Hermes and memory-provider inference must use the gateway.
Never fall back to upstream credentials or call a paid hosted provider with unknown
charges. `native_pilot` checks the spending service before any live turn. Unknown
usage retains the full reservation. The cap covers routed traffic, not unrelated
applications holding an upstream key. Commit coherent checkpoints and final code;
never commit private credentials, run homes, stores or encrypted artifacts.

## Working rules

- Development results are disposable. Prioritize the current realistic implementation; do not retain old schemas, protocols, compatibility branches or artifacts solely to support earlier results. Regenerate or remove obsolete results when useful. Historical notes below are debugging context, not implementation requirements. Keep active-run evidence accurate while investigating failures; never relabel a failed execution as a success.

- New native trials use `provider-only-v1`: Hermes sees and may invoke only exact tool names shipped by the selected provider. Disable built-in memory, session search, generic discovery/dispatch, filesystem, shell, browser and all other tools. Block disallowed attempts before execution and fail the trial even if it later answers correctly. Preserve native provider ingestion/prefetch and tool behavior; do not add retrieval hints. This is a tool boundary, not an OS sandbox. User explicitly chose this benchmark rule instead of requiring Docker.

- Latest user scope: test local memory backends with a practical, documented way to ingest daily batches of raw text. Both document import and real message capture qualify. Preserve actual speakers, threads and timestamps; do not invent assistant turns, summaries, extracted facts or custom extraction prompts. Hermes does not ingest the source messages; it only performs later recall. Skip cloud-only backends and providers whose ingestion would require artificial or impractical workarounds. Holographic remains excluded because it lacks a usable automatic source-ingestion interface. See [document-ingestion-provider-map.md](docs/document-ingestion-provider-map.md).
- User explicitly authorizes Memcal's real native iMessage ingestion as an exception to document import. Build a provider-independent synthetic Apple Messages database from frozen public messages, with realistic source metadata and joins. Do not tailor the database schema, contacts, identity metadata or fields to Memcal, and do not write extracted facts. Preserve it as a reusable source fixture for future iMessage connectors. Other providers receive the same day's raw messages through their supported document or message-batch API.

- Inspect `git status` and relevant diffs before editing. Preserve user code changes; do not reset the tree. Saved development results may be regenerated or removed as needed.
- Fix Memcal product bugs directly in `/workspace/memcal` and commit them in the Memcal repository, including future fixes. Keep benchmark code and its commits in PACBench. Do not hide provider bugs with provider-specific benchmark workarounds. Record the actual provider revision used by current tests.
- Set every native provider process to the frozen scenario's named timezone (`TZ`); Eastern scenarios use `America/New_York`, with seasonal offsets rather than a permanent EST offset. Preserve explicit offsets in clock pins. Regenerate development fixtures when fixing source timestamps; keep each current run consistent with its actual inputs.
- Authors can supply a `scene` plus a short `facts` list of plain-language assertions. The older `scene` + concrete `test_intent` form remains supported. The writer chooses dialogue length within optional `message_range` bounds (2–50 per scene). Reject out-of-range drafts; never pad, duplicate, or truncate dialogue to hit a target. `at` anchors the fictional date when relative dates matter; `timezone` makes each day's time local (including DST). Message times, scene IDs, and thread IDs are otherwise derived. Set a thread ID only to join scenes into one conversation. Do not bring back A–F fixtures, beat files, pizza-specific runners, or a fixed pizza/date/time slot schema.
- Generate dialogue and a short writer note, verify that the dialogue establishes the scene and each fact, then freeze it. Derive and review an evidence-cited claim contract before either provider runs. Both arms get the same frozen messages and question. Providers and the scoring judge must not receive the private scene or test intent.
- The LLM decides claim meaning. Python validates IDs, citations, response shapes, and counts; it does not use string matching, date expansion, or entity substrings to score answers. Missing or malformed judge output leaves a run ungraded.
- Treat retrieval capability and Hermes behavior separately. Hindsight needs isolated SDK import, MCP connection, namespaced discovery and allowlist access, and a successful read-only bank recall. Record whether Hermes actually called recall. Pin Memcal maintenance and prefetch to the fictional clock.
- Current run artifacts must match their actual input hashes. Regenerate a run after changing its fixture instead of attaching stale grades to edited inputs.

## Follow-up policy (user-authorized)

- Bind the recovery policy to the frozen contract before provider arms run. Do not author/review unused per-fact questions upfront. After grading and natural-omission eligibility assessment, Spark writes ONE short message asking for ALL eligible implicit omissions, in an isolated call given only the original question and those facts; one short independent review checks coverage and answer leakage. Freeze and reuse the wording for matching missing-fact sets across arms.
- If zero expected facts are correct in the initial answer, skip recovery entirely: no question authoring/review, Hermes turn or recovery judge. Record `no_initial_correct_facts`, keep the zero score and continue any separately planned evaluation questions. Use the current natural-omission eligibility rules; historical protocol compatibility is not required.
- Otherwise ask at most one recovery message total, continuing the real recall conversation and fictional clock. Exclude correct facts; ask nothing if all are correct. Preserve the initial grade and award half credit separately per recovered fact.
- Opening retained memory can recover a fact. Explicit archive/original-source/message-connector access fails retention even if the answer becomes correct. Log attempted and blocked fallbacks. Unknown tool operations need review and get no retention credit.
- Log arguments/results, direct and nested call counts, discovery, repeated/failed operations, latency, reported token deltas and gateway project cost windows. Unknown usage/cost stays unknown; do not invent tool prices or simulate a connector.
- Audit the one-message-total limit, complete missing-fact coverage, session continuity, question hashes and recovery points. Use the current one-combined-message policy with natural-omission eligibility and the zero-recall gate. See [followups.md](docs/followups.md). Record initial misses and recovery effort accurately in current runs.

## Code and checks

- Examples: `examples/pizza_day.json` and its one-window and two-day correction variants. New bounded stages: `examples/pizza_two_day_stage.json` (one time change over two days) and `examples/pizza_two_trips_three_days.json` (two companions, independent threads, time/venue changes over three days). Score explicit commitment status; regenerate contracts as the tests evolve. The retired hand-built corpus, lexical adapter, and old browser were removed.
- Scene generation and verification: `pacbench/authoring.py`; audited model calls: `pacbench/model_calls.py`.
- Frozen claims and question: `pacbench/semantic_contract.py`; semantic verdicts: `pacbench/semantic_grade.py`.
- Generic runner and retrieval preflight: `pacbench/semantic_experiment.py`; provider seams: `provider_adapters/`.
- Explorer: `scripts/build_explorer_data.py`, `explorer/`, `serve_explorer.py`.
- Run `/workspace/pacbench-venv/bin/python -m pytest -q`, `node --check explorer/dashboard.js`, and the explorer data builder after relevant changes. MCP seam tests need loopback access. For live integration, use a bounded spec, inspect its saved JSON, and run `python3 -m pacbench.run_audit runs/<run-id>`; mocked tests alone do not establish provider behavior.
- Keep full model request, response, model identity, token usage, and reported cost in private run artifacts. The scene writer uses Meta Responses with stateless encrypted reasoning replay on revisions; the verifier and answer judge remain single-turn. Explorer data must redact `encrypted_content`. If the API omits a dollar charge, record it as unavailable.

## Current evidence and next work

The public Hermes memory documentation was checked on 7 October 2026. Eligible local paths are Mem0, Hindsight, OpenViking, ByteRover, Supermemory and Honcho, plus Memcal. Honcho's real message capture qualifies under the latest user instruction; document upload is not mandatory when a practical native message API exists. Holographic lacks automatic source ingestion. RetainDB and the documented Memori integration are cloud-only and excluded. See [provider map](docs/document-ingestion-provider-map.md) and [current diagnostic](docs/local-provider-diagnostic-2026-10-07.md).

The frozen small input has 25 messages and five facts. Memcal and Mem0 recalled 5/5; Hindsight recalled 4/5 and still missed the street after one natural follow-up. OpenViking answered 5/5 using the full original transcript, so retention is zero. ByteRover answered 5/5 once, but a fresh repeat hit its native 10-second Hermes query timeout and recalled nothing. Its retrieval paths remain under review. Supermemory recalled 5/5 after the connector adopted Hermes's native namespace resolver. Honcho accepted all 25 source messages but its native processing queue did not drain within three minutes, including with its documented snappy-deriver option; it has no valid recall score. Do not describe all providers as working.

Source import is lossless, not an accuracy adaptation. Native transcripts contain public source fields only. Honcho uses real source peers, written timestamps and metadata. ByteRover uses file curation without a benchmark prompt and requires native completion without pending review. Supermemory imports into the same canonical container tag Hermes searches. Memcal reads realistic Apple Messages and separate contacts through its normal pipeline. Keep installed server/SDK versions and setup receipts visible; do not patch provider prompts.

Provider tool traces include arguments, results, failures and retries. Where native journals expose internal provider tools, preserve those independently of Hermes calls, including prefetch. Absence of internal tracing is unknown, not zero. Per-tool token attribution remains unknown; actual gateway model calls and cost windows are recorded by role. A passing native audit verifies integrity, not provider quality.

Latest verification: 286 tests passed, 2 skipped; JavaScript syntax checks and explorer data build passed. Desktop/mobile Chromium verified per-message criteria and judges, exact provider tool cards, native internal task logs and Honcho's blocked-ingestion display. Completed-arm native re-audits pass; Honcho remains incomplete. The cumulative gateway bound was $3.2225898 including reservations. Completed-run cleanup follows native shutdown hooks and stops only processes whose working directory belongs to that private run, covering CLI grandchildren left by timeouts.

Next steps:

1. Resolve small-case practical blockers first: Honcho processing, ByteRover query timeouts and trace classification, and Hindsight's omitted street. Investigate native provider/integration behavior without supplying expected facts or provider-specific retrieval hints. Do not increase volume to hide these failures.
2. Repeat only when a concrete fix or unresolved concern warrants the cost. The user permits skipping impractical providers; unsupported/cloud-only systems need no paid trial. Re-read the gateway ledger before any further live work. At the latest diagnostic checkpoint the accounted upper bound, including reservations, remained below $4; the cap stays $10 total and $5/day.
3. Run the existing two-day correction and two-trip/three-day scenarios through successful native ingestion paths, preserving all source timestamps and one fresh evaluation session when requested. Then roughly 100 extra ordinary messages; defer 1000 until small stages are understood. Never generate 100k lines or pad dialogue.
4. Keep judge calibration, answer accuracy, retained-memory retrieval, source fallback and cost separate. Commit coherent code checkpoints. Memcal product bugs must be fixed and committed in the Memcal repository.

## Flexible checks and the explorer

- Accept `checks: [{id, prompt, facts, at?}]` for topic-independent questions and expected facts. Freeze contracts against verified dialogue and review whether each question should reveal its facts. Preserve supplied wording; failed truth or elicitation review blocks execution. See [flexible-checks.md](docs/flexible-checks.md).
- `evaluation_context: shared` continues one real evaluation session across questions and natural recovery. Evaluation begins fresh after ingestion. Prior answers resolve references but cannot supply facts absent from the current answer. Source fallback or unknown paths in that shared history also invalidate later retention credit.
- Never evaluate a question using source or processing state from its future. Native daily import currently completes all scheduled batches before evaluation; reject earlier checkpoints rather than silently leaking later messages.
- The explorer shows generated texts, daily native ingestion status, actual Hermes exchanges, expected facts, per-message verdicts, judge prompt/response/model and tool calls. Show provider-internal native tasks separately when available. Unknown usage stays unknown. Exclude credentials, provider homes and encrypted reasoning from browser data.
- Use the generic day timeline in the scenario timezone. Include evaluation-only days and keep blocked/failed arms visible. Source delivery must match frozen bytes/hashes; acknowledgement and completed processing are separate. Verify the latest UI in Chromium on desktop and mobile after rebuilding data.
