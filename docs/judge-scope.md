# Semantic-judge scope (P1 scoping note — no implementation)

Contract §33: structured values (times, dates, counts, status, addresses)
normalize deterministically; only semantic cases that cannot be decided by
normalization go to a narrow blinded model judge. The judge answers small
questions ("does this answer assert this specific claim?") and never sees
provider identity, aggregate score, hidden corpus beyond the relevant atom,
or whether a verdict would help any provider.

## 1. The deterministic paraphrase limit, with P0 evidence

`pacbench/grade.py` resolves every DATE/TIME/STATUS/YESNO/ENTITY slot without
a model. TEXT slots try deterministic containment first and only the residual
falls through to `judge=` (currently always `None` → residual is MISSED, a
conservative default that never invents a pass).

P0 pilot (`runs/pilot-20260920T193846Z`) exhibits both sides of the limit:

- Textbook judge-residual — `poker-collision/eric-now`, slot `status` (TEXT,
  expected `still on`): answer "You're on for Eric's game Sat Sep 26, 8pm …
  bringing chips" semantically asserts the claim, containment misses it, and
  the slot reads MISSED. A blinded judge asking "does this answer assert the
  user is still on for Eric's game?" should credit it. Contrast with the same
  answer's `time` slot (`8pm` MISREPRESENTED via the history clause "moved it
  from 7pm") — that verdict is about history-cue filtering (§4), not
  paraphrase, and a judge must not override it.
- True miss, judge must not save — `coat-weekend/anything-fun`, slots
  `opt-concert` / `opt-market` (TEXT, expected `concert` / `farmers market`):
  the answer surfaces neither option at all. This is a retrieval/recall miss
  (re-classified P1 as category 4, tool-selection). A judge that credits
  these would be inventing passes — calibration must include such negatives.

Rule of thumb for the implementer: the judge may only ever flip MISSED →
CORRECT on TEXT residuals, never excuse a MISREPRESENTED slot, and every
flip must cite the answer span that carries the claim.

## 1b. Seed calibration labels (contract co-author, agreed)

The only three TEXT residuals in the frozen pilots, labeled by the contract
co-author as the seed calibration set. A future judge must reproduce these
before scored use; calibration as a whole needs a bigger sample first.

- `coat-weekend/anything-fun`, `opt-concert` (expected `concert`): answer
  surfaces neither option → **MISSED**. A judge crediting this would invent
  a pass ("pass factory" — the judge must never infer the assistant
  "basically knew" something it did not assert).
- `coat-weekend/anything-fun`, `opt-market` (expected `farmers market`):
  same answer → **MISSED**, same reason.
- `poker-collision/eric-now`, `status` (expected `still on`): answer "You're
  on for Eric's game Sat Sep 26, 8pm … bringing chips" → **CORRECT**. A
  correct event-status atom must not rescue the same answer's incorrect
  time atom, and the judge must not overwrite deterministic slot grading.

## 2. Judge contract (to be implemented, not here)
- Signature: `(answer: str, claim: str) -> bool`, one blind binary question
  per residual TEXT slot. Inputs are the raw answer and the single claim
  string; nothing else is in scope.
- Blindness (blinded by construction, not by prompt): provider identity,
  aggregate score, other slots, corpus observations, and support manifest
  are never arguments — the call site (`runner.grade_answer` → `grade`)
  cannot leak what it does not pass.
- Calibration + human-agreement gate: before scored use, the judge is
  measured against human labels on a fixed residual sample including
  known-negatives (e.g. the anything-fun misses above). Ship threshold and
  sample in the artifact; below-threshold judge stays unwired and residuals
  stay MISSED.
- Regrade path: regrading saved answers under a recorded grader version
  must never need re-ingestion. The run manifest already records
  `grader: "deterministic (no judge)"`; a wired judge bumps that string
  (e.g. `deterministic + judge/<name>@<calibration-id>`), and per-atom
  `grade.json` entries record which verdicts came from the judge.

## 3. Reproducibility lesson (observed P1)

Pilot grades are frozen artifacts pinned to the grader that produced them.
Commit `a7c3d8b` (YESNO first-cue polarity) landed after the P0 pilot:
recomputing `dentist-depth/canceled` under HEAD flips its saved CORRECT to
MISSED. That is the system working as designed (grader versions differ),
not pilot invalidation — but it proves the regrade protocol in §2 must be
followed literally: never recompute pilot grades under a new grader and
compare them to frozen classifications.

## 4. Out-of-scope observations for other lanes (no action here)

- `grade.py` is read-only for Benchmark: the "moved it from 7pm" history-cue
  gap (`_HISTORY_CUES` lacks "moved it from"-style infixes) belongs to the
  M2 grading lane.
- `coat-weekend/concert-rsvp`: fixed at corpus 0.2.0 per the co-author —
  the DATE/empty-expected slot is now a YESNO commitment-status atom
  (`rsvp`: expected `no`) plus a separate DATE atom for the known concert
  date. An empty DATE sentinel muddled the opportunity-vs-commitment
  distinction; see `life_c_coat.py`.
