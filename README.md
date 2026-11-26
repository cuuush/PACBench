# PACBench

PACBench tests whether a personal agent remembers changing plans. Each memory
provider receives the same synthetic messages and email. Hermes then uses that
provider’s tools to answer questions about the people, places and dates.

## Run Pizza Week with OpenRouter

The reviewed Pizza Week corpus is checked into the repository. With Git and
Python 3.11 or newer, you can verify it and preview the benchmark offline.

```sh
git clone https://github.com/cuuush/PACBench.git
cd PACBench
python3 -m pacbench run pizza-week --plan
```

Expect 80 messages across six source days, 17 questions and 52 grading criteria
per provider. The plan prints the path to its `summary.json`.

To run all nine providers, use a Linux x86-64 machine with Docker running,
uv, Node.js 22.21 or newer, and the system build tools. Setup downloads several
GB of pinned dependencies and starts the local embedding service. Run it once
from the checkout. [Host prerequisites](vendor/README)

```sh
./scripts/setup
export OPENROUTER_API_KEY='your-openrouter-key'
python3 -m pacbench run pizza-week --budget-usd 5
```

Choose your own USD limit. The `5` above is a spending ceiling for this
invocation. A complete run can cost more. PACBench looks up
prices for the default DeepSeek / DeepInfra Flex endpoint and accounts for
model calls against that limit.
By default, the command prints the path to `summary.json` under `artifacts/runs/`.
Read `summary.json` for provider outcomes and `repeat-1/` for individual
trials. Use `--providers memcal mem0` to run a subset or `--output PATH` to
choose a new destination. Keep the sibling `<output>.openrouter-private/` directory
with your local run if you need its spending records.

## Run a new scenario

Write one JSON spec describing the persona, scenes and questions. Generate
the corpus once, then reuse the frozen directory for each comparison.
Generation and evaluation each take their own spending limit.
[Spec format and examples](docs/authoring-options.md)

```sh
python3 -m pacbench generate specs/pizza_month.json --output artifacts/corpora/month --budget-usd 5
python3 -m pacbench run artifacts/corpora/month --budget-usd 5
```

`run` accepts a prepared directory, its `week.json`, or the name of a dataset
under `datasets/`. Keep the whole generated directory together. The same two
commands work for a day, a month, or a directory of persona specs processed
in sequence. [Run a batch](docs/authoring-options.md#run-multiple-personas)

## Explore saved results

Run the explorer from the checkout and open [localhost:8137](http://127.0.0.1:8137/index.html).

```sh
python3 serve_explorer.py
```

The explorer shows source conversations, nightly imports, agent tool calls,
answers, grading criteria and observed costs. Use `python3 serve_explorer.py 8138`
to choose another port.

The saved Pizza Month run contains 2,868 messages, 30 nightly imports and 15
final questions. Memcal, Mem0 and Hindsight completed ingestion and have some
ungraded answers. Supermemory and Honcho stopped during ingestion. The public
files contain the evidence available for inspection. Resuming a provider trial
requires its original private store. [Pizza Month results](runs/pizza-month-local-20261105) ·
[Native audit](runs/pizza-month-local-20261105/native-audit.json)

## How it works

The separate Pizza Week r2 study uses seven nightly documents and 49 ordered
question turns. Its continuation guide covers offline conformance and regrading
saved answers. [Frozen r2 study](docs/pizza-week-continuation.md)

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

## Providers

The active local lineup is **Memcal, Mem0 OSS, Hindsight, Supermemory, Honcho,
OpenViking, ReMe, Cognee and MemOS**. The provider map records
native delivery methods, setup and limitations. [Provider map](docs/document-ingestion-provider-map.md)

ByteRover is retired from the active benchmark at the user's direction. Its
saved results and audit support remain available as historical evidence.

The [contribution guide](CONTRIBUTING.md) covers validation and provider-specific
requirements.

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
