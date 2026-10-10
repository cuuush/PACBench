# Current Pizza Week diagnostic

Latest run: [pizza-week-natural-confounders-all-20261010-r3](runs/pizza-week-natural-confounders-all-20261010-r3/summary.json). **94 messages, 17 scenes, 18 questions**, low noise, Monday 5 October through Sunday 11 October, America/New_York. Source generation is finished and separate from execution: all arms share the same frozen input, questions, criteria, recovery policy and tool boundary.

| Provider | Observed initial results | Qualification |
| --- | --- | --- |
| Memcal | 17/18 pass | One implicit omission recovered by one ordinary event open; 65.5/66 fact points including half credit. |
| Mem0 | 18/18 pass | Completed native ingestion and all checks. |
| Hindsight | 18/18 pass | Completed native ingestion and all checks; one provider HTTP 502 reservation remains held. |
| OpenViking | 10 tool-policy failures, 8 dependent skips | Hermes attempted original imported documents; blocked before execution. Answers were often already correct from search summaries. This is not proof of storage loss. |
| Supermemory | 5 pass, 5 fail; 8 ungraded | Stopped after 12 provider-side HTTP 400 failures despite document status “done.” Results are confounded by the unhealthy model route, not a clean retention ranking. |
| Honcho | 18 ungraded | Monday source messages acknowledged, but native processing queue did not drain within the 180-second deadline. |
| ByteRover | 18 ungraded | Native curation completed but returned a pending review. The connector correctly withheld recall; no automatic approval was injected. |

All seven local connectors were attempted. The overall run is **incomplete**, and its native audit explicitly reports the four incomplete arms. The completed Memcal, Mem0 and Hindsight arms have no native integrity errors. Preparation passes its integrity audit. Excluded providers remain Holographic (no practical automatic ingestion), RetainDB and Memori (no verified local Hermes backend).

## Natural complications

The existing week includes two independent Friday pizza outings, time and venue changes confined to the right outing, an unnamed pond cancellation in a group chat while separate appointments with the same people remain agreed, and Ben's dinner rescheduled from iMessage through email. Questions ask for counts, current details and change history, rather than requiring a preferred assistant clarification policy.

Four newly generated scenes add two storylines:

- Alex suggests Sunday dinner at Juniper; the user declines, then agrees to Sunday lunch at Birch. Ben separately has Saturday dinner at Juniper. Fresh-session questions must distinguish accepted plans from declined proposals and the two people/venues.
- Devon asks the user to collect a spare key Sunday at 11 am from the bartender at Luigi's, the earlier pizza venue; Devon will not be there. On Saturday Devon says the key is already back. Sunday questions must cancel this favor while keeping Alex's lunch.

Thirteen original scenes were reused unchanged; only four new scenes were generated and verified once. Raw source bodies have no accidental sender-label prefixes. Input SHA-256: `157469ee365fa1cbf481292cbfc2b15192615911eedb6b41c9060875b7d72d0c`.

## Memcal diagnosis and fix

