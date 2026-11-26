# Continue Pizza Week

Start from the public PACBench repository. To inspect the frozen Pizza Week
inputs and run offline conformance, use Python 3.12. These commands use the
standard library and need no provider services or API keys. The conformance
command uses a local test double to record file releases and question turns.

```sh
git clone https://github.com/cuuush/PACBench.git
cd PACBench
python3 -m pacbench.specs
python3 -m pacbench.workflow conform --track nightly-document --seven-daily-documents \
  --output "$(mktemp -d)/pizza-week-conformance"
```

To run the bundled 80-message benchmark on OpenRouter, follow the
[fresh-clone quickstart](../README.md#run-pizza-week-with-openrouter). This
guide covers the separately frozen r2 study and its original private state.

The frozen r2 source has 142 synthetic records and seven releases at 21:00
America/New_York. In the [seven-document condition](pizza-week-workflow.md),
each file contains that day's authored texts and emails in chronological
order, with the original source metadata and no carried-forward context.
The manifest records the one email export copy excluded by native MemCal
parsing. Wait for the exact release's native readiness before submitting
its questions. The continuing account has 49 ordered turns, 31 authored chats,
and 192 criteria across 47 gradable turns. Record the actual session layout,
release manifest, provider versions and model routes with every cohort.
The older single-chat and per-thread conditions remain available for reference.
The archived [delayed-reply spec](../specs/pizza_week_challenging.json) is a
separate authored scenario.

The recorded baseline judge is Luna with `semantic-judge-context-v4`.
Explicit criteria, the [question-scoped judge with exact evidence spans](question-scoped-judge.md),
and the [development calibrations](luna-judge-development.md) are opt-in.
Use these offline checks to inspect the frozen contracts and the saved-answer
entry point.

```sh
python3 -m pacbench.week_spec_audit
python3 -m pacbench.spec_first_pilot
python3 -m pacbench.question_judge_week inventory --candidate question-scoped-assertions-spans-v2
```

For the focused test suite, install its pinned development and provider
dependencies into a local virtual environment.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt httpx==0.28.1 \
  starlette==1.6.0 honcho-ai==2.9.0 supermemory==5.0.1 openai==2.36.0
python -m pytest -q tests/test_pizza_week_reliability.py tests/test_workflow_daily_documents.py \
  tests/test_honcho_week.py tests/test_supermemory_week.py tests/test_cohort_regrade.py \
  tests/test_question_judge_batch.py tests/test_question_judge_spans.py \
  tests/test_week_spec_audit.py tests/test_prose_judge_controls.py
```

The [local integration and receipt commands](../CONTRIBUTING.md#focused-pizza-week-checks)
exercise the full focused set.

## Regrade saved answers

A fresh clone contains the public corpus, source, fixtures and aggregate results.
The machine that ran the original trials keeps the private run manifests,
provider readbacks, saved answers and receipts. Keep the original directories
together. Set `COHORT` to the [cohort manifest](cohort-regrade.md) that points
to those runs and `NEW_OVERLAY` to a new private output directory. To import
recorded judge replies, supply the response journal, its verified SHA-256 and
the judge ID.

```sh
python3 -m pacbench.cohort_regrade "$COHORT" --revision luna-context-v4-v1 --verify
python3 -m pacbench.cohort_regrade "$COHORT" --revision luna-context-v4-v1 \
  --response-journal "$REPLIES" --journal-sha256 "$REPLY_SHA" \
  --judge "$JUDGE" --output "$NEW_OVERLAY"
```

For a live continuation, the original launch commands still verify their
existing authorization, provider state and receipt files.

For the next comparison, freeze the six synthetic answers and expected
decisions, then pair Luna v4 with the candidate on the same pinned route.
Check cutoff and wording controls before drawing conclusions from the replies.
Compare saved provider cohorts with matching release, chat and judge conditions
and report coverage alongside any score. See the
[paired validation plan](question-scoped-judge.md#compatibility-and-evaluation-plan-declare-before-any-calls)
for the independent efficacy check.

## Recover retired branch history

PACBench keeps the exact historical tips under their original names in
`refs/archive/pizza-week-20261125/`. Fetch them explicitly from a clone.

```sh
git fetch --no-tags origin 'refs/archive/pizza-week-20261125/*:refs/archive/pizza-week-20261125/*'
git for-each-ref --format='%(objectname) %(refname)' refs/archive/pizza-week-20261125/
```

To run the older 333-target source-gate and reviewed-runtime tests, use the
archived November 13 preparation. After fetching the refs above, extract the
original corpus from the repository root into an unused destination. The
tests use the commit for the exact reviewed runtime and verify the preparation's
manifest and its sibling files.

```sh
test ! -e artifacts/pizza-week-hybrid-stream-v7-20261113 &&
test ! -L artifacts/pizza-week-hybrid-stream-v7-20261113 &&
git archive a7bc36729db23152f61b928df5cda2149e717859 \
  artifacts/pizza-week-hybrid-stream-v7-20261113 | tar -x
python3 -m pytest -q tests/test_reviewed_runtime.py \
  tests/test_role_routes.py -k 'reviewed_pair or unreviewed_source or source_gates'
```
