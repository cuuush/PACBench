# PACBench agent guide

## Goal and limits

Evaluate every bundled memory provider in the pinned Hermes checkout, plus Memcal,
through normal native Hermes behavior. Pizza Day is a wiring diagnostic, not the
benchmark's destination. Add providers incrementally; Holographic and local OSS Mem0
are now native paths alongside Memcal. Never substitute fake retrieval, seed expected facts,
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
- Fix Memcal product bugs directly in `/workspace/memcal` and commit them in the Memcal repository, including future fixes. Keep benchmark code and its commits in PACBench. Do not hide provider bugs with provider-specific benchmark workarounds. Record provider code changes as a new test condition and preserve earlier traces.
- Set every native provider process to the frozen scenario's named timezone (`TZ`); Eastern scenarios use `America/New_York`, with seasonal offsets rather than a permanent EST offset. Preserve explicit offsets in clock pins. Do not silently rewrite older source timestamps.
- Authors can supply a `scene` plus a short `facts` list of plain-language assertions. The older `scene` + concrete `test_intent` form remains supported. The writer chooses dialogue length within optional `message_range` bounds (2–50 per scene). Reject out-of-range drafts; never pad, duplicate, or truncate dialogue to hit a target. `at` anchors the fictional date when relative dates matter; `timezone` makes each day's time local (including DST). Message times, scene IDs, and thread IDs are otherwise derived. Set a thread ID only to join scenes into one conversation. Do not bring back A–F fixtures, beat files, pizza-specific runners, or a fixed pizza/date/time slot schema.
- Generate dialogue and a short writer note, verify that the dialogue establishes the scene and each fact, then freeze it. Derive and review an evidence-cited claim contract before either provider runs. Both arms get the same frozen messages and question. Providers and the scoring judge must not receive the private scene or test intent.
- The LLM decides claim meaning. Python validates IDs, citations, response shapes, and counts; it does not use string matching, date expansion, or entity substrings to score answers. Missing or malformed judge output leaves a run ungraded.
- Treat retrieval capability and Hermes behavior separately. Hindsight needs isolated SDK import, MCP connection, namespaced discovery and allowlist access, and a successful read-only bank recall. Record whether Hermes actually called recall. Pin Memcal maintenance and prefetch to the fictional clock.
- Preserve saved run artifacts and their original hashes. Do not attach an old run to an edited fixture by source ID alone.

## Follow-up policy (user-authorized)

- Bind the recovery policy to the frozen contract before provider arms run. Do not author/review unused per-fact questions upfront. After grading, Spark writes ONE short message asking for ALL missing or wrong facts, in an isolated call given only the original question and those facts; one short independent review checks coverage and answer leakage. Freeze and reuse the wording for matching missing-fact sets across arms.
- If zero expected facts are correct in the initial answer, skip recovery entirely: no question authoring/review, Hermes turn or recovery judge. Record `no_initial_correct_facts`, keep the zero score and continue any separately planned evaluation questions. New recovery reports use protocol 4; audit historical protocols using their original rules.
- Otherwise ask at most one recovery message total, continuing the real recall conversation and fictional clock. Exclude correct facts; ask nothing if all are correct. Preserve the initial grade and award half credit separately per recovered fact.
- Opening retained memory can recover a fact. Explicit archive/original-source/message-connector access fails retention even if the answer becomes correct. Log attempted and blocked fallbacks. Unknown tool operations need review and get no retention credit.
- Log arguments/results, direct and nested call counts, discovery, repeated/failed operations, latency, reported token deltas and gateway project cost windows. Unknown usage/cost stays unknown; do not invent tool prices or simulate a connector.
- Audit the one-message-total limit, complete missing-fact coverage, session continuity, question hashes and recovery points. Legacy per-fact (protocol 1) and component-based combined (protocol 2) trials remain separately auditable; protocol 3 retains historical minimal isolated authoring, while new reports use protocol 4 with the zero-recall gate. See [followups.md](docs/followups.md). Do not rewrite older runs or erase their first-answer penalties.

## Code and checks

- Examples: `examples/pizza_day.json` and its one-window and two-day correction variants. New bounded stages: `examples/pizza_two_day_stage.json` (one time change over two days) and `examples/pizza_two_trips_three_days.json` (two companions, independent threads, time/venue changes over three days). Score explicit commitment status and preserve older contracts. The retired hand-built corpus, lexical adapter, and old browser were removed.
- Scene generation and verification: `pacbench/authoring.py`; audited model calls: `pacbench/model_calls.py`.
- Frozen claims and question: `pacbench/semantic_contract.py`; semantic verdicts: `pacbench/semantic_grade.py`.
- Generic runner and retrieval preflight: `pacbench/semantic_experiment.py`; provider seams: `provider_adapters/`.
- Explorer: `scripts/build_explorer_data.py`, `explorer/`, `serve_explorer.py`.
- Run `/workspace/pacbench-venv/bin/python -m pytest -q`, `node --check explorer/dashboard.js`, and the explorer data builder after relevant changes. MCP seam tests need loopback access. For live integration, use a bounded spec, inspect its saved JSON, and run `python3 -m pacbench.run_audit runs/<run-id>`; mocked tests alone do not establish provider behavior.
- Keep full model request, response, model identity, token usage, and reported cost in private run artifacts. The scene writer uses Meta Responses with stateless encrypted reasoning replay on revisions; the verifier and answer judge remain single-turn. Explorer data must redact `encrypted_content`. If the API omits a dollar charge, record it as unavailable.

