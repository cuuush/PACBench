# Contract clarifications 2026-09-21 (co-author rulings)

Seven rulings, received verbatim 2026-09-21. They clarify the v0.1
contract; where they overrule a prior implementation choice, the code
change cites the ruling. Each entry ends with implementation status.

Sequence mandated by the co-author: (1) finish benchmark-integrity
mechanisms → (2) prove one 1–2k-obs Spark life → (3) add Hindsight on
its native lifecycle → (4) run Memcal + Hindsight on the same large
frozen life and audit manually → (5) finalize the generation pipeline
from mechanics learned, not from who won → (6) scale to 30 lives +
true held-out. Never tune Memcal against the future held-out corpus.

## Q1. Judge calibration — IMPLEMENT ROUTE, PENDING HUMANS

- Judge model: `gpt-5.6-sol`, medium reasoning, all else fixed. Route
  verified 2026-09-21: Codex CLI (`codex exec -m gpt-5.6-sol`, provider
  openai, medium effort) — NOT OpenRouter. Hermes-subagent route fails
  (meta-ai 404, copilot unlicensed); codex is the working path.
- Extremely narrow: one atom + one answer + minimum temporal metadata.
  Never provider identity, scores, other atoms, or source corpus.
- Grader version records exact resolved model id + prompt hash +
  settings. Material model change = new grader version + recalibration.
- Calibration set: ≥200 atom-answer judgments, human-built (not today's
  three residuals), positive + negative heavy, covering paraphrases,
  negation, historical framing, old-vs-current, declined framing,
  uncertainty, contradictory answers, partial assertions, mixed
  historical+current answers. Two maintainers label independently, then
  adjudicate to gold.
- Bars: ≥97% agreement, Cohen's κ ≥ 0.90, false-credit ≤ 2%
  (over-generous judges are the dangerous direction).
- Fail ⇒ residuals go to human review until repaired + recalibrated.
- Post-calibration: audit ≥10% of judged residuals (min 20) in both
  directions in comparative runs.
- Never route deterministically-resolved atoms through the judge.
- Status: route proven; calibration-set builder + judge callable unwired;
  200 labels need two humans (blocked on people, not code).

## Q2. Spark proof acceptance — SPEC RECORDED, AWAITING SPEND

Nine proof points for life #1 (1–2k obs, realistic channels, multiple
stories): (1) structural validation works; (2) must_include works;
(3) support IDs emitted; (4) semantic fact-realization QA actually wired
and run; (5) contradiction scan run; (6) Spark-invented extra facts
detected/surfaced, never silent truth; (7) evidence style ≈ filler
style (manual inspect — needle must look like hay); (8) human confirms
rubric follows rendered observations incl. support; (9) difficulty
measured from frozen render, not requested. Notes: 1, 3, 5, 9 already
exist as code — the proof wires them to generation. $50 runaway guard
(safety cutoff, NOT acceptance; raise deliberately if legitimate).
Lives 2–30 review per life: all scored support windows, every
validator/contradiction/uncertain-QA warning, story truth + rubric,
plus ≥5% ambient sample (min 50, across channels + week, noncontiguous);
systematic problems ⇒ enlarge sample or reject/regenerate.

## Q3. Lifecycle optionality — IMPLEMENTED THIS COMMIT

Scheduled maintenance optional; N/A providers stay valid; readiness =
ready per native lifecycle; never invent nightly work or penalize a
hook-less provider. See CHANGELOG. (Overrules the prior skips→invalid
posture for hook-less providers; memcal skip semantics unchanged.)

## Q4. Declined-framing exoneration — IMPLEMENTED 2026-09-21

"Skipping per your note" / "you said you can't make it" is NOT a
forbidden suggestion; the penalty applies when the declined/expired
thing is presented as a viable current option, commitment, or
recommendation. Cluttering answers with irrelevant declined options
would be a separate relevance metric, never a false-memory error.
Measured baseline (0.2.0 pilots): both systems suggested the declined
festival with declined-framing (1 each) — expect both to clear after
the fix, documented as regrade-shift, never rewritten into frozen runs.
Implemented 2026-09-21: `_suggestion_text` strips at sentence scope
(the memcal "but you said you can't make it" declines via anaphora
across the "but", which clause-splitting shreds); cues extended
(skip-stem, can/couldn-t-make-it, no-to covering told-X-no). Verbatim
regrade of both frozen pilot answers: 1 → 0 each. Known boundary
pinned by test: one declined item shields a second suggested trap in
the same sentence — deterministic layer exonerates, §33 judge owns it.

