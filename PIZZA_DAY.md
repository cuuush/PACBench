# Current Pizza Week diagnostic

The current run is [pizza-week-recall-all-20261010](runs/pizza-week-recall-all-20261010/summary.json): the same 42 verified messages, seven low-noise scenes, six factual questions, and seven configured local providers. Wednesday establishes two Friday outings. Thursday changes the Maya/Leo outing's time and restaurant, rejects another restaurant, and has Leo drop out then return. Hermes sessions are shared within each morning and fresh between mornings and for the final Devon venue question.

| Provider | Observed answer results | Hermes tool calls | Finding |
| --- | --- | ---: | --- |
| Memcal 0.8.2 | 5 pass, 1 fail | 26 | Initially reported one merged outing. Hermes recovered and repaired both using archive/original-message tools. Both full-detail answers fail retention. |
| Mem0 OSS | 5 original passes; one judge-induced incomplete check | 6 | All six answers convey the expected facts. The judge incorrectly required repeating Friday in the updated-details answer; the corrected judge review gives 12/12. |
| OpenViking | 6 pass | 25 | Correct answers, but original-document reads fail retention. Fresh Devon lookup took 12 calls. |
| Supermemory local | 5 pass, 1 fail | 4 | Friday count omitted Devon. Three searches recovered both outings on the next direct question. |
| Hindsight local | 6 pass | 6 | Native ingestion and consolidation completed; answers used retained facts. |
| ByteRover local | 6 pass | 2 | First query timed out; second succeeded. Prefetch and internal query/curation work also incur provider inference. |
| Honcho local | No recall: readiness failed | 0 | Native capture accepted 18 messages, but only 9 processing units completed before the deadline; 3 were in progress and 6 pending. |

These are development diagnostics, not calibrated rankings. Answer correctness and retention are separate. The six questions contain 30 independently graded facts. Saved retention accounting is Memcal 3/30, Mem0 27/30 (affected by the judge bug), OpenViking 0/30, Supermemory 29/30, Hindsight 30/30 and ByteRover 30/30. Source fallback propagates through a shared assistant session. Hermes's repairs also persist into later sessions: later success does not establish correct initial extraction.

## Harness issues caught and corrected

Preparation rejected an overbroad Friday count contract that demanded venue/time details. The authored expectation now explicitly judges only the number of outings. Preparation then passed, with the original source bundle hash unchanged.

The answer judge penalized Mem0 for omitting the word Friday even though the question and ongoing conversation establish today's scope. The judge now respects inherited event/day scope. A [saved regression review](runs/pizza-week-recall-all-20261010/judge-scope-review/summary.json) accepts the correct Mem0 answer and still rejects Memcal's wrong count. Original trial grades remain unchanged; the explorer shows that review beside them. The attempted unnecessary follow-up failed authoring review and was never delivered. Other conversations continued.

ByteRover's shipped `brv_query` searches the curated knowledge tree, but our operation mapping left it unclassified. The mapping and a retention regression test are fixed. Only deterministic effort/retention accounting was recomputed; answers, judge verdicts, usage and tool traces are unchanged. The [review receipt](runs/pizza-week-recall-all-20261010/byterover-path-review.json) retains the original accounting.

Preparation integrity passes. Native audits accept the five complete arms; the overall audit stays failed because Mem0's recovery diagnostics and Honcho's ingestion are incomplete. Those failures are visible and have not been relabelled as successful trials.

## Ingestion and cost

Each system received the same raw daily source records through its native interface. Memcal used the provider-independent Apple Messages database, separate contacts and normal nightly processing at 03:00 Eastern. Other systems used documented document import or real source-message capture. No Hermes ingestion conversations, summaries or seeded facts were used. Recall exposed only the selected provider's exact shipped tools.

Every paid call used Spark Contributor through the shared gateway. The accounted upper bound rose by approximately **$2.06** during this run/review, to **$5.45 cumulative** and **$4.54 today**, below the $10 total/$5 daily caps. Failed or unresolved calls retain reservations. Post-shutdown usage is saved per provider; unknown token totals and per-tool attribution remain unknown. Top-level tool counts do not include provider prefetch or background model work.

The explorer reads the latest public evidence directly from `runs/`. It shows messages, native daily ingestion, actual answers and tool results, criteria, judge prompts/responses, scope reviews and model usage. Private homes, databases, credentials and encrypted replay remain ignored. The superseded run and duplicate preparation were removed.

On your Mac, after pulling the `work` branch:

```sh
python3 serve_explorer.py
```

Open `http://localhost:8137/?run=pizza-week-recall-all-20261010`. The cloud server's localhost is not your Mac's localhost.

The reusable frozen input is [preparation/week.json](runs/pizza-week-recall-all-20261010/preparation/week.json). Preparation and provider execution remain separate. Native re-auditing requires private runtime files; a fresh checkout can inspect the saved evidence/audits without them.

## Next steps

1. Fix the genuine Memcal event merge in the Memcal repository with lifecycle tests; do not tailor the source fixture or benchmark ingestion to it.
2. Diagnose Honcho's queue/deriver throughput before another full attempt. Do not turn accepted messages into a readiness pass.
3. Rerun the small frozen week with the corrected judge before treating scores as comparable. Review both passes and failures.
4. Give the owner a realistic display name in the next corpus. Only then increase chatter gradually.

The final group catch-up repeats the new time and venue. That is plausible, but final recall alone does not prove inference of the earlier DM-to-group link without restatement. We judge the current distinct outings, not an invented narration of internal processing.
