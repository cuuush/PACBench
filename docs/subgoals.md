# Subgoal ledger — first trustworthy large-life, two-provider pilot

Source of truth is PACBENCH_CONTRACT.md; this file is a status mirror,
not a substitute. A subgoal is COMPLETE only when its contract
definition is met with evidence. PARTIAL means work is done but the
definition needs something outside this repo's control (clock, humans)
or a still-running job. Nothing here weakens any requirement.

## Criterion 1 — Spark pipeline proof: PARTIAL
Done: LifeSpec (`pacbench/corpus/life_f_race.py`, 1,592 obs / 11 cps /
21 truth), structural validation CLEAN, must_include entailment,
support IDs + manifest, `gen_qa` screens (filler clean, noise 2.6% in
band), contradiction scan (28 candidates, all dispositioned),
extra-fact review process (proven on live `c'mon` hit + screen fix;
ruled §7 correct, keep recording validator changes), review-packet
builder, measured difficulty (threads 135–297 obs).
Render bundle PARTIAL by spend decision: 1,299/1,592 beats ok, 1
operational failure (f1177 `security training due soon`,
transport TimeoutError, attempts 1 — resumable, not semantic).
Render HALTED by Chris 2026-09-21 to stop spend; do NOT relaunch
without explicit go (overrides consolidated §16-step-1 "may continue").
Consolidated ruling 2026-09-21 (16 sections) now governs: persistent
sequential life + delta ingest replaces per-checkpoint fresh
namespaces (§1); canonical frozen bundles replace draft-text scored
runs (§5); no semantic retry (§2); `mentioned_at` NOT accepted as
bookkeeping — wall-clock temporal characterization required (§4);
packet needs expansion-tail + length distributions (§6); 9 proof
points + 5%-ambient review + Chris sign-off (§8); judge: 200 drafts
stand, spot-check only, residuals extend, κ≥0.90 bar (§§9–10);
3 fixed-config reps (§12); 13-step order (§16).

## Criterion 2 — adversarial simulated time: PARTIAL (was COMPLETE)
Memcal clock probe FAIL→PASS (`probe-clock`, `[Simulated now]` prefix,
`FictionalClockIntegrity` test) stands. Hindsight event_date probe
PASS stands, BUT consolidated §4 rejects the `mentioned_at`-as-
bookkeeping stance: wall-clock temporal characterization now
required (two facts, very different simulated ages, near-identical
real ingest times; `query_timestamp` for recall where supported).
If wall-clock ranking effects can't be controlled via a supported
mechanism, mark the temporal coverage limited/unsupported — a
provider limitation is a result. Resumed-session + memory-op clock
verification also approved and pending.

## Criterion 3 — real Hindsight adapter: COMPLETE
`provider_adapters/hindsight_adapter.py`: embedded PG + MemoryEngine +
local embeddings (fully local), internal LLM = existing META gateway
(Chris decision, zero new credentials), retain_async ingest with
`written_at`→`event_date`, `run_graph_maintenance_job` scheduled work,
MCP-native retrieval (no automatic brief — ruled §3 legitimate
capability gap, never compensate), fail-closed keys, honest readiness.
Live-proven: retain→recall→maintenance→ready→cleanup (toy data) +
isolated Hermes `mcp test` (✓ 540ms, 36 tools). Characterized
2026-09-21: near-dupe second retain → `[]` with content recall-absent
(2/2 reps) — ruled §2 a measured provider outcome: NO semantic retry,
empty-id returns recorded in usage. Exact duplicates DO get fresh ids.
10 offline tests green.

## Criterion 4 — two-provider pilot on the frozen life: BLOCKED
Blocked twice over: (a) spend — Chris halted paid runs 2026-09-21,
no relaunch without explicit go; (b) design — primary path must
become one persistent sequential life with delta ingest (§1),
canonical frozen-bundle runtime (§5), Hindsight-native packets +
batch/async ingest (§1), `query_timestamp` recall (§4). Per-
checkpoint fresh namespaces + cumulative replay is implementation
drift, removed from the primary path (diagnostic branches only).
13-step order in §16 governs.

## Criterion 5 — manual-audit packet: PARTIAL
Builder COMPLETE (`pacbench/review_packet.py`, deterministic, tested;
draft packet generates). The audit itself needs pilot traces
(blocked behind criterion 4).

## Criterion 6 — judge calibration pool: PARTIAL
Draft side COMPLETE: 200 candidates (`POOL_VERSION 0.2.0-draft`),
22–26 per category, no-label discipline tested. Labels need two
independent humans (Chris + co-author); uncalibrated judge stays out
of held-out claims. Pilot residuals EXTEND the versioned pool.

## Criterion 7 — comparative_claim_valid stays false: COMPLETE
`comparative_claim_valid: false` held; no held-out comparison exists
to claim. Gated on the contract's real held-out + repetition +
frozen-config + calibrated-grading + external-provider requirements.

## Verification — END-TO-END PILOT: BLOCKED
Frozen 1,000–2,000-obs generated life through memcal + Hindsight with
valid traces, grading, manual-review artifacts, green suite. Gated on
criteria 1 tail → sign-off → 4 → 5.
