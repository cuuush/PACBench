# Probe Dispositions

Running log. Each entry: what was established, what was not, attribution,
comparative-validity impact. Realizations are never deleted or overwritten.

## P0-9 realization 1 — INCONCLUSIVE

- Trace: `runs/probe-p09-20260922T032018Z/trace.json` (preserved).
- Submitted: 2 msgs, one stable key `text:fam`, `update_mode=append`, chronological.
- Engine: `retained 0/2` (2 empty-id returns), TokenUsage zeros, 1 call, 2.37s.
- Established: PACBench submission machinery (stable key, delta-only, append mode).
- Not established: anything about append association/recall (empty provider state).
- Attribution: provider-side empty return; PACBench + adapter behaved correctly.
- Impact: none on comparative validity (no score, no claim).

## P0-10 realization 1 (first substantive; proc_3fc94cb10ae0 was execution zero) — INCONCLUSIVE

- Trace: `runs/probe-p10-20260922T034737Z/trace.json` (preserved).
- Submitted: 2 facts, one stable key `text:probe-tx`, `update_mode=append`,
  event_dates 2026-03-01 / 2026-09-18, one batch.
- Engine: `retained 0/2` (2 empty-id returns), TokenUsage zeros, 1 call, 2.49s.
- Established: submission side again correct; `return_usage=True` verified
  shape-only in engine source (`memory_engine.py:5354,5559,5586`) — not the cause.
- Not established: the entire temporal-ranking question (nothing retained).
- Attribution (revised): pattern is now 0/2 on the new conversation-append path
  twice in a row (P0-9r1, P0-10r1) vs 5/8 partial retention on the old
  per-message-replace path in smoke, with ~2.4s vs ~28s wall-clock. Reclassify
  from "stochastic drop" to **suspected systematic append-on-new-document
  interaction**: folded append execution against a nonexistent document yielding
  zero facts with zero usage. Ruled out by code reading: op-id dedup (none
  exists), defense-block (wrong result shape — that path yields `[]`, not
  `[[],[]]`), delta-None (falls back to full retain), return_usage (shape-only).
- Impact: none on comparative validity yet; blocks the temporal-equivalence
  question until resolved.
- Branch action: **paid progression STOPPED** per predeclared disposition.
  P0-9 realization 2 is ON HOLD (not cancelled — it is paid and the halt covers it).
  HISTORICAL 2026-09-22: r2 subsequently ran and finished INCONCLUSIVE; the halt
  stands until the transport-remediation validation.

## P0-9 remediation validation, corrected-harness run — cycles 1–2 PASS, cycle-3 CONSOLIDATION-DROP (provider finding)

- Trace: `runs/probe-p09-20260922T150359Z/trace.json` (99.6s). Corrected checker
  performed as proven: no false alarms.
- **Cycle 1: PASS both flavors** (2 results, day-1 recoverable, no leak).
- **Cycle 2: PASS both flavors** (3 results) — including cross-day ASSOCIATION:
  "originally planned after 5pm ... rescheduled to 6pm ... confirmed by Mia as
  Gus was running late". Multi-day append association WORKS. This is what P0-9
  was designed to test, now demonstrated.
- **Cycle 3: consolidation-drop, CONFIRMED by targeted re-recall.**
  Distinguisher (`runs/targeted-p09c3-20260922T151134Z/trace.json`, 8.5s, 3
  recall ops, zero retains): bank alive (inventory returns facts), but targeted
  "Was the car washed, and what paperwork was mentioned?" still misses day-3.
  The cycle-3 memory is attributed to day-3 obs (metadata source_ids
  ['s-06','s-05']) yet its TEXT is the old Sept 18/19 summary — consolidation
  reprocessed the combined doc (6090 tokens ~2x) and kept the prior summary,
  dropping the new sentences. Recall ranking exonerated; write-side confirmed.
