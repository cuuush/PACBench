# PACBench agent guide

## Working rules

- Inspect `git status` and relevant diffs before editing. Preserve saved run artifacts and user changes; do not reset the tree.
- Authors can supply a `scene` plus a short `facts` list of plain-language assertions. The older `scene` + concrete `test_intent` form remains supported. The writer chooses dialogue length. `at` anchors the fictional date when relative dates matter; `timezone` makes each day's time local (including DST). Message times, scene IDs, and thread IDs are otherwise derived. Set a thread ID only to join scenes into one conversation. Do not bring back A–F fixtures, beat files, pizza-specific runners, or a fixed pizza/date/time slot schema.
- Generate dialogue and a short writer note, verify that the dialogue establishes the scene and each fact, then freeze it. Derive and review an evidence-cited claim contract before either provider runs. Both arms get the same frozen messages and question. Providers and the scoring judge must not receive the private scene or test intent.
- The LLM decides claim meaning. Python validates IDs, citations, response shapes, and counts; it does not use string matching, date expansion, or entity substrings to score answers. Missing or malformed judge output leaves a run ungraded.
- Treat retrieval capability and Hermes behavior separately. Hindsight needs isolated SDK import, MCP connection, namespaced discovery and allowlist access, and a successful read-only bank recall. Record whether Hermes actually called recall. Pin Memcal maintenance and prefetch to the fictional clock.
- Preserve saved run artifacts and their original hashes. Do not attach an old run to an edited fixture by source ID alone.

## Code and checks

- Examples: `examples/pizza_day.json` and its one-window and two-day correction variants. The retired hand-built corpus, lexical adapter, and old browser were removed.
- Scene generation and verification: `pacbench/authoring.py`; audited model calls: `pacbench/model_calls.py`.
- Frozen claims and question: `pacbench/semantic_contract.py`; semantic verdicts: `pacbench/semantic_grade.py`.
- Generic runner and retrieval preflight: `pacbench/semantic_experiment.py`; provider seams: `provider_adapters/`.
- Explorer: `scripts/build_explorer_data.py`, `explorer/`, `serve_explorer.py`.
- Run `/usr/local/bin/python3 -m pytest -q`, `node --check explorer/dashboard.js`, and the explorer data builder after relevant changes. MCP seam tests need loopback access. For live integration, use a bounded spec, inspect its saved JSON, and run `python3 -m pacbench.run_audit runs/<run-id>`; mocked tests alone do not establish provider behavior.
- Keep full model request, response, model identity, token usage, and reported cost in private run artifacts. The scene writer uses Meta Responses with stateless encrypted reasoning replay on revisions; the verifier and answer judge remain single-turn. Explorer data must redact `encrypted_content`. If the API omits a dollar charge, record it as unavailable.

## Current evidence

- `runs/semantic-pizza-native-20260924/` is the first native iMessage Pizza Day run: 25 messages, two threads, one daily batch. Both arms passed retrieval preflight, used memory tools, and received five correct verdicts. Hindsight's discovered `list_memories` call was blocked by the old allowlist; that read-only tool is now allowed.
- `runs/semantic-pizza-correction-20260924/` tests an earlier 6pm proposal replaced by 7pm in one window. Both arms received the same 26 messages and input hash. Memcal answered all six claims; Hindsight's recall and memory listing omitted 6pm, and Hermes admitted it could not compare the times. This is a retrieval/representation diagnostic, not a ranking.
- `runs/semantic-pizza-two-days-20260924/` tests daily deltas: 10 messages and maintenance on day one, 24 messages and maintenance on day two. Both arms had the same input hash, called memory tools, and answered all six claims. That saved run used offset-free timestamps; current examples use a named time zone.
- `runs/semantic-pizza-two-days-offset-20260924/` is the same generated dialogue with `-04:00` timestamps and a neutral Hindsight preflight query. Both arms had matched input hashes, used memory tools, and answered six claims. The text itself was too repetitive, so the current example uses a named time zone and same-thread writer continuity.
- `runs/semantic-pizza-natural-two-days-live-20260924/` revealed a harness policy bug: Hindsight's preflight recall passed, but Hermes's `tool_call` bridge to recall was blocked. Its six misses are invalid as a provider comparison. The bridge is now allowed, and blocked retrieval attempts make an arm invalid.
- `runs/semantic-pizza-natural-v10-live-20260924/` is a full two-day run: 30 messages, 11 then 19 per daily batch, identical input hashes, both retrieval preflights and memory use, six correct claim verdicts per arm. Hindsight got a local batch-shape error, retried one recall, and answered. This is diagnostic, not comparative.
- `runs/semantic-pizza-week-live-v15-20260928/` is the first full seven-day run: 116 messages over six active days (30, 15, 12, 18, 13, 28) and an empty Sunday batch. Both arms had the same input hash, seven valid morning checkpoints, working retrieval access, and actual memory-tool use. Its independent run audit passes with no errors or warnings. Memcal had one missed claim on Sunday; Hindsight missed the 5th Street detail once and the superseded 6pm history on three later mornings. These are uncalibrated diagnostic verdicts, not comparative scores. The saved Hermes JSON stream clipped some tool outputs at 5,000 characters; the isolated session databases retain the full messages. The explorer builder now reads those full saved messages for this run, and future `turn.json` records capture them directly.
- `runs/semantic-20260923T140000Z/calibration-facts/report.json` has nine live judge results matching nine proposed labels; human review is pending. Older runs retain their own frozen bundles and hashes. See `PIZZA_DAY.md` for earlier failures and provenance.

## Next steps

1. Review the nine proposed Pizza Day calibration labels with the author, especially uncertainty and historical mentions. Resolve disagreements and rerun calibration before interpreting scores comparatively.
2. Review the seven daily contracts and answer judgments from the full week using `calibration/PIZZA_WEEK_REVIEW.md`. The current broad question scores every accumulated authored fact, including old historical details; decide which omissions a personal catch-up answer should actually count as failures. Add matched follow-up questions for specific history if needed.
3. Check extra assertions in answers against exposed dialogue. The current claim judge measures authored-fact coverage but does not grade unsupported additions. Keep this separate from claim omissions.
4. Expand beyond Pizza Day only when the seven-day, three-life pilot in `PACBENCH_CONTRACT.md` is ready for review. Current runs are pipeline diagnostics, not comparative scores. Run repeated matched trials after calibration and report access, retrieval choice, answer quality, and policy blocks separately.
