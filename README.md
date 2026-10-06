# PACBench

PACBench tests whether a personal assistant can recall a plan from a fictional conversation. [Pizza Day](examples/pizza_day.json) is the current small example: the author describes a chat and lists five facts the assistant should remember. The writer adds natural side chatter and chooses the message count.

Native **Holographic and local OSS Mem0** now run through Hermes's bundled
plugins alongside Memcal. The pilot inventories all eight bundled providers,
uses isolated homes with normal additive built-in memory, and records missing
setup explicitly. Nothing substitutes fake retrieval or expected facts.

Use **Spark Contributor** through the separate shared gateway at
`/workspace/muse-gateway`: $5 per UTC day across projects and $10 total for this
task. It tracks authoring, judging, Hermes and Mem0 extraction separately.
Clients use its local credential; upstream credentials stay in the service.
See that repository's README for startup and accounting.

```sh
python -m pacbench.semantic_experiment examples/pizza_day.json --prepare-only --output runs/pizza-prepared-new
python -m pacbench.native_pilot --prepared runs/pizza-prepared-new --providers holographic mem0 memcal --output runs/pizza-native-new
python -m pacbench.native_audit runs/pizza-native-new
```

Actual 25-message trials uncovered a missing-clock grading bug (fixed for every
provider) and inconsistent Mem0 persistence across fresh-session repetitions.
Holographic recalled all five facts in the corrected-clock repetition; Mem0's
first trial recalled the plan after native writes, while its second found no
saved plan. Memcal recalled four of five facts, omitting the street. Total
accounted upper bound before follow-up testing was $0.0341. These are diagnostic observations, not
calibrated rankings.

[Pizza Day plus roughly 100 messages](examples/pizza_day_plus_100.json) adds five
ordinary exchanges bounded to 18–22 messages each. It is an authoring spec,
not generated dialogue. Investigate the small-case failure before expansion;
+1000 remains deferred. See the [audit](docs/audit-2026-10-06.md).

New runs ask **one neutral follow-up per initially missed or wrong fact**.
The first-answer penalty remains; a correct recovery earns half credit.
Archive or original-message fallback fails retention even if it finds the answer.
Full tool arguments/results, call counts, latency and model cost windows are saved.
The live Memcal follow-up recovered the street with one `memcal_open(E1)`:
initial 4/5, recovery 4.5/5, no archive search. See [follow-up policy and traces](docs/followups.md).

## Run Pizza Day

See [PIZZA_DAY.md](PIZZA_DAY.md) for the complete setup, live command, artifact checks, and findings from the latest run.

Bind `MODEL=muse-spark-1.3-contributor` in the runtime. Start the shared gateway with the upstream `META` credential. PACBench resolves a local gateway credential and project URL, and does not use a personal Hermes profile.

```sh
python -m pacbench.semantic_experiment examples/pizza_day.json --prepare-only
```

Preparation generates the chats, checks each authored fact against the rendered messages, and freezes a question and evidence-cited contract. It stops before either memory provider runs. Remove `--prepare-only` to ingest the same frozen messages into Memcal and Hindsight and ask both the same question. Each provider arm records tool access, actual calls, the answer, and separate verdicts for each fact.

The runner groups new messages by fictional day. Each provider ingests that day's messages once and runs its native maintenance before the next day. [The one-window correction](examples/pizza_day_correction.json) and [two-day correction](examples/pizza_day_two_days.json) test whether an old 6pm time stays distinct from the agreed 7pm plan. [Pizza Day week](examples/pizza_day_week.json) uses Monday–Sunday chats with human times. It freezes one evidence-cited claim and temporal state per authored fact for each day, then asks Hermes the following morning. Sunday has no new messages but still gets a checkpoint: earlier facts carry forward as history, while Tuesday's correction keeps the old 6pm time superseded. The examples specify `America/New_York`; each local time gets the offset for its own fictional day. Both providers receive the same frozen timestamp and text.

The five Pizza Day facts cover Devon, Luigi's, 5th, Friday September 11, and 7pm. The date fact does not require the year in an assistant answer. For fact-authored scenes, the contract keeps the author's wording exactly; a model supplies dialogue citations and independently reviews support and ambiguity. An unsupported fact stops preparation. The scoring judge sees the frozen claims, the exact public question including fictional clock context, and the answer, never the private scene description.

The generic runner also accepts `scene` plus `test_intent` prose and derives claims from verified dialogue. Pizza Day uses the clearer authored fact list. [Authoring options](docs/authoring-options.md) explains the choice.

The scene writer uses Meta Responses and can replay saved encrypted reasoning when revising a rejected draft. The verifier and answer judge use single-turn calls. Each model call saves its request, response, model, tokens, and any reported dollar charge in the private run directory. An unavailable charge is recorded as unavailable. Incomplete or malformed judgments leave a run ungraded.

## Inspect a run

```sh
python serve_explorer.py
```

The WebUI shows Pizza Day's authored facts, rendered texts, writer note and prompt, each scene-verifier decision with message citations, the frozen contract, and answer judgments. Provider views show the question, Memcal brief or recall calls, tool arguments and results, answer, and claim-level reasons. Encrypted reasoning is retained in the private run record and redacted from explorer data. Each run uses its own saved bundle and hash. Old run artifacts remain on disk, but the retired historical corpus and its browser are gone.

## Calibrate and check

The [nine Pizza Day answers](calibration/pizza_answers.json) cover a full answer, omissions, wrong details, uncertainty, and a venue mentioned only as history. `runs/semantic-20260923T140000Z/calibration-facts/report.json` contains nine real judge results matching nine **proposed** labels. A person still needs to review those labels before using the scores to compare providers. Calibration input is bound to one contract hash; use the run's saved `calibration-proposed.json` with that run's contract.

The first live seven-day diagnostic is `runs/semantic-pizza-week-live-v15-20260928/`: 116 messages, one quiet day, seven valid morning checkpoints per provider, matched input hashes, and observed memory-tool use in both arms. [PIZZA_DAY.md](PIZZA_DAY.md) records the verdicts and remaining grading questions.

Each new run also writes `run-audit.json`. The audit checks frozen hashes and citations, exactly-once daily input, matched provider packets, fictional clocks, preflight access, and complete claim judgments. Run it again on a saved result with `python3 -m pacbench.run_audit runs/<run-id>`. A passing trace audit establishes experiment integrity; human-reviewed calibration is still required before interpreting answer scores comparatively.

```sh
python -m pytest -q
node --check explorer/dashboard.js
python scripts/build_explorer_data.py
```

MCP seam tests need loopback access. Pizza Day is a diagnostic case; proposed calibration labels need human review before comparative scores. [AGENTS.md](AGENTS.md) records the current evidence and next work.
