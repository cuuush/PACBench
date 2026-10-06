# One combined follow-up for all missing facts

New native pilots and semantic experiments ask **one combined follow-up for ALL
initially missed or wrong facts**. There is at most one recovery message total;
correct facts are excluded. Both an all-correct answer and an answer with zero correct expected facts get no follow-up. Zero recall includes answers where every fact is missing or wrong; it skips question authoring, review, provider turns and recovery judging, recording `skip_reason: no_initial_correct_facts`. Eligibility is assessed separately for each evaluation question.

The policy is bound to the frozen contract before testing. Questions are written
only when needed. Spark receives just the original question and the missing
facts in a stateless call, with this short instruction:

> Write one short follow-up asking for these missing details without supplying
> their answers or tool instructions.

The API also requests a JSON question field. One brief independent Spark review
checks coverage and leakage. The reviewed message is hashed and reused across
providers missing the same fact set. Expected fact values never go to Hermes.
No provider names, tools, trace history or instructions to try harder are given
to the question writer. Hermes continues its actual recall session under the
same fictional clock and chooses its own tools. No failed turn or judge is
retried as a second recovery message.

New reports use `protocol_version: 4`, with `minimum_initial_correct: 1`. Earlier per-fact (protocol 1), component-based combined (protocol 2), and minimal-authoring (protocol 3) trials remain unchanged and auditable under their recorded policies.

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
source without provenance. For a combined follow-up, fallback conservatively disqualifies all targeted
facts, since this trace does not assign each fact to a particular tool result. Unknown tool operations need a reviewed classification and receive no
retention credit. New providers must add documented operation mappings, not
invent SDK behavior. External message connectors are not configured in these
local trials; no connector result is simulated. Native Memcal archive tools are
available under the same ordinary provider allowlist.

Each arm writes `followups/recovery.json`, with the initial effort and every
attempt's operation log, including arguments, results, failures, repeated
operations, discovery and nested tool dispatches. Top-level calls and total tool
operations are separate: dispatch wrappers and nested requested operations both
count toward the latter. `followups/combined/turn.json`, `question-plan.json`, `judgment.json` and
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

Native and semantic saved-run audits check one message total covering every failed fact,
frozen questions, session continuation, clocks, tool/token metrics and recovery
points. Older saved runs without follow-ups remain unchanged.

## Earlier per-fact Pizza Day trial (preserved)

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
