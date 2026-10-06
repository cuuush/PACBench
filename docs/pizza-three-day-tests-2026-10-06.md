# Pizza Day stages and Eastern clock fix — 2026-10-06

These are genuine native Hermes diagnostics using Spark Contributor and scratch
homes: Memcal, Holographic, and local OSS Mem0. Normal built-in memory remains
additive; recall starts a fresh session. Every arm within a stage gets the same
verified, frozen messages, question, and independently reviewed claim contract.
No facts were seeded, tool results simulated, or memory writes forced.

## Gradual scope

- Retest: the original 25-message Pizza Day bundle, five facts.
- Two-day stage: 20 messages, one Devon plan changing from 6pm to 7pm, seven
  independently scored facts including commitment status and the superseded time.
- Three-day stage: 50 messages, five conversations over September 8–10 in
  `America/New_York`, with a September 11 morning recall. Devon's Friday plan
  changes time and restaurant; Maya's separate Saturday plan changes time.
  Fifteen facts cover current plans, commitments, and superseded details.

The two new stages share exactly the same first two realized conversations.
Their source IDs/contracts are separately frozen; old runs remain unchanged.
Each scene is bounded at 8–12 messages (all five realized as ten). No padding or
truncation is used. The +100 and +1000 volume stages were not run.

Final plans: Devon, Friday September 11 at 7pm, Mario's on Pine Street;
Maya, Saturday September 12 at 1pm, Rosa's on Oak Street. Originally Devon's
plan was Luigi's on 5th at 6pm, and Maya's plan was at noon. Both are confirmed.
These answer keys went only to authors/reviewers and the grader, never Hermes.

## Before the clock fix

| Stage | Provider | Initial correct | Retention points after recovery |
| --- | --- | ---: | ---: |
| 25-message retest | Memcal | 5/5 | 5/5 |
| same | Holographic | 5/5 | 5/5 |
| same | Mem0 | 0/5 | 0/5 |
| Two days | Memcal | 7/7 | 0/7: explicit source read |
| same | Holographic | 7/7 | 7/7 |
| same | Mem0 | 0/7 | 0/7 |
| Three days, two trips | Memcal | 15/15 | 0/15: two explicit source reads |
| same | Holographic | 12/15 | 12/15; recovery caveat below |
| same | Mem0 | 12/15 | 12/15 |

Saved roots in private `runs/`:
`pizza-native-retest-20261006-three-day-stage-live`,
`pizza-two-day-stage-native-20261006`, and
`pizza-two-trips-three-days-native-20261006`. All three trace-integrity audits
pass. The first attempted retest was blocked before any model call because the
local gateway had stopped; it remains in
`pizza-native-retest-20261006-three-day-stage`. The existing durable gateway
ledger and local Ollama service were restarted without resetting accounting.

Memcal correctly updated an existing event and kept the outings distinct. But
its two-day recall called `memcal_open(E1)` then `memcal_open_source(E1)`; its
three-day recall opened both events and both original sources. Event opens
already contained current fields and change history. The strict policy counts
explicit source reads as retention failure, conservatively for all facts in that
answer. This shows unnecessary source access, not proof the events lacked facts.
These are retained chat-source reads, not a live iMessage connector call.

Mem0's small retest used one initial search and six recovery searches (32.03s);
the two-day trial used three initial searches and four recovery searches
(16.73s). Neither recovered a fact. In the three-day trial it made real native
adds/updates, retained all twelve current-plan facts, and missed the three
superseded details: Devon's old time/venue and Maya's old time. Four follow-up
searches (22.05s) recovered none. Do not force writes to hide the variability.

Holographic's three-day trial missed Devon's old time and Maya's restaurant and
street. It made no durable write for Maya's original venue on day two. Its
follow-up used four operations (two retained-memory reads and two feedback
writes), taking 30.26s, with no new facts recovered.

## Follow-up coverage bug and minimal fix

