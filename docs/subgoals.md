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
extra-fact review process (proven on live `c'mon` hit + screen fix),
review-packet builder, measured difficulty (threads 135–297 obs).
Pending: render tail (~950/1,592 beats, background job running
untouched), rendered packet, Chris + co-author sign-off.
Early signal @799 renders: length ratio 1.71x, token Jaccard 0.28
(real rewriting, 0 verbatim-lower), 0 added atoms (current screens).

## Criterion 2 — adversarial simulated time: COMPLETE
Memcal clock probe FAIL→PASS (`probe-clock`, `[Simulated now]` prefix,
`FictionalClockIntegrity` test). Hindsight probe PASS 2026-09-21:
relative dates resolve against authoring `event_date` (Fri→09-18,
Thu→09-17 from Wed 09-16), not wall clock. Evidence: suite
(`ClockProbeVerdict`, `FictionalClockIntegrity`), audit addenda.

## Criterion 3 — real Hindsight adapter: COMPLETE
`provider_adapters/hindsight_adapter.py`: embedded PG + MemoryEngine +
local embeddings (fully local), internal LLM = existing META gateway
(Chris decision, zero new credentials), retain_async ingest with
`written_at`→`event_date`, `run_graph_maintenance_job` scheduled work,
MCP-native retrieval (no automatic brief — recorded capability gap),
fail-closed keys, honest readiness. Live-proven: retain→recall→
Live-proven: retain→recall→maintenance→ready→cleanup (toy data) + isolated Hermes `mcp test`
(✓ 540ms, 36 tools). Characterized 2026-09-21: near-dupe second retain
→ `[]` with content recall-absent (2/2 reps) — a provider silent drop,
not a merge; adapter retries once, then names dropped source_ids in
usage. Exact duplicates DO get fresh ids. 10 offline tests green.

## Criterion 4 — two-provider pilot on the frozen life: BLOCKED
Needs: finished renders → rendered packet → sign-off (criteria 1
tail). Will run memcal + Hindsight on the identical frozen life with
full traces; every failure preserved.

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
