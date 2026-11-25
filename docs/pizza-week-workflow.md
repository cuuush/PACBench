# Conversational Pizza Week

The current spec is [pizza-week-conversation-v2](../specs/pizza_week_workflow_v2.json).
One person imports records each evening and returns to the same memory account. Related questions share a chat. The standard front end uses the pinned Hermes CLI and warm gateway. Declare its clock and tool isolation patch, actual model and route, provider version, service clock, permissions and storage separately. Vendor-default front ends would be separate conditions.

## What a week measures

The synthetic source has 142 messages. The historical document condition in the retained Week runs makes one UTF-8 export per observed channel and thread for records that became available to the account since the preceding cutoff. Seven releases at 21:00 America/New_York yield 38 Messages files and 10 email files in that condition. SDKs that accept text receive the same contents as file uploaders. A question before the cutoff uses the previous collection. Arrival sets the first eligible evening. An explicit later access event (`available_at`) can delay it, for example when joining a thread exposes older messages. A prior collector’s polling time has no effect on eligibility. Comparisons use instants, including across DST.

The seven-document projection uses the same release clocks, frozen questions and
criteria. It groups only that day's newly authored Messages and emails in
chronological order using the original source metadata. The projection checks
creation, arrival and access at release time. The frozen layout manifest
records the one verified email export copy excluded by native MemCal parsing.
The byte and source record hashes bind that projection.

The historical file projection copies each body and quotation, uses the recorded speaker, owner direction, timestamp and thread, and includes observed rosters and email headers when available. A roster change establishes when membership was observed. A precise join time needs its own source event. The compiler preserves supplied deidentified reply and address headers. The frozen source has no structured reply headers to replay. All its written, received and collected timestamps coincide. The regression fixtures exercise late arrivals, partial headers and DST separately.

| Continuing-account measure | Turns | Criteria | Interpretation |
| --- | ---: | ---: | --- |
| Initial answer | 41 | 185 | Score the first response to each retained initial question, at its original simulated time. |
| Conversational follow-up | 2 | 3 | Related Friday and Saturday questions follow Thursday's game retrospective in a single Friday morning chat. |
| User correction | 2 | 0 | An authored clarification changes the user's own preference or intended scope. |
| Correction uptake | 2 | 2 | The next question depends on the just-delivered correction. |
| Correction persistence | 2 | 2 | A later question asks about it from a fresh chat on the same account. |

The primary schedule has 49 turns in 31 chats. For example, on Friday the user clarifies that their “porch list” covers Saturday only. The next turn asks which days the list covers. Saturday's fresh chat tests whether the account retained that preference. A second chain distinguishes dinner reminders from household errands. Every arm gets identical user-authored corrections immediately after the same completed predecessor regardless of whether its answer was correct. These turns introduce user preferences; they disclose no answer key or later source message. A failed dispatch stops the sequence and leaves remaining stages unmeasured. The lunch conversation also spans a nightly import, allowing a later answer to use the new documents alongside earlier discussion.

The **isolated control** uses all 42 original questions and 188 criteria, including the fresh-chat lunch duplicate. The base must start empty. Each control copy must prove its own deployment, distinct store or namespace, source prefix, and zero prior chats or conversational writes. The primary and control have separate denominators. The semantic judge is `semantic-judge-context-v4`, inherited from `9f2737cb`. It receives the simulated question time, prior conversational turns, answer and applicable claims after collection. Authorized native source reads count toward correctness. Only verdicts bound to the recorded judge prompt, model and exclusive Bedrock Global route enter the score. Prior turns carry their simulated dates. Terminal access or billing failures stop further judge calls. Incomplete driver receipt coverage remains visible and does not prevent answering.

## Provider routes

These are candidate interfaces documented at the reviewed versions. The [capability manifest](../pacbench/workflow_tracks.py) gives the primary source for each route, the release evidence and required permissions. Deployment checks must record the actual installation. An email-only connector can exercise ingestion; it cannot answer the mixed-channel question set.

