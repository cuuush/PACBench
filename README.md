# PACBench 0.1

PACBench is a benchmark for memory systems used by persistent personal assistants.
It asks: **can a memory-backed assistant maintain an accurate, actionable,
provenance-aware model of a person's current life as information arrives,
changes, conflicts, expires, and becomes relevant across many channels?**

Systems earn credit for correct assistant behavior, however they store or
retrieve information internally.

## Status

v0.1 is a standalone benchmark **package**: corpus + validation + freeze /
versioning + deterministic grading + a runner CLI driving isolated Hermes turns.

W4 reality: `python -m pacbench` provides `list-lives` (offline: lives and
checkpoints, no model), `smoke` (one bare turn through the isolated clone),
`run-checkpoint` (one checkpoint end to end via Hermes + provider), and
`run-pilot` (the full development pilot, `--provider memcal` or
`--provider lexical`). The isolated Hermes
clone lives in `vendor/` with fairness-neutral shims re-applied from
`patches/`; the harness (`pacbench/harness.py`, `pacbench/live.py`) provisions
scratch homes and never touches the live profile. The Memcal adapter lives
outside the scored package (`provider_adapters/memcal_adapter.py`) behind the
provider contract. A second, deliberately dumb baseline — keyword
archive-search (`provider_adapters/lexical_adapter.py`, no model calls on the
provider side) — receives the same observations under the same conditions
(§45.5). One checkpoint has been proven live end to end; `runs/`
artifacts are debugging output, not results.

What is **not** claimed (listed, not papered over):

- Any claim about which system wins. Per-provider pilot snapshots are
  comparable side by side (see below), but `comparative_claim_valid` stays
  `False` until the 30-life comparative corpus with declared splits exists.
- The calibrated semantic judge for paraphrased TEXT slots (the `judge=` seam in
  `pacbench/grade.py`; without it, unmatched paraphrases grade as MISSED,
  never as invented passes).
- Model-based fact-realization validation and the 30-life comparative corpus.
- Driver auth: scored runs use the PACBench-owned `.env` (`MODEL` + `META`)
  only — never live-profile credentials. See `.env.example`.

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
python3 -m pacbench list-lives # lives + checkpoints (offline, no model)
```

Live commands need the PACBench-owned `.env` (see `.env.example`) plus the
isolated clone in `vendor/` — never live-profile credentials:

```sh
python3 -m pacbench smoke                                          # wiring proof
python3 -m pacbench run-checkpoint --life <id> --checkpoint <id>  # one checkpoint
python3 -m pacbench run-pilot                                     # dev pilot (memcal)
python3 -m pacbench run-pilot --provider lexical                  # dev pilot (baseline)
```

## Corpus

Four synthetic development lives, each covering 7 consecutive simulated days
(Mon 2026-09-07 – Sun 2026-09-13) plus older history, over email, text /
iMessage, WhatsApp, GroupMe, Hermes user turns, and Slack where the life
needs it (corpus 0.2.0: 251 observations, 27 checkpoints):

| Life | Exercises | Matched control |
| --- | --- | --- |
| `dentist-depth` (A) | update depth (4 moves + cancel + rebook), history, durable vs transient facts, task | stable eye exam |
| `poker-collision` (B) | entity collision (John's vs Eric's game), source authority (hearsay vs organizer), stale repeats | single-occurrence team lunch |
| `coat-weekend` (C) | proactive reminder, opportunity vs commitment, abstention on unresolved plans | silent visit (no reminder should fire) |
| `reunion-thread` (D) | thread-size difficulty (key facts buried in a ~150-message group chat), hearsay vs organizer, stale repeats | work picnic in a small thread |

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

## Comparative pilots (snapshots, not findings)

Two providers have received the same observations under the same conditions —
same corpus hash (`afb87c08…`, corpus 0.1.0), same driver
(`muse-spark-1.3-contributor`), same collection times, same grading:

| provider | checkpoints | slots | correct | missed | misrep. | P | R | F1 | turns | tool calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| memcal (`runs/pilot-20260920T193846Z`) | 17 | 24 | 16 | 5 | 3 | 0.800 | 0.667 | 0.727 | 25 | 42 |
| lexical (`runs/pilot-20260920T203711Z`) | 17 | 24 | 18 | 0 | 6 | 0.750 | 0.750 | 0.750 | 25 | 53 |

Read this exactly as labeled: the dumb baseline **edged** Memcal (F1 0.750 vs
0.727) by retrieving everything (0 missed) while misleading more (6
misrepresented — stale/conflicting keyword hits asserted as current, against
Memcal's 5 missed / 3 misrepresented). On 55 observations with distinctive
keywords, raw term overlap is sufficient — which is precisely why these small
lives cannot discriminate memory systems, and why a Memcal win here would have
been a sanity check rather than a victory. Discriminating power arrives with
corpus scale (200+ obs lives). `comparative_claim_valid` stays `False`; no
system wins anything until the 30-life comparative corpus exists.
