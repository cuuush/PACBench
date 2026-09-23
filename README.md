# PACBench

PACBench tests whether a personal assistant can answer from a frozen fictional conversation through a memory provider. A scene can be authored as a thread description plus a short list of required facts, each written as a plain-language assertion. The generator can write natural side chatter, while a separate verifier checks that the conversation actually establishes every fact. The earlier `scene` + `test_intent` form remains supported. See [authoring options](docs/authoring-options.md) for the tradeoffs.

## Run a semantic experiment

`examples/ice_cream.json` shows a thread description, one fictional occurrence time, and three required facts. The writer chooses the conversation length; PACBench assigns message timestamps afterward. Scene and thread IDs default from scene order. A thread ID is only needed when several scenes belong to the same conversation. Maintenance defaults to one hour before a next-morning question. `examples/pizza_day.json` and `examples/movie_night.json` show the earlier intent form. The runner is scenario-agnostic:

```sh
/usr/local/bin/python3 -m pacbench.semantic_experiment examples/pizza_day.json
/usr/local/bin/python3 -m pacbench.semantic_experiment examples/ice_cream.json --prepare-only
```

Set the model credentials used by `pacbench.creds` first. The run writes to a new `runs/semantic-*` directory and prints its path. The facts guide the writer, are checked against the rendered conversation by a separate verifier, and become evidence-cited claims for answer grading only after that review passes. The writer also returns a short author-facing note about its creative choices. PACBench saves the authored and resolved metadata separately. `--prepare-only` stops after freezing the dialogue, contract, and common question. A full run sends the same bundle and question through Memcal and Hindsight. Every model request, response, token usage, and available charge is recorded; if the API omits a charge, the artifact says so. A failed model call or malformed judge response leaves the arm incomplete.

The isolated Hermes runtime needs the MCP SDK for Hindsight. Install its pinned extra requirement after creating `vendor/venv-hermes-isolated`:

```sh
/usr/local/bin/python3 -m pip --python vendor/venv-hermes-isolated/bin/python install -r vendor/hermes-isolated-extra-requirements.txt
```

The Hindsight arm tests SDK import, MCP connection and namespaced tool discovery, allowlist access, and an actual read-only recall from its test bank before scoring a turn. Its result separately records whether Hermes chose to call recall. Memcal maintenance and turn prefetch are pinned to the fictional clock. A mismatch invalidates that arm.

## Inspect runs

```sh
/usr/local/bin/python3 serve_explorer.py
```

The local WebUI rebuilds its artifact index when loaded or refreshed. Story shows the rendered texts or emails, writer prompt and note, and the separate rendering review. Each provider gets a chat view with the question, any automatic brief, tool calls and returned memory, answer, and a clickable verdict badge with the answer judge's per-fact reasons. Saved files holds the detailed artifacts. The viewer accepts multiple saved question/answer exchanges when present; the current semantic runner freezes one question per experiment. The earlier corpus browser remains at `legacy.html`. Historical runs retain their original saved observations and hash even if a current fixture has changed. Missing internal traces are labeled “not captured.”

## Calibration and tests

`calibration/pizza_answers.json` contains proposed labels for a full answer, omissions, wrong details, uncertainty, and a historical venue mention. Run the same judge against the frozen contract with:

```sh
/usr/local/bin/python3 -m pacbench.semantic_calibration runs/semantic-<run>/contract.json calibration/pizza_answers.json runs/semantic-<run>/calibration
pytest
```

The proposed labels require human review before semantic scores are used to compare providers. The older `run-life` CLI and historical benchmark artifacts remain available for provenance; older design notes in `docs/` describe prior workflows and may refer to removed corpus fixtures.

Current development rules and next steps are in [AGENTS.md](AGENTS.md).
