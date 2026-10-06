# PACBench agent guide

## Goal and limits

Evaluate every bundled memory provider in the pinned Hermes checkout, plus Memcal,
through normal native Hermes behavior. Pizza Day is a wiring diagnostic, not the
benchmark's destination. Add providers incrementally; currently Holographic and
Mem0 are the next two paths. Never substitute fake retrieval, seed expected facts,
or silently skip a broken provider.

The user set a **$10 total ceiling for this task**, with a shared **$5 per UTC
day** gateway cap across projects. Use **Spark Contributor** only. The separate
`/workspace/muse-gateway` Git repository owns the durable ledger and local
credential; service state lives outside Git in `/workspace/shared/muse-gateway`.
All live authoring, judging, Hermes and Mem0 extraction paths must use the gateway.
Never fall back to upstream credentials or call a paid hosted provider with unknown
charges. `native_pilot` checks the spending service before any live turn. Unknown
usage retains the full reservation. The cap covers routed traffic, not unrelated
applications holding an upstream key. Commit coherent checkpoints and final code;
never commit private credentials, run homes, stores or encrypted artifacts.

## Working rules

- Inspect `git status` and relevant diffs before editing. Preserve saved run artifacts and user changes; do not reset the tree.
- Authors can supply a `scene` plus a short `facts` list of plain-language assertions. The older `scene` + concrete `test_intent` form remains supported. The writer chooses dialogue length within optional `message_range` bounds (2–50 per scene). Reject out-of-range drafts; never pad, duplicate, or truncate dialogue to hit a target. `at` anchors the fictional date when relative dates matter; `timezone` makes each day's time local (including DST). Message times, scene IDs, and thread IDs are otherwise derived. Set a thread ID only to join scenes into one conversation. Do not bring back A–F fixtures, beat files, pizza-specific runners, or a fixed pizza/date/time slot schema.
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

## Current checkout evidence

- Historical `runs/semantic-*` artifacts described below are absent from this environment; those entries are provenance, not revalidated results.
- `runs/pizza-contributor-prepared-20261006` freezes 25 real model-authored messages and five claims. Its preparation audit passes. All subsequent native arms reuse that exact frozen input.
- `runs/pizza-native-holographic-mem0-20261006` completed actual native Holographic and local OSS Mem0 exposure/recall. Both recalled the plan using native tools. Preserve its old 4/5 grades: the judge lacked the public fictional clock, a harness bug fixed generically by giving it the exact question context Hermes received.
- `runs/pizza-native-clock-context-20261006` repeats both arms in fresh homes. Holographic received 5/5 diagnostic judgments. Mem0 received 0/5: exposure made no explicit memory writes and later searches returned no plan. Native trace audit passes. A failed recall is still an authentic diagnostic result, never a green quality pass.
- Mem0 uses the documented OSS route: `mem0ai`, local Qdrant, local Ollama `nomic-embed-text`, and Contributor extraction through the gateway. The pinned Hermes CLI rejects the plugin README's setup flags; preserve that failure and use the README manual configuration route via its native config builder. `sync_max_chars=6000` follows the README's recommendation for 8k embedders, disclosed consistently for all stages. No vendor code or extraction prompts are patched.
- Investigate Mem0's native asynchronous sync: shutdown waits only five seconds; saved gateway extraction calls completed after the exposure process exited. Its second local history is empty. This suggests a lifecycle failure, not a proven extraction-quality diagnosis. Do not force writes, seed facts, extend only this provider's lifetime, or erase the failure.
- Native runs retain Hermes's normal additive built-in memory in scratch homes. Session search is disabled, and recall starts a new session. Report Hermes-plus-provider behavior, never isolated provider quality. The older SDK/MCP semantic condition is separate.
- Provider inventory covers all eight bundled plugins plus Memcal. Unconfigured hosted/back-end paths remain explicit blockers; never imply they were tested.
- Current usage-derived uncached upper bound: $0.0292289 before the Memcal trial, no outstanding reservations. Re-read the gateway ledger for the final amount; this is not an invoice total.
- Offline suite: 212 passed, 2 skipped before adding native-audit regressions. Use `/workspace/pacbench-venv/bin/python` here; `/usr/local/bin/python3` is absent.

## Historical evidence (not present in this checkout)

