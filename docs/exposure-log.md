# Exposure log (§39 + ruling 2026-09-21 Q7)

Aggregate inspection — F1, comparisons, latency, error counts, curves —
never exposes a life. Detailed inspection of a life's evidence, rubric,
output, trace, or failure — anything that could inform implementation,
prompts, grader, provider config, or Memcal — marks it
`exposed_after_review` for future corpus versions: the result is
preserved and the life stays as regression, but it is never fresh
held-out again. Aggregate-only review leaves lives held out; no
repeated peeking while claiming untouched.

Every exposure adds one row: life + date + reason +
version-after-which-exposed. A life with role `exposed_after_review`
must appear here (enforced by `ExposureLog` tests); the reverse is
immediate — log the row in the same change that flips the role.

| life | date | reason | version-after-which-exposed |
| ---- | ---- | ------ | --------------------------- |
| _(none — held-out is empty; this table is the mechanism waiting for lives)_ | | | |
