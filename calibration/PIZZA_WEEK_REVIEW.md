# Pizza Day week: human score review

Run: [`semantic-pizza-week-live-v15-20260928`](../runs/semantic-pizza-week-live-v15-20260928/summary.json). Its [trace audit](../runs/semantic-pizza-week-live-v15-20260928/run-audit.json) passes. These model verdicts are **proposals**, not calibrated scores.

The fixed question each morning was: “Can you catch me up on the plans and outings I've discussed lately, including what changed or happened?” All 96 authored-fact/checkpoint pairs were judged in each arm. Repeated checkpoints test persistence, but their verdicts are correlated; they are not 96 independent facts or experiments.

| Pattern to review | Assistant behavior | Judge verdicts |
| --- | --- | --- |
| Hindsight, Friday morning | Named Luigi's but omitted “on 5th.” | One `missed`: Monday venue-street fact. |
| Hindsight, Saturday through Monday mornings | Gave the final 7 p.m. pizza time and correctly treated dinner as past, but omitted the original 6 p.m. agreement and the 6→7 change. | Six `missed` verdicts: the same two history claims at three checkpoints. |
| Memcal, Monday morning | Said the user did not attend the ducks outing but did not explicitly say the user had never agreed to it. | One `missed`: prior undecided status. |

For each pattern, a human reviewer should decide whether the broad catch-up question reasonably required that detail. The question must stay fixed across both arms; review the saved answer, frozen claim, dialogue citation, and judge reason before accepting a label. If the historical time is too specific for a broad catch-up, add a matched follow-up question about changes in a future frozen contract rather than silently changing these saved verdicts.

The earlier [nine-answer calibration set](../runs/semantic-20260923T140000Z/calibration-proposed.json) covers individual omissions, wrong values, uncertainty, and historical mentions for a simpler five-fact question. It also awaits human labels. Neither set checks unsupported extra assertions; that requires a separate evidence-backed review.