- Meaning for PACBench: this is architecture-behavior signal, not a blocker —
  Hindsight summarizes on append-reprocess and may lose detail sentences, while
  attribution metadata stays correct. Exactly the kind of provider difference
  the benchmark exists to measure. Preserved as a provider finding; scored
  runs must expect summarization loss on long evolving docs.

- First attempt (`runs/probe-p09-20260922T145911Z`): retention SUCCEEDED 1/1
  (3018/107, 26.9s) but the script misread packet-count "1/1" as failure
  (stale per-message "2/2" check). Fixed to X==Y packet parsing.
- Second attempt (`runs/probe-p09-20260922T150128Z`): retention 1/1 again, but
  the verdict check demanded VERBATIM source texts inside recall output — while
  recall correctly returns EXTRACTED FACTS ("Mia plans to pick up the car..."
  + "Car is ready on Friday... involving Gus", all text:fam, 4 results, 2
  source facts, correct temporal grounding). End-to-end memory WORKS; the
  checker was wrong. Fixed to majority-token coverage (`_covered`), proven by
  offline replay over the stored trace: raw True, synth True, no future leak
  in either flavor — cycle 1 would have PASSED and advanced.
- Lesson (twice learned): read the trace, never trust the verdict line.
  Corrected harness re-running on identical frozen evidence to complete the
  authorized validation (recall phases 1–3).

## P0-9 realization 2 — INCONCLUSIVE (empty again; STOP per record)

- Trace: `runs/probe-p09-20260922T041624Z/trace.json` (preserved alongside r1).
- Identical signature: cycle 1 `retained 0/2`, TokenUsage zeros, 2.34s.
- Decision-record rule fires: **no realization 3. Paid progression stops;
  redesign only after review.**

## Zero-spend hardening (directive steps 6+8) — DONE, suite 410 green

- Exposure: `from_owner` + `structured` pinned (8→10 fields); authorship tests
  assert the catch. Verified 408, now 410 with the ablation work.
- Ablation: time filter compares parsed datetimes (naive→UTC, `ValueError` on
  garbage, no string fallback); mixed-offset adversarial test included.
