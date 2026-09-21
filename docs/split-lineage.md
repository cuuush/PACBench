# Split lineage map (§39)

Lineage — not life names — is what split-independence is checked on.
Close variants (same beats, traps, names, or timings under a rename)
share one lineage string whatever their split.

| Lineage | Lives | Roles | Ruling |
|---|---|---|---|
| `dentist-cleaning` | dentist-depth | dev | Unique dev lineage. `dentist-friday` shares only incidental surface (one Friday-cleaning reminder vs the 4-move saga — no shared beats or traps), so it stays out; controls inherit their parent's lineage instead (below). |
| `poker-reschedule` | poker-collision, poker-move, poker-unchanged | dev + 2 calibration | Same move shape (Friday 7 at Sam's → Saturday 8), same Sam/Jordan surface, stale-repeat trap in both. Close variants by any reading. |
| `weekend-opportunities` | coat-weekend, weekend-plans, dentist-friday | dev + 2 calibration | Same "anything fun this weekend?" shape with expired + declined traps. `dentist-friday` is `weekend-plans`' control — a control is part of its parent's experiment, so it inherits the parent's lineage even though its content is a pointed dentist question. |
| `reunion-picnic` | reunion-thread | dev | Unique. |
| `revoke-dinner` | revoke-week | dev | Unique. |

Consequences (enforced by `LineageDiscipline`, tests/test_v01.py):

- Calibration is derivative: every calibration lineage contains a
  development life. Small families debug the pipeline against their dev
  parent; they can never be promoted into comparative evidence.
- Held-out must be lineage-unique: no held-out life may share lineage
  with any other life. Vacuous today (held-out is empty) — standing
  guard for the 30-life corpus, where each new life needs a genuinely
  new family, not a rename.
- Controls share lineage with their `control_of` target (a control that
  drifts families is a different experiment).
- Moving a life between lineages, or adding a life, means editing the
  EXPECTED table deliberately — silent re-familiedrift fails the suite.
- Lineage ships in the freeze bundle (`freeze.canonical`), so a
  re-familied corpus moves the pinned hash.
- Exposure (ruling 2026-09-21 Q7): aggregate inspection never exposes;
  detailed inspection of a life's evidence/rubric/output/trace/failure
  flips its role to `exposed_after_review` and logs the row in
  docs/exposure-log.md the same change. Results preserved as
  regression, never fresh held-out again.
