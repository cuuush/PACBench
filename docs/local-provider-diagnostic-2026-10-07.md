# Local memory-provider diagnostic — 7 October 2026

The same frozen 25-message Pizza Day input was imported without Hermes ingestion or benchmark-owned fact extraction. Its five expected facts are companion, restaurant, street, date and time. These are small wiring/recall diagnostics, not calibrated provider rankings.

Eligibility is a local memory backend plus practical supported daily document or source-message ingestion. All nine Hermes-documented integrations were reviewed; six qualify for local trials, plus Memcal. Holographic lacks suitable automatic source ingestion; RetainDB and the documented Memori integration are cloud-only and excluded. [Provider map and setup evidence](document-ingestion-provider-map.md).

## Observed results

| Provider | Native import | Initial answer | Retention credit | Hermes recall tools / natural follow-up |
| --- | --- | --- | --- | --- |
| Memcal | Completed, real iMessage reader + normal nightly processing | 5/5 | 5/5 | One `memcal_open`; no follow-up |
| Mem0 | Completed, raw transcript through native OSS extraction | 5/5 | 5/5 | One `mem0_search`; no follow-up |
| Hindsight | Completed retain/consolidation | 4/5 | 4/5 | One initial `hindsight_recall`, one eligible street follow-up using recall; street remained missing |
| OpenViking | Completed resource indexing | 5/5 | 0/5 | `viking_search` then full `viking_read` of original source; no follow-up |
| ByteRover | Completed native file curation | First trial 5/5; repeat 0/5 | First trial unclassified, no credit; repeat zero | First: query + status. Repeat: query, status, query; both queries timed out at the native Hermes 10-second limit; no follow-up on zero recall |
| Supermemory | Completed document extraction | Corrected connector trial 5/5 | 5/5 | One `supermemory-search`; no follow-up |
| Honcho | 25 native source messages acknowledged; queue did not drain within 180 seconds | Not evaluated | Not scored | No Hermes recall against unfinished ingestion |

ByteRover's repeat preserved four native provider task records with two recorded internal tool calls. Those are provider tasks, distinct from the three Hermes tool invocations. Native journals show its own context-tree reads and query synthesis. Their timestamps/task IDs and exact records remain inspectable; some provider activity can outlive a Hermes tool timeout. Unknown retrieval classification gets no retention credit.

Honcho was tested through its real session/peer message API, including a repeat with documented `DERIVER_FLUSH_ENABLED=true`. That repeat still ended with 8 work units in progress and 17 pending, out of 25 total. This establishes incomplete processing within the benchmark bound, not a proven permanent inability to process messages. It is currently blocked rather than forced into a passing recall test.

## Harness issues corrected

- Supermemory: direct import used a hyphenated container tag while Hermes's native resolver converted it to underscores. Extraction succeeded but recall searched a different namespace. The connector now uses the same native resolver. The earlier zero-recall trial stays zero; the fresh corrected trial recalled all five facts.
- ByteRover: the CLI emits progress and final JSON records, rather than one JSON object. Completion parsing now requires the native final successful completion and no pending review. Native task journals also reside in a nested `task-history/data` directory; capture now follows that layout.
- Tool failure logging: Hermes stream events mark `is_error`. Preserve that flag even when a loop warning follows the error JSON, instead of treating the resulting non-JSON text as success.
- Native audit: scan benchmark-owned turn artifacts instead of all JSON files under provider stores. Private provider state is not a benchmark turn and can contain non-JSON/empty data. Re-audit now passes the completed arms; Honcho remains incomplete.
- Shutdown: native CLI grandchildren survived completed/timed-out ByteRover turns. They were stopped, with private cleanup receipts. A general run-directory cleanup hook now follows native shutdown hooks; it does not match unrelated processes by executable name or alter provider tools.

No provider extraction/retrieval prompt was patched. Supermemory and Honcho run their published native local services, with gateway inference and local Ollama embeddings. Memcal product changes belong in its own repository; none were needed for this batch of changes.

## Evidence and checks

Prepared input: `runs/pizza-contributor-prepared-20261006`. Input hash: `62153788eaee6207430d5726c97967a09f1d653530e865435bb3252f0b3e2b21`.

- `runs/pizza-local-memory-check-20261007`: six completed arms, including the original Supermemory namespace failure. Native integrity re-audit passes after the audit scanner repair; original answer grades are unchanged.
- `runs/pizza-local-supermemory-scope-20261007`: fresh corrected Supermemory import/recall; native audit passes.
- `runs/pizza-local-byterover-traces-20261007`: fresh ByteRover repeat with native internal journals, zero recall due to tool timeouts; native audit passes. Passing integrity does not turn failed recall into success.
- `runs/pizza-local-honcho-daily-batch-20261007`: source-message acknowledgements and bounded queue statuses; incomplete-arm audit fails as expected, no recall or fabricated score.

Hermes built-in memory, session search, generic discovery/dispatch, filesystem, shell, browser and all non-provider tools were disabled. Offered schemas and attempted calls were checked against each provider's exact shipped tool names. This is a benchmark tool boundary, not an OS sandbox.

The gateway's cumulative accounted upper bound at this checkpoint was **$3.2225898 including reservations**, covering earlier development and these trials. This is not an invoice. Uncertain requests retain their reservation. The limits remain $10 total and $5 per UTC day. Actual per-arm model calls and available tokens are saved separately for Hermes, memory-provider inference and benchmark overhead; per-tool token attribution remains unknown.

Current regression suite and browser checks are recorded in `AGENTS.md`. Do not run more paid repetitions merely to seek a better answer. Investigate the small native failures first; expand successful paths to the two-day correction and two independent plans across three days before adding roughly 100 extra messages.
