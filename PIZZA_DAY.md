# Current Pizza Week diagnostic

Latest run: [pizza-week-natural-confounders-all-20261010-r3](runs/pizza-week-natural-confounders-all-20261010-r3/summary.json). **94 messages, 17 scenes, 18 questions**, low noise, Monday 5 October through Sunday 11 October, America/New_York. Source generation is finished and separate from execution: all arms share the same frozen input, questions, criteria and recovery policy. OpenViking was rerun after its mixed reader was removed from Hermes’s schemas; other grants remain unchanged.

| Provider | Observed initial results | Qualification |
| --- | --- | --- |
| Memcal | 17/18 pass | One implicit omission recovered by one ordinary event open; 65.5/66 fact points including half credit. |
| Mem0 | 18/18 pass | Completed native ingestion and all checks. |
| Hindsight | 18/18 pass | Completed native ingestion and all checks; one provider HTTP 502 reservation remains held. |
| OpenViking | 7 pass; 11 ungraded | Rerun with `viking_read` absent from exposed schemas: zero blocked tool attempts. Three provider-side HTTP 400s triggered native shutdown before the remaining checks. |
| Supermemory | 5 pass, 5 fail; 8 ungraded | Stopped after 12 provider-side HTTP 400 failures despite document status “done.” Results are confounded by the unhealthy model route, not a clean retention ranking. |
| Honcho | 15 pass; 3 fail | Observer-corrected native diagnostic. Internal original-message searches receive no retention credit. Friday/Saturday readiness preceded new work enrollment; accelerated assistant capture uses host timestamps. This is not a clean memory ranking. |
| ByteRover | 18 ungraded | Native curation completed but returned a pending review. The connector correctly withheld recall; no automatic approval was injected. |

All seven local connectors were attempted. The overall run is **incomplete**. Saved audits establish the integrity of Memcal, Mem0 and Hindsight. OpenViking's separate delivered-trace audit covers seven checks and three source days, without claiming full-week completion. Honcho's new audit correctly rejects two premature readiness receipts. Preparation passes its integrity audit. Excluded providers remain Holographic (no practical automatic ingestion), RetainDB and Memori (no verified local Hermes backend).

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

- Honcho's configured Hermes observer now joins source sessions **before** messages arrive, using native observation settings verified by the API. Previously, Hermes queried a perspective that had not witnessed those sources. No assistant messages or manually extracted facts were inserted. A scoped, bounded processing wait replaces the premature three-minute timeout. A second race required a pre-submission queue baseline: newly acknowledged work must enroll and complete before readiness, rather than accepting old completed counts. The old full-week receipts retain their audit failures; a separate consecutive-batch native check verifies the fix.
- Honcho's native reasoning journal now supplies sanitized internal tool arguments/results. Direct original-message tools remain hidden from Hermes, but the backend's own `search_messages`/`grep_messages` fallback is visible and gets no retention credit. Background work without a proven query match stays uncredited pending review. Semantic judges remain unchanged: Leo was missing from both pizza-detail answers, and the accepted Alex lunch was missed.
- Spark's native 2500-token Honcho allowance was tested and truncated extraction. The existing 16384 allowance is restored through documented model configuration; it is a maximum, not a target output size. Native Hermes Honcho capture does not supply `created_at`, so simulated-date prefixes do not change captured assistant-turn timestamps. Source-message timestamps are preserved. This limits accelerated interactive-week conclusions; it is not proof of the cause of particular misses.
- Hindsight now uses its documented native `meta` adapter with the gateway URL. The generic OpenAI adapter sent unsupported forced tool choices. No Hindsight product code or source content was patched.
- Source-policy auditing reads actual Hermes `input` arguments as well as `arguments`. Live guards still run before dispatch. The regression covers both formats.
- A rejected English criterion or combined follow-up draft gets one revision using independent reviewer feedback and another independent review before delivery. Criteria stay bound to authored facts. The agent still receives at most one combined follow-up.
- Narrow questions no longer require unrelated details repeated from earlier answers. All final criteria were frozen before the published accuracy arms; the independent verification remains required.
- A failed or missing native receipt no longer aborts auditing later providers. Incomplete arms remain incomplete.
- Future runs stop paid checks in an arm after three definitive provider-side HTTP 400/422 responses in its isolated project, invoking that arm’s native shutdown hook and run-owned process cleanup. A single fallback, pending request or successful request missing usage does not trigger the rule. This cost barrier was added after the observed Supermemory run; it governed the subsequent reader-free OpenViking run, shutting down only that native server after three errors.

