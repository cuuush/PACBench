# PACBench Audit Log (chronological, lossless for provider findings)

Single chronological record. Supersedes and replaces (git-removed 2026-09-22):
`docs/probe-dispositions.md`, `docs/probe-decision-record.md`, `docs/subgoals.md`,
`docs/comparative-gates.md`, `docs/contract-clarifications-2026-09-21.md`.
Day-to-day narration, duplicate scoreboards, and stale per-realization tables were cut;
every provider finding, frozen value, gate state, and spend figure was kept.
Evidence pointer per claim: bundle hash / run dir / manifest field.
Disk verification 2026-09-22: probe/discrim/targeted trace dirs are pruned (see §10);
the four audit arms `runs/life-20260922T153714Z|154042Z|155428Z|160232Z` exist;
frozen hashes `2f5a1a73…` / `a93b4682…` are pinned in run `manifest.json`
(`bundle_hash`), NOT in current `runs/*-bundle.json` (re-rendered since).

## 1. 2026-09-21 — Contract clarifications (co-author, verbatim rulings Q1–Q7)

Mandated sequence: (1) benchmark-integrity mechanisms → (2) one 1–2k-obs Spark life →
(3) Hindsight on native lifecycle → (4) Memcal+Hindsight same frozen life + manual audit →
(5) finalize generation pipeline from mechanics, not who won → (6) 30 lives + held-out.
Never tune Memcal against future held-out. Proceed-order detail: judge-pool draft →
clock probe (DONE 2026-09-21: host leak root-caused, `[Simulated now]` prefix, re-probe
passes) → Spark life ($50 guard, Chris sign-off) → Hindsight adapter → same-life audit →
200-example set + calibration → scale with dev/calibration/held-out split.
- Q1 judge: `gpt-5.6-sol` via Codex CLI (medium); one atom + answer + minimal temporal
  metadata; ≥200 human judgments, 2 independent labelers + adjudication; bars ≥97% /
  κ≥0.90 / false-credit ≤2%. IMPLEMENT ROUTE, PENDING HUMANS.
- Q2 Spark proof: 9 proof points for life #1; lives 2–30 review: all support windows +
  every warning + ≥5% ambient (min 50). SPEC RECORDED, AWAITING SPEND.
- Q3 lifecycle optionality: maintenance optional, hook-less providers valid. IMPLEMENTED.
- Q4 declined-framing: penalty only when declined/expired thing is presented as viable;
  `_suggestion_text` sentence-scope; both 0.2.0 pilots regraded 1→0. IMPLEMENTED.
- Q5 repetition: 3 reps, frozen config, predeclared seeds (`run-pilot --seeds a,b,c`).
  IMPLEMENTED.
- Q6 corpus acceptance: depth 0/1/2/4/8 + ≥1 depth-8 story; ≥80% non-support, ~2–10%
  direct support; all three half-life gaps, each >1 lineage, ≥1 in held-out. SPEC.
- Q7 held-out exposure: aggregate-only review leaves held-out; detailed inspection marks
  `exposed_after_review` + exposure-log row. IMPLEMENTED (`docs/exposure-log.md`).

## 2. 2026-09-21 — Subgoal ledger snapshot (status mirror, CONTRACT governs)

C1 Spark pipeline proof: PARTIAL (life_f_race 1,592 obs/11 cps/21 truth; render HALTED
by Chris at 1,299/1,592 — f1177 transport TimeoutError, resumable; no relaunch w/o go;
consolidated 16-section ruling governs: sequential life + delta ingest, frozen bundles,
no semantic retry, wall-clock characterization, 9 proof points + 5% ambient + sign-off,
200 drafts stand, 3 reps, 13-step order). C2 simulated time: PARTIAL (Memcal clock
FAIL→PASS stands; Hindsight event_date PASS stands; `mentioned_at`-as-bookkeeping
rejected — wall-clock characterization required). C3 Hindsight adapter: COMPLETE
(embedded PG + MemoryEngine, META gateway, `written_at`→`event_date`, fail-closed keys;
near-dupe second retain → `[]` ruled §2 measured provider outcome, no semantic retry).
C4 two-provider pilot: BLOCKED (spend halt + design). C5 audit packet: PARTIAL (builder
done, audit gated on C4). C6 judge pool: PARTIAL (200 drafts `POOL_VERSION 0.2.0-draft`,
labels need 2 humans). C7 comparative_claim_valid=false: COMPLETE (held).

