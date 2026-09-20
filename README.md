# PACBench 0.1

PACBench is a benchmark for memory systems used by persistent personal assistants.
It asks: **can a memory-backed assistant maintain an accurate, actionable,
provenance-aware model of a person's current life as information arrives,
changes, conflicts, expires, and becomes relevant across many channels?**

Systems earn credit for correct assistant behavior, however they store or
retrieve information internally.

## Status

v0.1 is a standalone benchmark **package**: corpus + validation + freeze /
versioning + deterministic grading + a runner skeleton with explicit
stubs and seams for the Hermes isolated runtime and provider adapters.

What is **not** in 0.1 (listed, not papered over):

- Hermes isolated runtime (`pacbench/hermes.py` — policy + seam only).
- Provider adapters (`pacbench/adapters.py` — contract only; no concrete adapter).
- Live runs, comparative provider results, and any claim about which system wins.
- The calibrated semantic judge for paraphrased TEXT slots (the `judge=` seam in
  `pacbench/grade.py`; without it, unmatched paraphrases grade as MISSED,
  never as invented passes).
- Model-based fact-realization validation and the 30-life comparative corpus.

## Install

Requires Python 3.11+, no third-party dependencies.

```sh
pip install -e .
```

## Run

```sh
python3 -m unittest            # full suite (moved + new 0.1 tests)
python3 -m pacbench.validate   # deterministic structural validation
python3 -m pacbench.freeze     # corpus version, counts, sha256
```

## Corpus

Three synthetic development lives, each covering 7 consecutive simulated days
(Mon 2026-09-07 – Sun 2026-09-13) plus older history, over email, text /
iMessage, WhatsApp, GroupMe, Hermes user turns, and Slack where the life
needs it:

| Life | Exercises | Matched control |
| --- | --- | --- |
| `dentist-depth` (A) | update depth (4 moves + cancel + rebook), history, durable vs transient facts, task | stable eye exam |
| `poker-collision` (B) | entity collision (John's vs Eric's game), source authority (hearsay vs organizer), stale repeats | single-occurrence team lunch |
| `coat-weekend` (C) | proactive reminder, opportunity vs commitment, abstention on unresolved plans | silent visit (no reminder should fire) |

Two smaller incident families from the starting material are kept as
regression coverage: `poker-move` / `poker-unchanged` (write-time recency) and
`weekend-plans` / `dentist-friday` (implicit context).

Every life keeps three layers distinct: **world truth** (`Life.truth`), **evidence**
(`Life.observations` — the only thing adapters ever see), and **expected assistant
behavior** (`Checkpoints` with hidden atomic rubrics). The support manifest
(`pacbench/manifest.py`) links facts to intended evidence at authoring time and
is never scored-run visible.

## Grading

Atomic rubrics, deterministic first: each checkpoint is N small slot judgments
(CORRECT / MISSED / MISREPRESENTED) over normalized dates, times, statuses,
entities, and text. `pacbench/report.py` aggregates task success, precision /
recall / F1, current vs historical accuracy, complete-plan accuracy, abstention
accuracy, opportunity recall, forbidden-suggestion counts, and per-difficulty-tag
breakdowns.

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
