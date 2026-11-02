# Assistant conversations

Put conversations under the weekday where they happen. Each has an `id`, a human-readable `time` and ordered `turns`. Every turn has an `id`, the public `prompt`, and an English `expect` description. No fact IDs or matching lists are needed.

```json
{
  "id": "morning",
  "time": "9 am",
  "turns": [
    {"id": "plans", "prompt": "What's the deal with pizza tomorrow?",
     "expect": "Two Friday outings: Maya and Leo at noon at Mario's on Pine Street, and Devon at 6 pm at Luigi's on 5th Street."},
    {"id": "devon", "prompt": "What time am I meeting Devon?",
     "expect": "6 pm Friday."},
    {"id": "maya", "prompt": "And Maya?",
     "expect": "Noon Friday."}
  ]
}
```

Turns in the same conversation resume one actual Hermes session. A different conversation ID starts fresh while preserving the provider's memory. Reuse an ID on a later day to resume it. Each turn can override `time`; otherwise turns are two minutes apart. Planned story turns are distinct from omission-recovery follow-ups.

Preparation derives independently assessable criteria from each expectation, verifies them against available source and scripted user instructions, and reviews whether the public prompt fairly requests them. Expectations, scenes and private judging criteria never enter Hermes questions or source imports. Future source batches are ingested only when their native processing has completed by the simulated turn time.

For supplied factual questions, `event-scoped-v7` uses one independent request to review both the drafted criteria and the supplied question. Both reviews must pass; the shared request and response are bound to the frozen contract. Generated questions and behavioral checks retain their existing review sequence. Corpus-wide realism review remains separate.

Keep `expect` to the facts being graded. Put background and traps in the source scenes: for a yes/no question, a correct yes/no need not retell a joke or explain every rejected option. Inspect the derived criteria before provider execution; an English scope limit can still be misread by the draft and review models.

When a list is complete, say so explicitly and check the derived claim preserves that meaning. “Maya is attending” cannot rule out an invented extra attendee. Spot-check the actual judge with a plausible wrong answer before scaling a new condition; keep that receipt separate from provider results.

Current-state questions can expose superseded plans, partial changes and declined reschedule proposals. Grade the final agreed state: an unresolved old/new alternative or an extra cancelled outing is incorrect. Clearly labeled history is fine, and a provider returning historical records is not itself a failure. Keep unrelated correct attributes independent when another detail is stale.

Use factual recall for the memory benchmark. Do not use an ambiguous cancellation plus a mandatory clarification as proof of memory quality. Separately, `kind: behavior` can grade an authored observable reply or clarification and gets no omission recovery. An acknowledgement does not prove a retained update or external calendar action. A later fresh conversation checks persistence. `trigger: clarification` sends its scripted reply only when the semantic judge identifies a clarification request; replies are not invented to rescue the agent. Use `depends_on` for later checks that require that reply. Contracts also bind conditional evidence dependencies, so an undelivered instruction cannot become assumed truth.

Use `requires: ["earlier-turn-id"]` when a later test assumes a public instruction was delivered, including across fresh sessions. Required IDs must be unique preceding turns. An acknowledged update is not proof of persistence; check it later through the provider in a fresh conversation. Undelivered or ungraded prerequisites skip dependent checks rather than inventing successful writes.

For Supermemory and OpenViking, a dependent fresh session also waits for the accepted native document/task to finish processing. This uses read-only status APIs and saved receipts, without flushing or resubmitting data. A simulated hour does not advance a real backend's queue. A failed or unavailable completion receipt leaves the dependent question ungraded. Native defaults and the daily budget still apply.

Each turn gets pass/fail for judged behavior. Malformed judges, missing script truth and execution errors remain ungraded with a reason. Independent conversations continue; unavailable dependent turns skip with a cause. A wrong answer remains a failure and does not stop unrelated checks. Review passes as well as failures while developing.

[Omission recovery](followups.md) keeps its original policy: one combined question for silent implicit omissions, partial credit separately, and none for zero recall, incorrect details, explicit misses or admitted lookup failure. Tools, gateway cost windows, native provider work and actual session continuity remain logged. The explorer's **Criteria & judges** view shows the frozen criteria and decisions per answer.


Fresh-session checks that depend on an earlier assistant update wait for native
write completion. Honcho uses its current per-session setup default, witnesses
the actual captured user input, and checks enrolled owner work plus native queue
errors. It retains its normal accumulation delay. Empty write discovery cannot
prove completion. These are availability-gated recall trials; simulated question
clocks are not measurements of wall-clock aging or a response-time SLA.

Answer evidence diagnostics can be requested alongside the existing semantic judgment. They report whether captured prefetch or direct tool results contain evidence for each criterion, and separately assess factual assertions made beyond the criteria against the cited source excerpts. These are diagnostic observations and do not change claim scores or recovery. Source coverage is partial: missing support is unverifiable, and only direct contrary evidence supports a contradiction. Captured inputs record truncation and missing citations; provider background traces are not treated as visible to the answer. Availability alone does not establish whether the assistant used or ignored evidence. Malformed diagnostics remain ungraded without discarding an otherwise valid answer grade; older judgments remain unchanged.
