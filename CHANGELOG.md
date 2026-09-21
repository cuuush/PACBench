# CHANGELOG

## Unreleased (corpus 0.3.0)

- Corpus: new development life `race-week` (F) — long-horizon noisy
  recall: Harbor 10k training saga (9 updates), birthday venue chain
  (4 updates), superseded car/dinner times, durable peanut allergy,
  time-bounded deadlines (early-bird, RSVP), very-short-lived states
  (heads-down, meter), declined/expired/wrong-person traps, denied
  storm-cancellation rumor. 1,592 obs / 11 checkpoints at 2.6% measured
  support (Q6 band 2–10%); filler programmatic from timeless templates
  (seeded) with gen_qa screens (temporal/commitment/slot-token/negate
  clean). Corpus now 1890 observations / 44 checkpoints across 10
  lives; freeze hash re-pinned deliberately; CORPUS_VERSION 0.2.0→0.3.0.
- Generation QA (`pacbench/gen_qa.py`, criterion 1 proof wiring):
  `added_atoms` (extra-fact screen for Spark renders), `screen_life`
  (filler discipline incl. negate-cue dodge for the YESNO scan),
  `noise_report` (measured support ratio). Difficulty measured on the
  frozen draft: relevant threads 135–297 obs/checkpoint, ages 1–118h,
  multi-channel on 3/11. Spark pass (renderer muse-spark-1.3, verified
  route) + review packet + sign-off still ahead; GAPS account untouched
  (first instances of all three gap classes recorded, closure proposed
  for co-author review).

## Unreleased (corpus 0.2.0)

- Corpus: new development lives `reunion-thread` (D) — thread-size difficulty
  (move + hearsay + stale repeat buried in a ~150-message group chat, work
  picnic as small-thread control) — and `revoke-week` (E) —
  cancellation-reversal (exactly one active booking), explicit forget (old
  code stays history, never current), completed-task silence. Corpus now 298
  observations / 33 checkpoints across 9 lives; freeze hash re-pinned
  deliberately (twice: new life, then calibration roles).
- Grading: YESNO forbidden polarity is scoped to proposition+time
  (co-author correction): topic-implying cues (canceled/cancelled/called
  off) aid recall but never convict; a pure-polarity fallback covers
  pure-past answers; mixed-temporal answers without pure cues read MISSED
  (honest recall limit, judge territory). Splitter preserves negations and
  cues are normed before matching. Every required YESNO slot names the
  opposite polarity as forbidden, so false affirmations/denials grade
  MISREPRESENTED, not MISSED (invariant tested). Same class fix for
  HISTORICAL slots: a wrong past value with the expected absent is
  confabulation even when past-tense phrasing hides it from the
  current-text filter — while history-aware answers naming both values
  stay CORRECT. Regrading pre-0.2.0 frozen artifacts under this grader
  shifts old MISSED verdicts — compare frozen grades only, never recompute.
  Prompt-leak validation is word-boundaried (substring matching overfired:
  "no" lives inside "Nova").
- Error taxonomy (§36): every required slot with forbidden values declares
  its error class (`rubric.ERROR_CLASSES`: stale, wrong-person,
  wrong-occurrence, invented, expired, duplicate — enforced by validation);
  reports break MISREPRESENTED out by class instead of conflating.
- Runner: `--provider lexical` (§27 diagnostic baseline, stdlib-only) for
  `run-checkpoint` / `run-pilot`; first measured comparison (lexical F1 0.750
  vs frozen memcal 0.727 on the 3 shared lives — a sanity check at 55 obs,
  not a finding; per the co-author the lexical baseline does NOT satisfy
  §45.5, which needs a real external memory system:
  `docs/comparative-gates.md` records all 14 gates for
  `comparative_claim_valid`, now split from `efficiency_claim_valid`).
- Usage: per-turn driver tokens (session-delta method) + memcal dream spend
  in pilot efficiency; artifact redaction preserves token-count keys.
