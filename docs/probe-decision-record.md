# Probe Decision Record (frozen before spend — 2026-09-22)

Binding for P0-9 realization 2 and P0-10 realization 1. No spend occurs under
this record except the bounded probes below; nothing else is authorized.

## Frozen state

- HEAD: `66678d4` "P0 no-spend milestone" (working tree clean except `?? scripts/`,
  `?? docs/pacbench-status-2026-09-22.md`, both probe-harness/report only).
- Probe scripts (sha256):
  - `scripts/live_probe_p09_evolution.py` `f9d576c0…e028`
  - `scripts/live_probe_p10_temporal.py` `7514bf00…1341c`
- Adapter/config digest (sha256):
  - `provider_adapters/hindsight_adapter.py` `2a8f93f4…0fa4c`
  - `pacbench/source_documents.py` `2b6e31ae…603212`
  - `pacbench/exposure.py` `32b1358c…9133b0`
  - `pacbench/resume.py` `e25fb6bd…59aaeb`
- Environment: `/usr/local/bin/python3`, Python 3.12.8, hindsight-api 0.10.0,
  driver/model muse-spark-1.3-contributor via repo `.env` (600, credential present;
  no values recorded here). No code changes to any of the above during probes.

## Spend ceiling

User-level ceiling **below $20 total** (stricter than the repo's $50 runaway
guard). Bounded maxima: P0-10 = 5 provider ops (1 retain + 4 recalls, ≤ ~5 LLM
calls); P0-9 = 9 provider ops (3 retains + 6 recalls, ≤ ~10 LLM calls). Both run
in scratch tempdir homes, banks `pacbench-probe-p{09,10}-*`, never `~/.hindsight`,
never a normal namespace, never the large life. Prior smoke: ~20 calls, cents.

## P0-9 history and retry rule

- Realization 1: `runs/probe-p09-20260922T032018Z/trace.json`, verdict
  INCONCLUSIVE (cycle 1 retained 0/2, stochastic empty-id drop; submission side
  proven: stable key `text:fam`, append, delta-only). PRESERVED — never deleted,
  never overwritten.
- Rule: **exactly one additional realization** (realization 2), preserved
  alongside realization 1. If realization 2 also retains nothing usable → stop,
  no realization 3. No "retry until green."

## P0-10 history

- Launch proc_3fc94cb10ae0 died by infra reap: empty run dir, no trace, no paid
  ops reached (presumed; no usage telemetry exists). Retained in operational
  history as execution zero. Next invocation = first substantive realization,
  script unchanged.

## Addendum 2026-09-22: fixture thread split (evidence-identical)

`tests/test_temporal_probe.py` fixtures changed threads `probe-tx` →
`probe-tx-old` / `probe-tx-new` as a consequence of the reviewed one-item-per-
packet adapter rule (two same-doc items in one batch is the undocumented shape
that returns empty). Texts, timestamps, single-batch structure, and the ranking
invariant are unchanged; P0-9 `CYCLES` texts/threads untouched. The live scripts
import these fixtures dynamically, so effective P0-10 inputs move from 1 doc to
2 docs with identical evidence exposure — recorded here, not hidden. P0-9/P0-10
re-runs under the new shape still require explicit go.

## Branch dispositions (predeclared, no improvisation)

```text
P0-10
 ├─ PASS → continue to P0-9 realization 2
 ├─ WALL-CLOCK (PASS-WITH-DOCUMENTED-WALL-CLOCK-DEPENDENCY) → simulated-time
 │   compatibility review: does the dependency change SCORED behavior vs Memcal?
 │   If yes, temporal-equivalence gate stays OPEN until neutralized/constrained/
 │   fairly excluded. Does NOT auto-close the gate.
 ├─ FAIL → preserve incompatibility, diagnose, stop paid progression
 └─ INCONCLUSIVE → diagnose infrastructure, stop paid progression

P0-9 realization #2
 ├─ usable retention → assess append/document-association semantics
 └─ empty again → preserve both realizations, stop; redesign only after review
```

## Post-probe order (after dispositions written)

1. Probe disposition per probe (established / not established / attribution:
   PACBench vs Hermes vs adapter vs Hindsight / comparative-validity impact).
2. Zero-spend exposure-authorship adversarial test (sender/thread/role-only
   mutations must fail the equivalence gate) — before any large comparative life.
3. Op-id hardening (derive/assert boundary internally, flag #2).
4. Ablation timestamp parsing (datetimes, not strings, flag #4).
5. Large-life audit readiness declaration ("machinery validated to spend on one
   frozen development life" — not pilot-complete, not comparative-valid).
6. Exactly one frozen development life, both providers, then full end-to-end
   audit before anything else. No render-tail resume; no judge labels first.

## Addendum 2026-09-22 (post-validation): frozen digests superseded

Prior pins f9d576c0 (p09) / 7514bf00 (p10) / 2a8f93f4 (adapter) / 32b1358c
(exposure) / e25fb6bd (resume) are superseded by current sha256s:
p09 bac8d638…, p10 8fb1bfa4…, adapter 71582866…, exposure 81dea23e…,
resume f991e658…. source_documents 2b6e31ae… unchanged (still fresh).
Changes, all reviewed: (a) adapter packetization fix — one joined-transcript
item per packet (dodges same-doc grouping-path empty); (b) op-id hardening —
canonical instant derivation shared resume/adapter/live; (c) probe-harness
checker fixes — retained X/Y packet counting, majority-token `_covered`
(P0-9), month-and-day `_temporal_covers` (P0-10): recall returns extracted
facts, never verbatim source sentences; (d) exposure 8→10 fields
(from_owner+structured). No new spend authorized by this addendum.