## Current checkout evidence

- Historical `runs/semantic-*` artifacts described below are absent from this environment; those entries are provenance, not revalidated results.
- `runs/pizza-contributor-prepared-20261006` freezes 25 real model-authored messages and five claims. Its preparation audit passes. Earlier small native arms reuse that exact frozen input; later stages have separately frozen bundles.
- `runs/pizza-native-holographic-mem0-20261006` completed actual native Holographic and local OSS Mem0 exposure/recall. Both recalled the plan using native tools. Preserve its old 4/5 grades: the judge lacked the public fictional clock, a harness bug fixed generically by giving it the exact question context Hermes received.
- `runs/pizza-native-clock-context-20261006` repeats both arms in fresh homes. Holographic received 5/5 diagnostic judgments. Mem0 received 0/5: exposure made no explicit memory writes and later searches returned no plan. Native trace audit passes. A failed recall is still an authentic diagnostic result, never a green quality pass.
- Mem0 uses the documented OSS route: `mem0ai`, local Qdrant, local Ollama `nomic-embed-text`, and Contributor extraction through the gateway. The pinned Hermes CLI rejects the plugin README's setup flags; preserve that failure and use the README manual configuration route via its native config builder. `sync_max_chars=6000` follows the README's recommendation for 8k embedders, disclosed consistently for all stages. No vendor code or extraction prompts are patched.
- Investigate Mem0's native asynchronous sync: shutdown waits only five seconds; saved gateway extraction calls completed after the exposure process exited. Its second local history is empty. This suggests a lifecycle failure, not a proven extraction-quality diagnosis. Do not force writes, seed facts, extend only this provider's lifetime, or erase the failure.
- Native runs retain Hermes's normal additive built-in memory in scratch homes. Session search is disabled, and recall starts a new session. Report Hermes-plus-provider behavior, never isolated provider quality. The older SDK/MCP semantic condition is separate.
- Provider inventory covers all eight bundled plugins plus Memcal. Unconfigured hosted/back-end paths remain explicit blockers; never imply they were tested.
- Latest usage-derived uncached upper bound: $0.4752186 including $0.1081344 in reservations at the three-day snapshot. Re-read the gateway ledger before further work; this is not an invoice total.
- Latest offline suite: 233 passed, 2 skipped; gateway suite: 17 passed. Use `/workspace/pacbench-venv/bin/python` here; `/usr/local/bin/python3` is absent.

- `runs/pizza-native-one-followup-20261006`: fresh Memcal and Holographic arms, same 25 messages. Memcal missed the street initially, then recovered it with exactly one native `memcal_open(E1)` and no source search; initial 4/5, separate recovery 4.5/5. Holographic scored 5/5 initially and got no follow-up. Both trace audits pass. The recovery turn took 33.71s including two driver model calls, with a $0.002847 gateway cost upper bound.

- Minimal isolated follow-up trials: Memcal 4/5 then 4.5/5 with one event open; Holographic 5/5; Mem0 varied between 5/5 and 0/5 with no recovery after three searches in each of two combined follow-ups. Final Mem0 repetition scored 5/5 and needed no follow-up. Full traces and policy-version distinctions are documented in [live test results](docs/followup-tests-2026-10-06.md).

- User-requested staged tests: 25-message retest, 20 messages over two days, then 50 over three days with two independent outings and changes. After the direct Memcal clock fix (`523e76b`) and uniform Eastern process configuration, diagnostic verdicts were Memcal 15/15, Holographic 13/15, Mem0 15/15. Earlier source-read penalties and failed recalls remain preserved; Memcal also had a stale headline. See [staged results](docs/pizza-three-day-tests-2026-10-06.md). The historical follow-up coverage bug was fixed with a minimal semantic instruction and isolated live author/reviewer regression; old recovery questions were not replaced.

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

