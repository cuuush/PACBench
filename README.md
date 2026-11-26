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

## Try it offline

You need Git and **Python 3.11 or newer**. From a terminal, clone the repo and
plan a run using the checked-in Bike Pickup data. This needs no credentials,
services or package installation.

```sh
git clone https://github.com/cuuush/PACBench.git
cd PACBench
python3 -m pacbench run runs/bike-pickup-native-clock-20261105/preparation \
  --output "$(mktemp -d)/bike-plan" --plan
```

The command prints the path to `summary.json`. Expect nine selected memory systems,
two source days, five questions and 15 grading criteria per provider. The plan
verifies the inputs and shows what a live run would do.

## Set up a live run

The **runner** is the machine where you run PACBench. A **provider** is one of
the memory systems it tests. The **corpus** is the frozen set of conversations,
questions and grading criteria shared by all providers. The **model gateway** is
a local service that forwards model requests and records spending.

On Linux x86-64, use the setup script to check your machine, install the pinned
tools, and leave the local embedding service running. Installation downloads
several GB and does not make paid model requests. See the exact prerequisites
and setup modes in the [runtime guide](vendor/README). The first check on a
fresh machine lists the missing components. Keep access to the plugin
repositories available when running the providers.

```sh
./scripts/setup --check
./scripts/setup --install --start-services
./scripts/setup --check
```

For the default paid path, the operator needs a Meta credential authorized for
Spark Contributor and the gateway's $10 UTC daily limit. Supply `META` through
your local secret manager or managed environment and keep it out of Git and chat.
In a separate terminal with that credential available, start the gateway from
the repo root. This example creates private state for a genuinely new campaign.

```sh
mkdir -p "$HOME/.local/state"
vendor/venv-hermes-isolated/bin/python -m pacbench.gateway \
  --state "$HOME/.local/state/pacbench-gateway" \
  --initialize-new-state
```

Leave the gateway running. For later starts, omit `--initialize-new-state` and
use the same directory. To continue a prior paid campaign, use its original
private ledger and authorization. The historical OpenRouter launchers use their
own task-specific authorization and state.
[Existing campaign setup](CONTRIBUTING.md#run-a-new-experiment)

## Run your own scenario

In the terminal where you will run PACBench, make `META` available and activate
the Python environment installed by setup. It includes the libraries PACBench
uses to load the provider plugins. Use an existing author spec or write one in
the same format. [Scenario authoring](docs/authoring-options.md)

```sh
. vendor/venv-hermes-isolated/bin/activate
export PACBENCH_GATEWAY_STATE="$HOME/.local/state/pacbench-gateway"
export MODEL=muse-spark-1.3-contributor
export TMPDIR=/var/tmp

./scripts/setup --check-live &&
python -m pacbench.specs specs/pizza_week.json &&
python -m pacbench generate specs/pizza_week.json --output artifacts/corpora/pizza-week &&
python -m pacbench run artifacts/corpora/pizza-week --output artifacts/runs/pizza-week-001
```

Successful preparation writes `week.json`. Keep its sibling files so you can
use that exact corpus again without generating and reviewing the conversations
again. `run` selects all nine active providers by default. Use
`--providers memcal mem0` to choose a subset or `--plan` to check the frozen
corpus offline before starting a live run. Each live run writes `summary.json`
and per-provider evidence under `repeat-1/`. A `blocked` result means an execution
prerequisite failed, such as gateway access or spending availability. Read the
reported reason before retrying and use a new output directory.

For multiple personas, put one spec per file in `specs/personas/`. Give a
changed spec a fresh name, such as `maya-v2.json`, so its corpus stays distinct.
Use the same activated terminal to run one batch attempt at a time. The loop
stops at the first error and records the summary path for each successful run.
To retry unfinished personas, inspect the failed attempt and keep the same
`batch` with a new `attempt` label. Earlier attempts stay in their original
directories. If interrupted before writing a completion file, check that
attempt's summary before retrying. If preparation stopped partway, inspect its
error, then use
`python -m pacbench generate SPEC --output CORPUS --resume` for a compatible
partial corpus. Use a new batch label when you intend to rerun every persona.

```sh
(
  set -e
  batch=batch-001
  attempt=attempt-001
  for spec in specs/personas/*.json; do
    [ -f "$spec" ] || continue
    name=$(basename "$spec" .json)
    corpus="artifacts/personas/$name/corpus"
    batch_dir="artifacts/personas/$name/runs/$batch"
    [ ! -s "$batch_dir/completed" ] || continue
    if [ ! -f "$corpus/week.json" ]; then
      python -m pacbench.specs "$spec"
      python -m pacbench generate "$spec" --output "$corpus"
    fi
    run="$batch_dir/$attempt"
    python -m pacbench run "$corpus" --output "$run"
    printf '%s\n' "$run/summary.json" > "$batch_dir/completed"
  done
)
```

To try the same authored scenario without paying for preparation, download the
original 80-message corpus as `week.json`. That archived manifest is self-contained.
[Download the original Pizza Week corpus](https://raw.githubusercontent.com/cuuush/PACBench/825ad85e578c8e7d545c9e5fa3fa0713f912faaa/runs/pizza-week-natural-messages-all-20261012-r1/preparation/week.json)

```sh
python3 -m pacbench run week.json --output "$(mktemp -d)/pizza-week-plan" --plan
```

The plan covers six source days and 17 questions. After live setup, use the same
`run` command with the activated `python`, a fresh output path, and no `--plan`.
Pizza Week r2 has separate frozen data and a 49-turn conversation schedule.
[Pizza Week r2 continuation and offline checks](docs/pizza-week-continuation.md)

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

The [conversational workflow guide](docs/pizza-week-workflow.md) documents
offline conformance and the durable controller shared with Pizza Day.
The [Pizza Week continuation guide](docs/pizza-week-continuation.md) has the
clean checkout commands, frozen conditions and saved-answer regrade workflow.

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

The active local lineup is **Memcal, Mem0 OSS, Hindsight, Supermemory, Honcho,
OpenViking, ReMe, Cognee and MemOS**. Integration does not mean every provider has completed
the current scenario. See the [provider map](docs/document-ingestion-provider-map.md)
for native delivery methods, configuration and known limitations.

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
