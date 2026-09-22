# Hindsight transport

**Current rule: one stable conversation `document_id` per logical
conversation, day-batch deltas appended with `update_mode="append"`.**
Ingest sends exactly ONE joined-transcript item per conversation packet
(`hindsight_adapter.py` ingest). The item carries the packet's stable
conversation `document_id` with `update_mode="append"` (email singletons:
per-message ids with `"replace"`, honest upsert); its content is the packet's
messages in chronological `written_at` order joined as
`sender (written_at): text` lines — verbatim, no summaries. Context names the
thread and its senders; `event_date` is the earliest `written_at` in the
packet; metadata carries `source_ids` / `senders` / `written_at_start` /
`written_at_end` / `logical_document`. One document_id appears on exactly one
item per batch: vendor docs retain a full conversation as a single item, and
the server refuses batches that share one document_id across items. This
supersedes both older shapes — per-message `document_id = source_id` with
engine-default `"replace"`, and per-message items sharing the packet's
conversation id.

## Day transport semantics (all days, v1)

- **Day 1:** retain the conversation available through that collection cycle
  (full day-1 exposure under the conversation `document_id`).
- **Day 2 / day 3:** deliver **only newly exposed information** through the
  append mechanism above — observations whose `collected_at` falls in the new
  collection cycle. Never re-send day-1 history; never expose future messages
  early (an observation is evidence only once collected; cf. `validate_life`
  "before it is collected (future evidence)").
- **Never one-doc-per-message** for conversational sources: messages in one
  logical conversation share one `document_id` (natural source boundaries per
  [source-documents](source-documents.md)) and ride as ONE joined-transcript
  item per packet. Within a packet, message lines serialize chronologically
  by `written_at`; across days, stored order is exposure order (see
  late-arrival case).
- Each packet keeps verbatim text (one `sender (written_at): text` line per
  message), senders, `event_date` (= earliest authoring `written_at`, the
  native time anchor), thread, and packet metadata (`source_ids`,
  `written_at_start/end`).

## The six cases

### (a) New messages — IN SCOPE (v1)

A message with a never-before-exposed `source_id`, collected in the current
cycle: include it in the day's delta batch under the conversation
`document_id` with `update_mode="append"`, positioned chronologically within
that batch. This is the only v1 write path for conversation growth.

### (b) Late-arriving older messages — IN SCOPE (v1, as new exposures)

Expressible today: `BeatPlan.arrival_delay_min / collection_delay_min`
(`pacbench/authoring.py`) decouple `written_at` < `arrived_at` <
`collected_at`, and the corpus currently carries 2 of 1890 observations with
`collected_at != arrived_at`. Specified behavior: a late arrival is a **new
exposure at its collection time**, handled exactly like (a) — appended in its
collection day's batch with its true `written_at` as `event_date`, positioned
chronologically *within that batch*. Cross-batch stored order therefore
follows exposure order, not global chronology; full chronological
re-serialization of stored history (a `replace` with the complete known
conversation) is explicitly NOT v1 — it would resend history and break the
delta-only cost model. The intra-batch chronological rule is unaffected.

### (c) Edited messages — OUT OF SCOPE (v1, explicit)

There is no content-edit-in-place API in the pinned version:
`update_document` is tags-only (see version note below). The corpus cannot
express an edit either: `Observation` carries no mutation markers, is frozen,
and authoring emits exactly one immutable observation per beat. **V1 behavior:
edits do not exist in the corpus, so the adapter never encounters one; if
provider-native edit support or a corpus edit representation lands later, the
specified future mechanism is `replace` with the full corrected conversation
body (or `delete_document` + re-retain), never a silent partial append.**
Until then, the guard test pins the invariant so an edit-shaped fixture fails
loudly instead of flowing silently.

### (d) Deleted messages — OUT OF SCOPE (v1, explicit)

`delete_document` exists but deletes whole documents, and the corpus has no
way to author a deletion (no tombstone/marker field, absence of a
`source_id` means "never existed"). **V1 behavior: deletions do not exist
in the corpus; the adapter must never call `delete_document` for conversation
evolution in v1.** Future mechanism when the corpus can express deletes:
`delete_document` on the affected conversation document followed by re-retain
of the surviving history, or message-level tombstoning if the provider gains
it — to be specified when the capability exists, not assumed now.

