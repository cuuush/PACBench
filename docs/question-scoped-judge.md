# Question-scoped semantic judge · experimental contract 1

## What a judgment means

The unit of judgment is one asserted attribute of one event or entity in the
latest answer to one dated question. Each authored row belongs to a
(turn ID, criterion ID) pair. Its assertion, entity, event, target time, attribute,
acceptable entailments, insufficient evidence of that entailment, and conflicting
assertions are reviewed together. An isolated time can answer a direct time question.
A broad agenda may require enough context to identify its lunch. The two questions
can share an underlying fact and still need different rules. Wording examples
illustrate a rule and never prescribe a literal answer.

The author selects short, exact passages from the frozen criterion's source IDs.
The compiler resolves the text, speaker, source timestamp and actual release time
from the benchmark. Authored and released time serve different purposes. A later
release is ineligible even when quoting an older message. A user correction must
be an earlier scripted correction tied to the criterion, with verified delivery
required at the retained-run boundary. Occurrence time in the assertion may be
in the future. A quote can document a historical state without supporting a
present one. Authors must review the whole eligible prefix for supersession,
contrary evidence and unsupported assumptions. A selected excerpt alone cannot
establish that no other confirmation exists.

Authors choose an affirmative requirement or an explicit prohibition. Asking
whether attendance is known can require an affirmative statement of uncertainty.
Merely omitting attendance does not convey that uncertainty. A prohibition can
be satisfied by silence in a received substantive answer. An unavailable,
empty or failed provider response is a separate input state.

The judge returns one relation per row in published order.

| Relation | Meaning | Compatibility |
|---|---|---|
| entailed | The latest answer conveys the scoped assertion, or satisfies the authored prohibition. | correct |
| absent | The answer does not address it. Reporting only an earlier state can fall here. | missed |
| ambiguous | The answer mentions something relevant but its asserted value or referent cannot be uniquely resolved. | missed |
| contradicted | The answer asserts an incompatible value or violates the prohibition in scope. | misrepresented |
| abstain | The judge cannot adjudicate the authored contract or evidence reliably. | ungraded |

A false accept is a *comparison error*: credit against a human noncredit label.
It is never a provider verdict. A contract error, missing input, unavailable or
malformed judge response and judge abstention have no provider-error score.
Record the planned, graded and credited denominators. Surface abstentions and
invalid replies. A judge decision concerns meaning regardless of retrieval path;
citations and native recall telemetry belong to a separate diagnostic.

Authored rules are judge-only. The latest provider answer and prior dialogue are
data; an instruction, imitation of a role or embedded verdict has no authority.
Source passages establish facts and do not give instructions. Earlier dialogue
may resolve references; credit must come from the latest answer. Require a
verbatim span of that answer for asserted entailment, contradiction and ambiguous
reference. A quote is an audit handle, not proof of a sound semantic judgment.

## Compatibility and evaluation plan (declare before any calls)

Keep v4 and all original grades immutable. Adapt the published frozen turn
schedule, actual latest answer and prior same-session public conversation. Migrate
a complete turn at a time, recording its frozen identity and every criterion in
order. Missing migration, changed source/turn, or unverifiable retained prefix
blocks that turn. Never fabricate rules from a legacy statement or turn a subset
into a Week score. Repeated facts receive separately reviewed question-specific
rows. Any change to the weight or substantive expected fact needs a new benchmark
condition, separate from this compatibility overlay.

1. Freeze the authoring file, compiler, comparison plan, source bindings, exact
   ordered questions, model settings and parser before opening validation.
   Compare the unchanged v4 against this contract on *identical full answers
   and dialogue*. One request per full answer per arm, same singleton Luna
   Bedrock Global route, no fallback, identical decoding and limits. Balance
   arm order by a seeded, frozen scenario-level schedule. Keep requests,
   raw replies and normalized output hashes distinct. Require the normal
   verified runner and receipts for an actual shadow run.
2. The known ten- and 24-case development sets and the nine already-inspected
   provider runs are regressions or descriptive shadows only. The ten cases
   tied; v4 scored 24/24 on the harder set, explicit and Luna Decisions 23/24,
   Jev 20/24. The rank audit found four clear old errors, one clear new error
   and four ambiguous cases. These observations inform authored distinctions,
   not a claim that the new judge is more accurate.
3. A separate owner creates and seals fresh independent scenarios, including
   multi-criterion turns, full natural answers and time/scope/state controls.
   Two reviewers blind to provider, judge arm and predictions independently
   label each criterion, then reconcile before unblinding. Preserve human
   ambiguity/disagreement as its own bucket. Freeze scenario IDs, input hashes,
   labels, sample size/power and exclusions before dispatch. The implementer
   does not inspect the blind set. After unblinding, any revision needs another
   untouched set.