- Splits & repetitions (§§39–40): declared split table (5 development, 4
  calibration, 0 held-out — the empty held-out is why no comparison is
  valid); `run-pilot --repetitions N` runs each life N times into namespaced
  dirs with per-rep F1 in the report. Pilot labels fixed (`dev-pilot`, live
  life/rep counts in REPORT.md instead of a stale "3-life" string).
- Readiness honesty (§42) + real-time allowance (§30): only a finished
  background pass reads ready — memcal skips and pre-run states read
  not-ready (a skip means the provider was not fully exercised), same for
  the lexical no-op before its first pass. `live.maintain` already treated
  both as incomplete; readiness now agrees instead of contradicting it. Run
  manifests record the declared per-turn real-time budget (`turn_timeout_s`).
- Integrity + measured difficulty (co-author directives): standing rules in
  `docs/benchmark-integrity.md` (never optimize to win; exposure is forever;
  diagnose conservatively; small-corpus phase over). Exposure is a
  first-class role (`exposed` joins the validated split vocabulary).
  `pacbench/difficulty.py` measures difficulty from the frozen corpus
  (relevant-thread size, total exposed, required channels, evidence age,
  support batches) instead of copying author tags. Failure diagnoses carry
  `basis: trace|heuristic|none` (Q11). Fictional-clock integrity is tested
  at the harness boundary: `run_turn` exports exactly the fictional `at`
  as PACBENCH_NOW (never host date), meeting the vendor shims that anchor
  the isolated clock to it (§30). STATUS/TEXT norms pinned in isolation:
  containment semantics, the conservative no-judge → MISSED default, the
  per-claim judge seam (fail-closed: an indiscriminate judge convicts
  forbidden first, never manufactures a pass), and the corpus-anchored
  eric-now/status paraphrase residual as documented judge territory (§33).
- Contradiction scan (§12.3): `pacbench/contradictions.py` flags every
  non-support observation asserting a forbidden value in the current
  tense under grader semantics; all 96 hits carry reviewed dispositions
  (zero accidental — deliberate difficulty, different entity/event/topic/
  procedure, filler coincidence, prompt-side trigger, or verified-benign
  scan limits), enforced by test. The scan also caught a live grader
  false-conviction path ("noon" inside "afternoon" on reunion now/time),
  fixed with word-boundaried pure-alpha clock words plus regression test.
- Story lineage (§39): `Life.lineage` declares each life's story family;
  close variants share one lineage whatever their split (poker-move /
  poker-unchanged are poker-collision variants; weekend-plans derives
  from the weekend-opportunities family; controls inherit their parent's
  lineage — a self-check caught dentist-friday mis-familied and the
  ruling was corrected). `LineageDiscipline` enforces: declared map,
  held-out lineage-unique (standing guard, held-out empty), calibration
  always derivative of development, controls share lineage. Lineage ships
  in the freeze bundle; hash re-pinned deliberately.
- Half-life account (§18): `HalfLifeCoverage` maps every lifetime class
  to exercising slots (8 HISTORICAL event→history slots, 4 expired
  traps + 2 declined traps with error-class completeness, durable
  dentist-name, temporary gate-code) or to declared gaps owned by the
  generated-life spec (very-short-lived states, durable preferences,
  time-bounded validity). Tagging pass: weekend gala/5k traps now carry
  error="expired" (taxonomy lumps declined by design); hash re-pinned.
  Pilot-measured: both 0.2.0 pilots suggested the declined waterfront
  festival with declined-framing (1 forbidden each) — the mention counts;
  whether declined-framing should exonerate is open grading design.
- Co-author rulings 2026-09-21 (`docs/contract-clarifications-2026-09-21.md`,
  seven answers, mandated sequence: integrity → Spark proof → Hindsight →
  same-life audit → finalize pipeline → 30 lives + held-out). Judge route
  verified: `gpt-5.6-sol` via Codex CLI (openai provider, medium effort) —
  NOT OpenRouter; Hermes-subagent route fails (meta-ai 404, copilot
  unlicensed). Lifecycle optionality implemented: `maintenance_applicable`
  on the adapter contract; N/A providers record not-applicable and stay
  valid; readiness = ready per native lifecycle (lexical always-ready);
  memcal skip semantics unchanged. 182 green.
