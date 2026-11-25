# Continue Pizza Week

Start from the public PACBench repository. Use Python 3.12 for the local checks.

```sh
git clone https://github.com/cuuush/PACBench.git
cd PACBench
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt httpx==0.28.1 \
  starlette==1.6.0 honcho-ai==2.9.0 supermemory==5.0.1 openai==2.36.0
python -m pacbench.specs
python -m pacbench.workflow conform --track nightly-document --seven-daily-documents \
  --output "$(mktemp -d)/pizza-week-conformance"
```

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
python -m pacbench.week_spec_audit
python -m pacbench.spec_first_pilot
python -m pacbench.question_judge_week inventory --candidate question-scoped-assertions-spans-v2
python -m pytest -q tests/test_pizza_week_reliability.py tests/test_workflow_daily_documents.py \
  tests/test_honcho_week.py tests/test_supermemory_week.py tests/test_cohort_regrade.py \
  tests/test_question_judge_batch.py tests/test_question_judge_spans.py \
  tests/test_week_spec_audit.py tests/test_prose_judge_controls.py
```

The [local integration and receipt commands](../CONTRIBUTING.md#focused-pizza-week-checks)
exercise the full focused set.

On an authorized runner with the original private run directories, verify the
[cohort manifest](cohort-regrade.md) before opening a fresh overlay. The same
command can import recorded replies by supplying a verified response journal,
its SHA-256 and the judge ID.

```sh
python -m pacbench.cohort_regrade "$COHORT" --revision luna-context-v4-v1 --verify
python -m pacbench.cohort_regrade "$COHORT" --revision luna-context-v4-v1 \
  --response-journal "$REPLIES" --journal-sha256 "$REPLY_SHA" \
  --judge "$JUDGE" --output "$NEW_OVERLAY"
```

The original executor retains the private run manifests, native readbacks,
saved answers and response receipts. A fresh clone contains the public corpus,
source, fixtures and aggregate evidence. Runner-specific live launchers verify
their own existing authorization, application state and receipt files.

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
