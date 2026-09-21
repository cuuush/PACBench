# Exposure log (§39 + ruling 2026-09-21 Q7, fields extended per proceed ruling)

Aggregate inspection — overall F1, aggregate current-state /
historical accuracy, difficulty curves, aggregate failure-category
counts, latency and cost summaries — never exposes a life. Detailed
inspection of a life's evidence, rubric, output, trace, or failure —
which exact life failed, the hidden expected answer, the support
messages, the provider trace, the assistant response — marks it
`exposed_after_review` immediately: the original result is preserved
and the life stays as regression, but it never counts as fresh
held-out evidence again. No repeated peeking while claiming untouched.

Every exposure adds one row in the same change that flips the role.
Required columns (co-author spec): life ID, story lineage, date
exposed, who inspected it, reason for exposure, benchmark version of
the original held-out run.

| life | lineage | date | who | reason | version |
| ---- | ------- | ---- | --- | ------ | ------- |
| _(none — held-out is empty; this table is the mechanism waiting for lives)_ | | | | | |
