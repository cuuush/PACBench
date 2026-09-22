# Temporal ledger — P0-10 (Hindsight)

Temporal fidelity is NOT globally working. This ledger is the standing
record; do not mark it working without closing the items below, and do
not silently downgrade open findings into "bookkeeping."

Source: `plan.md` P0-10. Gate: C4 item 11 ("temporal-risk ledger is
explicit and the adversarial probe has been run"). Status mirror:
`docs/subgoals.md` Criterion 2.

## 1. Proven

- `written_at` → `event_date` plumbing: the adapter passes authoring
  `written_at` as Hindsight's native `event_date` time anchor.
  Pointers: `provider_adapters/hindsight_adapter.py` lines 26–28
  (design note), 103–109 (`_parse_event_date`), 331–339 (per-item
  `event_date` wiring in `ingest`); test
  `Ingest.test_user_turns_redirected_event_date_parsed` in
  `tests/test_hindsight_adapter.py` (event_date parsed, day == 18).
- Relative-date probe PASS stands (subgoals Criterion 2: Memcal
  `probe-clock` FAIL→PASS plus Hindsight `event_date` probe PASS).
- Probe harness structure is proven green offline:
  `tests/test_temporal_probe.py` (FakeEngine, no network) — two facts,
  one batch, supported `event_date`/`query_timestamp` controls only,
  ranking assertion follows simulated age.

## 2. Likely but untested

- Event-relative ranking under `query_timestamp`: supplying the
  supported fictional `event_date` at ingest and the query-time control
  at recall *should* make ranking follow simulated age rather than
  wall-clock — but this has NOT been demonstrated against the live
  engine. The live-engine execution path in `tests/test_temporal_probe.py`
  (`LiveTemporalProbe`) is written and spend-gated skipped; it runs only
  after explicit spend approval (C4 item 11).

## 3. Known wall-clock dependency

- `mentioned_at` is deliberately NOT set: Hindsight defaults it to
  processing (host wall-clock) time. Pinned by
  `MentionedAtWallClock.test_contents_carry_event_date_not_mentioned_at`
  — contents carry `event_date`, never `mentioned_at` — so any future
  `mentioned_at` change is a deliberate characterization-backed
  decision, not drift.
- Ingest-time recency effects are uncharacterized: two facts with very
  different simulated ages but near-identical real ingest times may
  still rank or interpret by wall-clock (mention/ingest timing) even
  after the supported controls are supplied. Until the live probe runs,
  every recency-weighted Hindsight claim stays invalid.

## 4. Unsupported

- Everything else: no global temporal-fidelity claim; no stale-vs-fresh
  adjudication; no expiration, half-life, or historical-reconstruction
  interpretation grounded in wall-clock-contaminated ranking; no
  `mentioned_at`-as-bookkeeping shortcut (consolidated §4 rejects it).

## Affected families (flagged until characterized)

- Stale-versus-current state.
- Opportunity expiration.
- Temporary-state / half-life behavior.
- Historical reconstruction.