1. The one-combined-follow-up policy has been exercised in repeated small native trials; see [results](docs/followup-tests-2026-10-06.md). Add explicit commitment-status coverage in a new frozen contract before increasing complexity; Memcal volunteered a wrong tentative status outside the original five claims. Preserve old grades. Continue testing the one-follow-up policy. Distinguish retained-detail opens from archive/message fallback, preserving first-answer misses and recovery effort. Recheck clocks, exact exposures, session boundaries and judge context with `python -m pacbench.native_audit runs/<native-run>`. Native audit does not grade answer strings or establish comparative quality.
2. Investigate the native Mem0 background-sync lifecycle with a normal persistent Hermes session. Preserve immediate-CLI-exit failures; any lifecycle change must apply uniformly and define a separate disclosed condition. Do not increase volume to conceal an unresolved small-case failure.
3. Run the user-requested two-day and two-trip/three-day stages before increasing unrelated volume. Freeze and verify each once, then give every native arm the same messages and contract. Preserve the shared opening conversations through the writer cache, checking their realized text rather than assuming cache identity. Repeat small cases after harness fixes. Then use the checked-in +100 spec (five distinct ordinary exchanges of 18–22 messages each), freezing once for every arm. The stage generator makes no calls and never pads/truncates dialogue. Defer +1000 until smaller trials are understood; never generate a 100k-line corpus.
4. Add native Hindsight next using its bundled plugin, recommended backend setup and a run-owned namespace, then remaining providers whose real setup and costs can be bounded. Existing SDK ingestion is not evidence for that condition.
5. Review calibration labels before comparative scores. Keep capability, actual tool use, answer judgment, policy failures and cost separate. Successful wiring does not guarantee persistence or recall.

## New code

- `pacbench/native_pilot.py`: native inventory, recommended isolated local setup, read-only preflight, uniform public transcript handoff and fresh-session recall.
- `pacbench/native_audit.py`: independent saved-trace integrity checks, including complete frozen input, clocks, session separation and judge context.
- `pacbench/followups.py`: frozen neutral follow-up plans, one-attempt recovery, partial-credit scoring, source-fallback failures and full retrieval effort logs.
- `pacbench/gateway_client.py`: shared authenticated spending boundary, no upstream fallback.
- `pacbench/stages.py`: bounded incremental authoring specs without filler.
- Generic validity fixes: failed audits produce failed CLI exits; any blocked recall invalidates an arm; provider duplicates fail before paid generation; answer judges receive the public clock; managed MODEL/META bindings support runtime configuration with explicit .env precedence.

## Flexible checks and shared Hermes sessions

- Use `checks: [{id, prompt, facts, at?}]` for user-supplied questions and expected facts. Freeze each contract against the verified dialogue and review prompt elicitation before native arms run. Preserve the supplied wording; failed truth or elicitation review blocks execution. Keep the schema topic-independent. See [flexible-checks.md](docs/flexible-checks.md).
- `exposure_context` and `evaluation_context` independently select `shared` or `isolated`; `exposure_delivery: scenes` delivers one scene per ingestion turn. Evaluation starts fresh after ingestion and resumes its actual session for later checks/recovery. Prior public answers resolve references only, never substitute for facts missing in the latest answer. Audit clocks, session IDs, token deltas and each message's judge criteria.
- In a shared evaluation context, prior source/archive/message reads or unknown operations also invalidate later retention credit. Preserve the original answer grades and log inherited failure explicitly.
- `python -m pacbench.evaluation --prepared ... --spec ... --output ...` freezes new checks against unchanged source dialogue. Preserve original artifacts; record a separate condition and input provenance.
- Explorer must include native runs, planned checks, each message's expected facts/verdicts, exact judge prompt/response/model and actual tool calls. Never export credentials, provider home files or encrypted reasoning.

- Shared-context stage: `examples/pizza_shared_context.json`, 49 verified messages, five ingestion turns in one session, two evaluation questions in a separate shared session. Memcal answers 16/16 but retention 0/16 due to original-source opens; Holographic and Mem0 answer 15/16 and fail their one original-time recovery. Holographic full audit and both completed arms' sequence audits pass; the Memcal/Mem0 run also contains explicitly blocked Honcho and remains incomplete. See [shared-context results](docs/pizza-shared-context-tests-2026-10-06.md).
- New recovery policies use `specific-missing-facts-v2`: ask for the specific missing detail and reject broad recaps that can omit it. Keep earlier policy wording and saved questions unchanged; do not give providers additional retries to repair benchmark wording.
- Latest offline suite: 240 passed, 2 skipped. Live Chromium checks verify per-message criteria, missed/wrong recovery verdicts, tool logs and source-retention failure visibility. A new-prompt preparation against the unchanged 49-message bundle also passed its live review/audit.

- Final shared-context accounting snapshot: $0.5751122 cumulative upper bound including $0.1081344 held. The isolated specificity regression rejected the broad follow-up and approved a precise original-time question without another provider turn.

- Zero-recall recovery gate verified: 244 passed, 2 skipped. All-missing, all-wrong and mixed zero-correct answers invoke no recovery model/provider calls; later planned shared questions still execute. Historical zero-recall protocol-3 trials and the shared-context trial retain passing audits under their original policies. No paid calls were needed for this policy change.
