# Current Pizza Week diagnostic

The current run is [runs/pizza-week-twists-20261007](runs/pizza-week-twists-20261007/summary.json). It has 42 verified messages in seven low-noise scenes and nine planned assistant turns. Wednesday establishes two Friday outings; Thursday changes one outing's time and restaurant, rejects another restaurant, and has Leo drop out then return. That saved trial included clarification, cancellation and fresh-session persistence checks; the revised author week below replaces those checks with factual recall.

The same frozen messages went to Memcal and Mem0. Memcal read the synthetic Apple Messages database and separate contacts through its native reader, then ran normal nightly processing at 03:00 Eastern. Mem0 OSS ingested the raw daily documents with native extraction, local Qdrant and Ollama embeddings. Hermes received only questions and each provider's normal context and shipped tools. All paid calls used Spark Contributor through the shared gateway.

## Findings to inspect

- Memcal extraction proposed distinct Devon and Maya outings, but storage combined them. Hermes recovered the initial nine answer facts using 15 tool calls, including archive and conversation lookups: answer correctness was 9/9, retention was 0/9. The gateway recorded 16 Hermes requests, 184,791 input tokens and 6,097 output tokens for that turn. It then explicitly named both outings and cancelled both. This demonstrates awareness of two plans; our expected clarification was an assistant-policy preference, not a memory failure. The old script consequently skipped six dependent turns. Independent Leo-history recall continued.
- Mem0 recalled both outings and asked which one to cancel. Hermes cancelled Devon's outing with `mem0_delete`. It correctly recalled the changed Maya outing at 5 pm at Mario's on Pine Street with Leo. Later, when asked to restore Devon's outing in a fresh session, it asked for the missing date and place. Final recall named Devon again but lacked his original time and venue. Hermes chose deletion; this action path mixes assistant tool choice with memory behavior and does not isolate retrieval quality.
- Preparation has a fairness bug: some frozen criteria require details beyond the English expectation, including a cancelled outing's old time/address and repeated group-plan details for each person. The generator and reviewer now explicitly limit future criteria to the expectation. This run's criteria and judgments remain unchanged for inspection; its numerical grades are diagnostic, not a fair comparison.
- The answer judge accepted Memcal's incorrect “closing shift Thursday night” wording while grading the narrower work-shift criterion. Generated texts also occasionally address the owner as “Me.” Both need review before larger cases.

The explorer shows source dialogue, processing clocks, tool arguments/results, model usage, per-turn criteria, judge prompts/responses, follow-ups and skipped-turn causes. It reads `runs/` directly; public evidence and the reusable frozen week ship with Git. Private provider homes, databases, credentials, caches and encrypted replay records remain local.

```sh
python serve_explorer.py
python -m pacbench.preparation_audit runs/pizza-week-twists-20261007/preparation
python -m pacbench.native_audit runs/pizza-week-twists-20261007
```

Native re-auditing requires private runtime files. A fresh checkout can inspect the saved audits and public evidence without them. The reusable corpus is `runs/pizza-week-twists-20261007/preparation/week.json`; repairing criteria should reuse these messages rather than regenerate them.

## Revised memory questions

[weeks/pizza_week.json](weeks/pizza_week.json) keeps all seven source scenes and now has six recall-only turns:

| Session | Natural question | What is judged |
| --- | --- | --- |
| Thursday morning | How many pizza plans did I make for Friday? | Two distinct outings; no demand to recap every detail. |
| Same session | Who am I meeting, and where and when? | Devon at 6 pm at Luigi's on 5th Street; Maya and Leo at 7 pm at Rosa's on Oak Street, Friday. |
| Friday morning, fresh | How many pizza plans do I have today? | Still two; changing a venue/time and Leo leaving/rejoining do not create extra outings. |
| Same session | What's the latest plan for each one? | Devon's original outing and the updated Maya/Leo outing at 5 pm at Mario's on Pine Street. |
| Same session | Did Leo sort out his shift? | He found cover and is coming. No requirement to name the coworker or repeat unrelated plan details. |
| Friday noon, fresh | Where am I meeting Devon tonight? | Luigi's on 5th Street, isolating it from the other outing's restaurant change. |

There are no cancellation acknowledgements, preferred clarification behaviors or action-dependent branches in this baseline. Before/after recall happens in separate sessions; within-session questions remain natural follow-ups. Only implicitly omitted expected details may get the existing single recovery question. Direct requests for a count, place or time do not get a second chance when that requested detail is missing or wrong.

The final group catch-up repeats the new time and venue. That is plausible dialogue, but final recall alone does not establish that the provider inferred the earlier DM-to-group link without that restatement. We judge the correct distinct current outings, not a narrated reconstruction of the ingestion process.

The stored grades remain evidence of the old script, not scores for this revision. The source messages can be reused; new criteria must be independently reviewed and frozen before a new run. No messages or provider answers were rewritten during this audit, and no paid model calls were made.

Next: prepare the revised six checks against the existing 42-message corpus, inspect their criteria for scope, then rerun with Memcal 0.8.2 and Mem0. Investigate genuine false event merges in the Memcal repository. Give the owner a realistic display name in a future corpus; increase noise after the small case is sound.