| Arm and reviewed version | Nightly document route | First-party connection and qualification |
| --- | --- | --- |
| MemCal 0.8.11 | No verified ordinary mixed-document uploader | Both channels via native macOS Messages and IMAP. Requires a platform-created synthetic Messages store, consent, a synthetic mailbox, real headers and as-of cursors. Each synced channel reports its source IDs and observed scenario cutoff separately from the physical service clock. |
| Mem0 OSS 2.2.1 | One native Memory.add text input per file with inference enabled | Application-managed email ingestion does not establish a first-party connector. |
| Hindsight 1.2.1 | Bank retain_files upload, with operation/readback evidence | No verified historical source connector. |
| OpenViking 0.5.4.1 | Native temporary upload and resource creation, with indexing evidence | The email bot receives chat requests. Historical Inbox/Sent ingestion is unverified. |
| Supermemory local server 0.0.9, SDK 5.0.1 | Native document add using its default processing mode | Hosted Gmail sync is email-only and needs an eligible plan and label grants. The local binary establishes no hosted version or compatibility. |
| Honcho 3.6.1, SDK 2.9.0 | Native session file upload as one attributed daily document, with exact readback, representation completion and synced source embeddings | Official Gmail importer, email-only. Requires a synthetic OAuth grant and observed thread/peer mapping. |
| ReMe 0.4.2.2 | Publish the complete file into the native watched resource directory and witness card/index completion | No verified source connector. The reviewed Hermes integration supplies automatic recall. |
| Cognee 1.6.8, Hermes integration 1.4.3 | Native permanent remember uploader with the per-thread text and dataset scope | Official incremental Gmail sync, email-only. Requires an installed connector, source grant and actual cursor. |
| MemOS local 2.0.21 | Excluded. The reviewed route restored memory backups and repaired embeddings | No verified ordinary mixed-document upload or qualifying connector. |

## Running offline

From the checkout, run the planner to see the exact source digest, cutoff instants, stage denominators and all nine declarations. The conformance command requires a new private output directory.

```sh
python3 -m pacbench.workflow plan --track nightly-document
python3 -m pacbench.workflow conform --track nightly-document --output /tmp/pizza-doc-check
python3 -m pacbench.workflow conform --track nightly-document --seven-daily-documents --output /tmp/pizza-daily-check
python3 -m pacbench.workflow conform --track first-party-connector --output /tmp/pizza-sync-check
python3 -m pacbench.workflow conform --track nightly-document --state isolated-control --output /tmp/pizza-control-check
```

The seven-document conformance option asks the complete 49-turn schedule
through the authored 31 chats on one account. It verifies native request
shapes through recording fixtures for the supported generic upload adapters.
Provider-specific launchers retain their own source, process, and admission
requirements. MemCal's real Messages and Mail acquisition is a distinct
connector condition.

Add `--provider` to exercise one declaration. Recording fakes call the submission adapters and read back the submitted data. Fake connectors expose successive synthetic source prefixes at a cursor. The offline answer is a sentinel and receives no semantic grade. Prompt caching and paid costs were unmeasured.

For a provisioned synthetic deployment, [run_current_week](../pacbench/workflow_runner.py) accepts a declared condition, a bound account, a new evidence directory and an optional judge callable that records transport-backed attempts under the run’s `model-calls/` directory. [NativeAccount and WarmHermes](../pacbench/workflow_native.py) adapt the native uploader or source reader and normal CLI session. The native condition pins the offered Hermes tool catalog for each reviewed provider version. Every answer request must retain the approved model and route. The installer must bind native acceptance, completion and inventory inspection, connector grants/cursors, open-session idle and chat-end write readback, model route and retry lineage. Isolated copies additionally need a native readback of zero prior chats and conversational writes. Gmail routes need working official sync implementations supplied by the deployment. The offline CLI provisions none of these. A missing binding or failed proof leaves an arm unqualified.

## Audit closure and verification boundaries

