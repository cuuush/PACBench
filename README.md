# PACBench

PACBench measures whether Hermes can answer questions using a local memory provider after authentic ingestion of daily messages. Pizza Day is the small development test. Every provider receives the same frozen raw text through its documented document or message API; Memcal uses its real iMessage and secure IMAP readers against reusable synthetic source fixtures. Separate Contacts records associate the same person's messaging and email accounts. The provider performs extraction. Hermes starts afterward and may call only that provider's shipped tools.

Use Spark Contributor through `/workspace/muse-gateway`. All authoring, judging, agent and provider inference share a hard **$10 per UTC day**, with no lifetime cap. Daily spending resets at midnight UTC. Unknown costs retain historical bounds on request dates; active requests stay reserved across midnight. If the gateway blocks the daily budget, stop until reset. Never route around it.

```sh
python -m pacbench.specs
python -m pacbench.prepare specs/pizza_week.json --output artifacts/corpora/pizza-week
python -m pacbench.native_pilot --prepared artifacts/corpora/pizza-week/week.json --providers memcal --output runs/pizza-current
python -m pacbench.native_audit runs/pizza-current
python serve_explorer.py
```

Inspect current sources, answers, judgments and costs in the explorer; machine-readable evidence lives in `runs/`. The explorer reads `runs/` directly on each refresh and shows source messages, daily ingestion, actual memory tools, expected facts and the judge's per-answer decisions. The run index is small; selected details, full model calls and frozen bundles load on demand. The current run's public evidence ships with Git, so a fresh checkout displays it with `python serve_explorer.py`. Credentials, provider homes, databases, caches and encrypted replay records stay ignored. Development artifacts are disposable; retain only the current trial and its necessary source evidence.

`specs/` contains one hand-authored JSON per scenario, whether it spans a day, a month or several months. [Pizza Day](specs/pizza_day.json), [Pizza Week](specs/pizza_week.json) and [Pizza Month](specs/pizza_month.json) use the same channels, weekday scenes, inline facts, noise and assistant expectations. Each pins its first Monday with `week_start`. `python -m pacbench.specs` lists and structurally validates every file without model calls or generated files; it continues past bad specs and reports duplicate IDs and JSON keys. Semantic source and judging reviews still happen during preparation.

Generate and verify a spec once, then reuse its frozen corpus across providers and reruns. Keep reusable preparation under `artifacts/corpora/` and selected public execution evidence under `runs/`. The existing `week.json` filename is a corpus manifest, regardless of duration. Named conversations support shared and fresh Hermes sessions, scripted clarifications and later persistence checks. Independent conversations continue after execution or judging errors. One natural follow-up can recover silently omitted implicit details for half credit; wrong answers, admitted lookup failures, missed explicit requests and zero recall get no follow-up. Source/archive fallback fails retention even when the answer is correct. [Judging and recovery](docs/followups.md) explains the policy.

[Pizza Month](specs/pizza_month.json) reuses those chats across a `weeks` array, with exactly 30 daily imports and upcoming-plan questions only after ingestion. Its first Monday is pinned to 2026-09-14; run the frozen corpus with an explicit message bound such as `--max-messages 4500`. Preparation is reusable; provider execution never regenerates the messages. Author-file edits do not change an existing frozen corpus or its recorded grades.

[The provider map](docs/document-ingestion-provider-map.md) lists eligible local integrations and practical blockers. [Authoring](docs/authoring-options.md) and [shared questions](docs/flexible-checks.md) describe the flexible schema. Start small, then corrections over two days, two plans across three days, and roughly 100 extra messages; defer 1000 until those stages work.

```sh
python -m pytest -q
node --check explorer/dashboard.js
node --check explorer/timeline.js
```

Commit public artifacts under `runs/` with the code. There is no generated dashboard or separate result snapshot. Model-call JSON includes readable prompts, responses and usage; encrypted reasoning is retained separately under ignored `model-calls/private/` for local writer retries. `specs/` contains the authoring inputs. Inspect staged artifacts for credentials before pushing.

Offline tests establish harness integrity, not provider accuracy. A passing saved-run audit also does not establish a ranking. See [AGENTS.md](AGENTS.md) for operating rules.

Recall exposes only the selected provider's shipped tools. New trials permit
native message/source search: correct answers keep their grade, while source-assisted
turns earn no retention credit and all observed costs are recorded. Memcal event
opening receives normal credit. See [the tool boundary](docs/provider-tool-boundary.md).

Independent provider jobs can share `--question-dir` to reuse reviewed recovery wording safely. Publish source-identical terminal runs with `python -m pacbench.publish_run <run> [<run> ...] --preflight <canary-run> --output runs/<current>`, then `python -m pacbench.cost_review runs/<current>`. Publication excludes private state, preserves colliding model-call filenames under component paths, and keeps failed native canaries separate from month answers. Its audited `preparation/week.json` manifest can be passed directly to `--prepared`; keep the preparation directory together. Files are hashed and reused without regenerating or reverifying messages. Publication removes unreferenced authoring receipts and stores repeated exact prompt text once in a hash-checked shared store; keep `shared-text.json` with the preparation.

For reruns, use `--select provider=<run-path>` to select an arm explicitly. When multiple attempts are explicitly supplied, unselected attempts stay separate and unscored. Use `--discard-replaced` to omit replaced development arms from publication while keeping their original local evidence and all gateway charges. Publish only the selected component runs to keep development evidence small. Removing obsolete artifacts never removes their charges from the gateway ledger. Completed arms in an interrupted matrix can be audited with `python -m pacbench.native_audit <run> --providers mem0 hindsight`.

An interrupted OpenViking import can resume with `python -m pacbench.native_resume <run>`, before any Hermes answers exist. It verifies the unchanged corpus, configuration and successful import prefix, reopens the native store and checks persisted resources. Failed or partial imports are rejected; completed days are not ingested again.
