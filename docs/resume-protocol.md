# Resume / idempotency protocol (P0-8)

Crash recovery for provider writes. The rule: **never guess whether a
write landed — classify, then act per state.**

## The four write states

For every provider write the runner preserves enough information to
classify an interrupted operation as exactly one of:

| State | Meaning | Resume action |
|---|---|---|
| `not-submitted` | Runner never sent the batch; nothing could have landed. | `submit` — fresh submit under the deterministic op id. |
| `submitted-unknown` | Provider accepted the batch but the runner died before recording completion. The write may or may not have landed. | `query-status-or-rebuild-from-prefix` — see below. **Never blind re-append.** |
| `completed` | Provider reported success, **including success that semantically extracted nothing** ("empty-success"). | `never-resubmit` — zero second attempts. |
| `failed` | Provider reported failure before completion was recorded. | `submit` — retry under the **same** deterministic op id. |

Pure helpers live in `pacbench/resume.py`:

- `classify_write(submitted, completion_recorded, status) -> state`
- `resume_action(state) -> action` (unknown states raise — fail closed)
- `same_operation_id(batch_key) -> str`, deterministic
  `uuid5(NAMESPACE_URL, "bank:collected_at-boundary")`, matching the
  Hindsight adapter's `ingest` derivation and `live.py`'s
  `import_batches` boundary (max `collected_at` of the batch, full ISO).

## The dangerous point

```
runner sends batch ──▶ provider ACCEPTS ──✕ CRASH ──▶ completion never recorded
                            ▲                            ▲
                     submitted=True            completion_recorded=False
                                              → submitted-unknown
```

This is the only point where duplication is possible, and it is why
`submitted-unknown` exists as a separate state. A simulated crash test
around exactly this point (`tests/test_resume.py`) must stay green:
fake engine accepts, runner record missing → action is
`query-status-or-rebuild-from-prefix`, never a blind append.

## `submitted-unknown`: query, don't guess

1. **Query status where supported.** For Hindsight, use the supported
   operation-status mechanism keyed by the deterministic `operation_id`
   (`same_operation_id(bank, collected_at-boundary)` — the id is stable
   across resume, so the query addresses the same operation the crash
   interrupted).
2. **Rebuild from a known prefix if safe reconciliation is impossible.**
   When the provider cannot answer whether the write landed, start an
   isolated realization from a known-good prefix rather than appending
   onto a possibly-half-written bank.
3. **Never blind re-append.** Re-sending the batch as a fresh write
   risks double ingestion (day-2 deltas applied twice, conversation
   documents multiplied). Retry is allowed **only** as operational
   retry under the *same* idempotent operation identity.

What counts as "supported" is provider-version-dependent; the adapter
— not the runner — owns that detection. If op-status is unavailable,
step 2 (rebuild from prefix) is the safe default, not a fresh submit.

## Empty-success: success is success

A successful operation that semantically extracted nothing (provider
returned success with empty ids) is `completed`, **not** a failure and
**not** an excuse for a second attempt. It receives `never-resubmit` —
zero second semantic attempts merely because PACBench disliked the
result. Re-trying an empty extraction as a "new" write would both
violate idempotency and bias the benchmark toward non-empty-looking
runs.

## Operation identity

- Derived **solely** from `(bank, collected_at-boundary)`:
  `uuid5(NAMESPACE_URL, f"{bank}:{boundary}")`.
- `bank` = provider bank (`f"pacbench-{namespace}"`); `boundary` =
  the batch's max `collected_at` (full ISO, exact-instant benchmark
  clock). Never the transport day key, never wall-clock `ingested_at`,
  never life/checkpoint/story ids (cf. P0-2, P0-5).
- Same batch → same id on every attempt, including resume-after-crash.
  Different batch → different id. No random ids, no per-attempt salts.
- `reset()` stays rerun-idempotent (drops the bank's data first), so a
  same-namespace rerun cannot double-ingest against surviving rows —
  resume-with-same-op-id and rerun-from-clean-reset are the two
  non-duplicating paths; blind re-append is neither.

## Runner obligations (wiring, not in `resume.py`)

Per provider write, the runner must durably record — in
`import_batches` (§41) or equivalent — at minimum:

- the batch key (`bank`, `collected_at` boundary) → op id;
- `submitted` (sent to provider);
- `completion_recorded` + provider `status` (heard back and wrote it down).

On resume the runner replays `classify_write` → `resume_action` per
uncompleted batch and follows the table. Anything else (provider
op-status query, prefix rebuild) is adapter/runner wiring that these
pure helpers deliberately do not perform.
