# PACBench

PACBench measures whether Hermes can answer questions using a local memory provider after authentic ingestion of daily messages. Pizza Day is the small development test. Every provider receives the same frozen raw text through its documented document or message API; Memcal uses its real iMessage reader against a reusable synthetic Apple Messages database. The provider performs extraction. Hermes starts afterward and may call only that provider's shipped tools.

Use Spark Contributor through `/workspace/muse-gateway`. All authoring, judging, agent and provider inference share a $10 task cap and $5 per UTC day cap. Unknown charges remain reserved. Never route around the gateway.

```sh
python -m pacbench.prepare examples/pizza_day.json --output runs/pizza-prepared
python -m pacbench.native_pilot --prepared runs/pizza-prepared --providers mem0 --output runs/pizza-current
python -m pacbench.native_audit runs/pizza-current
python serve_explorer.py
```

[Pizza Day](PIZZA_DAY.md) describes the latest clean result. The explorer shows source messages, daily ingestion, actual memory tools, expected facts and the judge's per-answer decisions. Credentials, provider homes and encrypted reasoning stay private. Development artifacts are disposable; retain only the current trial and its necessary source evidence.

Questions and expected facts are plain-language lists, with no pizza-specific slots. Multiple questions can share one real evaluation session. One natural follow-up can recover silently omitted implicit details for half credit; wrong answers, admitted lookup failures, missed explicit requests and zero recall get no follow-up. Source/archive fallback fails retention even when the answer is correct. [Judging and recovery](docs/followups.md) explains the policy.

[The provider map](docs/document-ingestion-provider-map.md) lists eligible local integrations and practical blockers. [Authoring](docs/authoring-options.md) and [shared questions](docs/flexible-checks.md) describe the flexible schema. Start small, then corrections over two days, two plans across three days, and roughly 100 extra messages; defer 1000 until those stages work.

```sh
python -m pytest -q
node --check explorer/dashboard.js
node --check explorer/timeline.js
python scripts/build_explorer_data.py
```

Offline tests establish harness integrity, not provider accuracy. A passing saved-run audit also does not establish a ranking. See [AGENTS.md](AGENTS.md) for operating rules.
