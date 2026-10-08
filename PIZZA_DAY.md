# Current Pizza Week diagnostic

The current run is [runs/pizza-week-twists-20261007](runs/pizza-week-twists-20261007/summary.json). It has 42 verified messages in seven low-noise scenes and nine planned assistant turns. Wednesday establishes two Friday outings; Thursday changes one outing's time and restaurant, rejects another restaurant, and has Leo drop out then return. Thursday and Friday assistant conversations test clarification, cancellation and fresh-session persistence.

The same frozen messages went to Memcal and Mem0. Memcal read the synthetic Apple Messages database and separate contacts through its native reader, then ran normal nightly processing at 03:00 Eastern. Mem0 OSS ingested the raw daily documents with native extraction, local Qdrant and Ollama embeddings. Hermes received only questions and each provider's normal context and shipped tools. All paid calls used Spark Contributor through the shared gateway.

## Findings to inspect

- Memcal extraction proposed distinct Devon and Maya outings, but storage combined them. Hermes recovered the initial nine answer facts using 15 tool calls, including archive and conversation lookups: answer correctness was 9/9, retention was 0/9. The gateway recorded 16 Hermes requests, 184,791 input tokens and 6,097 output tokens for that turn. It then cancelled both outings instead of asking which one, so six dependent story turns were skipped. Independent Leo-history recall continued.
- Mem0 recalled both outings and asked which one to cancel. Hermes cancelled Devon's outing with `mem0_delete`. It correctly recalled the changed Maya outing at 5 pm at Mario's on Pine Street with Leo. Later, when asked to restore Devon's outing in a fresh session, it asked for the missing date and place. Final recall named Devon again but lacked his original time and venue.
- Preparation has a fairness bug: some frozen criteria require details beyond the English expectation, including a cancelled outing's old time/address and repeated group-plan details for each person. The generator and reviewer now explicitly limit future criteria to the expectation. This run's criteria and judgments remain unchanged for inspection; its numerical grades are diagnostic, not a fair comparison.
- The answer judge accepted Memcal's incorrect “closing shift Thursday night” wording while grading the narrower work-shift criterion. Generated texts also occasionally address the owner as “Me.” Both need review before larger cases.

The explorer shows source dialogue, processing clocks, tool arguments/results, model usage, per-turn criteria, judge prompts/responses, follow-ups and skipped-turn causes. It reads `runs/` directly; public evidence and the reusable frozen week ship with Git. Private provider homes, databases, credentials, caches and encrypted replay records remain local.

```sh
python serve_explorer.py
python -m pacbench.preparation_audit runs/pizza-week-twists-20261007/preparation
python -m pacbench.native_audit runs/pizza-week-twists-20261007
```

Native re-auditing requires private runtime files. A fresh checkout can inspect the saved audits and public evidence without them. The reusable corpus is `runs/pizza-week-twists-20261007/preparation/week.json`; repairing criteria should reuse these messages rather than regenerate them.

Next: repair false event merges directly in the Memcal repository with lifecycle regressions; review criteria scope and the answer judge's handling of contradictions; give the owner a realistic display name; then rerun the same small corpus. Increase noise only after those checks are sound. Acknowledgements alone are not persistence evidence, and source fallback earns no retention credit.
