# Natural recovery: live diagnostic, 6 October 2026

Run: `runs/pizza-natural-recovery-native-20261006`. The unchanged frozen bundle has 49 messages, five ingestion turns over three days and two same-Friday pizza plans. Ingestion shares one native Hermes conversation; the two evaluation questions share a separate fresh conversation. All arms use Eastern time, normal additive Hermes memory and recommended native provider configuration. No facts are seeded and no retrieval result is simulated.

| Provider | Initial correct facts | Retention points including half-credit recovery | Recovery |
| --- | --- | --- | --- |
| Memcal | 14/16 | 15/16 | Both streets silently omitted; one combined question, two event opens, both recovered |
| Holographic | 15/16 | 15/16 | Original Devon time omitted; one probe, wrong answer, no recovery credit |
| Mem0 OSS | 16/16 | 16/16 | None needed |

These are small diagnostic observations, not a calibrated provider ranking. None of these evaluation paths used archive/message fallback. Explicitly requested misses, wrong answers, admitted retrieval failures and zero recall prohibit rescue; only silent implicit omissions qualify. Any such terminal failure cancels recovery for the whole answer. Earlier protocols and scores are unchanged.

## Actual recovery effort

| Provider | Tool operations | Turn seconds | Hermes model calls | Hermes input/output tokens | Hermes cost upper bound | Provider model calls during recovery |
| --- | --- | --- | --- | --- | --- | --- |
| Memcal | Two `memcal_open` | 7.60 | 2 | 33,510 / 264 | $0.0034038 | 0 |
| Holographic | One `fact_store` probe | 13.83 | 2 | 23,336 / 268 | $0.0023872 | 0 |

Zero provider inference during these windows does not imply every provider operation is free. Across this entire live run, Mem0's own gateway project made seven extraction calls: 57,080 input and 11,742 output tokens, $0.0080564 upper bound. Hermes totals were $0.0255023 for Memcal, $0.0216122 for Holographic and $0.0223762 for Mem0. Memcal and Holographic recorded no separate provider-project model calls in this run. Local embedding computation is outside these cloud-token figures.

Every turn retains its original usage window. Mem0 requests still pending at turn completion retain unknown tokens and full reservations; their subsequent settlement is saved separately in each arm's `model-usage-after-settlement.json`. Neither costs nor token counts are divided among tools without causal evidence. Writer/reviewer usage is separate benchmark overhead, visible with the question record.

## Issues found and fixed

Holographic's recovery writer asked “Did the originally agreed time for the Devon pizza plan change?” The reviewer accepted it, but a yes/no question does not necessarily elicit the missing original time. The trace and failed recovery remain intact. Future authoring revision `specific-missing-facts-v3` adds one sentence asking for the missing value, and its reviewer rejects a yes/no question when a value is required. A separate real Spark authoring check produced “What was the originally agreed time for the Devon pizza plan?” This check exercised question authoring only; no provider was rerun and no extra recovery credit was awarded.

The final native audit initially rejected the new usage sidecars as extra dialogue files. The auditor now recognizes only sidecars corresponding to expected exposure files and still rejects unexpected sidecars. A regression verifies both cases. Re-audit passes with no errors. `summary.before-usage-audit-fix.json` and `native-audit.before-usage-audit-fix.json` preserve the original failure; the current summary records this repair, zero new provider calls and unchanged judgments.

Five separate hand-authored classifier calibration samples exercised implicit omission, explicit request failure, admitted inability, wrong value and zero recall through real Spark judging. All five decisions matched the policy. These are labeled calibration samples, not authentic provider performance evidence, in `runs/natural-recovery-policy-check-20261006`.

## Verification and spending

- Offline suite: 255 passed, two skipped. JavaScript syntax checks pass.
- Live Chromium checks cover 21 saved runs on desktop/mobile, actual Memcal recovery tokens, two event opens and 11/12 question retention points. Judge categories, reasons and exact tool arguments/results are inspectable.
- Spark clients reject upstream URL/key overrides before transport. Hermes and provider models use separate authenticated gateway projects; no upstream fallback exists in these benchmark paths. Contributor remains the selected model.
- Final gateway snapshot: $0.6591132 accounted task-wide, including $0.1081344 held for an older uncertain call. These are uncached cost bounds and reservations, not an invoice. Caps remain $5 per UTC day and $10 total. Snapshot: `runs/natural-recovery-final-budget-report.json`.

Next, exercise the value-question fix on another bounded native trial and review judge eligibility on ambiguous requests. Keep the same frozen facts when diagnosing variation. Expand toward roughly 100 additional messages only after these small checks; do not generate a large corpus or give a failing provider special ingestion treatment.
