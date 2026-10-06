# Delegated prompt audit — 2026-10-06

The user requested a subagent audit and shorter, isolated Spark follow-up
prompting. The subagent performed a read-only audit of follow-ups, scene
writing/verification, contract generation and grading. It made no paid calls.

Changes implemented:

- Removed upfront author/reviewer calls for per-fact follow-up components.
  Bind the policy before testing; write a question only when facts are missed.
- Generate ONE combined recovery message in an isolated Spark call containing
  only the original question and the missing facts. Use one brief independent
  review for answer leakage and coverage. Cache the reviewed question by contract,
  policy and missing IDs so matching arms share the same wording.
- Reduced overlapping prohibitions to a short request: ask for the missing
  details without supplying answers or tool instructions. Clarify only that the
  message is from the user to their assistant, avoiding ambiguous pronouns. No provider trace,
  tool catalog, private scene description or conversational history goes to the
  author. Actual expected facts stay with the author/reviewer and grader.
- Removed old ticket-purchase examples from generic contract question prompts.
  Retained the general requirement not to presuppose unsupported facts.
- Removed forced final acceptance after every date/time/place detail. Verify
  clear commitment in context, with evidence, rather than scripted dialogue order.
- Removed current-plan-only wording from grading; assess the state and time
  described by each claim, including historical claims.

The short, uniform public transcript handoff and native provider prompts were
unchanged. Old frozen dialogues, questions, grades and runs remain unchanged.
Protocol 1 (per-fact) and protocol 2 (component-based combined) remain auditable;
new runs use protocol 3 (minimal isolated authoring, one combined message total).

Offline verification: 232 tests passed, two skipped; targeted grading/recovery
checks passed after the final wording change. Live results are recorded in
[live test results](followup-tests-2026-10-06.md). A trace audit establishes integrity, not calibrated
provider quality.
