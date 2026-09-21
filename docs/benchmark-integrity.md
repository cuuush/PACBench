# Benchmark integrity rules (standing)

These override convenience, always. Cited by commit messages that touch
corpus, grader, or reporting.

1. **Never optimize PACBench to make any provider win.** Development
   failures are product targets. If a provider loses a valid case,
   preserve the case and the result. Never remove, weaken, or reclassify
   a valid scenario because of who performs better on it. (Contract
   co-author directive, 2026-09-20.)
2. **Exposure is forever and first-class.** Lives are `development`,
   `calibration`, `held-out`, or `exposed_after_review` (held-out that
   was inspected in detail — marked immediately, preserved as
   regression, never fresh held-out again; see docs/exposure-log.md).
   Current dev/calibration lives are permanently exposed: never use
   them as held-out comparative evidence. Aggregate-only review never
   exposes; repeated peeking while claiming untouched is forbidden.
3. **Diagnose conservatively.** Trace-confirmed diagnoses and heuristic
   diagnoses are labeled as such (`live.classify` `basis` field).
   `unknown` beats a fake diagnosis.
4. **Small-corpus phase is over.** No more tiny hand-authored lives before
   the generated large-life path is proven (one 1–2k-obs Spark-rendered
   life with wired semantic QA, per the co-author's nine proof points).
   Reports polish waits behind that milestone.
5. **Never tune on who won.** Do not change grading rules, prompts, or
   provider configs based on which system won a pilot. Changes address
   genuine grading correctness or ambiguity only. (Contract co-author
   directive, 2026-09-21.)
6. **Roles stay separate.** Corpus renderer, Hermes driver,
   provider-internal models, and grader are separate roles — never the
   same model serving two masters in one comparison. Held-out
   generation comes from the frozen benchmark spec and LifeSpec
   requirements, never from "make a case that defeats provider X": the
   generating model must not see provider outputs or known failures.
   Held-out material lives behind a curator boundary (Chris or a
   designee); the tuning agent receives aggregate held-out results
   only, and detailed inspection marks `exposed_after_review`
   immediately. (Contract co-author directive, 2026-09-21.)