## 3. Comparative gates (comparative_claim_valid = false; still false, §9)

14 gates; token/pricing accounting is NOT an accuracy gate (split:
`comparative_claim_valid` vs `efficiency_claim_valid`). Status: gate 5 open (lexical
diagnostic only — no real external provider scored comparatively), gate 3 open
(held-out empty). Milestone: Memcal + lexical diagnostic complete; Memcal + real
external provider pending. (Single-realization revoke/race audits ship
INCONCLUSIVE-by-design; they do not touch this flag.)

## 4. 2026-09-22 — Frozen decision record (binding pre-spend; digests as amended §6)

HEAD `66678d4` "P0 no-spend milestone". Ceilings: user-level below $20 total; P0-10 ≤5
provider ops (1 retain + 4 recalls); P0-9 ≤9 ops (3 retains + 6 recalls). Scratch
tempdir banks `pacbench-probe-p{09,10}-*`, never `~/.hindsight`. P0-9 rule: exactly one
more realization after r1; empty again → stop, no r3, redesign only after review.
P0-10 exec-zero `proc_3fc94cb10ae0` (infra reap, no trace, no spend) retained as history.
Branch dispositions: P0-10 PASS→P0-9r2; WALL-CLOCK→review (never auto-closes gate);
FAIL→preserve+stop; INCONCLUSIVE→diagnose+stop. P0-9r2: usable retention→assess;
empty→preserve both, stop. Post-probe order: dispositions → authorship adversarial test
→ op-id hardening → timestamp ablation → large-life readiness → one frozen dev life,
both providers, full audit. P0-10 fixtures: `probe-tx` → `probe-tx-old`/`probe-tx-new`
(evidence-identical thread split under one-item-per-packet rule; P0-9 CYCLES untouched).

## 5. Probes, realizations 1–2 — both INCONCLUSIVE, paid progression STOPPED

- P0-9 r1 (`runs/probe-p09-20260922T032018Z`, pruned, prose preserved): 2 msgs, stable
  key `text:fam`, append, chronological; engine `retained 0/2` (empty-id), zero usage.
  Submission side proven; append association/recall not established. Attribution:
  provider-side empty return.
- P0-10 exec-zero then r1 (`runs/probe-p09-20260922T034737Z` analog
  `runs/probe-p10-20260922T034737Z`, pruned): 2 facts, key `text:probe-tx`, event_dates
  2026-03-01/2026-09-18; `retained 0/2`, zeros. `return_usage=True` verified shape-only
  (`memory_engine.py:5354,5559,5586`), not causal. Reclassified systematic: 0/2 on
  new-conversation-append path twice vs 5/8 partial retention on per-message-replace
  smoke path (~2.4s vs ~28s). Ruled out: op-id dedup (none exists), defense-block
  (wrong shape), delta-None (falls back to full retain).
- P0-9 r2 (`runs/probe-p09-20260922T041624Z`, pruned): identical `retained 0/2`
  signature → decision-record rule fires: no r3.
- Impact on comparative validity: none (no scores, no claims). Paid progression STOPPED
  pending transport remediation.

## 6. Zero-spend transport resolution (2026-09-22, all read-only except reviewed fix)

- Exposure-authorship gate STRENGTHENED: `from_owner` flip initially missed → pinned
  `from_owner`+`structured` into projection (8→10 fields, `pacbench/exposure.py`); suite
  408 green (later 410, then 430 — see gates).
- Timestamp ablation CLOSED: parsed datetimes (naive→UTC, `ValueError` on garbage, no
  string fallback) + mixed-offset adversarial test.
- Archaeology: passing 1-item vs failing 2-item requests differ in exactly one material
  field — item count sharing one `document_id`. 0.10.0 docstring blesses multi-item
  batches; refined suspect: multi-item `append` group vs nonexistent document
  (`memory_engine.py:5438-5456` → grouping → append-base read, no-row case).
