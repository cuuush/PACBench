# Benchmark integrity rules (standing)

These override convenience, always. Cited by commit messages that touch
corpus, grader, or reporting.

1. **Never optimize PACBench to make any provider win.** Development
   failures are product targets. If a provider loses a valid case,
   preserve the case and the result. Never remove, weaken, or reclassify
   a valid scenario because of who performs better on it. (Contract
   co-author directive, 2026-09-20.)
2. **Exposure is forever and first-class.** Lives are `development`,
   `calibration`, `held-out`, or `exposed` (held-out that materially
   informed an implementation change — moved in the next corpus version,
   never silently). Current dev/calibration lives are permanently
   exposed: never use them as held-out comparative evidence.
3. **Diagnose conservatively.** Trace-confirmed diagnoses and heuristic
   diagnoses are labeled as such (`live.classify` `basis` field).
   `unknown` beats a fake diagnosis.
4. **Small-corpus phase is over.** No more tiny hand-authored lives before
   the generated large-life path is proven (one 1–2k-obs Spark-rendered
   life with wired semantic QA, per the co-author's nine proof points).
   Reports polish waits behind that milestone.
