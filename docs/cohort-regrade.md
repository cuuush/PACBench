# Offline cohort regrading

Verify the declared arms and plan every saved answer in one command.

```sh
python -m pacbench.cohort_regrade /path/to/cohort.json --revision luna-explicit-criteria-v1 --verify
```

The default command, without `--verify`, prints the verified plan and fails on
an invalid arm. With `--verify` it inspects every declared arm, reports missing
or invalid inputs alongside verified ones, and exits nonzero if an arm fails
verification. Coverage shows missing turns, incomplete native execution and
any unavailable document readback evidence. The report contains no answer
text or prompts.

The cohort manifest lists retained arm manifests and their SHA-256 digests.
Paths are relative to the cohort manifest. Use the provider ID recorded by the
run. A single arm path works as input without a cohort wrapper.

```json
{
  "schema": "pacbench-regrade-cohort-v1",
  "runs": [
    {"provider": "memcal", "manifest": "memcal/workflow-scope.json", "sha256": "SHA256_OF_SCOPE"},
    {"provider": "mem0", "manifest": "mem0/workflow/run.json", "sha256": "SHA256_OF_RUN_JSON"},
    {"provider": "supermemory", "manifest": "supermemory/workflow/workflow.jsonl", "sha256": "SHA256_OF_LEGACY_JOURNAL"}
  ]
}
```

- A current arm directory, `workflow-scope.json`, `result.json`, or
  `workflow/run.json` locates its original `workflow.sqlite3` and
  `workflow-artifacts/`. Keep the bound scope beside the workflow directory.
  The document track uses the original `workflow/workflow.jsonl` for native
  intake and readback evidence. An absent document journal is reported as
  unavailable. An explicit result manifest must agree with its saved store.
- An original, pre-SQLite `workflow.jsonl` supplies its own native condition,
  schedule, verified answers and available original verdicts. Use the branch
  with the original compiled spec if its digest differs. A torn append stays
  outside the verified prefix.
- Public aggregate results and answer-only exports lack the original retained
  store or journal bindings. They cannot supply a regrade plan.

The reader verifies the original run and scope hashes, ingestion track and
admission protocol when recorded, compiled spec and dataset manifest,
ordered questions with exact criteria, and content-addressed artifacts.
Saved question clocks, session history, answer, and source inventory must
agree with the original cutoff. For MemCal, the indexed source plan and
aggregate collection receipts provide the native IDs, channel cursors and
Dream readiness at each completed release. The collection witness count
comes from those observations. Document providers have separate document
acceptance and readback evidence. The plan preserves each arm's original
condition, including the canary flag and any recorded MemCal product
manifest. A version string alone does not establish that product manifest.

Registered revisions are `luna-context-v4-v1`, the original v4 prompt, and
`luna-explicit-criteria-v1`, the experimental diet, RSVP, roster and schedule
rules. Both record the Luna Bedrock Global route with fallback disabled and
reuse the original verdict parser and criterion granularity. Source and
scorer digests are bound to the plan.

Use a new, private overlay directory to retain the plan or analyze supplied
responses.

```sh
python -m pacbench.cohort_regrade /path/to/cohort.json \
  --revision luna-explicit-criteria-v1 --output /path/to/regrades/explicit-v1
python -m pacbench.cohort_regrade /path/to/cohort.json \
  --revision luna-explicit-criteria-v1 --output /path/to/regrades/explicit-v1 \
  --responses /path/to/response-records.json
```

Response records contain `plan_id`, `cell` (`provider/turn-id`),
`prompt_sha256`, a unique `attempt_id`, and the raw string `response`.
Identical imports are idempotent. A reserved attempt with no response remains
ambiguous, and the same cell cannot be reserved again. Changed input, grades
or revision require a new versioned overlay. Provider and criterion deltas
compare with the original saved verdicts, with null deltas for unavailable
grades. Imported responses retain their receipt-unverified status.

The CLI works offline and only opens source stores read-only. It uses the
same `judge_options`, `judge_prompt` and `grade_semantic` logic as the
original judge-only workflow. Live dispatch and billing reconciliation require
separate integration in the original runtime.

## Import and compare a recorded judge result

Use one command to import one complete selection from a retained response journal.
Choose a fresh output directory for each judge recording.

