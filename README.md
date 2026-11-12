# PACBench — Personal Agent Calendar Benchmark

PACBench tests whether a personal agent can keep track of plans as conversations
change. After a memory system ingests days or weeks of synthetic messages and
emails, [Hermes](https://github.com/NousResearch/hermes-agent) answers questions
about upcoming plans using that system's tools.

The questions cover everyday calendar problems: rescheduling across chats,
changed venues, someone dropping out, cancellations, dietary requirements and
old email chains that no longer describe the current plan.

**PACBench is in active development.** The repository is public so others can
inspect the method, explore the evidence and contribute. The scenarios,
integrations and judging are still being tested; the saved runs are development
evidence, not a calibrated leaderboard.

## Explore the evidence

You can inspect the saved runs locally with **Python 3.11 or newer**. No model
credentials, provider services or package installation are needed for the explorer.

```sh
git clone https://github.com/cuuush/PACBench.git
cd PACBench
python3 serve_explorer.py
```

This opens the explorer at [localhost:8137](http://127.0.0.1:8137/index.html).
It reads the evidence in `runs/` directly and shows source conversations, nightly
ingestion, agent tool calls, answers, judging criteria and observed costs.
To choose another port, use `python3 serve_explorer.py 8138`.

The saved [Pizza Month run](runs/pizza-month-local-20261105) contains 2,868
messages, 30 nightly imports and 15 final questions. It is **incomplete**, and its
[saved native audit](runs/pizza-month-local-20261105/native-audit.json) reports
failure. Memcal, Mem0 and Hindsight completed ingestion but have some ungraded
answers. Supermemory and Honcho stopped during ingestion and did not reach
recall. These outcomes must remain distinct from wrong answers.

Public receipts support inspection of what happened. They do not include the
private provider stores needed to repeat every native audit or resume an
interrupted service.

## How it works

1. **Author a scenario.** A JSON spec describes people, channels, scenes and
   natural questions. Scenes can supply exact messages or ask a writer to generate
   a conversation. Day, week and month scenarios use the same format.
2. **Prepare and freeze the sources.** Generate synthetic conversations, review
   their coherence and answerability, and freeze the messages and judging criteria
   before any provider runs. Reuse that same corpus across providers.
3. **Ingest chronologically.** Each provider receives the raw sources through
   its native import or capture interface. Memcal uses its real Messages,
   Contacts and TLS IMAP readers against synthetic fixtures. The harness waits
   for native processing before asking questions.
4. **Ask through Hermes.** The agent starts with only the selected provider's
   shipped tools. Other connectors, OS tools and built-in memory are unavailable.
5. **Inspect answers and their evidence.** Record semantic grades, retrieval
   paths, processing failures, latency and observed model usage separately.

Providers receive source text and metadata, without authored answer keys,
extracted fact lists or benchmark summaries. Scenarios use a fixed simulated
calendar in `America/New_York`; accelerated replay does not measure real-time
aging. Clock conditions and configuration choices are recorded for each trial.

## What the scores mean

- **Answer quality:** Did the response correctly describe the plan at the
  question's simulated date? Missing judgments and execution errors stay ungraded.
- **Retention:** Did the answer come from retained memory? Native source search
  is allowed, but source-assisted answers earn zero retention credit. Ordinary
  retained-record opening and attached citations keep normal credit; uncertain
  internal paths remain uncredited pending review.
- **Recovery:** A single reviewed follow-up may recover silently omitted implicit
  details for half credit. Wrong values, explicit missed requests and retrieval
  failures do not qualify.
- **Cost:** Agent, provider and benchmark work are recorded separately where
  observable. Deferred processing still costs money; missing usage stays unknown.

Ingestion success alone does not prove recall, and an offline integrity check
does not establish provider accuracy. See the [tool boundary](docs/provider-tool-boundary.md)
and [judging and recovery policy](docs/followups.md) for the precise rules.

## Providers and setup

The active provider lineup is **Memcal, Mem0, Hindsight, Supermemory, Honcho and
OpenViking**. Historical ByteRover evidence remains available for audit. Integration
does not mean every provider has completed
the current scenario. See the [provider map](docs/document-ingestion-provider-map.md)
for native delivery methods, configuration and known limitations.

Running a new trial requires separate provider services, pinned Hermes
integrations, embeddings and model access. It is a self-hosted development
workflow. PACBench's budget gateway runs from this repository; it requires the
existing private ledger before any paid work. The [contribution guide](CONTRIBUTING.md)
explains the prerequisites, validation commands and next steps.

## Contribute

Useful starting points include reviewing source conversations and judging
criteria, investigating small provider compatibility failures, improving the
explorer and adding realistic scenarios. Please read [CONTRIBUTING.md](CONTRIBUTING.md)
before running paid experiments or changing evaluation behavior.

- [Scenario authoring](docs/authoring-options.md)
- [Conversations and persistence checks](docs/flexible-checks.md)
- [Native runtime setup](vendor/README)
- [Agent operating rules](AGENTS.md)

## License

[MIT](LICENSE).
