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

## Q5. Repetition protocol — TO IMPLEMENT

Three reps for the first comparative claim; config frozen across reps
(corpus, prompts, model, reasoning/temperature, provider config,
timeouts, tool budget — temperature NEVER varies); three predeclared
seeds where supported, else record provider-managed randomness. Reps
measure run-to-run stochastic robustness of one product configuration.
Same-seed determinism diagnostics are optional extras, not primary reps.
(`--repetitions N` + namespacing exist; seed pre-declaration and
randomness-provenance recording are missing.)

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

## Q7. Held-out exposure protocol — TO IMPLEMENT

Aggregate inspection (F1, comparisons, latency, error counts, curves)
never exposes. Detailed inspection of a life's evidence/rubric/output/
trace/failure — anything that could inform implementation, prompts,
grader, provider config, or Memcal — marks it `exposed_after_review`
for future versions (result preserved; kept as regression, never fresh
held-out again). Aggregate-only review leaves lives held out; no
repeated peeking while claiming untouched. Exposure log required:
life + date + reason + version-after-which-exposed. (Code has `exposed`;
needs the co-author's `exposed_after_review` value + the log.)