- Remaining cleanup (flag #2 op-id hardening, comment wording, version registry)
  deliberately queued behind the live-path decision — op-id touches the adapter.

## Zero-spend archaeology (2026-09-22) — multi-item same-doc is ON-contract

Offline payload reconstruction (pure helpers only, no engine): the passing
single-item and failing two-item requests differ in exactly ONE material field —
item count sharing one `document_id`. Mode, key scheme, key order, metadata
keys, op-id derivation all identical.

Pinned 0.10.0 `retain_batch_async` docstring promises the shape: "Store multiple
content items as memory units in ONE batch operation", with `doc1, doc2, doc1`
in its own example; ingress comment: "items sharing a document_id are ALLOWED
here and folded into one document". `RetainContentDict` carries per-item
`document_id` + `update_mode` as first-class keys.

Refined suspect: **multi-item `append` group against a nonexistent document**
(doc-creation batch) — the shared-doc append group's combined-body/base
resolution short-circuits before extraction fan-out
(`memory_engine.py:5438-5456` → grouping → append-base read hitting the no-row
case; cf. `orchestrator.py:368-369`). Single-item append-on-new-doc works, so
this is specifically the grouped path, not append alone. Exact inner gate
unverified (needs engine tracing).

Consequence: PACBench's batching is adapter-correct, not off-contract favoritism
— and the empty return on a SUPPORTED shape is provider behavior worth
preserving either way. The three-arm discriminator remains the right next paid
step (arm A replays the exact failing shape).

## Adapter fix applied (2026-09-22) — one item per packet, suite 410 green

- `provider_adapters/hindsight_adapter.py`: one joined-transcript item per
  packet (`sender (written_at): text` lines, chronological), packet metadata
  with full source-id list, same doc ids, append/replace unchanged, op-id +
  telemetry + transport untouched. `retained X/Y` now counts packets.
- Tests + `docs/hindsight-update-semantics.md` updated to the new shape.
- Independently verified: 410 passed, 2 skipped.
- Audit note: P0-10 fixtures split one doc into two (same evidence); P0-9
  CYCLES byte-identical. Decision record addendum written.

## Doc-hunter findings (2026-09-22) — vendor docs explain the shape

Three parallel doc tracks, all read-only:

- **Retain reference:** batches blessed ("Batch ingestion is the recommended
  approach") but EVERY multi-item example uses distinct doc ids; append framed
  as one incremental item onto an *existing* document ("a new message arrives —
  just retain again", "send only the new content"). Key line: **"A full
  conversation should be retained as a single item."**
- **Troubleshooting:** GitHub issue vectorize-io/hindsight#3363 (closed) — the
  HTTP server REFUSES batches whose items share a `document_id` with **400**
  ("must have a unique document_id to avoid race conditions"), even though the
  OpenAPI example shares one id. Our embedded path never surfaces 400; the
  empty-[[],[]]-with-zero-usage is plausibly the local-engine translation of
  that same guard. Next-step if pursued: capture raw status/body + poll
  operations/`memory_unit_count`/`no_facts` metric to confirm.
- **Concepts:** document = source container/idempotency key; native evolution =
  sequential retains (re-send full content + replace, or single-item append
  deltas); batches = many DISTINCT documents. No guide shows two same-id items
  in one batch; append-on-absent never blessed as a creation path.
- Debug knobs available (unused so far): prompts/preview, dry-run extraction
  endpoint, per-bank LLM tracing, `HINDSIGHT_API_LLM_HTTP_LOG_LEVEL=DEBUG`.

## Candidate adapter fix (NOT applied — needs review)

> SUPERSEDED 2026-09-22 by "Adapter fix applied" below — the fix was reviewed,
> approved, and applied. Kept as audit history.

Join each conversation packet into **one content item** (chronological transcript,
same doc id, append) instead of one item per message. This matches the vendor's
stated pattern ("a full conversation as a single item", "send only the new
content"), dodges the duplicate-id guard entirely, preserves SMART 1-call/day,
and keeps delta-only ingest. It is adapter correctness per vendor docs, not
benchmark favoritism — but it changes packetization, so it waits for explicit
review, not a quiet fix.

## Three-arm discriminator (2026-09-22) — GROUPING-PATH isolated

- Trace: `runs/discrim3-20260922T142202Z/trace.json` (preserved). 4 retains,
  0 recalls, 194.1s.
- Arm A (grouped same-doc, 1 call): **0/2, zero tokens** — reproduces the probe
  empties exactly.
- Arm B (sequential same-doc, 2 calls): **1/1 + 1/1, full extraction**
  (~2.9k in each) — append-onto-existing-doc works, including the second call.
- Arm C (grouped two-docs, 1 call): **2/2, full extraction** — multi-item
  invocation is fine.
- Verdict GROUPING-PATH: the trigger is specifically the engine's same-document
  group path on document creation. Sequential appends and multi-doc batches are
  fully functional.
- Consequences:
  - P0-9's association question is now testable through the B-proven path:
    sequential one-item ingests preserve identical logical evidence exposure
    while using demonstrated-extraction calls. Requires explicit go (paid).
  - Open adapter question (NOT decided): whether the adapter should avoid the
    grouped same-doc shape (one-item-per-call for same-doc groups) or preserve
    it as provider behavior. Cost implication is large either way for race-week
    (multi-message day-packets), so this needs review, not a quiet fix.

## Revised attribution (scoreboard, all live retains)

> SUPERSEDED 2026-09-22: the `5/6 vs 0/6` table below is stale. Rolled-up
> item-level totals after discrim3: **one-item-per-document paths 9/10
> retained** (smoke 3/4 + discrim A/B 2/2 + discrim3 B 2/2 + C 2/2) vs
> **same-document multi-item grouping 0/8** (P0-9r1, P0-10r1, P0-9r2, discrim3 A).
> The "vary item-count" experiment below has happened (discrim3). Kept history.

Scoreboard across all live retains (stale — see marker above):

| shape | result |
|---|---|
| single item / single doc (smoke per-message replace) | 3/4 retained |
| single item (discrim A replace + B append-on-new-doc) | 2/2 retained, full extraction |
| **two items / one doc (P0-9r1, P0-10r1, P0-9r2)** | **0/6 retained, extraction never ran** |

The split is 5/6 vs 0/6 on **batch-item-count per document**, not on
append-vs-replace (exonerated 2/2). Suspect: the engine's fold/grouping path for
multi-item same-doc batches ("Only appends fold", fold-plan contiguous-prefix
rules) producing an empty execution — a question for engine-side investigation,
not an adapter redesign yet. The discrim proved the adapter's packets, modes,
op-ids, and telemetry correct in both arms; the next experiment (post-review)
should vary item-count per doc (1×2-docs vs 2×1-doc, same content), not mode.

## Spend ledger (corrected; units kept distinct)

Earlier chat message ("≈8 micro-calls") did not reconcile: 2+2+6=10, not 8.
Correct accounting per the unit chain:

| stage | count |
|---|---|
| observations/items submitted | 8 (P0-9r1: 2, P0-10r1: 2, discrim: 1+1, P0-9r2: 2) |
| provider retain requests | 5 (one per ingest call: r1, r1, A, B, r2) |
| extraction invocations observed | 2 (discrim A+B; zero-token requests count as NOT observed) |
| LLM calls observed | not separately measurable — folded into extraction-observed |
| reported tokens | ~5.9k in / ~260 out (discrim arms only; probes reported zeros) |
| unreported usage | 3 empty requests (zeros) + 4 smoke requests (pre-telemetry nulls) |
| estimated cost | fractions of a cent; $30 cap essentially untouched |

Rule going forward: never blur observations → requests → extraction → tokens → cost.
A zero-token request is NOT a model call.

Rolled forward with discrim3 (2026-09-22): +6 items submitted (14 total), +4 retain
requests (9 total), +3 extraction-observed (B×2, C×1 — 5 total; A3 empty),
+~11.7k in / ~440 out (≈17.6k / ≈700 cumulative), +1 unreported (A3).

## P0-10 status — PASS (remediation realization, corrected checker)

- Trace: `runs/probe-p10-20260922T151636Z/trace.json` (preserved). Ingest 2/2
  (0 empty-id, 5975 in / 270 out); submission keys exactly
  {text:probe-tx-new, text:probe-tx-old}.
- The run printed INCONCLUSIVE only because the checker demanded the VERBATIM
  source sentences ("the lake cabin rental ends October 5") inside extracted-
  fact prose ("...ends ON October 5, 2026 | When: ...") — impossible on its
  face (third occurrence of the same verbatim-checker defect class as P0-9).
- Corrected checker (month-and-day anchored coverage; `_temporal_covers`,
  cross-validated no month cross-match) replayed over the PRESERVED ordered
  evidence — no new paid ops, deterministic reclassification, not a re-run:
  - with-control RAW: October #1/#2 (final 1.0999/1.0998), March #3/#4
    (0.9942/0.9941) → positions (old=2, new=0), new above old.
  - without-control RAW: IDENTICAL order (old=2, new=0; March 0.9922/0.9921).
  - synthesized both arms: new-first.
- Verdict under the frozen taxonomy: **PASS** — with-control RAW ranking
  follows simulated age (October above March), and the uncontrolled arm ranks
  the same, so NO wall-clock dependency is evidenced (plain PASS, not
  pass-with-dependency). Ranking deterministic to full float precision across
  arms. Re-running live for identical evidence would spend 5 ops to relearn
  nothing; the reclassification is recorded here with the replay method.
- Temporal gate: CLOSED. Combined with P0-9 cycles 1–2 PASS + cycle-3
  consolidation-drop finding, both spend-gated probes are now classified.

## Trace stubs and superseded runs (completeness)

- 2026-09-22 cleanup: all probe/discrim/targeted trace dirs and Sep 20–21
  smoke-era checkpoint/pilot/life dirs were deleted per operator direction
  (1.4GB → 114MB; runs/ is gitignored scratch). Findings from every deleted
  trace are preserved in prose in this file; only the four audit arms
  (life-20260922T153714Z/154042Z/155428Z/160232Z), frozen bundles, stdout
  logs, and the render bundle were kept. Trace paths cited above that no
  longer exist on disk are marked by this note.

- `runs/targeted-p09c3-20260922T150951Z/trace.json` (255B): SUPERSEDED stub —
  first targeted-recall attempt, failed before any provider op with
  `AttributeError: _config_resolver` (recall-first session needs explicit
  engine `initialize()`; retain path does it implicitly). Zero spend, zero
  evidence. Superseded by `targeted-p09c3-20260922T151134Z` (WRITE-SIDE).
- `runs/probe-p10-20260922T151636Z/trace.json` (28KB): the P0-10 remediation
  realization classified PASS above (ingest 2/2, October-above-March both
  arms, corrected checker replay).

## Exposure-authorship adversarial test — GATE STRENGTHENED (zero-spend)

- `tests/test_exposure_authorship.py`: sender-only and thread-only mutations
  fail loudly (digest differs, equivalence raises). `from_owner` flip initially
  MISSED (digest identical) — documented, then strengthened.
- Strengthening: `from_owner` + `structured` pinned into the exposure projection
  (8→10 fields; `pacbench/exposure.py`). `Observation.public()` already carried
  both; no adapter/live/probe changes. Test flipped to assert the catch, plus a
  `structured`-flip test. Suite: **408 passed, 2 skipped**.
- Directive item closed: authorship-equivalence now enforced by the gate itself,
  not just the bundle hash.

## Discriminating experiment (replace vs append, 2026-09-22) — STOCHASTIC

- Trace: `runs/discrim-20260922T041513Z/trace.json` (preserved). 2 retains,
  0 recalls, 55.7s total.
- Arm A (replace, smoke shape): retained 1/1, 2942 in / 144 out, 24.08s.
- Arm B (append on brand-new doc, probe shape): retained 1/1, 2941 in / 118 out,
  23.98s — matches smoke's ~28s extraction profile, real token telemetry.
- Verdict STOCHASTIC on the tested axis with a wording correction: **single-item
  append on a fresh document is validated** (arm B retained with full extraction),
  but append is only *partially* exonerated — multi-item same-document append
  remains suspect (see below). The probe empties (2.4s, zero tokens, extraction
  never ran) were not caused by append-on-new-doc alone.
  Bonus data point: per-retain cost ≈ 3k in / 130 out — fractions of a cent.
- Side observation (infra noise, not validity-relevant): engine LLM-trace writer
  logs cross-loop write failures (`llm_trace.py:581`, bad file descriptor) —
  trace-recorder noise, retain/recall payloads unaffected.
- Consequence per predeclared logic: resume probe order → P0-9 realization 2
  launched (proc_3fdeba6379cc, 9 ops). HISTORICAL: r2 ran, INCONCLUSIVE, halt
  stands. P0-10's ranking question still needs a transport-remediation
  realization — authorized by the 2026-09-22 plan ONLY after P0-9 remediation
  demonstrates ingestion, not yet.

## First bounded audit — LAUNCHED (revoke-week × memcal, then hindsight)

- Authorization: user go 2026-09-22, $30 hard cap, rate card explicitly
  unverified (record observed telemetry, never guess cost).
- Frozen bundle: `runs/revoke-week-bundle.json` hash
  `2f5a1a739c0285340eff3cd8974f47210dbfecfa99360b7b3692737182ec1093`
  (47 obs, 6 checkpoints). Sequential arms (memcal → hindsight) for clean
  failure attribution; PACBENCH_ALLOW_MAINTENANCE_SPEND=1 (multi-day gate,
  spend recorded via runs rows / maintenance telemetry).
- Arm 1: proc_3a3e8afd9c01 (memcal, --timeout 1500). Oversight: deleg_9deb35c3.
- Arm 1 DONE exit 0 → `runs/life-20260922T153714Z`: memcal F1 0.667 (4/6
  correct; duplicate 1, stale 1, misrepresented 2, missed 0;
  cancel-reversal 3/4, deletion 1/2, historical 0.5). Note: "retired 1
  spooled items older than 2026-08-23" (archive hygiene line, preserved).
- Arm 2: hindsight launched on the same frozen bundle (sequential).
- Oversight (deleg_9deb35c3): memcal arm CLEAN — 6/6 artifacts, valid,
  10/10 turns rc 0, model_calls=8, 113016 in / 250 out, 8 dream passes with
  real tokens, cost_usd null (untracked, not a violation), no retry traces,
  single-write dir. Open note: 47 exposed vs 43 ingested (4 obs never
  archived — by design or finding; for post-run review).
- Arm 2 DONE exit 0 → `runs/life-20260922T154042Z`: hindsight F1 0.2 (1/6
  correct; invented 1, stale 2, misrepresented 3, missed 2;
  cancel-reversal 0/4, deletion 1/2, historical 0.0). Stdout showed repeated
  `LLM trace write failed ... attached to a different loop` warnings
  (llm_trace.py — triaged by review agent, see below).
- Post-run review dispatched (deleg_3dbd9eee): trace review + failure
  classification + validity/spend/race-week-gate.

## Revoke-week audit review (2026-09-22) — both arms valid, comparison discriminates

- Memcal `life-20260922T153714Z`: F1 0.667 (4/6, missed 0). Hindsight
  `life-20260922T154042Z`: F1 0.2 (1/6, invented 1, stale 2, missed 2).
  Same frozen bundle 2f5a1a73, both exit 0, both valid, no retries.
- Trace review: Hindsight arm CLEAN (6/6 artifacts, hash match, 10/10 rc 0,
  11 retain calls 113075/2124). llm_trace cross-loop warnings = COSMETIC
  (grading evidence intact; same known-benign recorder class). Caveat: stdout
  warnings not persisted in run dir — capture stdout/stderr to file in future
  arms (launch-time redirect, no code change).
- Failure classification (no re-grading): THREE new findings. (1) Hindsight
  recall omission at agent layer — zero recall ops, hallucinated tool names
  outside the allowlist — atop provider ingest empties on critical batches
  (10/26 retained, ~16 empty-id); cancel-reversal 0/4 is over-determined, not
  pure storage collapse. (2) Deletion-compliance refusal SHARED by both arms
  (gate-old 0/2): in-context forget instruction suppressed 4410 without
  retrieval attempts. (3) Memcal dinner-count: memory correct end-to-end,
  grader heuristic flagged party-size phrase. Consolidation-drop signature
  matched by zero misses here (correctly does not generalize).
- Validity: bundle+single-realization+comparative-false all PASS; suite 430
  green; CONTRACT/comparative-gates untouched; 47-vs-43 exposure BY DESIGN
  (4 hermes user-turn replays, never DB writes; ingested sets identical).
- Spend observed: ~34 model calls, ~290k in / ~8k out, ~16 min wall; cost_usd
  null (rate card unverified) — $30 headroom qualitative only.
- Race-week: CONDITIONAL GO per review — conditions: (a) verify rate card so
  cost_usd is real; (b) no-retry discipline; (c) single realization, frozen
  bundle+digest first; (d) comparative_claim_valid stays False (race-week
  ships INCONCLUSIVE-by-design, not a winner). Awaiting explicit go (larger
  paid step; condition (a) needs operator input).

## Race-week audit — LAUNCHED (all four conditions satisfied)

- Rate card (operator 2026-09-22): Muse Spark 1.3 Contributor, $0.10/M in,
  $0.20/M out. Recomputed: spent-to-date ≈ $0.033 (revoke ~$0.031 + probes
  ~$0.003); race-week padded UB ≈ $0.58 = 1.9% of the $30 cap. Condition (a)
  closed with real numbers; (b)(c)(d) enforced by procedure.
- Frozen bundle: `runs/race-week-bundle.json` hash
  `a93b46828e69f35b0d7fec5704b039bd8a9c7b330f4ac812f3094f0314e2ec73`
  (1592 obs, 11 checkpoints, 204 retains, 37 days).
- Arm 1 DONE exit 0 → `runs/life-20260922T155428Z`: memcal race-week F1 0.889
  (12 correct, missed 3, precision 1.0, recall 0.8; update-depth-8 2/2,
  historical 1.0, negative-control 0/1, opportunity_recall 0.33).
- Arm 2: hindsight launched (proc_e2827e999e74), stdout to
  `runs/race-hindsight-stdout.log`.
- Oversight (deleg_13819ba7): memcal race arm PASS — 6/6 artifacts, valid,
  11/11 graded, 20/20 turns rc 0, hash match, 1592 exposed, driver tokens
  42068/3060 real, no retry strings, stdout log complete with warnings.
- Rules in force: no retries, every failure/anomaly preserved, no weakening.

## Bulk-ingest verdict (2026-09-22) — current shape matches vendor guidance

Two-track check (docs research + SDK 0.10.0 introspection), both read-only:

- **Shape: MATCH.** Stable document_id per conversation + append deltas + per-
  packet timestamp + `Name (timestamp): text` lines is exactly the vendor's
  streaming-transcript pattern ("a full conversation as a single item", "send
  only the new content", label-every-line format, explicit past timestamps for
  backfills, day granularity fine).
- **Transport: MATCH.** Day-batches already group multi-thread packets into one
  multi-item `retain_batch_async` call with distinct doc ids each (the arm-C
  proven shape) — the "many sequential single-item calls" concern does not
  apply; cross-day appends are sequential, never concurrent on one doc id.
- **Stay SYNC, not async:** `submit_async_retain` exists but async responses
  carry NO usage metrics — adopting it would blind our token telemetry
  (unknown-never-zero discipline). Local embedded engine has no HTTP hold
  problem. Revisit only if batches outgrow single-call latency.
- **File upload / archive import: correctly ignored** — binaries only, no
  per-item timestamp/append semantics (10 files/100MB cap).
- **No dedicated bulk/import API exists** — multi-item retain_batch IS the bulk
  path. No adapter change warranted; vendor Batch-API cost option is
  OpenAI/Groq/Gemini-only (N/A to Meta Spark).

## Gate states (recorded 2026-09-22)

- **Hindsight adapter transport:** RESOLVED — one chronological transcript item
  per logical conversation packet.
- **Grouped same-doc multi-item engine behavior:** CONFIRMED FAILURE MODE /
  provider implementation finding — preserved separately, not scored.
- **P0-9 semantic gate:** OPEN — r1/r2 INCONCLUSIVE; eligible for one
  transport-remediation validation (predeclared, no retries).
- **P0-10 temporal gate:** OPEN — r1 INCONCLUSIVE; remediation realization only
  after remediation-path ingestion is demonstrated.
- **Exposure equivalence:** CLOSED, with authorship strengthening.
- **Timestamp ablation:** CLOSED.
- **Operation-id robustness:** CLOSED 2026-09-22 — canonical instant derivation
  shared by resume/adapter/live; mixed-format identity tests green; non-Hindsight
  coverage gap declared in `docs/resume-protocol.md`. Independently verified:
  430 passed, 2 skipped.
- **Large frozen dev-life gate:** OPEN — after P0-9 + P0-10 resolve, go directly
  to one large development-life audit, not another cloud of tiny probes.
