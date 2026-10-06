# One follow-up per fact

Every new native pilot and semantic experiment freezes one neutral question for
each claim before any provider sees the conversation. A model authors these
questions and a separate model call checks that they do not reveal expected
values, other hidden details, provider names or tool hints. The plan is hashed
and bound to the frozen contract. Every arm shares that plan.

After the first answer is graded, only initially missed or misrepresented facts
get a follow-up. Correct facts get none. Each failed fact gets exactly one user
question, in contract order, continuing the real recall session with the same
fictional clock. Hermes chooses its own tools. Later questions share the evolving
conversation, as normal user follow-ups would; they are not independent cold
queries. Failed turns or judges are saved and never retried as a second question.

The original grade is immutable. The separate recovery score awards 1 point for
an initially correct fact and 0.5 for a correct follow-up. An incorrect or
ungraded follow-up earns zero. These are uncalibrated diagnostic points, not a
provider ranking.

## Retention and retrieval effort

An event/page open accesses retained memory. Normal evidence bundled with that
open does not automatically make it a source fallback. Memory searches and tool
discovery remain visible as additional effort. Explicit archive searches,
original-evidence reads, conversation reads or message-connector access fail
retention even if the subsequent answer is correct: recovered answer and
retained fact are reported separately, with zero recovery credit for that path.
An attempted source access also counts, including when policy blocks it.

The initial answer's source fallback conservatively disqualifies all facts in
that answer; the benchmark does not pretend to know which fact came from which
source without provenance. For follow-ups, fallback disqualifies that targeted
fact. Unknown tool operations need a reviewed classification and receive no
retention credit. New providers must add documented operation mappings, not
invent SDK behavior. External message connectors are not configured in these
local trials; no connector result is simulated. Native Memcal archive tools are
available under the same ordinary provider allowlist.

Each arm writes `followups/recovery.json`, with the initial effort and every
attempt's operation log, including arguments, results, failures, repeated
operations, discovery and nested tool dispatches. Top-level calls and total tool
operations are separate: dispatch wrappers and nested requested operations both
count toward the latter. `followups/fact-N/turn.json`, `judgment.json` and
`grade.json` preserve the full trace and semantic decision. A timeout retains any
partial tool events. Resumed turns recover their full tool outputs from the
actual Hermes session DB.

Per-turn latency includes model inference, tools and process startup. Token
counts use differences of reported session totals; missing, reset or inconsistent
counters remain unknown. Gateway project activity during each recovery turn
records model call counts, settled uncached cost bounds and changes in holds.
Background calls can settle later, so these windows are not exact per-tool
invoices. Tool prices are not guessed, and local lookup wall time is not confused
with model inference time. No arbitrary threshold labels an operation expensive;
the saved counts, time and costs let you assess the work directly.

Native and semantic saved-run audits check exactly one attempt per failed fact,
frozen questions, session continuation, clocks, tool/token metrics and recovery
points. Older saved runs without follow-ups remain unchanged.

## Actual Pizza Day trial

`runs/pizza-native-one-followup-20261006` reused the original frozen 25-message
Contributor dialogue, with fresh native Memcal and Holographic homes.

Memcal initially answered four of five facts, omitting the street. Its sole
follow-up was **“What street is it on?”**. Hermes chose
`memcal_open({"ref":"E1"})` and answered **“On 5th — Luigi's on 5th.”** It used
one tool operation, no discovery, no repeated/failed calls, no archive search
and no message connector. First-answer score remains 4/5; recovery score is
4.5/5. The turn took 33.71 seconds including model inference; its gateway window
recorded two driver model calls and a $0.002847 uncached cost upper bound.
The reported output-token delta was unavailable and remains unknown.

Holographic initially answered all five facts and received no follow-up. Both
arms passed the native trace audit. Final cumulative task accounting was
$0.047903 with no holds, well below the $10 task cap and $5/UTC-day cap. Only the
native Memcal event-open path was exercised live; source-fallback policy is
covered by offline regressions, not claimed as a live connector test.