- Declined-framing exoneration (ruling Q4): `_suggestion_text` strips at
  sentence scope (the ruled "but you said you can't make it" declines via
  anaphora across the "but"); cues extended (skip-stem, can/couldn't-make-it,
  no-to). Verbatim regrade of both frozen 0.2.0 pilot answers: 1 → 0 each
  (regrade-shift, frozen runs untouched). Mixed-sentence shielding pinned as
  known judge-territory boundary. 185 green.
- Repetition protocol (ruling Q5): `run-pilot --seeds` with fail-closed
  count match; per-rep seed on life records + `rep_seeds`; frozen
  `repetition_config` (provider, driver, corpus hash, timeout) in
  report.json and REPORT.md; omitted seeds record `provider-managed`.
  189 green.
- Exposure protocol (ruling Q7): role vocab uses the co-author's exact
  `exposed_after_review`; docs/exposure-log.md + `ExposureLog` tests
  (term, row shape, every-exposed-life-logged); aggregate-vs-detailed
  rule in docs/split-lineage.md. 192 green.
- Judge-calibration candidate pool (proceed step 1):
  `pacbench/judge_calibration.py` v0.1.0-draft, 104 candidates across
  the co-author's eight categories (16 paraphrase/negation, 12 each of
  the rest); one atom + one answer + minimum metadata per item, NO
  label field anywhere (drafting ≠ labeling, enforced by test). Final
  200 = pool survivors + large-life audit residuals; judge must
  calibrate before held-out scoring. Exposure log widened to the
  six required columns; integrity laws added (never tune on who won,
  roles stay separate). 199 green.
- Adversarial clock probe (proceed step 2): `probe-clock`
  asks the isolated driver for today's date under a fictional clock.
  First live run FAILED (host-date leak: answered 2026-09-21 under
  PACBENCH_NOW=2026-09-09) — root cause: the shim governed tool-time
  only; nothing told the model the fictional date, so model-time stayed
  host-anchored in ALL prior pilots (disclosed caveat on frozen scores:
  evidence said Sep 8–14 while the model believed ~Sep 20–21). Fix:
  `run_turn` carries `[Simulated now: at]` in chat context, recorded in
  turn artifacts; re-probe PASSES. 206 green.

## 0.1.0 (2026-09-20)

Standalone benchmark package: corpus + validation + freeze/versioning +
deterministic grading + runner CLI over isolated Hermes turns.

- Corpus: three synthetic development lives (`dentist-depth`,
  `poker-collision`, `coat-weekend`, 7 simulated days each plus history) with
  matched controls, plus `poker-move` / `poker-unchanged` and
  `weekend-plans` / `dentist-friday` regression coverage. Truth, observations,
  and checkpoint rubrics kept distinct; support manifest never scored-run
  visible.
- Grading: atomic slot judgments (CORRECT / MISSED / MISREPRESENTED) with
  deterministic normalization; report aggregates task success, precision /
  recall / F1, current vs historical accuracy, plan accuracy, abstention,
  opportunity recall, forbidden suggestions, difficulty tags.
- Runner (W4): `python -m pacbench list-lives` (offline), `smoke`,
  `run-checkpoint`, `run-pilot` through the PACBench-owned isolated Hermes
  clone (`vendor/`, shims via `patches/`); scratch homes only, never the live
  profile. Memcal provider adapter behind the contract
  (`provider_adapters/memcal_adapter.py`). One checkpoint proven live end to
  end; no comparative results claimed.
- Generation: authoring spec + `must_include` render gate; Spark rendering
  optional via `.env` (see `.env.example`).

Not in 0.1.0: calibrated semantic judge for paraphrased TEXT slots
(unmatched paraphrases grade MISSED), model-based fact-realization
validation, 30-life comparative corpus, comparative provider results.
