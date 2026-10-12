# Current natural-message diagnostic

The published run uses **retained-memory-only-v2**. New runs now permit native source tools with a separate retention penalty; the published grades and permissions are unchanged.

Latest run: [pizza-week-natural-messages-all-20261012-r1](runs/pizza-week-natural-messages-all-20261012-r1/summary.json). **80 messages, 19 scenes, 17 questions**, low noise, 5–11 October 2026, America/New_York. The same frozen messages are imported through six chronological daily batches. Questions begin after all imports. This is an accelerated dated-message backfill, not a month-long wall-clock decay experiment. The environment stopped during OpenViking; completed arms were preserved and the last three restarted with identical source/contracts later on the same UTC day. Their questions keep the frozen Oct 11 simulated date, while native internals may use Oct 12 host time.

| Provider | Initial model verdicts | Qualification |
| --- | --- | --- |
| memcal | 15 pass, 2 fail, 0 ungraded | Fresh corrective run after a generic recovery-review fix. One lunch-label judgment and its vague follow-up are flagged as harness issues; do not treat them as demonstrated loss of people/place/time. Street/day omissions recovered in one combined follow-up. |
| mem0 | 17 pass, 0 fail, 0 ungraded | Completed native daily ingestion and all initial checks. |
| hindsight | 12 pass, 5 fail, 0 ungraded | Native ingestion completed. Recall did not surface the duck-pond identity/location/cancellation or the key recovery. One failure involved Hermes choosing the upcoming Tuesday despite both appointments being returned. |
| honcho | 14 pass, 2 fail, 1 ungraded | Answer correctness is separate from retention: native original-message searches are logged and earn no memory-only retention credit. Background paths without causal attribution remain uncredited pending review. One turn timed out; one recovery-review error remains explicit. |
| openviking | 2 pass, 2 fail, 13 ungraded | Six daily imports completed. Four initial checks were delivered before repeated native model HTTP 400s stopped further checks. One missed Maya’s rejection reason; one is the flagged lunch-label judgment. Mixed viking_read remains hidden. |
| supermemory | 0 pass, 0 fail, 17 ungraded | Native completion and model health are checked separately. Repeated rejected model requests stop the arm; unexecuted checks remain ungraded. |
| byterover | 0 pass, 0 fail, 17 ungraded | Native curation requires review when it returns pending; no automatic approval or fabricated facts are injected. |

The original model verdicts remain inspectable. These small development grades are **not a calibrated ranking**. Answering from an original-message search does not establish retention. Import/setup failures and missing judgments are not counted as wrong remembered facts.

## What the messages test

Two Friday pizza outings are several hours apart. Maya's lunch changes time and venue across a DM and a group; Leo drops out and rejoins across two days after Tyler covers his shift. An unnamed duck-pond cancellation must not cancel separately arranged calls or coffee. Ben moves one dinner from text to email. Alex declines dinner but accepts lunch. Devon resolves a key-pickup favor himself. Jen arranges a second Tuesday call the following week, which must remain distinct from the application call.

The corpus is deliberately small. Terse replies and uneven pacing replace repetitive workday openings. Source/thread identifiers are opaque: labels such as “called off” never enter provider inputs. Readable authoring labels and citation maps stay in preparation/audit files. Names, membership, sender ownership, timestamps and channel information remain intact. Synthetic timing has not been calibrated to a real population distribution.

## Harness findings

- The judge now sees source channel and thread context. Missing that context made unnamed cancellations unfair to evaluate.
- Expected facts are limited to what each question reasonably requests. A proposed plan is not an agreement, and elapsed appointments do not prove attendance.
- Draft citation labels map exactly to real opaque source IDs; unknown labels fail. Independent evidence and question reviews remain required.
- A recovery-authoring error preserves the valid initial verdict and is counted separately. Object-specific why/what-happened questions are legitimate; a boolean status question must not be treated as an assertion that its answer is true.
- The current lunch-label criterion is overly fine-grained for a useful noon-pizza answer. A follow-up referring only to “the first agreed outing” also lacks stable event scope. Uniform per-provider scope reviews are saved; original grades remain unchanged. Fix scoped claim identities before Pizza Month.
- Provider workers shut down between arms. Repeated native HTTP 400/422 errors stop further paid checks. A gateway budget refusal stops live work for the UTC day.

