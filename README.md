# PACBench

PACBench is a benchmark for memory systems used by persistent personal assistants.
It asks: **can a memory-backed assistant maintain an accurate, actionable,
provenance-aware model of a person's current life as information arrives,
changes, conflicts, expires, and becomes relevant across many channels?**

**One-line status.** The benchmark runs end to end and scores: frozen evidence
in, sequential life episode, deterministic grading out. Current work is corpus
realism (scale, difficulty, grading calibration). **No comparative claim is
valid** — `comparative_claim_valid` stays `False` until the held-out
comparative corpus with declared splits, repetitions, frozen configs,
calibrated grading, and a real external provider exists.

Systems earn credit for correct assistant behavior, however they store or
retrieve information internally. Nobody earns credit for the harness helping
them: every failure is preserved, attributed, and traceable to source evidence.

## Status

The primary path is a **sequential life episode** (`run-life`): one persistent
provider state per life × provider × repetition, frozen observation bundles in,
delta ingest in time order, isolated Hermes turns, deterministic grading out.
Corpus 0.3.0 (1,890 observations, 44 checkpoints across 6 development lives);
suite 430 passed, 2 skipped; `validate` clean.

Providers: **Memcal** (native lifecycle: archive ingest, nightly dream,
prefetch brief, 21 Hermes tools), **Hindsight** (native lifecycle: batched
async retains with deterministic UUID5 operation IDs, MCP recall/reflect
server, no semantic retry of empty retains), and **lexical** (deliberately
dumb keyword baseline — diagnostic, never an external provider).

What is **not** claimed (listed, not papered over):

- Any claim about which system wins (see above).
- The calibrated semantic judge for paraphrased TEXT slots (the `judge=` seam
  in `pacbench/grade.py`; without it, unmatched paraphrases grade as MISSED,
  never as invented passes). A 200-item calibration pool is drafted and
  awaiting two independent human labelers.
- Model-based fact-realization validation and the 30-life comparative corpus.
- Driver auth: scored runs use the PACBench-owned `.env` (`MODEL` + `META`)
  only — never live-profile credentials. See `.env.example`.

## Install

Requires Python 3.11+, no third-party Python dependencies for the harness
itself. Provider backends have their own homes:

- Memcal: a checkout at `~/code/memcal` (native plugin + dream backend).
- Hindsight: the `hindsight_api` package importable under `/usr/local/bin/python3`
  (3.12) — the Homebrew 3.14 `python3` on PATH cannot see it.

```sh
pip install -e .
```

## Offline checks (free, no model, no network)

```sh
python3 -m unittest            # full suite
python3 -m pacbench.validate   # deterministic structural validation
python3 -m pacbench.freeze     # corpus version, counts, sha256
python3 -m pacbench list-lives # lives + checkpoints (offline, no model)
```

## Smoke test (end to end, ~$0)

The smoke runs an extremely minimal unregistered diagnostic life through the
exact production path — frozen bundle → sequential episode → graded artifacts —
on both real providers. It proves the pipeline, not the providers: low scores
with clean attribution are a working benchmark, not a failure.

**The life.** `pizza-day` (Thursday 2026-09-10, unregistered plumbing material,
never corpus evidence): Devon asks Leo to grab pizza Friday; 4 observations,
2 checkpoints (what / when+who), zero replay turns. Deliberately tiny so a full
two-provider loop costs a handful of driver calls.

**Prerequisites.** PACBench-owned `.env` (`MODEL` + `META`), the isolated Hermes
clone in `vendor/`, the memcal checkout, and `hindsight_api` (see Install).
**Use `/usr/local/bin/python3` for every live command** — background shells
default to a 3.14 `python3` that cannot import `hindsight_api`. Never relaunch
spend-bearing commands without an explicit go after a halt.

**1. Freeze the bundle** (offline, free — the `runs/` dir is gitignored
scratch, so each machine builds its own):

```sh
/usr/local/bin/python3 -c "
from pacbench.corpus.life_h_pizzaday import lives
from pacbench.frozen import freeze_observations, write_bundle
h = write_bundle(freeze_observations(lives()), 'runs/tiny-pizza-bundle.json')
print('BUNDLE_HASH=' + h)"
```