| Audit finding | Implemented or retained distinction | Evidence needed for a native result |
| --- | --- | --- |
| 1. Input route and comparability | Separate document and connector entry paths. Six share the per-thread export; connectors advance their own upstream cursor. Route, source scope, processor defaults and gateway patch are explicit. | Verify installed native signatures, source account boundaries and actual deployment identity. |
| 2. Sessions and persistent writes | Continuing arms keep one account. A resume uses the physical native session; the runner checks its history, warm process and routed input, and closes it only after its last turn. Isolated controls require source-only readback. | Witness native idle/write completion, background work and isolation for the installed provider. |
| 3. Unsupported interfaces and clipping | Refuse unsupported routes before writing. Trace comparisons use the same field at storage, native output and model input, including nested MemOS text. Missing witnesses yield unknown. | Observe what each native reader returned before attributing clipping. A disabled route receives no score. |
| 4. Timing, identity and leakage | Order by real instants, enforce written ≤ arrived ≤ collected, validate every claim against its allowed prefix, preserve unknown join/header fields, and check source inventory before each answer. Scan actual input for unavailable verbatim source bodies of at least 40 characters. | Witness upstream as-of visibility, actual collection/service time and readiness. A text scan cannot rule out paraphrases or privileged upstream access. |
| 5. Acceptance, answer surface and repetition | Journal submission, native acceptance and readiness separately. Save the model input, personal recall, ordered tool groups/IDs, raw CLI stream, native history, final answer and request lineage. Count repeated requests/results and retries separately. | Need independent store/reader witnesses and attempt-parent evidence to attribute clipping, retries or a service failure. Similar queries alone establish no retry. |
| 6. Version, tooling and consent | Per-route source/version disclosures and explicit front end, grants, model route and patch identity. Unverified hosted versions remain unknown. | Check the installed integration, actual scoped grants, exact served tools and software identity. |
| 7. Conformance | Recording fixtures exercise all nine declarations, both state policies and unsupported outcomes. A local native Hermes test resumes a three-turn chain with a loopback model/storage fixture and inspects its actual request and session DB. | These tests establish harness behavior. Native service readiness, connector fidelity, model accuracy, costs and caching require separate observation. |

See [workflow regression cases](../tests/test_workflow_realism.py) and [delivery adapters](../pacbench/workflow_delivery.py). The private JSONL journal retains exact observed inputs for review. A partial or uncertain run keeps its evidence in its original directory and cannot silently replay an import or replace it with a score.

## MemCal collection preflight

To check the frozen source through the stock 0.8.11 readers, create a private
Maildir runtime and select a fresh output directory. The product interpreter
needs MemCal's declared dependencies. Set `PACBENCH_MEMCAL_SRC` to the clean
0.8.11 checkout if it is outside PACBench's default sibling path.

```sh
python -m venv /tmp/pacbench-imap
/tmp/pacbench-imap/bin/python -m pip install pymap==0.36.7
python -m pacbench.memcal_transport_preflight \
  --bundle datasets/pizza-week/r2/sources/bundle.json \
  --bundle-hash 009056e03e398a0a14b1f3249d5a0480ddf3cb1787c9afd34d2f0fa475b0196b \
  --server-python /tmp/pacbench-imap/bin/python --output /tmp/memcal-transport-check
```

The preflight imports the eligible messages into a private loopback server with
IMAP APPEND, checks each stored header and body (allowing Maildir header folding
and line endings), and compares server UIDs with the stock collector's cursors
after every 21:00 release. The source provides subject, body, sender, direction
and written time. Synthetic sender addresses and stable transport Message-IDs
are recorded separately. Only supplied To, Cc and reply headers are presented
to the native reader. The preflight records the scenario cutoff and actual UTC
collection interval separately.

The Linux Messages/Contacts fixtures exercise the database readers and historical
joins. First-party iMessage qualification requires a platform-created synthetic
Apple store and its authorized native connection. The preflight excludes Dream,
Hermes turns, judging and model calls.

### Incremental MemCal arm

The MemCal first-party track uses stock 0.8.11 at `e67b8d9`, the repository's
pinned Hermes runtime and its isolation patch. The input gate checks the exact
continuing week, including 49 scheduled turns in 31 chats and the 192 criterion
identities across 47 gradable turns.

The Linux coordinator can reach a dedicated synthetic Mac through BlueBubbles.
The read-only site preflight also supports the direct Messages reader on a
dedicated macOS login. Live process admission currently needs Linux process
identity and file-descriptor checks. A provisioned site must supply the
historical Apple roster and source-ID mapping, native as-of releases, and
read-only IMAP access to both Inbox and Sent. The site interface, expected
source observations and independently pinned mapping are documented in
[workflow_memcal_source.py](../pacbench/workflow_memcal_source.py). The
site's Python module supplies `release(cutoff)`, `observe(cutoff)`,
`deployment()`, `bindings_path` and `bindings_sha256`. The repository
factory composes those site observations with the shipping MemCal readers,
stock native processing and the warm Hermes chat.