```sh
python -m pacbench.cohort_regrade /path/to/cohort.json \
  --revision explicit-criteria-v1 --response-journal /path/to/recorded-responses.jsonl \
  --judge candidate --journal-sha256 EXPECTED_RECORDING_SHA256 \
  --output /path/to/regrades/explicit-v1-candidate
```

The model-neutral revisions are `context-v4-v1` and `explicit-criteria-v1`.
They freeze the same rubric and parser as their Luna counterparts. The journal
supplies the selected model, route, endpoint and fallback setting. The existing
`luna-*` revisions also require their registered model, route and fallback
setting. The earlier `--responses` JSON array remains available for unverified
offline observations.

A recorder or read-only export on the original runner supplies the JSONL
journal. Pin its SHA-256 from the retained recording. Each line must end with a
newline. Its first object contains:

```json
{"schema":"pacbench-regrade-response-journal-v1","plan_id":"FROZEN_PLAN_ID","revision":"explicit-criteria-v1","judges":{"candidate":{"model":"example/model","route":"example/provider","endpoint":"https://judges.example.invalid/v1/semantic","fallback":false,"response_format":"semantic-verdicts-v1"}}}
```

Each subsequent object identifies one recorded result with `judge`, `cell`
(`arm/turn-id`), a unique `attempt_id`, `input_sha256`, `prompt_sha256` and
the raw text `response`. The `request` object contains the recorded
`model`, `route`, `endpoint`, `fallback` and exact `prompt`. The
`receipt` object contains the recorded `model`, `route`, `endpoint`,
unique `request_id`, `status` of `complete` and `response_sha256` of the
UTF-8 response text. The recorder must preserve actual request, response and
terminal identity. The planner's `snapshot()` provides the expected hashes
and prompts locally. A recorder must bind its actual prompt to that plan.

`semantic-verdicts-v1` uses a complete JSON object with a `verdicts` array
containing exactly the frozen criterion IDs in order. Each item has `id`,
`verdict` and `reason`. Unsupported result formats, incomplete originals,
absent responses, mismatched provenance and bad verdict objects abort the
selected import before an overlay opens. Other declared judges in the same
recording can be selected with their own outputs. The importer binds the
exact journal and records each request ID and response digest. Upstream
receipt authenticity and billing reconciliation are separate.

The overlay contains `report.json` with original and regraded denominators,
correct counts, and matched per-criterion deltas for every arm. Unknown
original judgments remain ungraded. Replaying the exact recording is
idempotent. An overlay rejects a different recording or judge selection.

### Seven daily files

A run may record `input_projection: "seven-daily-files-v1"` in its
workflow identity or journal spec. A cohort entry can also name this projection
for an existing recording. The verifier matches the seven ordered submit and
readback records to the exact public r2 daily file bytes, source IDs and release
timestamps. It supports the frozen v2 nightly clock and the original r2
batch clock as distinct conditions. This adapter uses the existing v2
continuing-account question and criterion contract. It does not equate the
earlier 42-question r2 evaluation with the v2 49-turn schedule.

Use a unique `arm` when the same provider appears under two conditions. The
optional `ingestion_protocol` pins the recorded arm admission identifier.
The comparison reports conditions with their release clocks and question
schedule hashes. It provides per-arm deltas without a cross-condition ranking.

## Offline authoring and judge controls

```sh
python -m pacbench.week_spec_audit
python -m pacbench.spec_first_pilot
python -m pytest -q tests/test_cohort_regrade.py tests/test_week_spec_audit.py \
  tests/test_spec_first_judge.py tests/test_spec_first_volunteer_evidence.py \
  tests/test_prose_judge_controls.py tests/test_judge_development*.py
```

The Week audit checks the archived 49-turn, 192-criterion schedule and matches
the authored fact lists to their scored tracks. It reports versioned scope
exceptions and checks cited evidence against the archived release times.

For the synthetic booking comparison, `pacbench.prose_judge_controls.prepare_cases()`
provides each question, answer, ordered claims and references visible by the
cutoff. The [expected verdicts](../tests/fixtures/prose_booking_key_v1.json) stay
separate from judge inputs. The offline tests check rendering and comparison
using synthetic responses. Semantic judge performance requires model responses.

See [Luna judge development](luna-judge-development.md) for the ten-case pilot.
Run `python -m pacbench.judge_development_challenge --help` for the 24-cell
synthetic challenge's plan and comparison commands.
