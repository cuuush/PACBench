# `collected_at` definition + `ingested_at` split (P0-5)

Frozen: 2026-09-22. This document is the normative definition; code
comments point here, not the reverse.

## Definition

`collected_at` = **the simulated time PACBench's source collector
discovers and exposes the observation** (observation.py: the harness is
scheduled to expose it; before collection no system can be expected to
know the update).

Properties:

- It is **source-discovery/exposure time**, part of the corpus truth.
- It is **immutable through transport**: day-batch grouping, adapter
  submission order, and host wall-clock must never rewrite it.
- Provider batching changes only **grouping** (which observations share
  one `adapter.ingest` call), never per-observation chronology.

## The split: `collected_at` vs `ingested_at`

| Field | Clock | Meaning | May derive ... |
|---|---|---|---|
| `collected_at` (per observation; batch `boundary = max(...)`) | simulated benchmark clock | when the collector exposed the observation | op-ids, `MEMCAL_TODAY` pin, horizon/write-precedence, exposure gating, difficulty ages |
| `ingested_at` (per `import_batches` entry) | host wall-clock, ISO-8601 UTC at the actual `adapter.ingest` submit moment | run artifact: when this run physically submitted the batch | nothing semantic; diagnostics and ordering audits only |

### Why op-id derivation and the `MEMCAL_TODAY` pin stay on simulated `collected_at`

Horizon checks, write precedence, and `created_at` must see the
benchmark clock, never the host clock — otherwise a run executed on
Tuesday would score differently from the same run executed on Friday,
and simulated evidence ages would collapse to "everything arrived just
now". Concretely:

- Hindsight `operation_id = uuid5(NAMESPACE_URL, f"{bank}:{collected_at}")`
  (hindsight_adapter.py): idempotent operational retry identity keyed to
  the **simulated** collection boundary. Keying it to wall-clock
  `ingested_at` would make every re-run / resume a *different*
  operation instead of a safe retry of the same one.
- Memcal `MEMCAL_TODAY = collected_at` pin (memcal_adapter.py, restored
  afterwards): the product's horizon/write-precedence logic must reason
  about fictional event dates, not the CI machine's date.

If a future provider genuinely needs the real submit time (e.g. a
wall-clock temporal-fidelity probe, P0-10), it reads the manifest's
`ingested_at` — the adapter contract itself never receives it.

## Audit table: every `collected_at` site

Classification: **D** = discovery-time (simulated, immutable);
**R** = run artifact (wall-clock or record-of-D).

| # | Site | Class | Notes |
|---|---|---|---|
| 1 | `pacbench/observation.py` — `Observation.collected_at` field + module docstring | D | Source of truth: "when the harness exposes it". |
| 2 | `pacbench/observation.py` — `delivery_order` sort key | D | Exposure order; tiebreak on `source_id`. |
| 3 | `pacbench/observation.py` — `chronology_ok` invariant (`collected < arrived` is bad) | D | Corpus check on simulated clocks. |
| 4 | `pacbench/authoring.py` — beat → `_stamp(... arrival + collection delay)` | D | Corpus authorship of discovery time. |
| 5 | `pacbench/runner.py` — `exposed_observations` (`collected_at <= until`) | D | Exposure gate: expectations follow this clock. |
| 6 | `pacbench/frozen.py` — `OBS_KEYS` carries `collected_at`; freeze sort key | D | Frozen bundle preserves discovery time verbatim. |
| 7 | `pacbench/live.py` — `_batched_by_collection` day key | D (grouping) | Transport grouping only, never event time. |
| 8 | `pacbench/live.py` — `boundary = max(collected_at)` passed as `adapter.ingest` moment (both call sites) | D | Batch's latest discovery instant is the adapter's collection moment. |
| 9 | `pacbench/live.py` — `import_batches[].collected_at = boundary` | R-of-D | Manifest record *of* the discovery boundary. |
| 10 | `pacbench/live.py` — `import_batches[].ingested_at` (**new**, both call sites) | R | Wall-clock submit moment; semantic no-op. |
| 11 | `pacbench/live.py` — `overlay_bundle_observations` sort; `do_turn`/`replay_turns` `at=` scheduling | D | Bundle order + simulated replay clock. |
| 12 | `pacbench/adapters.py` — `ingest(obs, collected_at, ...)` signature | D | Contract: the instant is a simulated boundary. |
| 13 | `provider_adapters/hindsight_adapter.py` — op-id `uuid5(bank:collected_at)` | D | Deliberately simulated (see rationale above). Do not switch to `ingested_at`. |
| 14 | `provider_adapters/hindsight_adapter.py` — per-message `document_id = source_id`; payloads keep own timestamps | D | Chronology preserved inside the batch. |
| 15 | `provider_adapters/hindsight_adapter.py` — maintenance `operation_id=f"pacbench-{at}"` | D | Simulated checkpoint time. |
| 16 | `provider_adapters/memcal_adapter.py` — `MEMCAL_TODAY = collected_at` pin + restore | D | Deliberately simulated (see rationale above). Do not pin wall-clock. |
| 17 | `provider_adapters/memcal_adapter.py` — `within_horizon(written_at)`; `record_source` note `"pacbench ingest @ {collected_at}"` | D / R-of-D | Horizon on simulated clocks; note records which boundary. |
| 18 | `provider_adapters/lexical_adapter.py` — `ingest` param; per-doc `collected_at` stored verbatim | D | Baseline stores discovery time untouched. |
| 19 | `provider_adapters/lexical_plugin/__init__.py` — retrieval recency tiebreak reads stored `collected_at`; `sync_turn` writes `""` | D | Reads discovery time; user-turn rows carry none (not evidence). |
| 20 | `pacbench/validate.py:112` clock parse; `:117-119` chronology; `:158` future-evidence gate (`collected_at > cp.at`) | D | Structural validity on simulated clocks. |
| 21 | `pacbench/difficulty.py` — `total_exposed`, `evidence_age_hours`, `support_batches` | D | Volume/staleness/depth measured on discovery time. |
| 22 | `pacbench/review_packet.py:136` support-window display | D | Human review shows discovery time. |
| 23 | `pacbench/contradictions.py` — `Candidate.collected_at` | D | Carried observation metadata. |
| 24 | Corpus modules (`corpus/*.py`) — authored `collected_at` values | D | Fixture data, simulated by construction. |
| 25 | `pacbench/live.py` — manifest `started_at`, namespace timestamp | R | Pre-existing wall-clock run artifacts; unchanged. |

## Acceptance (P0-5)

- [x] `collected_at` never rewritten by batching (payloads ride along untouched).
- [x] `ingested_at` recorded per `adapter.ingest` call in both `live.py` sites.
- [x] Op-id seed + `MEMCAL_TODAY` pin documented as intentionally simulated.
- [x] Regression test `tests/test_ingested_at.py`: same-day distinct-`collected_at`
      pair shares one transport batch, keeps original times/order at the
      adapter, and the op-id still derives from the `collected_at` boundary.
