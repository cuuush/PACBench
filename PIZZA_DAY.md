# Current Pizza Week diagnostic

Latest run: [pizza-week-memory-only-memcal-20261010](runs/pizza-week-memory-only-memcal-20261010/summary.json). The frozen week has **72 messages, 13 scenes and 12 checks**. Memcal passes **11/12**, earns **44/46 retention points**, and makes **14 Hermes tool calls**. No archive/source-reader attempts or follow-ups occur. Preparation and native integrity audits pass.

| Check | Answer | Retention | Hermes tools |
| --- | --- | ---: | ---: |
| Initial duck-pond plan | Pass | 7/7 | 1 |
| Unnamed group cancellation | Pass | 3/3 | 1 |
| Separate Jen call and Alex coffee | Pass | 2/2 | 2 |
| Ben dinner count | Pass | 1/1 | 0 |
| Ben dinner after email reschedule | Pass | 3/3 | 1 |
| Initial pizza count | Pass | 1/1 | 0 |
| Initial pizza details | Pass | 10/10 | 3 |
| Updated pizza count | Pass | 1/1 | 0 |
| Updated pizza details | Pass | 11/11 | 2 |
| Leo shift cover | Fail | 0/2 | 2 |
| Fresh-session Devon venue | Pass | 2/2 | 1 |
| Fresh-session Ben dinner | Pass | 3/3 | 1 |

## What changed

Memcal product commit [495452d](https://github.com/cuuush/memcal/commit/495452d), pushed to `main`, removes the clock-only inference that elapsed confirmed or tentative appointments happened. Past plans retain their agreed status and say attendance is unconfirmed. Person encounter counts also require occurrence evidence. The nightly pass repairs older changes specifically attributed to `code:past` when no later status evidence supersedes them; explicit observations and attendance confirmations remain intact. Unsupported older rows without attributable history are not rewritten.

The native Tuesday snapshot now leaves Jen's 10 am call and Alex's 11 am coffee `confirmed`, while declining only the pond outing. Hermes says they “still show as planned, with nothing about them being cancelled.” It no longer claims they happened.

Recall uses `retained-memory-only-v1`: only an exact subset of provider-shipped tools is offered. Archive search, source opening, conversation history and message activity are disabled. Dispatch blocks excluded tools before execution, and an attempt fails the check even if the answer is right. Native event/page payloads are unchanged and may include attached source citations. This measures retained memory and its attached evidence; it is not a facts-only storage test. The UI shows the grant and excluded tools.

## Why Leo still fails

Hermes answers: “I don't see anything about Leo's shift — nothing stored on that.” It calls `memcal_search_wiki` and `memcal_open`, with no archive fallback. Neither expected claim appears in the answer, so the check fails without a follow-up.

The real reader ingested the messages. Extraction saved the pizza plan and its updates, but left notes/wiki empty and omitted the shift-cover explanation from the event evidence exposed to Hermes. Leo remains in the event's participant list; that fact was retained, even though this answer does not restate it. The missing cover explanation is a retention/extraction problem, rather than a source-delivery failure.

The pond cancellation is different: one ordinary event open exposes the declined state and attached cancellation evidence. Hermes answers it correctly without archive search. Its earlier fallback was unnecessary.

## Realism and comparison

The same source bundle, prompts and criteria were replayed from fresh stores; no writer or verifier regenerated the messages. Bundle hash: `217e2b265c9df7a56192dc06955f5db6afa8ada24b13177b3868dd2a1c8236e4`.

The week includes a pond plan cancelled through unnamed group messages, separate same-day appointments with those participants, Ben's dinner rescheduled from iMessage through email, and two distinct pizza outings with time/venue changes. Native processing cancels only the pond outing, keeps the separate appointments, moves one Ben dinner to Saturday at 7 pm at Juniper Cafe, and preserves the two pizza identities and corrections. Initial pizza details also include Ben's true Saturday dinner as an extra; expected pizza facts are correct, and the extra lookup counts toward tool effort.

The prior source-enabled run had 12/12 correct answers, 39/46 retention points and 19 tool calls. Its archive-assisted Leo answer received no retention credit. The current condition blocks that route and exposes the missed answer. A single fresh extraction under a different tool condition is not a causal accuracy ranking. [Development review](runs/pizza-week-memory-only-memcal-20261010/development-review.json) records the comparison and diagnosis; superseded run folders are removed.

Apple Messages SQLite fixtures and separate Contacts are provider-independent. Email uses RFC 5322 messages, distinct contact-linked accounts and real TLS IMAP against Memcal's native reader. No source reader is replaced, no facts are seeded, and Hermes does no ingestion. Nightly processing runs at 03:00 America/New_York; questions cannot see future batches. Named conversations preserve Hermes context, while separate sessions test later recall. This remains a small low-noise synthetic diagnostic; the text-only IMAP fixture is not a full consumer email server.

## Validation and costs

All **204 harness tests**, **2478 Memcal unittest cases** (39 skipped), and **400/400 temporal integration checks** pass. Preparation and native trace audits pass. These checks establish implementation/trace integrity, not calibrated provider accuracy.

All inference used Spark Contributor through the capped gateway. Accounted upper bounds are **$5.70 total / $4.78 today**, including retained unknown reservations, below the $10 total and $5 daily caps. This replay added about **$0.035**. Captured provider work: 12 model requests, 35,878 input and 32,042 output tokens, $0.0100. Hermes: 28 requests, 190,641 input and 7,571 output tokens, $0.0206. All 40 provider/Hermes requests settled with known token counts; judging is logged separately. Request and turn windows do not establish the exact causal cost of an individual tool.

## Inspect and reuse

Public sources, criteria, answers, tool results, native processing and judges live directly in `runs/`. Private homes, databases, credentials and encrypted artifacts remain ignored. On your Mac, pull `work`, run `python3 serve_explorer.py`, and open:

`http://localhost:8137/?run=pizza-week-memory-only-memcal-20261010&day=2026-10-09&provider=memcal-repeat-1`

The latest evidence ships with Git and displays without private runtime stores. Cloud localhost is separate from your Mac. Reuse [preparation/week.json](runs/pizza-week-memory-only-memcal-20261010/preparation/week.json) without generating messages again. Native re-auditing requires the private runtime files; saved audit results remain visible in a fresh checkout.

Next: investigate general retention of event-related explanations and change context in Memcal, using this frozen failure. Do not insert expected facts or special-case Leo. Keep the corpus small until that behavior is understood, then add about 100 realistic noise messages and freeze that next corpus once.
