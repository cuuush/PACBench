# Hindsight conversation-update semantics (P0-3, spec-first)

Spec only. No adapter code changes here; `provider_adapters/hindsight_adapter.py`
and `pacbench/live.py` are untouched. The adapter implements this document in a
later milestone — until then, any behavior below not covered by the current
per-message ingest is explicitly marked, and anything out of scope for v1 says
so instead of silently pretending to work.

## 1. Pinned-version capability note

- Installed package: `hindsight-api 0.10.0`
  (`hindsight_api.__version__`; `importlib.metadata.version("hindsight-api")`).
- The run manifest already pins this: `pacbench/live.py::_provider_recipe`
  (lines 253–258) records `hindsight_api_version` for every `hindsight` run,
  so a future comparative run is reproducible from its manifest.
- Every API claim below was read off the installed source at
  `/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/hindsight_api/`.
  There are **no** public `MemoryEngine` methods named `update`, `append`,
  `replace`, or `upsert` (verified against the installed `dir(MemoryEngine)`).
  Content evolution goes through exactly these entry points:
  - `retain_batch_async` (`engine/memory_engine.py:5283`) — per-item dicts
    (`RetainContentDict`, `engine/retain/types.py:112`) carrying `content`
    (required), `context`, `event_date`, `metadata`, per-item `document_id`,
    and per-item `update_mode`. The batch-level `document_id` parameter is
    **deprecated**; idempotent retry rides on `operation_id`, with
    `get_operation_status` (`memory_engine.py:19710`) and `retry_operation`
    (`memory_engine.py:19949`).
  - `update_document` (`engine/memory_engine.py:9671`) — **tags only**
    (`tags: list[str] | None`); it takes no content argument and cannot edit
    message text. Tag changes propagate to memory units and trigger
    re-consolidation.
  - `delete_document` (`engine/memory_engine.py:9487`) — deletes the document
    and all associated memory units and links.
  - Bank-level `update_bank` (`memory_engine.py:20146`) and `delete_bank`
    (`memory_engine.py:10328`) are bank config/lifecycle, not message
    mutation. `reprocess_document` (`memory_engine.py:12688`) re-extracts
    under the bank default; `import_documents_async` (`memory_engine.py:7082`,
    `on_conflict='skip'`) is archive import, not a conversation-update path.

## 2. Most-native-safe evolving-document mechanism

Per-item `update_mode` (`engine/retain/types.py:132,152`,
`Literal["replace", "append"]`):

- `"replace"` (**default**) deletes old data and reprocesses the document.
- `"append"` concatenates new content onto the existing document and
  reprocesses (`append_document_body`, `engine/retain/orchestrator.py:120`).

The native-safe mechanism for an evolving conversation is therefore:

> one stable conversation `document_id` (P0-2) + `retain_batch_async` with
> per-item `update_mode="append"` for each day's newly exposed messages.

Why this one, with citations:

- **Append is monotonic by engine contract.** `assert_append_extends_stored_body`
  (`engine/retain/orchestrator.py:147`) raises `AppendWouldTruncateDocument`
  (`orchestrator.py:132`) rather than silently overwriting committed content —
  a failed append is recoverable and the caller resubmits, with retain
  idempotent by `operation_id`.
- **Only appends fold** (`plan_retain_fold`, `engine/retain/fold.py:151`):
  cumulative appends coalesce into one execution in submission order; a replace
  ("this body supersedes what is stored") always runs alone, so day-2 deltas
  must never be modeled as replaces.
- **Delta retain is chunk-granular** (`retain_batch` docstring,
  `engine/retain/orchestrator.py:~1300`; `upsert_document_metadata`,
  `engine/retain/fact_storage.py:321`): upserting a document that already has
  chunks re-processes only changed chunks; unchanged chunks keep their facts,
  entities, and links. Append deltas therefore do not pay full re-extraction
  of day-1 history.
- **Do not confuse `strategy` with update mode.** The `strategy` parameter on
  `retain_batch_async` selects LLM/config resolution
  (`_resolve_retain_config`, `memory_engine.py:5745`), not append-vs-replace.

Consequence for the current adapter: today's ingest
(`hindsight_adapter.py:330-348`) sends per-message `document_id = source_id`
with **no** `update_mode` key, i.e. engine-default `"replace"` — which is
honest only because each document holds exactly one message (a same-`source_id`
rerun upserts its own document instead of duplicating). The moment document
identity becomes conversation-level (P0-2), day-2 deltas **must** set
`update_mode="append"`, or day 2 would wipe day 1. That switch is
implementation work, not this spec.

## 3. Day transport semantics (all days, v1)

- **Day 1:** retain the conversation available through that collection cycle
  (full day-1 exposure under the conversation `document_id`).
- **Day 2 / day 3:** deliver **only newly exposed information** through the
  append mechanism above — observations whose `collected_at` falls in the new
  collection cycle. Never re-send day-1 history; never expose future messages
  early (an observation is evidence only once collected; cf. `validate_life`
  "before it is collected (future evidence)").
- **Never one-doc-per-message** for conversational sources: messages in one
  logical conversation share one `document_id` (natural source boundaries per
  P0-1). Within a single day's batch, messages serialize chronologically by
  `written_at` (P0-4); across days, stored order is exposure order (see
  late-arrival case).
- Each message keeps verbatim text, sender, `event_date` (= authoring
  `written_at`, the native time anchor), thread, and per-message metadata.

## 4. The six cases

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
delta-only cost model. The intra-batch chronological rule (P0-4) is unaffected.

### (c) Edited messages — OUT OF SCOPE (v1, explicit)

There is no content-edit-in-place API in the pinned version:
`update_document` is tags-only (§1). The corpus cannot express an edit either:
`Observation` carries no mutation markers, is frozen, and authoring emits
exactly one immutable observation per beat (§5). **V1 behavior: edits do not
exist in the corpus, so the adapter never encounters one; if provider-native
edit support or a corpus edit representation lands later, the specified future
mechanism is `replace` with the full corrected conversation body (or
`delete_document` + re-retain), never a silent partial append.** Until then,
the guard test pins the invariant so an edit-shaped fixture fails loudly
instead of flowing silently.

### (d) Deleted messages — OUT OF SCOPE (v1, explicit)

`delete_document` exists (§1) but deletes whole documents, and the corpus has
no way to author a deletion (no tombstone/marker field, absence of a
`source_id` means "never existed", §5). **V1 behavior: deletions do not exist
in the corpus; the adapter must never call `delete_document` for conversation
evolution in v1.** Future mechanism when the corpus can express deletes:
`delete_document` on the affected conversation document followed by re-retain
of the surviving history, or message-level tombstoning if the provider gains
it — to be specified when the capability exists, not assumed now.

### (e) Source reorder — OUT OF SCOPE (v1 for collected history, explicit)

`Observation` has no sequence field and is frozen; there is no representation
for "already-collected messages now arrive in a different order", and append
monotonicity (`AppendWouldTruncateDocument`, §2) forbids rewriting stored
order to match. **V1 behavior: reorder of already-collected history is
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
rejects as a duplicate, §5.)

## 5. Corpus guard: edits/deletes/reorders cannot silently flow

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