### (e) Source reorder — OUT OF SCOPE (v1 for collected history, explicit)

`Observation` has no sequence field and is frozen; there is no representation
for "already-collected messages now arrive in a different order", and append
monotonicity (`AppendWouldTruncateDocument`) forbids rewriting stored order
to match. **V1 behavior: reorder of already-collected history is
inexpressible and unsupported; reorder among not-yet-collected messages is
just collection order and is handled as (b).** A future reorder semantic
(full re-serialization under `replace`, or provider reorder support) is out of
scope until the corpus can express it.

### (f) Backfilled / previously-missing messages — IN SCOPE (v1, as new exposures)

A message with an old `written_at` exposed for the first time (new `source_id`,
`collected_at` in the current cycle) is indistinguishable from (b) at the
provider boundary and uses the same mechanism: append in the collection day's
batch with true `written_at` as `event_date`. It must never rewrite or
re-stamp already-ingested history. (Distinguished from (c): a backfill is a
`source_id` never before exposed; an edit would reuse one — which validation
rejects as a duplicate.)

## Corpus guard: edits/deletes/reorders cannot silently flow

Verified against the current tree (measured: 10 lives, 1890 observations,
0 with `arrived_at != written_at`, 2 with `collected_at != arrived_at`):

1. **Schema carries no mutation markers.** `Observation`
   (`pacbench/observation.py:37-58`) has exactly ten fields — `source_id`,
   `channel`, `written_at`, `arrived_at`, `collected_at`, `sender`, `thread`,
   `text`, `from_owner`, `structured` — and no `document_id`, `update_mode`,
   `operation_id`, `supersedes`, `deleted`, `op`, or version/sequence field.
   Provider document identity and update mode therefore cannot leak into the
   corpus; they are adapter-side derivations.
2. **Frozen single-write authoring.** `Observation` is a frozen dataclass
   (mutation raises); `build_life` (`pacbench/authoring.py:103-147`) emits
   exactly one observation per beat with `source_id = plan.id/local_id`, and a
   duplicate beat `local_id` raises `AuthoringError`.
3. **Validation rejects identity reuse.** `validate_life`
   (`pacbench/validate.py:104-106`) reports `duplicate observation source_id`,
   so an edit-shaped fixture (same `source_id`, new text) or a double-exposure
   fails validation instead of reaching any adapter. `chronology_ok` plus the
   "before it is collected (future evidence)" check further pin the
   exposure clock the delta semantics depend on.
4. **Deletes and reorders are unrepresentable.** Absence of a `source_id`
   means "never existed" (there is no tombstone field to say "existed, now
   removed"); frozen observations have no order field beyond their clocks, so
   collected history cannot be re-sequenced.

Because the mutating cases are *inexpressible* (not merely unhandled), the v1
guard is a test pinning this invariant (`tests/test_update_semantics.py`),
not a `validate.py` rejection of expressible-but-forbidden input — there is no
such input to reject, and `validate.py` is therefore unchanged. If a future
corpus revision adds mutation markers, that revision must add the
corresponding `validate.py` rejection and extend the guard test first.

## History

Plan.md P0-3, merged from `docs/hindsight-update-semantics.md` (spec-first:
no adapter code changes in that milestone; the adapter implements this
document in a later milestone). Pinned-version capability note at authoring
time: installed package `hindsight-api 0.10.0`; run manifests pin
`hindsight_api_version` (`pacbench/live.py::_provider_recipe`); no public
`MemoryEngine` methods named `update`, `append`, `replace`, or `upsert`
exist — content evolution goes through `retain_batch_async` (per-item dicts
with `content`, `context`, `event_date`, `metadata`, per-item `document_id`,
per-item `update_mode`; batch-level `document_id` deprecated; idempotent
retry via `operation_id` with `get_operation_status` / `retry_operation`),
tags-only `update_document`, whole-document `delete_document`, and
bank-level `update_bank` / `delete_bank` (lifecycle, not message mutation).
Re-verify every API claim here against the installed engine source before
relying on it on a newer pinned version.
