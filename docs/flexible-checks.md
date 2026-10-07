# Assistant conversations

Put conversations under the weekday where they happen. Each has an `id`, a human-readable `time` and ordered `turns`. Every turn has an `id`, the public `prompt`, and an English `expect` description. No fact IDs or matching lists are needed.

```json
{
  "id": "morning",
  "time": "9 am",
  "turns": [
    {"id": "plans", "prompt": "What's the deal with pizza tomorrow?",
     "expect": "Two Friday outings: Devon at 6 pm at Luigi's, and Maya at 7 pm at Rosa's with Leo."},
    {"id": "cancel", "kind": "behavior", "prompt": "Actually, cancel pizza tomorrow.",
     "expect": "Ask which outing I mean, without claiming either is cancelled."},
    {"id": "clarify", "kind": "behavior", "trigger": "clarification", "depends_on": "cancel",
     "prompt": "The one with Devon.", "expect": "Acknowledge that I am not going with Devon."}
  ]
}
```

Turns in the same conversation resume one actual Hermes session. A different conversation ID starts fresh while preserving the provider's memory. Reuse an ID on a later day to resume it. Each turn can override `time`; otherwise turns are two minutes apart. Planned story turns are distinct from omission-recovery follow-ups.

Preparation derives independently assessable criteria from each expectation, verifies them against available source and scripted user instructions, and reviews whether the public prompt fairly requests them. Expectations, scenes and private judging criteria never enter Hermes questions or source imports. Future source batches are ingested only when their native processing has completed by the simulated turn time.

`kind: behavior` grades an observable reply or clarification and gets no omission recovery. An acknowledgement does not prove a retained update or external calendar action. A later fresh conversation checks persistence. `trigger: clarification` sends its scripted reply only when the semantic judge identifies a clarification request; replies are not invented to rescue the agent. Use `depends_on` for later checks that require that reply. Contracts also bind conditional evidence dependencies, so an undelivered instruction cannot become assumed truth.

Each turn gets pass/fail for judged behavior. Malformed judges, missing script truth and execution errors remain ungraded with a reason. Independent conversations continue; unavailable dependent turns skip with a cause. A wrong answer remains a failure and does not stop unrelated checks. Review passes as well as failures while developing.

[Omission recovery](followups.md) keeps its original policy: one combined question for silent implicit omissions, partial credit separately, and none for zero recall, incorrect details, explicit misses or admitted lookup failure. Tools, gateway cost windows, native provider work and actual session continuity remain logged. The explorer's **Criteria & judges** view shows the frozen criteria and decisions per answer.