- `runs/semantic-pizza-native-20260924/` is the first native iMessage Pizza Day run: 25 messages, two threads, one daily batch. Both arms passed retrieval preflight, used memory tools, and received five correct verdicts. Hindsight's discovered `list_memories` call was blocked by the old allowlist; that read-only tool is now allowed.
- `runs/semantic-pizza-correction-20260924/` tests an earlier 6pm proposal replaced by 7pm in one window. Both arms received the same 26 messages and input hash. Memcal answered all six claims; Hindsight's recall and memory listing omitted 6pm, and Hermes admitted it could not compare the times. This is a retrieval/representation diagnostic, not a ranking.
- `runs/semantic-pizza-two-days-20260924/` tests daily deltas: 10 messages and maintenance on day one, 24 messages and maintenance on day two. Both arms had the same input hash, called memory tools, and answered all six claims. That saved run used offset-free timestamps; current examples use a named time zone.
- `runs/semantic-pizza-two-days-offset-20260924/` is the same generated dialogue with `-04:00` timestamps and a neutral Hindsight preflight query. Both arms had matched input hashes, used memory tools, and answered six claims. The text itself was too repetitive, so the current example uses a named time zone and same-thread writer continuity.
- `runs/semantic-pizza-natural-two-days-live-20260924/` revealed a harness policy bug: Hindsight's preflight recall passed, but Hermes's `tool_call` bridge to recall was blocked. Its six misses are invalid as a provider comparison. The bridge is now allowed, and blocked retrieval attempts make an arm invalid.
- `runs/semantic-pizza-natural-v10-live-20260924/` is a full two-day run: 30 messages, 11 then 19 per daily batch, identical input hashes, both retrieval preflights and memory use, six correct claim verdicts per arm. Hindsight got a local batch-shape error, retried one recall, and answered. This is diagnostic, not comparative.
- `runs/semantic-pizza-week-live-v15-20260928/` is the first full seven-day run: 116 messages over six active days (30, 15, 12, 18, 13, 28) and an empty Sunday batch. Both arms had the same input hash, seven valid morning checkpoints, working retrieval access, and actual memory-tool use. Its independent run audit passes with no errors or warnings. Memcal had one missed claim on Sunday; Hindsight missed the 5th Street detail once and the superseded 6pm history on three later mornings. These are uncalibrated diagnostic verdicts, not comparative scores. The saved Hermes JSON stream clipped some tool outputs at 5,000 characters; the isolated session databases retain the full messages. The explorer builder now reads those full saved messages for this run, and future `turn.json` records capture them directly.
- `runs/semantic-20260923T140000Z/calibration-facts/report.json` has nine live judge results matching nine proposed labels; human review is pending. Older runs retain their own frozen bundles and hashes. See `PIZZA_DAY.md` for earlier failures and provenance.

## Next steps

1. Finish the matched native Memcal Pizza Day trial; audit clocks, exact exposures, session boundaries and judge context with `python -m pacbench.native_audit runs/<native-run>`. Native audit does not grade answer strings or establish comparative quality.
2. Investigate the native Mem0 background-sync lifecycle with a normal persistent Hermes session. Preserve immediate-CLI-exit failures; any lifecycle change must apply uniformly and define a separate disclosed condition. Do not increase volume to conceal an unresolved small-case failure.
3. Repeat small cases after harness fixes. Then use the checked-in +100 spec (five distinct ordinary exchanges of 18–22 messages each), freezing once for every arm. The stage generator makes no calls and never pads/truncates dialogue. Defer +1000 until smaller trials are understood; never generate a 100k-line corpus.
4. Add native Hindsight next using its bundled plugin, recommended backend setup and a run-owned namespace, then remaining providers whose real setup and costs can be bounded. Existing SDK ingestion is not evidence for that condition.
5. Review calibration labels before comparative scores. Keep capability, actual tool use, answer judgment, policy failures and cost separate. Successful wiring does not guarantee persistence or recall.

## New code

- `pacbench/native_pilot.py`: native inventory, recommended isolated local setup, read-only preflight, uniform public transcript handoff and fresh-session recall.
- `pacbench/native_audit.py`: independent saved-trace integrity checks, including complete frozen input, clocks, session separation and judge context.
- `pacbench/gateway_client.py`: shared authenticated spending boundary, no upstream fallback.
- `pacbench/stages.py`: bounded incremental authoring specs without filler.
- Generic validity fixes: failed audits produce failed CLI exits; any blocked recall invalidates an arm; provider duplicates fail before paid generation; answer judges receive the public clock; managed MODEL/META bindings support runtime configuration with explicit .env precedence.