**2. Run Memcal** (2 paid driver calls; ingest + maintenance are local):

```sh
/usr/local/bin/python3 -m pacbench run-life --life pizza-day --provider memcal \
  --bundle runs/tiny-pizza-bundle.json --bundle-hash <hash from step 1> --runs runs/
```

**3. Run Hindsight** (same bundle, same frozen evidence; ~4 provider retains
plus 2 driver calls):

```sh
/usr/local/bin/python3 -m pacbench run-life --life pizza-day --provider hindsight \
  --bundle runs/tiny-pizza-bundle.json --bundle-hash <hash from step 1> --runs runs/
```

A wrong `--bundle-hash` fails closed (`BUNDLE FAILED`, zero spend) — try it;
tamper-evidence is part of the test.

**Other commands.** `smoke` (one bare turn, wiring proof), `probe-clock`
(adversarial fictional-clock probe), `run-checkpoint` (legacy single-checkpoint
diagnostic with a fresh namespace per checkpoint — drift-prone, not the primary
path), `run-pilot` (full development pilot — scored use requires one
`--bundle LIFE=PATH` + `--bundle-hash LIFE=HASH` per development life;
missing/unpinned lives fail closed with `BUNDLE FAILED` before any spend).

**Spend gates.** Two entry points, both fail-closed: (1) the Hermes driver
turns (fail without the PACBench `.env` credential); (2) provider scheduled
work — memcal nightly dream / Hindsight graph maintenance — which spends on
the provider's own backend. Multi-day lives reach (2) and refuse to start
unless `PACBENCH_ALLOW_MAINTENANCE_SPEND=1` is set after an explicit
dream-backend + budget decision (H4). Single-day diagnostics (pizza-/toy-day)
never reach maintenance and run approval-free. Every run manifest records
`maintenance_spend_approved`, `provider_recipe` (hindsight_api version,
internal-LLM model, memcal commit, dream model), `tool_allowlist`, and the
versioned `grader` string.

## One development life

Same production path as the smoke, but with real corpus evidence. Freeze
offline (free), then run — e.g. `coat-weekend` on Memcal:

```sh
/usr/local/bin/python3 -c "
from pacbench.corpus import get_life
from pacbench.frozen import freeze_observations, write_bundle
h = write_bundle(freeze_observations([get_life('coat-weekend')]), 'runs/coat-bundle.json')
print('BUNDLE_HASH=' + h)"
/usr/local/bin/python3 -m pacbench run-life --life coat-weekend --provider memcal \
  --bundle runs/coat-bundle.json --bundle-hash <hash> --runs runs/
```

Multi-day lives reach provider maintenance: they refuse to start unless
`PACBENCH_ALLOW_MAINTENANCE_SPEND=1` is set after an explicit dream-backend +
budget decision (see Spend gates above). For all six development lives at once,
use `run-pilot` with one `--bundle` + `--bundle-hash` pair per life
(see `--help`); long jobs are poll-only — launch, then monitor read-only
(see `docs/pilot-ops.md`).

## Reviewing a run

Each run writes `runs/life-<UTC stamp>/`:

| Artifact | What to check |
| --- | --- |
| `validity.json` | `state` must be `valid`. Anything else names its reasons — read them before the grades. |
| `manifest.json` | `evidence_source` must be `frozen-bundle` with the pinned `bundle_hash`; `import_batches` shows per-batch ingest usage (archived/spooled/dropped counts). |
| `observations.json` | `ingested_ids` — every frozen observation accounted for. |
| `turns.json` | Per-turn prompt, answer, `tool_calls`/`tool_results`, token deltas. The autopsy lives here. |
| `grades.json` | `summary` (precision/recall/F1) plus per-checkpoint slot detail. |
| `memcal-home/` | Scratch store: `memcal.db` + `brief.md` (see below). |
| `hermes-home/` | Isolated Hermes home (config, plugin, sessions, logs). |

**The 60-second verdict:**

```sh
D=runs/life-<stamp>
python3 -c "
import json
print('validity:', json.load(open('$D/validity.json'))['state'])
print('evidence:', json.load(open('$D/manifest.json'))['evidence_source'])
print('summary:', json.dumps(json.load(open('$D/grades.json'))['summary']))"
```