## Native conditions and costs

Memcal uses its real Apple Messages/Contacts and TLS IMAP readers, then native processing. Other providers use documented document import or real source-message capture. No summaries, seeded memories, fabricated assistant turns or Hermes ingestion turns are supplied. Hermes starts in an empty directory outside Git with built-in memory and non-provider tools disabled. In this published condition, dedicated archive/original-source readers were hidden; Memcal's normal event opener remained available. New trials expose native source readers and keep answer correctness separate from their zero-retention penalty.

Every Spark call uses the shared gateway. The boundary is **$10 per UTC day, no lifetime cap**; unresolved charges remain held. Actual role-specific requests, tokens, failures, late settlements and conservative usage-price bounds are recorded. Per-tool causal token prices remain unknown. See [cost review](runs/pizza-week-natural-messages-all-20261012-r1/cost-review.json).

**285 offline harness tests and 26 gateway tests pass**. Source preparation passes its integrity audit. Saved native audits describe execution integrity, not quality, and retain incomplete-arm errors. Desktop/mobile and clean-checkout checks accompany publication.

## Measured efficiency

Six daily ingestion windows used 6 model calls for Mem0 (2.2 min, 0.9¢), 16 for Memcal (4.7 min, 1.2¢), 20 for Hindsight (4.2 min, 1.9¢), 40 for Honcho (17.5 min, 2.9¢), and 24 for OpenViking (10.2 min, 2.4¢). These are real native pipelines, not a single extraction call chosen by the benchmark. ByteRover used 27 calls across three attempted batches before requiring review. Supermemory used 16 calls across three batches with repeated rejected requests.

For the same 17 initial question windows, settled agent/provider usage bounds were **3.37¢ Memcal, 3.84¢ Mem0, 3.62¢ Hindsight**. Honcho used 19.17¢ for 17 attempts with only 16 completed. Its provider projects made 211 calls outside ingestion, mostly reasoning/capture rather than a simple lookup. Memcal made zero observed provider model requests during recall. This does not prove zero deferred processing cost. Including ingestion and follow-ups, the selected native projects cost 5.15¢ Memcal versus 4.73¢ Mem0; do not claim Memcal wins every cost scope. Shared judging/authoring is excluded from these arm totals.

See [matched turn windows](runs/pizza-week-natural-messages-all-20261012-r1/cost-comparison.json) and [full cost scopes](runs/pizza-week-natural-messages-all-20261012-r1/cost-review.json). Initial windows exclude generation, judging, earlier imports and later/deferred work. Provider work around a turn can be capture or consolidation; it is not causal per-tool lookup pricing. All figures are conservative configured usage-price bounds, not invoices. On October 12 the shared gateway snapshot contains about **85¢ settled and $8.17 held**, accounting for $9.02 against the $10 daily cap. Holds are not claimed actual spending and remain intact.

The gateway now records safe upstream error labels and protocol shape, with no raw prompts, credentials or error text. This fixes future diagnostics; it cannot reconstruct the exact reasons for already-redacted HTTP 400s. Gateway change `7d5d203` is committed locally in the separate gateway repository, which has no remote.

## Inspect and next step

On your Mac, pull `work`, run `python3 serve_explorer.py`, then open:

`http://localhost:8137/?run=pizza-week-natural-messages-all-20261012-r1&day=2026-10-11&provider=memcal-repeat-1`

The explorer reads `runs/` directly and shows source messages, daily ingestion, actual answers, criteria, judge prompts/responses, tool calls and scope reviews. Private stores and credentials are excluded.

Before Pizza Month, fix stable event anchors in criterion/follow-up generation, then reuse one frozen 30-day corpus with **30 daily imports and questions only afterward**. Repeated Friday plans should vary naturally across weeks. Run the next matched comparison with source access permitted. Investigate the observed native retrieval failures and incompatible routes without changing source truth or patching other memory products to make them pass. Memcal product bugs, if found, belong in its own repository.
