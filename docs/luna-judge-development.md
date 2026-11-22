# Luna judge development comparison

This isolated synthetic development test compares the existing
`semantic-judge-context-v4` rubric with versioned per-criterion rules. It does not
change the official judge, benchmark specification or published scores. The ten
cases and their preselected gold labels live in
`tests/fixtures/luna_judge_development_v1.json`. The request manifest freezes twenty
request bodies. These examples are authored controls, not recorded model answers.

```sh
python -m unittest tests.test_judge_development
python -m pacbench.judge_development --plan /tmp/luna-development-plan.json
python -m pacbench.judge_development --evaluate /path/to/observations.json
```

Plan creation refuses to overwrite existing files. This module performs no network
requests and has no live mode. Evaluation accepts a JSON list of
`{case_id, arm, response}` with a raw JSON response string; it reports rescues of
previously missed correct answers, newly credited wrong answers, scenario families
with both pair members correct, invalid outputs, unsolicited abstentions and
missing cells separately. Supplied responses are not proof of actual dispatch or
settlement. Gold labels never enter model requests.

Both arms use `openai/gpt-6-luna`, exclusively `amazon-bedrock/global`, fallback off,
required parameters enabled, streaming usage and a 32,768 output-token maximum.
The same one atomic criterion, question, answer and original output schema are
used in both arms. Only the appended criterion rule changes; order alternates by
case. Evidence-span requirements are a separate diagnostic, excluded here.

The conditional maximum liability envelope is **$0.278884 per attempt and
$5.577680 for all twenty attempts**. This conservatively allows 1,050,000 input
tokens at at most $0.25/M effective input cost and 32,768 output tokens at at most
$0.50/M. Before any externally admitted execution, the existing exact-route
catalog validator must account for every pricing tier and cache charge, reject
unknown dimensions, and satisfy `bound()` limits. This envelope is not a current
price observation, spending authorization or an enforced cap in this offline
module. Execution must use the existing approved prepaid account, verified
Auto Recharge off and finite nonresetting limit, current shared serial lock and
stop state, durable reservation before dispatch, and unique settled receipts.
One attempt per cell; stop on ambiguity or missing settlement. No alternative
route, automatic retry or account changes are permitted by this experiment.

## Standalone prepaid admission

`pacbench.judge_development_live` is the separate live entry point on the
authorized shared executor. It accepts only the twenty ordered bodies from
the reviewed manifest. The ordinary PACBench direct transport writes a
prepared attempt before each send. The next cell requires the prior exact
wire stream and a unique settled generation receipt. A stop, missing receipt,
interrupted stream or unexpected route ends the batch without an automatic
retry. The shared account lock remains held for the batch. The run directory
is single-use, including after interruption.

This observational path uses the existing prepaid balance and the provider's
finite nonresetting key limit. It requires the matching execution account,
retained operator confirmation of Auto Recharge off, and no contrary live
billing evidence. It never changes account settings. The conditional sizing
figure above is reference arithmetic for the original offline proposal.
