# PACBench

PACBench measures whether Hermes can answer questions using a local memory provider after authentic ingestion of daily messages. Pizza Day is the small development test. Every provider receives the same frozen raw text through its documented document or message API; Memcal uses its real iMessage and secure IMAP readers against reusable synthetic source fixtures. Separate Contacts records associate the same person's messaging and email accounts. The provider performs extraction. Hermes starts afterward and may call only that provider's shipped tools.

Use Spark Contributor through `/workspace/muse-gateway`. All authoring, judging, agent and provider inference share a hard **$10 per UTC day**, with no lifetime cap. Daily spending resets at midnight UTC. Unknown costs retain historical bounds on request dates; active requests stay reserved across midnight. If the gateway blocks the daily budget, stop until reset. Never route around it.

```sh
python -m pacbench.prepare weeks/pizza_week.json --week-start 2026-10-05 --output runs/pizza-prepared
python -m pacbench.native_pilot --prepared runs/pizza-prepared/week.json --providers memcal --output runs/pizza-current
python -m pacbench.native_audit runs/pizza-current
python serve_explorer.py
```

Inspect current sources, answers, judgments and costs in the explorer; machine-readable evidence lives in `runs/`. The explorer reads `runs/` directly on each refresh and shows source messages, daily ingestion, actual memory tools, expected facts and the judge's per-answer decisions. The current run's public evidence ships with Git, so a fresh checkout displays it with `python serve_explorer.py`. Credentials, provider homes, databases, caches and encrypted replay records stay ignored. Development artifacts are disposable; retain only the current trial and its necessary source evidence.

The [week spec](weeks/pizza_week.json) uses Monday–Sunday sections, reusable chat definitions, human-readable times and English expectations beside assistant turns. Generate and verify it once, then reuse its frozen `week.json` across runs. Named conversations support both shared and fresh Hermes sessions, scripted clarifications and later persistence checks. Independent conversations continue after execution or judging errors. One natural follow-up can recover silently omitted implicit details for half credit; wrong answers, admitted lookup failures, missed explicit requests and zero recall get no follow-up. Source/archive fallback fails retention even when the answer is correct. [Judging and recovery](docs/followups.md) explains the policy.

[Pizza Month](weeks/pizza_month.json) reuses those chats across a `weeks` array, with exactly 30 daily imports and upcoming-plan questions only after ingestion. To prepare it, use `--week-start 2026-09-14`; run the frozen corpus with an explicit message bound such as `--max-messages 600`. Preparation is reusable; provider execution never regenerates the messages.

[The provider map](docs/document-ingestion-provider-map.md) lists eligible local integrations and practical blockers. [Authoring](docs/authoring-options.md) and [shared questions](docs/flexible-checks.md) describe the flexible schema. Start small, then corrections over two days, two plans across three days, and roughly 100 extra messages; defer 1000 until those stages work.

```sh
python -m pytest -q
node --check explorer/dashboard.js
node --check explorer/timeline.js
```

Commit public artifacts under `runs/` with the code. There is no generated dashboard or separate result snapshot. Model-call JSON includes readable prompts, responses and usage; encrypted reasoning is retained separately under ignored `model-calls/private/` for local writer retries. `weeks/` contains the authoring inputs. Inspect staged artifacts for credentials before pushing.

Offline tests establish harness integrity, not provider accuracy. A passing saved-run audit also does not establish a ranking. See [AGENTS.md](AGENTS.md) for operating rules.

Recall exposes only the selected provider's shipped tools. New trials permit
native message/source search: correct answers keep their grade, while source-assisted
turns earn no retention credit and all observed costs are recorded. Memcal event
opening receives normal credit. See [the tool boundary](docs/provider-tool-boundary.md).

Independent provider jobs can share `--question-dir` to reuse reviewed recovery wording safely. Publish source-identical terminal runs with `python -m pacbench.publish_run <run> [<run> ...] --preflight <canary-run> --output runs/<current>`, then `python -m pacbench.cost_review runs/<current>`. Publication excludes private state, preserves colliding model-call filenames under component paths, and keeps failed native canaries separate from month answers. Its audited `preparation/week.json` can be reused directly with `--prepared`, without regenerating or reverifying messages.

For reruns, use `--select provider=<run-path>` to select an arm explicitly. Earlier attempts stay separate with their original evidence and costs; completed arms in an interrupted matrix can be audited with `python -m pacbench.native_audit <run> --providers mem0 hindsight`.