`retained-memory-only-v2` exposes an exact subset of shipped tools. Dedicated archives, original sources, conversation/activity readers, built-in memory, session search, filesystem, shell and browser tools are unavailable. OpenViking’s mixed `viking_read` is now omitted entirely from Hermes’s exposed schemas; Memcal’s normal event opener remains available. Unexpected excluded-tool calls still fail before execution. Native retained pages and their attached citations remain unchanged. This condition is a material architectural restriction for document-oriented retrieval systems, not a general provider ranking.

Memcal uses real native Apple Messages and TLS IMAP readers with provider-independent SQLite/RFC 5322 fixtures and separate Contacts identities. Other systems receive the identical raw daily documents or native source-message capture. No gold summaries, extraction hints or Hermes ingestion conversations are supplied. Native nightly processing occurs at 03:00 Eastern; questions cannot see future batches. Named sessions preserve Hermes context; fresh sessions retain only provider memory. Elapsed appointments do not prove attendance.

## Verification and costs

**273 offline harness tests pass**; JavaScript syntax checks pass. Earlier Memcal verification recorded **2,482 unittest cases, 39 skipped**, **400/400 temporal checks** and **92/92 challenges**. Saved answers, judge decisions and tool traces were reviewed. Offline checks and trace audits establish implementation integrity, not statistically calibrated accuracy.

| Completed arm | Hermes tools, including recovery | Hermes model requests | Provider model requests | Settled request cost / held |
| --- | ---: | ---: | ---: | ---: |
| Memcal | 22 | 41 | 20 | $0.04527 / $0 |
| Mem0 | 24 | 45 | 24 | $0.05289 / $0 |
| Hindsight | 20 | 42 | 74 | $0.06863 / $0.11766 |

These request totals exclude shared authoring/judging and do not establish the causal price of each individual tool. After-settlement receipts preserve role-specific tokens, late requests, HTTP statuses and unknown costs. In particular, Supermemory's failed-request reservations remain held.

The gateway now enforces **$10 per UTC day, with no lifetime cap**, as requested. Settled daily spending resets at midnight UTC. Unknown charges remain held, and a monetary refusal stops inference for that UTC day even if another request later settles. Existing history and holds were preserved; no budget bypass or refund was used.

The old $9.1872 accounting total comprised $2.2017 in settled usage-price upper bounds and $6.9856 in uncertain reservations. It was not $9.19 spent that day. Measured ingestion-window charges for this small corpus are about **1.44¢ Memcal, 0.87¢ Mem0, 2.34¢ Hindsight, and 2.63¢ Honcho**. These windows can miss asynchronous background work; the full observer-corrected Honcho driver/provider projects cost **16.66¢**, excluding shared judging. Native extraction, consolidation and recall can make multiple requests per batch. See [cost-review.json](runs/pizza-week-natural-confounders-all-20261010-r3/cost-review.json) for the dated ledger snapshot and scope; prices are conservative usage bounds, not invoices.

## Inspect and next steps

Only this current public run is retained. Source messages, native ingestion receipts, questions, answers, tool arguments/results and per-answer judges live in `runs/`. Private homes, credentials, databases and encrypted replay records remain ignored. Desktop/mobile Chromium and a fresh checkout are checked; the public explorer does not require private runtimes.

On your Mac, pull branch `work`, run `python3 serve_explorer.py`, then open:

`http://localhost:8137/?run=pizza-week-natural-confounders-all-20261010-r3&day=2026-10-11&provider=memcal-repeat-1`

Cloud localhost is separate from your Mac. Reuse [preparation/week.json](runs/pizza-week-natural-confounders-all-20261010-r3/preparation/week.json) without regenerating messages. Full native re-auditing requires private fixtures/homes; saved audit receipts remain portable.

Pizza Month will freeze one low-noise corpus, ingest **one daily batch for each of 30 days**, and ask questions **only after all ingestion**. Repeated Friday plans should differ by week, people, venue and updates while preserving source threads. Align final questions with the host date until simulated assistant capture is supported honestly. No paid month run has started.

Before including every configured arm, establish ByteRover's recommended review workflow and diagnose OpenViking/Supermemory's rejected model routes. Honcho's internal original-message fallback remains a retention failure under this condition. Memcal's normal event opener stays available. Do not relax guards or change facts to make a provider pass.
