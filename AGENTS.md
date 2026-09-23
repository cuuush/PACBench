# PACBench agent guide

## Working rules

- This worktree contains uncommitted work. Inspect `git status` and relevant diffs before editing. Preserve saved run artifacts and user changes; do not reset the tree.
- Authors can supply a `scene` plus a short `facts` list of plain-language assertions. The older `scene` + concrete `test_intent` form remains supported. The writer chooses dialogue length. `at` anchors the fictional date when relative dates matter; message times, scene IDs, and thread IDs are otherwise derived. Set a thread ID only to join scenes into one conversation. Do not bring back A–F fixtures, beat files, pizza-specific runners, or a fixed pizza/date/time slot schema.
- Generate dialogue and a short writer note, verify that the dialogue establishes the scene and each fact, then freeze it. Derive and review an evidence-cited claim contract before either provider runs. Both arms get the same frozen observations and question. Providers and the scoring judge must not receive the private scene or test intent.
- The LLM decides claim meaning. Python validates IDs, citations, response shapes, and counts; it does not use string matching, date expansion, or entity substrings to score answers. Missing or malformed judge output leaves a run ungraded.
- Treat retrieval capability and Hermes behavior separately. Hindsight needs isolated SDK import, MCP connection, namespaced discovery and allowlist access, and a successful read-only bank recall. Record whether Hermes actually called recall. Pin Memcal maintenance and prefetch to the fictional clock.
- Preserve historical hashes and their saved evidence. An old run without its original full bundle or prefetch trace must say `not captured`; never attach it to an edited fixture by source ID alone.

## Code and checks

- Prose examples: `examples/ice_cream.json`, `examples/pizza_day.json`, `examples/movie_night.json`.
- Scene generation and verification: `pacbench/authoring.py`; audited model calls: `pacbench/model_calls.py`.
- Frozen claims and question: `pacbench/semantic_contract.py`; semantic verdicts: `pacbench/semantic_grade.py`.
- Generic runner and retrieval preflight: `pacbench/semantic_experiment.py`; provider seams: `provider_adapters/`.
- Explorer: `scripts/build_explorer_data.py`, `explorer/`, `serve_explorer.py`.
- Run `/usr/local/bin/python3 -m pytest -q`, `node --check explorer/dashboard.js`, and the explorer data builder after relevant changes. MCP seam tests need loopback access. For live integration, use a bounded spec and inspect the saved artifacts in the browser; mocked tests alone do not establish provider behavior.
- Keep full model request, response, model identity, token usage, and reported cost in artifacts. If the API omits a dollar charge, record it as unavailable.

## Current evidence

- `runs/semantic-20260922T210816Z/` is the bounded pizza run. Both arms used the same bundle and contract and were marked valid. Hindsight's preflight reached its test bank, and its scored turn called `mcp__hindsight__recall`. Both answers received five correct claim verdicts. This single run is a diagnostic, not a comparative conclusion.
- `runs/semantic-20260922T215352Z/` is the corrected movie run. Its verified dialogue and frozen contract independently cover movie, theater, time, F11, and F12. Both arms had retrieval access, called it, and received five correct claim verdicts. The earlier `runs/semantic-20260922T214949Z/` is excluded from comparisons because its frozen question falsely said the user bought tickets that Sam bought; its original artifacts remain available.
- `examples/ice_cream.json` exercises thread description plus three required facts. `runs/semantic-20260923T022100Z/` is a real model-built, verified, prepared ice-cream scene with a three-claim frozen contract; no provider arm ran. An earlier verifier false positive remains excluded in `runs/semantic-20260923T021900Z/`. `docs/authoring-options.md` compares the authoring designs and lists the gap to the full benchmark contract.
- `runs/semantic-20260923T025244Z/` is the latest prepared ice-cream scene. Its writer returned a saved creative note; the verified 20-message chat and frozen three-claim contract are shown in the explorer. No provider arm ran.
- `runs/semantic-20260922T210816Z/calibration-v2/report.json` has nine judge results matching nine **proposed** labels. Those labels in `calibration/pizza_answers.json` are pending human review.
- `docs/pizza-boundary-diagnosis.md` records the old Memcal host-clock brief and the limits of what its checkpoint artifacts captured. Historical pizza bundle hash `c7e5b2e7...` differs from the current fixture hash; the explorer uses each run's saved exposure.
- `docs/revoke-week-diagnosis.md` records the per-turn Memcal snapshots recovered from Hermes's saved session. They refreshed and were injected with the fictional date. The old life runner ingested future evidence before replaying earlier user turns; a September 8 archive search returned the September 9 cancellation email. The explorer flags timestamp-proven historical exposures and excludes those arms. The life runner now advances ingestion, maintenance, and replay in fictional time order.

## Next steps

1. Review the nine proposed calibration labels with the author, especially uncertainty and historical mentions. Resolve disagreements and rerun calibration before using semantic scores comparatively.
2. Decide whether fact-authored threads should replace or coexist with intent-only authoring after reviewing the prepared ice-cream dialogue and contract. The fact list is a truth target; verifier citations are required before freezing. Do not score intended but unspoken facts.
3. Add a correction or ambiguity scenario to test stale versus current claims. Add a cross-thread contradiction scan and staged evidence/checkpoints before building seven-day lives. Preserve every failed preparation and provider attempt. The viewer can show multiple saved Hermes exchanges, but the semantic runner still needs a frozen multi-question contract and matched provider turns before using that path. Rerun a bounded temporal life after the chronology fix before trusting any new life scores.
4. Build three reviewed development lives that cover the contract's temporal, collision, cross-source, uncertainty, and proactive cases. Then plan calibration and held-out story families. Do not present the short pizza/movie pilots as the full benchmark.
5. Only after calibration and broader reviewed coverage, run repeated matched trials and compare providers. Report access failures and Hermes retrieval-policy failures separately from answer quality.
