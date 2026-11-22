# Offline cohort regrading

Plan every saved provider answer without listing cases:

```sh
python -m pacbench.cohort_regrade /path/to/cohort.json --revision luna-explicit-criteria-v1
```

A single native workflow `run.json` is also accepted. Cohort schema:

```json
{"schema":"pacbench-regrade-cohort-v1","runs":[
  {"provider":"memcal","manifest":"memcal/workflow/run.json","sha256":"SHA256_OF_RUN_JSON"},
  {"provider":"mem0","manifest":"mem0/workflow/run.json","sha256":"SHA256_OF_RUN_JSON"}
]}
```

Paths resolve relative to the cohort manifest. Each provider's complete registered
turn plan, verified saved surfaces, content-addressed evidence and original grades
come from its existing workflow progress store, opened read-only in one SQLite
snapshot. No provider adapter is started. No model transport is imported or called.
Unsupported export formats fail explicitly rather than guessing file names.

Coverage includes all planned turns and criteria, completed answers, missing turns
and execution-seal status. A missing answer is not graded as zero. The expected
set is the registered stored plan, not a hardcoded 49/192 denominator; this tool
does not independently recertify a corrupted plan against the original dataset.
It checks manifest/identity bindings, content-addressed artifact hashes, ordinal
continuity, duplicate runs/providers/criteria, final questions, original criterion
IDs and temporal conversation cutoffs. Full saved surface/activity/turn evidence,
original grades and source cutoffs are bound into the immutable overlay. This is
an integrity check of retained evidence, not a fresh audit of native ingestion.

Registered revisions:

- `luna-context-v4-v1`: unchanged v4 prompt.
- `luna-explicit-criteria-v1`: experimental versioned criterion-rule supplement
  for diet polarity, RSVP versus cancellation, roster scope and temporal precedence.
  It applies only when the criterion asks for the corresponding fact. It adds no
  quote requirement and retains the original verdict parser and granularity.

Both register Luna on Bedrock Global, fallback disabled. Registration binds the
rule text and scorer/planner source hashes; there is no mutable `latest` alias or
arbitrary external prompt loading. Official grades and the production judge are
unchanged. Uncertainty in old rubric interpretation is not retroactively gold.

To retain a separate versioned plan and analyze supplied saved responses:

```sh
python -m pacbench.cohort_regrade /path/to/cohort.json \
  --revision luna-explicit-criteria-v1 --output /path/to/regrades/explicit-v1
python -m pacbench.cohort_regrade /path/to/cohort.json \
  --revision luna-explicit-criteria-v1 --output /path/to/regrades/explicit-v1 \
  --responses /path/to/response-records.json
```

The overlay SQLite binding contains the full plan, prompts and their digests; keep
this directory private like the original runs. Output cannot be inside an original
run. A changed input, original grade or revision requires a new output directory.
Response records are a JSON list with `plan_id`, `cell` (`provider/turn-id`),
`prompt_sha256`, unique `attempt_id`, and raw string `response`. Duplicate/conflicting
imports fail; identical retained imports are idempotent. Reports separate per-cell
state, provider credit deltas and criterion verdict deltas. Unparsed/missing grades
have null deltas. Imported responses remain explicitly **receipt-unverified offline
analysis**, never official scores or evidence of paid settlement.

`Overlay.reserve` durably reserves a unique cell/attempt before any future external
admitted dispatch. A crash leaves an ambiguous attempt; there is no retry/reset API
and no automatic resend. It may only be completed by importing its matching saved
response. The CLI does not dispatch even on resume. Existing
`pacbench.workflow_grading` is reused through its shared `judge_options`, pinned
`judge_prompt` and `grade_semantic` logic; its in-place recovery writes are not called.

Live regrading remains a separate integration: original executor admission, account
lock, fresh prepaid/limit checks, prompt-specific scope and receipt reconciliation
must be reviewed before connecting any dispatcher. These offline commands do not
supply those capabilities or authorize charges. No live completeness claims are
made from synthetic tests.