Prepare the synthetic store under `CAMPAIGN/memcal/memcal-home`. The fresh
campaign contains only that prepared arm. The site JSON uses the fields of
`MemCalSite` in [workflow_memcal.py](../pacbench/workflow_memcal.py).
The billing confirmation contains the actual operator's `confirmed_by`,
timezone-qualified `confirmed_at`, and observed `auto_recharge` value
`off`. Keep the site module, source mapping, evidence and credentials outside
the answering agent's workspace. The default factory preserves the store's
existing native configuration. Use a git-free temporary root and the reviewed
runtime, with the original OpenRouter key available only to the accounting
transport. The commands below use a new arm and store for each stage.

```sh
python -m pacbench.workflow preflight --track first-party-connector --provider memcal --site CANARY_SITE.json
python -m pacbench.workflow_memcal_live CANARY_CAMPAIGN --site CANARY_SITE.json --deployment SITE_MODULE --billing-confirmation CONFIRMATION.json --canary
python -m pacbench.workflow preflight --track first-party-connector --provider memcal --site WEEK_SITE.json
python -m pacbench.workflow_memcal_live WEEK_CAMPAIGN --site WEEK_SITE.json --deployment SITE_MODULE --billing-confirmation CONFIRMATION.json --after-canary CANARY_CAMPAIGN/memcal
```

The four-turn canary must complete with native evidence, exact judge receipts,
and reconciled billing on the selected Luna route. Each subsequent turn joins
a dated conversation, captures the CLI return before checking the native write,
and becomes available to a separate evaluator queue as soon as those checks
pass. Authorized tool lookups count for correctness. Turn metrics identify
actual explicit calls, observed automatic recall and the upper bound at CLI
return. The arm exports `result.json` and `turn-metrics.jsonl`. Billing
reports the sum of unique settled generations and lists unresolved attempts.
Full accuracy requires the complete native week and all criterion identities.

`workflow/workflow.sqlite3` uses SQLite WAL on local disk. One account owner
orders native effects. A role, immutable inputs and an attempt bind each
external operation. Content-addressed files retain raw and verified evidence.
Readable event streams have a new segment per continuation. The queue admits
one grade after each verified turn and drains the remainder in bounded batches.
To recover grades from saved responses, or grade previously unsent verified
turns after the original coordinator exits, use the original arm directory.

```sh
python -m pacbench.workflow_grading WEEK_CAMPAIGN/memcal/workflow --reconcile --limit 32
python -m pacbench.workflow_grading WEEK_CAMPAIGN/memcal/workflow --grade --limit 32
```

A lost callback remains pending until a read-only native witness can prove its
effect. The controller can recheck an intact warm account, including a saved
CLI return, through `run_current_week(..., resume=True)`. The default fresh
launcher cannot attach to an orphaned gateway. Detached grading only needs the
verified checkpoint, its scope, receipts and the approved billing route.

SQLite keeps cutoff and turn queries indexed and progress exports paged. The
current native site contract returns a complete visible upstream prefix to
detect historical edits and membership changes. Long native runs still pay for
that readback. A site with an authenticated change feed or immutable snapshots
would be needed to reduce that cost while retaining the same evidence.

For an offline controller stress test, run
`python -m scripts.check_workflow_scale NEW_OUTPUT --days 365`. It uses
recording boundaries, kills the account process, consumes grade slots with
that process down, and verifies effect counts after restart. Its simulated
grades never produce a benchmark score.

## Terminal Pizza Week v2 results

The [sanitized results snapshot](../results/pizza-week-v2/summary.json) records
seven completed continuing-account arms: MemCal, Mem0 OSS, Hindsight,
OpenViking, ReMe, Cognee and MemOS OSS. Each completed 49 turns across 31 chats,
including 18 same-day continuations; 47 turns carry 192 criteria. Correct-criterion
counts are semantic grades, not retention-adjusted scores or a calibrated
leaderboard. Direct source attempts, successful source reads and generated source
summaries remain distinct; unconfirmed nested provenance is excluded.