**Attributing a miss** (do this before calling anything a provider bug):

1. `observations.json` → was the fact ingested? (`ingested_ids`)
2. Provider store → did it stick? Memcal: query `archive`/`spool` in
   `memcal-home/memcal.db`; Hindsight: `import_batches` usage notes count
   `empty-id returns` (retains the product stored nothing for — recorded,
   never retried).
3. `turns.json` → did Hermes even try? Empty `tool_calls` with an available
   allowlist is a Hermes answer failure, not a memory failure.

**Clock check.** The brief must render the *fictional* week. Reproduce what the
turn saw, offline and free:

```sh
MEMCAL_TODAY="2026-09-10T19:00:00" python3 -c "
import sys; sys.path.insert(0, '/Users/chris/code/memcal')
from memcal import brief, config, db
cfg = config.load('$D/memcal-home')
print(brief.render(db.open_db(cfg.db_path), cfg).strip()[:400])"
```

If it says "today is Thursday 10 September 2026", the `MEMCAL_TODAY` pin held.
A same-evening brief is *correctly* empty of text-derived facts (dream runs
nightly) — the question is whether Hermes then searched, not whether the brief
knew.

**What "done" means.** Both providers reach `valid` with graded artifacts and
every miss attributed to memory vs. Hermes vs. harness. Scores are evidence,
not a gate. A full two-provider smoke is ~10 paid driver calls on the META key
plus local-only provider work. Deliberately out of scope until explicitly
approved: the race-week render (hundreds of calls, currently halted) and a
live memcal dream pass (bills to the codex subscription, not META); a dry-run
dream on a scratch DB copy verifies the distillation queue for free.

## Corpus

Six synthetic development lives, each covering 7 consecutive simulated days
plus older history, over email, text /
iMessage, WhatsApp, GroupMe, Hermes user turns, and Slack where the life
needs it (corpus 0.3.0: 1890 observations, 44 checkpoints):

| Life | Exercises | Matched control |
| --- | --- | --- |
| `dentist-depth` (A) | update depth (4 moves + cancel + rebook), history, durable vs transient facts, task | stable eye exam |
| `poker-collision` (B) | entity collision (John's vs Eric's game), source authority (hearsay vs organizer), stale repeats | single-occurrence team lunch |
| `coat-weekend` (C) | proactive reminder, opportunity vs commitment, abstention on unresolved plans | silent visit (no reminder should fire) |
| `reunion-thread` (D) | thread-size difficulty (key facts buried in a ~150-message group chat), hearsay vs organizer, stale repeats | work picnic in a small thread |
| `revoke-week` (E) | cancellation-reversal (exactly one active booking), explicit forget (old code history, not current), completed-task silence | history probes mirror the current-state probes |
| `race-week` (F) | long-horizon noisy recall: 9-update training saga, venue chain, superseded times, durable allergy, time-bounded deadlines, very-short-lived states, declined/expired/wrong-person traps, denied rumor; 1,592 obs at 2.6% support | — (scale life, no control) |

Two smaller incident families from the starting material are kept as
regression coverage: `poker-move` / `poker-unchanged` (write-time recency) and
`weekend-plans` / `dentist-friday` (implicit context).

Declared splits (§39): the six lives above are `development` (full-system
debugging); the four small families are `calibration` (grader and pipeline
checks); `held-out` is empty until the comparative corpus exists — which is
why no comparative claim is valid yet. Moving a life between splits is
deliberate (pinned by `SplitDeclaration` in `tests/test_v01.py`).

Diagnostic lives (`toy-day`, `pizza-day`) are unregistered by design: they
exercise plumbing end to end but can never enter corpus evidence, splits, or
comparative claims.

Every life keeps three layers distinct: **world truth** (`Life.truth`), **evidence**
(`Life.observations` — the only thing adapters ever see), and **expected assistant
behavior** (`Checkpoints` with hidden atomic rubrics). The support manifest
(`pacbench/manifest.py`) links facts to intended evidence at authoring time and
is never scored-run visible.

### Browsing the corpus