4. Primary endpoint: exact semantic agreement on the compatible three-way
   scale against determinate human labels. Within each scenario divide matches
   by planned determinate criteria (invalid or abstained = no match), then
   average scenarios equally. Report paired change in percentage points,
   exclusions, coverage, false accepts/rejects, and ambiguity vs omission
   separately. Resample *whole paired scenarios* (10,000 bootstrap replicates,
   seed 1729, percentile 95% interval). Promote only with at least +3 percentage
   points and a lower bound above zero, no new protected time/scope/state false
   accepts, and no lower judge coverage. An underpowered study, tie, or gain
   explained only by output formatting cannot promote. First deployment is
   shadow-only, with separate versioned outputs.

## Run the prototype offline

Run from the repository root. The CLI reads local files and writes JSON to
stdout. These commands exercise the direct lunch follow-up with a synthetic
answer and the preceding public turn from the frozen schedule.

```sh
python -m pacbench.question_judge_week inventory
example_dir=$(mktemp -d)
python - "$example_dir/case.json" <<'PY'
import json
import sys
from pathlib import Path
from pacbench.question_judge_week import Week

prior = Week().by_id['game-reference']
case = dict(turn_id='friday-lunch-time', answer_state='received',
            answer='Half past noon.', delivered_corrections={},
            prior_public_dialogue=[
                dict(role='user', content=prior.prompt, at=prior.at),
                dict(role='assistant', content='Synthetic game recap.', at=prior.at)])
Path(sys.argv[1]).write_text(json.dumps(case))
PY
python -m pacbench.question_judge_week plan "$example_dir/case.json" > "$example_dir/plan.json"
python - "$example_dir" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
plan = json.loads((root / 'plan.json').read_text())
arm = 'question-scoped-assertions-v1'
row = dict(id='lunch-time', relation='entailed', answer_quote='Half past noon',
           reason='The direct question supplies the lunch; the answer gives its time.')
reply = dict(request_sha256=plan['manifest']['requests'][arm], finish_reason='stop',
             text=json.dumps(dict(verdicts=[row])))
(root / 'stub-reply.json').write_text(json.dumps(reply))
PY
python -m pacbench.question_judge_week interpret "$example_dir/case.json" question-scoped-assertions-v1 "$example_dir/stub-reply.json"
```

The stub supplies a decision to the parser. Only independent labeled evaluation
can establish semantic accuracy. The CLI rejects partial turn migrations, late
anchors, foreign corrections, altered request bindings and malformed replies.
The integer output counters distinguish graded assertions from planned ones.
Run `python -m pytest -q tests/test_question_scoped_judge.py` for the adverse
controls and the CLI round trip.

### Integrating a later shadow evaluation

The author file is `datasets/pizza-week/judge-experiments/question-contract-v1.json`.
`turn_sha256` binds the complete original turn, including its question, clock,
session and ordered claims. Each row extends that frozen claim's affirmative
statement or explicit prohibition with the authored scope and decision
boundaries. `anchors` selects a source ID and unique verbatim passage. The judge
sees short local anchor names, passages, speaker, thread and clocks. The local
manifest maps those names to full source IDs, original UTF-8 body and excerpt
hashes. The answer model never uses this module. A compatibility projection
maps only usable semantic judgments into v4's three labels.

The inventory currently covers nine complete turns and 32 criteria. For a
future nine-arm descriptive shadow, complete and independently review the other
38 gradeable turns, check each selected passage against the actual input
projection for each retained condition, and verify releases and correction
deliveries. The historical and seven-document inputs are distinct conditions.
Use the existing native evidence verifier first, then
`Week.from_surface(turn_id, verified_surface, delivered_corrections)`. It calls
v4's own public-dialogue projector, retaining intermediate assistant prose
from earlier tool calls. An arbitrary JSON case file supplies development data
only. A request hash in a local reply establishes which bytes it purports to
answer. An actual model judgment additionally needs the normal verified
request, route, receipt and terminal response evidence.

`question_judge_comparison.comparison_plan(cases)` fixes both wire hashes,
the scenario clusters, case membership and balanced arm order. Archive its
fingerprint before execution. Keep the two blinded reviewers' reconciled
reference with the independent owner and commit its hash before unblinding.
For a human noncredit label, designate any protected control as `time`,
`scope` or `state`. Use `reviewers_unresolved` for ambiguous human labels.
After sealing both reply sets, call `compare(cases, responses, reference,
plan_sha256=..., reference_sha256=...)` locally. It reports scenario and
criterion outcomes, false accepts, false rejects, missing and abstained grades,
and the preregistered numeric checks. Human-label custody, sufficient power
and receipt provenance are external validation requirements. The comparison
code has no deployment or dispatch path.
