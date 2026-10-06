# Small native follow-up tests — 2026-10-06

All runs reuse the same frozen 25-message Contributor-authored Pizza Day input
(`62153788eaee6207430d5726c97967a09f1d653530e865435bb3252f0b3e2b21`).
Each repetition uses fresh native Hermes/provider homes and recommended setups.
These are Hermes-plus-provider diagnostics, not calibrated provider rankings.
No facts were seeded and no provider prompts were patched.

## Current minimal isolated authoring (protocol 3)

| Saved run | Provider/repetition | Initial /5 | With recovery /5 | Follow-up tool operations |
| --- | --- | ---: | ---: | --- |
| `pizza-native-simple-combined-followup-20261006` | Memcal | 4 | 4.5 | One `memcal_open(E1)` |
| same | Holographic | 5 | 5 | None; no follow-up |
| same | Mem0 | 5 | 5 | None; no follow-up |
| `pizza-native-simple-combined-mem0-repeat-20261006` | Mem0, repetition 1 | 0 | 0 | Three `mem0_search` calls |
| same | Mem0, repetition 2 | 0 | 0 | Three `mem0_search` calls |
| `pizza-native-final-combined-mem0-20261006` | Mem0 | 5 | 5 | None; no follow-up |

Memcal's single message was “Where is the restaurant for our pizza plan
located?” Opening retained event E1 recovered 5th Street. The turn took 15.63s;
its driver gateway window recorded three model calls and a $0.0031036 uncached
upper bound. No archive/message-source access occurred.

The two failed Mem0 repetitions each used one combined message targeting all
five missed facts, reusing the same reviewed question: “Can you remind me who's
joining you, which restaurant, where it's located, and what day and time you
planned?” Both returned no recovered facts after three actual memory searches.
The audit caught ambiguous pronouns in this question. The final author/reviewer
prompt adds only “from the user to their assistant.” The final fresh trial had
complete initial recall, so that final wording did not produce a live follow-up;
combined-message behavior was exercised by the preceding trials and unit tests.
Saved old questions were preserved.

Mem0's successful minimal trial made two explicit native `mem0_add` calls during
exposure. Failures with automatic extraction alone and empty later searches
remain preserved. This variability does not establish reliable persistence or
prove an extraction-quality cause. Investigate ordinary session lifecycle next,
without forced writes or provider-specific lifetime adjustments.

## Earlier policies remain separate

`pizza-native-followup-repeated-20261006` ran two fresh repetitions per provider
under the earlier per-fact protocol. Memcal scored 4 then 4.5, and 5 without
follow-up; Holographic scored 5 twice; Mem0 scored 0 twice and failed all ten
individual recovery questions. Those Mem0 recovery turns used 17 searches total.

`pizza-native-combined-followup-20261006` ran three fresh arms with the earlier
component-based combined protocol: Memcal 4 then 4.5 with one event open,
Holographic 5 without follow-up, Mem0 0 then 0 with one message covering all five
facts. It is preserved separately from the shorter current authoring protocol.

The repeated Memcal 5/5 answer also volunteered “still a maybe.” Exposure omitted
`status` in its native add call, allowing Memcal's default possible-plan status,
despite clear commitment in the source conversation. Confirmation status was
outside the original five claims: this is a real unscored error, not a reason to
silently change old grades. Add it explicitly to a new frozen test contract.

## Verification and cost

All five saved run roots above pass native trace-integrity audits. That verifies
input, clock, session, question, tool and score provenance; it does not certify
provider quality. Source fallback gets zero recovery credit, including blocked
attempts, but no live message connector was configured or simulated here.

Offline suite: **232 passed, two skipped**. Complete tool arguments/results,
operation counts, latency, reported token deltas and gateway windows remain in
private `runs/<run>/repeat-<n>/<provider>/followups/recovery.json` and
`followups/combined/turn.json`. Missing usage stays unknown.

Final cumulative gateway accounting: **$0.1887835**, no reservations outstanding,
with $5/UTC-day and $10 task enforcement. This is an uncached-price usage upper
bound, not an invoice total. Snapshot:
`runs/pizza-native-final-combined-mem0-20261006/final-budget-report.json`.

Next: explicitly test commitment status and ordinary Mem0 session lifecycle at
Pizza Day scale before advancing to the checked-in +100-message stage. Keep
+1000 deferred while these small-case issues remain unresolved.