The earlier Holographic follow-up asked for Devon's *current* time when the
missed fact was his *original* time. Its initial short review incorrectly
approved coverage. A separate real Spark review now covers only Maya's two
facts, rejecting coverage of the historical time. Preserve the original run and
its initial grade; that turn is not a fair recovery test of Devon's old time.
A trace audit checks recorded integrity, not semantic reliability of the reviewer.

Added one short author instruction to preserve current versus historical meaning,
and the same check in review. New policy hashes include authoring revision
`current-vs-historical-v1`. The isolated live regression generated:
“Hey, can you recap my two pizza plans — what was the originally agreed time
for Devon and has it changed, and what's the current restaurant for Maya and
where is it?” Its independent review covers all three missing facts without
supplying answers. Full model calls are in
`runs/pizza-followup-temporal-regression-20261006`. This is a live author/reviewer
test, not another recovery attempt or a rewritten provider result.

## Direct Memcal fix and uniform Eastern configuration

Memcal's documented clock pin discarded an explicit `-04:00` offset and labeled
the unchanged wall time `+00:00`. A UTC-host reproduction is saved as
`clock-pin-diagnostic.json` in the original three-day run. Fix committed directly
to `/workspace/memcal` as **523e76b**: preserve aware pins; resolve naive pins
using the local timezone on the pinned date, including DST. No provider prompt,
retrieval behavior, or storage policy was patched.

PACBench commit **0da4d20** gives every native provider the frozen scenario's
`TZ=America/New_York`, records that condition, and audits uniformity. This is a
normal process timezone setting applied to all arms. Future Memcal product bugs
must be fixed and committed in the Memcal repository; the standing instruction
is in PACBench's `AGENTS.md`.

The fresh corrected run uses the unchanged 50-message bundle and contract:
`runs/pizza-two-trips-three-days-eastern-native-20261006`. Its condition file
records both commits. It began before the historical follow-up wording fix, so
its frozen recovery policy uses the previous authoring revision.

| Eastern three-day rerun | Initial correct | Retention points |
| --- | ---: | ---: |
| Memcal | 15/15 | 15/15 |
| Holographic | 13/15 | 13/15 |
| Mem0 | 15/15 | 15/15 |

All three corrected-run integrity audits pass. Memcal used two event opens,
no source reads. Its stored archive timestamps now correctly carry `-04:00`.
The answer's heading still says Luigi's while the location and explicit change
history say Mario's: a stale label that the frozen grader did not penalize.
Treat the 15/15 as a diagnostic judgment, not proof the entire answer is clean.
Holographic still missed Maya's restaurant/street and recovered neither after
one combined follow-up with two operations (16.42s). Mem0 used normal native
adds/updates and recalled all fifteen facts, unlike its earlier failures.
The repeats show variability; do not attribute changed retrieval choices to a
clock fix alone.

Snapshot accounting at completion of these stages: $0.4752186 including $0.1081344 in holds, under the $10 cap. The durable ledger remains authoritative;
see `final-budget-report.json` in the Eastern run. These are usage-derived
uncached-price bounds/reservations, not an invoice total.

## Checks and remaining limits

PACBench: 233 passed, two skipped. Memcal: 2,388 tests run in Eastern time,
39 skipped, no failures. Four new clock tests cover explicit summer/winter
instants on a UTC host, environment pins, naive Eastern pins across DST, and
both folds of the repeated autumn hour. Targeted clock sweeps pass all six
moments across UTC and Eastern.

Memcal's deterministic temporal integration benchmark is unchanged before and
after: 391/400 hard checks, six established failures and three frontier gaps.
It uses oracle diffs, not model accuracy. A pre-existing source-heading unit test
fails under UTC identically on baseline and fixed code because it expects Eastern
wall time; the full Eastern suite passes. Earlier test-environment failures from
the wrong venv/missing declared typedstream dependency and the private project
.env were resolved by using the installed dependency and temporarily isolating
that .env during unit tests; the file was restored unchanged.

Next: investigate native Mem0 sync lifecycle without provider-specific waits;
check why Holographic drops a plan on an intermediate day; test stale event
labels and contradictory answers with calibrated judgments. Keep volume bounded.
