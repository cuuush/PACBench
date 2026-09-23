# Authoring choice for the next PACBench corpus

The benchmark contract separates intended truth, the messages that establish it, and the assistant's later answer. A generated conversation cannot be its own unchecked answer key.

| Approach | Author writes | Advantage | Cost |
| --- | --- | --- | --- |
| Scene + test intent | Two prose paragraphs | Fastest to sketch | A model has to infer the exact facts the author meant; review can be fuzzy. |
| Thread + required facts **(recommended)** | A thread description and one short assertion per required fact | Pins truth while leaving dialogue style and side chatter open | The author must decide which details matter. |
| Generate, then choose facts | Only a thread description at first | Most creative freedom | The model may omit the important facts; choosing questions afterward risks cherry-picking easy cases. Useful for discovery, not scored comparisons. |

A fixed `date`/`time`/`place` schema would be easy for an ice cream plan, but it would grow special fields for movies, seats, cancellations, uncertainty, and tasks. A list of plain-language assertions keeps each detail independently judgeable without a domain schema:

```json
{
  "at": "2026-10-01T17:00:00",
  "scene": "A group chat with Mike, Sarah, and me. We catch up and eventually agree to get ice cream next Wednesday.",
  "facts": [
    "The meetup is Wednesday, October 7, 2026.",
    "The agreed time is 5pm.",
    "The agreed place is Sweet Scoops."
  ]
}
```

The generator sees the scene and facts and can write unrelated chatter, jokes, and a natural path into the plan. A separate verifier must cite messages that establish **each** fact and the scenario's commitment. It rejects a tentative suggestion or a contradiction. The contract then makes one evidence-cited claim per authored fact and checks the question for leaked answers or false premises. The frozen messages and question go to both providers. The answer judge sees only the question, answer, and frozen claims.

The author supplies one occurrence time when a relative date such as “next Wednesday” needs an anchor. PACBench assigns timestamps to however many messages the writer chooses, then derives the next-morning question time and preceding maintenance time by default. It derives internal scene and thread IDs from scene order. An explicit thread ID is useful only when several scenes are part of the same conversation.

The list is an authoring target, not ground truth by itself. If the dialogue never establishes 5pm, the build fails. New scored facts that emerge accidentally in filler need an explicit author review before freezing.

The first natural group-chat draft exposed a verifier false positive: it accepted a participant's tentative response before the final plan. That saved preparation is excluded. The tightened prompt produced a new prepared scene where all three people accept the final proposal, and its three claims cite the dialogue. This is one reviewed example, not proof that the verifier will catch every subtle commitment error.

## Work still needed for the full benchmark contract

The pizza and movie runs exercise direct recall in short threads. The contract also calls for coherent seven-day lives with older history, multiple channels and threads, stale and conflicting evidence, entity collisions, commitments versus invitations, corrections and cancellations, proactive reminders, uncertainty, and false-memory controls. The current runner ingests one frozen bundle at one boundary, so staged evidence delivery and checkpoints across time still need a generic schedule. A cross-thread contradiction scan and substantial human review are needed before scaling corpus generation. The nine proposed pizza calibration labels still need human review. Repeated matched trials, declared development/calibration/held-out splits, and thirty independent lives are needed before comparative claims.