`docs/corpus-explorer.html` is a generated, gitignored single page (no server,
no CDN — open it straight from `file://`) for reading every frozen observation
the way the providers see it: pick a life, step through it day by day, filter
by channel/thread, or search text and senders. Chat channels render as
bubbles (owner right-aligned); email renders as an inbox list; every message
carries its provenance (`source_id`, `written_at`, `collected_at`) in a
details toggle, and the header pins the bundle hash the view was built from.

Regenerate after any bundle change (build verifies each bundle hash
fail-closed before embedding):

    /usr/local/bin/python3 scripts/build_corpus_explorer.py

## Grading

Atomic rubrics, deterministic first: each checkpoint is N small slot judgments
(CORRECT / MISSED / MISREPRESENTED) over normalized dates, times, statuses,
entities, and text. `pacbench/report.py` aggregates task success, precision /
recall / F1, current vs historical accuracy, complete-plan accuracy, abstention
accuracy, opportunity recall, forbidden-suggestion counts, per-difficulty-tag
breakdowns, and a §36 error breakout (every required slot with forbidden
values declares its error class — stale, wrong-person, wrong-occurrence,
invented, expired, duplicate — in `rubric.ERROR_CLASSES`, enforced by
validation).

Two phrasing notes the corpus is written against: history-aware answers keep old
values in history-marked clauses ("it used to be Friday…", "originally…"), and
genuinely uncertain answers hedge explicitly ("you haven't confirmed…", "not
sure…") rather than guessing a value.

## Generation

Life authoring is specified by PACBench (`pacbench/authoring.py`); Spark only
writes surface language. Each beat carries a plain draft plus `must_include`
tokens, and `pacbench/render.py` rejects any rewrite that drops one — the model
does the surface work while the plan keeps the answer key. With no generator,
drafts ship verbatim, so the corpus builds offline and reproducibly.

To render through Spark, put the model and credential in `.env` (never
committed):

```sh
MODEL=muse-spark-1.3-contributor
META=<credential>
```

then:

```sh
python3 -m pacbench.generation --env .env                 # check config (secret redacted)
python3 -m pacbench.generation --env .env --render-sample # one live render (network)
```

Generation failures are reported, never silently substituted with another model.
Render-once/run-many: rendered output freezes into bundles that scored runs
consume verbatim — generation is never called during a scored run.

## Comparative pilots (snapshots, not findings)

Two systems have received the same observations under the same conditions —
same corpus hash, same driver, same collection times, same grading. But the
second system is a **diagnostic baseline, not an external provider**: per the
contract co-author, §45.5 is unmet and stays unmet until a real independent
memory system runs. Full gates: `docs/audit-log.md` §3.

The 0.1.0-era pilot snapshots (memcal F1 0.727 vs lexical 0.750 on 17
checkpoints) are archived in `runs/`; read them exactly as labeled — on 55
observations with distinctive keywords, raw term overlap suffices, which is why
small lives cannot discriminate memory systems. Discriminating power arrives
with corpus scale. `comparative_claim_valid` stays `False`; no system wins
anything until the 30-life comparative corpus exists.

## Docs

- `docs/audit-log.md` — the running audit log: every probe and audit
  realization, what was established vs not, attribution, gate states, spend
  figures, and comparative-validity impact. Realizations are never deleted.
  Supersedes the former `probe-dispositions.md`, `probe-decision-record.md`,
  `subgoals.md`, `comparative-gates.md`, and
  `contract-clarifications-2026-09-21.md`. Start here for current evidence.
- `docs/design/` — current design rules: `hindsight-transport.md` (one
  conversation packet transport), `source-documents.md` (provider-neutral
  document identity), `time-and-collected-at.md` (`collected_at` semantics),
  `memcal-spine.md` (Memcal source-spine audit), `resume-and-opids.md`
  (resume states, operation identity), `temporal-probes.md` (temporal probe
  status), `difficulty-and-cost.md` (difficulty, ablation, cost rules).
- `docs/pilot-ops.md` — interpreter pin, credentials, poll-only background
  discipline, render resume, smoke-before-pilot. Read before launching anything
  long-running.
- `docs/benchmark-integrity.md`, `docs/exposure-log.md`,
  `docs/judge-scope.md`, `docs/split-lineage.md` — contract compliance record.
- `docs/audit-2026-09-20.md` — the 0.1.0-era 36-question contract audit
  (historical snapshot; counts predate corpus 0.3.0).