## Q5. Repetition protocol — IMPLEMENTED 2026-09-21

Three reps for the first comparative claim; config frozen across reps
(corpus, prompts, model, reasoning/temperature, provider config,
timeouts, tool budget — temperature NEVER varies); three predeclared
seeds where supported, else record provider-managed randomness. Reps
measure run-to-run stochastic robustness of one product configuration.
Same-seed determinism diagnostics are optional extras, not primary reps.
(`--repetitions N` + namespacing exist; seed pre-declaration and
randomness-provenance recording are missing.)
Implemented 2026-09-21: `run-pilot --seeds a,b,c` (count must match,
else fail closed); every rep records its seed on the life record and in
`rep_seeds`; `repetition_config` in report.json freezes the one config
(provider, driver model+provider, corpus version+hash, timeout) by
construction; REPORT.md renders seeds. No `--seeds` ⇒ honest
`provider-managed` per rep, never pretended control.

## Q6. Generated-corpus acceptance — SPEC RECORDED

Depth: measured 0/1/2/4/8 coverage; ≥1 genuine depth-8 story (prefer
more); must read plausibly human, never flips-for-flips'-sake. Noise:
large lives ≥80% non-support observations, ~2–10% direct scored
support, measured in the final report from the frozen corpus (guard
against quiz-sheets and trivia swamps; noise may still be real life,
just not support for that checkpoint). Half-life: all three gaps
(very-short-lived, durable preferences/facts, time-bounded state) with
transition behavior tested (time-bounded affects answers before expiry,
stops after, stays historically recoverable), each in >1 lineage, ≥1
example ultimately in held-out.

## Q7. Held-out exposure protocol — IMPLEMENTED 2026-09-21

Aggregate inspection (F1, comparisons, latency, error counts, curves)
never exposes. Detailed inspection of a life's evidence/rubric/output/
trace/failure — anything that could inform implementation, prompts,
grader, provider config, or Memcal — marks it `exposed_after_review`
for future versions (result preserved; kept as regression, never fresh
held-out again). Aggregate-only review leaves lives held out; no
repeated peeking while claiming untouched. Exposure log required:
life + date + reason + version-after-which-exposed. (Code has `exposed`;
needs the co-author's `exposed_after_review` value + the log.)
Implemented 2026-09-21: `ROLES` carries `exposed_after_review` (our
invented `exposed` retired, nothing used it); docs/exposure-log.md is
the log (life + date + reason + version-after-which-exposed, empty
today — mechanism waiting for lives); `ExposureLog` tests enforce the
vocab term, row well-formedness, and every-exposed-life-is-logged;
docs/split-lineage.md states the aggregate-vs-detailed rule.

---

# Proceed rulings (co-author reply, 2026-09-21)

Mandated sequence from here (supersedes any earlier ordering):

1. Draft the judge-calibration candidate pool (agent drafts, humans
   label — drafting is not labeling; Chris may be one of the two
   labelers; the second must be another human blind to Chris's labels;
   co-author does not count; disagreements prefer joint adjudication,
   else a third human).
2. Run the adversarial clock probe (approved; bundle with live work;
   isolated PACBench Hermes state only; host vs fictional date must
   imply different answers).
3. Generate + validate one 1,000–2,000-obs Spark life (approved; actual
   Spark model + rendering path; $50 runaway guard stands as safety,
   not quality; stop-and-inspect near the guard, never silently
   degrade; Chris is the required human reviewer via a compact review
   packet — truth, checkpoints, support windows, warnings,
   contradiction + extra-fact findings, difficulty, ambient sample;
   accepted only on his explicit sign-off).
4. Build + validate the real Hindsight adapter (structure against docs
   now is fine; never a fake provider or stub result; unvalidated
   until ingestion/readiness/retrieval/isolation/cleanup smoke tests
   pass on a real instance; no creds ⇒ fail preflight with a setup
   requirement; Chris provides creds when reached).
5. Run Memcal + Hindsight on the same frozen large dev life; manual
   audit (deterministic grading + inspection; development material,
   not held-out evidence).
6. Finalize the 200-example set (pool subset + audit residuals —
   deliberately authored adversarial AND observed failures), human
   label, calibrate + freeze the judge. The judge must be calibrated
   before any held-out comparative scoring.
7. Scale toward 30 lives with real dev/calibration/held-out separation
   under the curator boundary.

Do NOT freeze the final 200 immediately — leave room for audit
residuals. Corpus renderer, driver, provider models, and grader are
separate roles (Spark need not match the driver family). Never change
grading rules on who won; only genuine correctness/ambiguity.