- Replace-vs-append discrim (`runs/discrim-20260922T041513Z`, pruned): A replace 1/1
  (2942/144), B single-item append-on-new-doc 1/1 (2941/118) — append-on-new-doc
  VALIDATED; multi-item same-doc append remains suspect. Per-retain ≈3k in/130 out.
  llm_trace cross-loop write failures = recorder noise, payloads unaffected.
- Doc-hunter: vendor docs bless batches with DISTINCT doc ids ("a full conversation as
  a single item", "send only the new content"); GitHub hindsight#3363: HTTP server
  REFUSES same-`document_id` batches with 400 (race-condition guard) while OpenAPI
  example shares one id — our empty-`[[],[]]`-with-zero-usage is plausibly the
  embedded-engine translation of that guard (raw status/body capture left as next step).
- Three-arm discrim3 (`runs/discrim3-20260922T142202Z`, pruned, 194.1s): A grouped
  same-doc 0/2 zero-tokens (reproduces probe empties); B sequential same-doc 1/1+1/1
  full extraction; C grouped two-docs 2/2 full extraction. **GROUPING-PATH isolated**:
  rolled-up item totals — one-item-per-document paths 9/10 retained vs same-doc
  multi-item grouping 0/8. Grouped same-doc on creation = CONFIRMED provider failure
  mode (preserved, unscored); sequential appends and multi-doc batches fully functional.
- Adapter fix APPLIED (reviewed): one joined-transcript item per packet
  (`sender (written_at): text`, chronological; same doc ids; append/replace, op-id,
  telemetry untouched); tests + `docs/hindsight-update-semantics.md` updated; 410
  green. P0-10 fixtures split 1 doc→2 (same evidence); P0-9 CYCLES byte-identical.
  Post-validation addendum (current sha256s): p09 `bac8d638…`, p10 `8fb1bfa4…`,
  adapter `71582866…`, exposure `81dea23e…`, resume `f991e658…`; source_documents
  `2b6e31ae…` unchanged. Changes: (a) packetization fix, (b) op-id canonical-instant
  derivation shared resume/adapter/live, (c) checker fixes (X/Y packet counting,
  `_covered` majority-token, `_temporal_covers` month-and-day), (d) exposure 8→10.
- Bulk-ingest verdict: shape MATCH (per-conversation doc_id + append deltas + per-packet
  timestamp + `Name (timestamp): text` = vendor streaming pattern); transport MATCH
  (day-batches group multi-thread packets into one multi-item call with distinct doc
  ids — the arm-C proven shape; cross-day appends sequential, never concurrent);
  stay SYNC (`submit_async_retain` carries no usage metrics — would blind telemetry);
  file-upload/archive-import correctly ignored; no dedicated bulk API exists
  (multi-item `retain_batch` IS the bulk path; vendor Batch-API cost option N/A to
  Meta Spark). No adapter change warranted.

## 7. P0-9 remediation — cycles 1–2 PASS, cycle-3 CONSOLIDATION-DROP (WRITE-SIDE)

Trace `runs/probe-p09-20260922T150359Z` (pruned, 99.6s), corrected checker, no false
alarms. Cycle 1 PASS both flavors (2 results, day-1 recoverable, no leak). Cycle 2 PASS
both flavors (3 results) incl. cross-day ASSOCIATION ("planned after 5pm …
rescheduled to 6pm … confirmed by Mia as Gus was running late") — P0-9's designed
question, demonstrated. Cycle 3 CONSOLIDATION-DROP, confirmed by targeted re-recall
(`runs/targeted-p09c3-20260922T151134Z`, pruned, 8.5s, 3 recall ops, 0 retains): bank
alive, but "car washed / paperwork?" still misses day-3 — memory attributed to day-3
obs (source_ids `s-06`,`s-05`) yet TEXT is the old Sept 18/19 summary: consolidation
reprocessed the combined doc (~6090 tokens, ~2x), kept the prior summary, dropped new
sentences. Recall ranking exonerated; WRITE-SIDE confirmed (metadata-right/text-stale).
PACBench meaning: architecture-behavior signal — Hindsight summarizes on
append-reprocess and may lose detail while attribution stays correct; scored runs must
expect summarization loss on long evolving docs. (Harness path: attempts
`…T145911Z` 1/1 retention misread as failure by stale 2/2 check → fixed X==Y packet
parsing; `…T150128Z` 1/1 retention misread by VERBATIM-source-text demand on extracted
facts → fixed `_covered` majority-token coverage, proven by offline replay over frozen
trace. Lesson: read the trace, never trust the verdict line. First targeted attempt
`…T150951Z` was a stub: zero provider ops, `_config_resolver` AttributeError —
recall-first sessions need explicit `initialize()`; superseded.)

## 8. P0-10 remediation — PASS (October-above-March, both arms, plain PASS)

Trace `runs/probe-p10-20260922T151636Z` (pruned, 28KB): ingest 2/2 (0 empty-id,
5975 in/270 out), keys exactly `{text:probe-tx-new, text:probe-tx-old}`. Live print
said INCONCLUSIVE only via the verbatim-checker defect (demanded source sentence "the
lake cabin rental ends October 5" inside extracted-fact prose "…ends ON October 5,
2026 | When: …" — third occurrence of the class). Corrected checker
(`_temporal_covers`, month-and-day anchored, no month cross-match) replayed over
PRESERVED ordered evidence, no new paid ops: with-control RAW October #1/#2
(1.0999/1.0998) above March #3/#4 (0.9942/0.9941) → (old=2, new=0), new above old;
without-control RAW IDENTICAL (old=2, new=0); synthesized new-first both arms.
Verdict under frozen taxonomy: **PASS** — RAW ranking follows simulated age, and the
uncontrolled arm ranks the same → NO wall-clock dependency (plain PASS, not
pass-with-dependency). Deterministic to full float precision. Temporal gate CLOSED;
with P0-9 §7, both spend-gated probes classified.

## 9. Spend ledger + Spark rate card (units kept distinct)

Rule: never blur observations → requests → extraction → tokens → cost; a zero-token
request is NOT a model call. Probes+discrim3 rolled: 14 items submitted, 9 retain
requests, 5 extraction-observed, ≈17.6k in / ≈700 out cumulative, probes ≈$0.003.
Rate card (operator 2026-09-22): Muse Spark 1.3 Contributor, **$0.10/M in, $0.20/M
out**. Spent-to-date ≈ **$0.033** (revoke ≈$0.031 + probes ≈$0.003); race-week padded
UB ≈ $0.58 = 1.9% of the $30 hard cap (user ceiling below $20 total stands).
Earlier "≈8 micro-calls" message did not reconcile (2+2+6=10) — corrected above.

## 10. Revoke-week audit (2026-09-22) — both arms valid, comparison discriminates

Authorization: user go 2026-09-22, $30 hard cap. Frozen bundle `runs/revoke-week-bundle.json`
hash `2f5a1a739c0285340eff3cd8974f47210dbfecfa99360b7b3692737182ec1093`
(47 obs, 6 checkpoints; pinned in both run manifests `bundle_hash`). Sequential arms,
`PACBENCH_ALLOW_MAINTENANCE_SPEND=1`, single realization, `comparative_claim_valid`
stays false.
- Memcal → `runs/life-20260922T153714Z` (verified on disk: grades/manifest/turns/
  validity; manifest tokens 113016 in/250 out, model_calls=8): **F1 0.667** (4/6;
  verified `grades.json` summary f1=0.6667, correct=4, missed=0; duplicate 1, stale 1,
  misrepresented 2; cancel-reversal 3/4, deletion 1/2, historical 0.5). Oversight
  CLEAN (6/6 artifacts, 10/10 turns rc 0, 8 dream passes real tokens, cost_usd null =
  untracked-at-the-time not violation, no retries, single-write dir). 47 exposed vs
  43 ingested BY DESIGN (4 hermes user-turn replays never DB-written; ingested sets
  identical).
- Hindsight → `runs/life-20260922T154042Z` (verified; 11 retain calls 113075/2124):
  **F1 0.2** (1/6; verified f1=0.2, missed=2; invented 1, stale 2, misrepresented 3;
  cancel-reversal 0/4, deletion 1/2, historical 0.0). Trace CLEAN (6/6, hash match,
  10/10 rc 0); llm_trace cross-loop warnings COSMETIC (evidence intact); stdout
  warnings not persisted — future arms redirect stdout/stderr to file at launch.
- Review findings (no re-grading): (1) **RECALL-OMISSION / grouping-path**: Hindsight
  zero recall ops + hallucinated tool names outside allowlist, atop provider ingest
  empties on critical batches (10/26 retained, ~16 empty-id) — cancel-reversal 0/4 is
  over-determined, not pure storage collapse; (2) **DELETION-COMPLIANCE refusal SHARED
  by both arms** (gate-old 0/2): in-context forget instruction suppressed 4410 without
  retrieval attempts; (3) **memcal dinner-count grader-heuristic note**: memory correct
  end-to-end, grader flagged party-size phrase. Zero misses here → consolidation-drop
  signature correctly NOT generalized.
- Validity: bundle + single-realization + comparative-false PASS; suite 430 green;
  CONTRACT/comparative-gates untouched. Spend ≈34 model calls, ≈290k in/≈8k out,
  ≈16 min wall; cost_usd null (rate card then unverified) — headroom qualitative only.
- Race-week CONDITIONAL GO: (a) verify rate card, (b) no-retry discipline, (c) single
  realization + frozen bundle/digest first, (d) comparative_claim_valid stays false
  (INCONCLUSIVE-by-design, never a winner).

## 11. Race-week audit (2026-09-22) — all four conditions satisfied; memcal arm graded

Rate card closed condition (a); (b)(c)(d) enforced by procedure. Frozen bundle
`runs/race-week-bundle.json` hash `a93b46828e69f35b0d7fec5704b039bd8a9c7b330f4ac812f3094f0314e2ec73`
(1592 obs, 11 checkpoints, 204 retains, 37 days; pinned in memcal manifest).
- Memcal → `runs/life-20260922T155428Z` (verified; 1592 exposed, driver tokens
  42068/3060 real, 20/20 turns rc 0, hash match; oversight PASS 6/6, 11/11 graded;
  stdout `runs/race-memcal-stdout.log`): **F1 0.889** (verified f1=0.8889, 12 correct,
  missed 3, precision 1.0, recall 0.8; update-depth-8 2/2, historical 1.0,
  negative-control 0/1, opportunity_recall 0.33).
- Hindsight arm launched (`proc_e2827e999e74`, stdout
  `runs/race-hindsight-stdout.log`, verified 151KB with llm_trace warnings +
  resource_tracker notice): run dir `runs/life-20260922T160232Z` exists but is
  INCOMPLETE on disk (only `hermes-home/config.yaml`; no grades/manifest/turns) —
  arm outcome NOT recorded here; no hindsight race-week result is claimed.

## 12. Gate states (2026-09-22, end of record)

- Hindsight adapter transport: RESOLVED (one chronological transcript item per packet).
- Grouped same-doc multi-item engine behavior: CONFIRMED FAILURE MODE / provider
  implementation finding — preserved, unscored.
- P0-9 semantic gate: CLOSED by §7 (cycles 1–2 PASS + cycle-3 consolidation-drop).
- P0-10 temporal gate: CLOSED by §8 (PASS, no wall-clock dependency).
- Exposure equivalence: CLOSED (authorship strengthening enforced by gate).
- Timestamp ablation: CLOSED. Operation-id robustness: CLOSED (430 green; non-Hindsight
  coverage gap declared in `docs/resume-protocol.md`).
- Large frozen dev-life gate: OPEN (next: one large development-life audit — now
  partially executed as revoke/race weeks above, hindsight race arm incomplete).
- `comparative_claim_valid`: **false** (unchanged; no held-out comparison exists).

## 13. Pruned-trace note (recorded once)

2026-09-22 cleanup per operator direction: all probe/discrim/targeted trace dirs and
Sep 20–21 smoke-era checkpoint/pilot/life dirs deleted (1.4GB → 114MB; `runs/` is
gitignored scratch). Findings from every deleted trace survive in prose above (§§5–8);
cited `runs/probe-*`, `runs/discrim*`, `runs/targeted-*` paths no longer exist on disk.
Kept: the four audit arms (§§10–11), frozen bundles, stdout logs, render bundle.