Product [9d48226](https://github.com/cuuush/memcal/commit/9d48226), pushed to Memcal `main`, fixes bounded source-preview ordering. Old citations filled the preview before newer retained context, hiding the shift-cover explanation. A paired read-only check of the same database showed the cover line absent before and present afterward within the same 12-line bound. This was a presentation problem, not demonstrated extraction loss. The generic fix prioritizes current field evidence and recent context without pizza-specific rules, gold facts or additional LLM work.

The current accuracy run used `9d48226`. Later product [33878cb](https://github.com/cuuush/memcal/commit/33878cb), also pushed, fixes backwards relative-day labels such as “-1 days later.” That display-only correction has offline regression coverage; the accuracy run was not repeated on it.

The sole current Memcal initial miss is the key-favor cancellation. “No — that's marked as not going” correctly states cancellation but silently omits why. The frozen recovery policy permits “Did Devon already get the spare key back?” The answer is yes, using one native `memcal_open` call, without archive access. Initial failure stays recorded; recovery earns half credit. The follow-up uses two Hermes model requests, 18,046 input / 583 output tokens and $0.0019212; the provider performs zero additional model requests in that window.

## Benchmark issues addressed

- Hindsight now uses its documented native `meta` adapter with the gateway URL. The generic OpenAI adapter sent unsupported forced tool choices. No Hindsight product code or source content was patched.
- Source-policy auditing reads actual Hermes `input` arguments as well as `arguments`. Live guards still run before dispatch. The regression covers both formats.
- A rejected English criterion or combined follow-up draft gets one revision using independent reviewer feedback and another independent review before delivery. Criteria stay bound to authored facts. The agent still receives at most one combined follow-up.
- Narrow questions no longer require unrelated details repeated from earlier answers. All final criteria were frozen before the published accuracy arms; the independent verification remains required.
- A failed or missing native receipt no longer aborts auditing later providers. Incomplete arms remain incomplete.
- Future runs stop paid checks in an arm after three definitive provider-side HTTP 400/422 responses in its isolated project, invoking that arm’s native shutdown hook and run-owned process cleanup. A single fallback, pending request or successful request missing usage does not trigger the rule. This cost barrier was added after the observed Supermemory run; it is not claimed to have governed that run.

`retained-memory-only-v2` exposes an exact subset of shipped tools. Dedicated archives, original sources, conversation/activity readers, built-in memory, session search, filesystem, shell and browser tools are unavailable. OpenViking's mixed reader additionally guards URI arguments; an attempted original-resource read fails the check even when the answer is correct. Native retained pages and their attached citations remain unchanged. This condition is a material architectural restriction for document-oriented retrieval systems, not a general provider ranking.

Memcal uses real native Apple Messages and TLS IMAP readers with provider-independent SQLite/RFC 5322 fixtures and separate Contacts identities. Other systems receive the identical raw daily documents or native source-message capture. No gold summaries, extraction hints or Hermes ingestion conversations are supplied. Native nightly processing occurs at 03:00 Eastern; questions cannot see future batches. Named sessions preserve Hermes context; fresh sessions retain only provider memory. Elapsed appointments do not prove attendance.

## Verification and costs

**245 offline harness tests pass**; JavaScript syntax checks pass. Memcal has **2,482 unittest cases, 39 skipped**, **400/400 temporal checks** and **92/92 challenges**. Saved answers, judge decisions and tool traces were reviewed for the completed arms. Offline checks and trace audits establish implementation integrity, not statistically calibrated accuracy.

| Completed arm | Hermes tools, including recovery | Hermes model requests | Provider model requests | Settled request cost / held |
| --- | ---: | ---: | ---: | ---: |
| Memcal | 22 | 41 | 20 | $0.04527 / $0 |
| Mem0 | 24 | 45 | 24 | $0.05289 / $0 |
| Hindsight | 20 | 42 | 74 | $0.06863 / $0.11766 |

These request totals exclude shared authoring/judging and do not establish the causal price of each individual tool. After-settlement receipts preserve role-specific tokens, late requests, HTTP statuses and unknown costs. In particular, Supermemory's failed-request reservations remain held.

The lifetime gateway ledger is **$8.8202724 accounted**, comprising **$2.159122 settled usage-price upper bounds** and **$6.6611504 uncertain reservations**, with no active reservations. This is lifetime accounting since setup, not $8.82 spent today or an invoice. Every Spark call used the gateway. The fixed **$10 lifetime** ceiling remains unchanged; the explicitly authorized 10 October daily ceiling is $10 within it. No held charges were refunded or cleared.

## Inspect and next steps

Only this current public run is retained. Source messages, native ingestion receipts, questions, answers, tool arguments/results and per-answer judges live in `runs/`. Private homes, credentials, databases and encrypted replay records remain ignored. Desktop/mobile Chromium and a fresh checkout are checked; the public explorer does not require private runtimes.

On your Mac, pull branch `work`, run `python3 serve_explorer.py`, then open:

`http://localhost:8137/?run=pizza-week-natural-confounders-all-20261010-r3&day=2026-10-11&provider=memcal-repeat-1`

Cloud localhost is separate from your Mac. Reuse [preparation/week.json](runs/pizza-week-natural-confounders-all-20261010-r3/preparation/week.json) without regenerating messages. Full native re-auditing requires private fixtures/homes; saved audit receipts remain portable.

Before a larger cross-provider run, resolve Honcho's processing deadline against observed progress, establish ByteRover's recommended review workflow, and diagnose Supermemory's rejected model requests. Decide explicitly whether OpenViking's document retrieval architecture belongs in this retained-memory condition. Do not relax guards or change questions to make it pass. For the stable arms, the next small step is roughly 100 realistic unrelated messages around these same storylines, frozen once; defer 1000 until readiness, tool enforcement and cost observability are reliable.