The canonical synthetic dataset remains `pizza-week/r2`, with dataset digest
`402a2e0a1ff9e7378ec2a915bc10f77bb60303bd7aef5146a9f29338bd174953`
and original source binding
`a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a`.
The v2 compiled spec digest is
`fff48e923997739490ff4c00893548cbf3775288a337c14666acb23d853636c8`.
The source and evaluator files are unchanged by this integration.

Honcho remains unscored: stock multipart import creates peer-attributed session
messages, while Gmail import is email-only; neither establishes the required
mixed-source native route. Supermemory remains unscored at 6/49 turns after
12 native document readbacks, stopped by the duplicate guard at the second
release. It was not restarted. Offline document-context/retry findings do not
qualify a repaired Supermemory runner, so those experiments are excluded.

The seven completed arms account for $1.309257440 in matched generation receipts.
Qualifications and incomplete attempts account separately for $0.908364540,
including the Supermemory partial. The current campaign total is $2.217621980
across 3,307 matched generations, with 16 unmatched attempts and zero recorded
transport retries. Refused requests lacking matching receipts are excluded from
actual charges. Incomplete Week attempts, qualification canaries, and the prior
campaign's $0.065193795 remain separate in the snapshot; the prior campaign is
not included in the current total. These are observed request charges, not a
complete provider lifecycle invoice. Raw receipts, logs and private timing
records are not published.

### Integrated fixes and verification boundary

The fixes were exported from eight separately verified execution trees based on
`7d2540ebc4ffe42d6f36b511ed636e29bea3f323`. All eight tree hashes were reproduced
before integration. The completed live arms qualify their respective trees;
the combined integration tree has offline regression coverage and has not been
rerun live.

- MemCal: native synthetic Messages/Contacts and isolated TLS IMAP fixtures;
  distinguish intended second looks from transport replay. This is fixture-backed
  native ingestion, not live Apple or upstream mail delivery.
- Mem0 OSS: exact document readiness, native history witnesses and separate live
  canary admission; local Qdrant/Nomic, with optional lexical dependencies absent.
- Hindsight: distinct native consolidation calls and a bounded, at-most-60-second
  Hermes observation clock. Native provider time and elapsed measurements remain
  physical. The final verified repair supersedes the earlier inherited runner.
- OpenViking: raw-byte native upload readbacks and source lookup provenance,
  distinguished from generated summaries.
- ReMe: native watched files, source-linked cards and indexed chunks. Its
  admission and send-time guards coexist with Cognee and MemOS guards.
- Cognee: stock inline promotion, contiguous Q&A capture and exact successful
  permanent-write argument provenance.
- MemOS OSS: stock MemoryOS 2.0.36 REST file ingest and scoped Hermes binding;
  omit unsupported `top_p` only for the identified native memory process on the
  exclusive Luna route. Capture requires either exact active graph evidence or
  accepted IDs plus exact successful native extraction. The completed arm had
  48 exact active captures and one accepted-and-processed capture; processing
  does not assert continued retention.

No third-party provider source was changed. The independent integration review
covered shared admission/dispatch, Hindsight clock composition, source/readiness
claims and snapshot accounting. Regression checks use synthetic inputs and local
services; they do not establish new live readiness or change saved grades.


### Saved-run comparison

The saved-answer overlay compares coverage and scores inside a shared source,
release, question, session, and model condition. A retained single-chat Honcho
Week belongs to its own comparison group. The `honcho_week` launcher has a
separately named `--session-layout authored-chats` condition. It keeps one
warm gateway and binds each fresh chat and continuation to the frozen
schedule, including independent native close observations.

A recorded response journal using `pacbench-regrade-response-journal-v2`
preserves a valid request/receipt pair with malformed model text as an
ungraded judgment. Other responses keep their grades. Each version requires
the full set of response identities, exact frozen prompts and valid recording
evidence. The report includes planned criteria, graded criteria and each
judge-output state. Provider accuracy comparisons require a shared condition
and complete grading coverage.
