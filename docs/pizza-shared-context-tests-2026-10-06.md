# Shared-context Pizza Day — October 6, 2026

The new frozen scenario contains 49 model-authored, independently verified messages across five conversations over September 8–10. Both trips are on Friday September 11: Maya at 1pm at Rosa's on Oak Street, Devon at 7pm at Mario's on Pine Street. Maya's original time was noon; Devon's original plan was Luigi's on 5th at 6pm. The scenario runs in America/New_York.

Every available arm received those identical messages in five native Hermes turns sharing one ingestion session. Evaluation started in a separate fresh session, then kept that session for both questions and recovery. No facts were seeded into stores and no retrieval was substituted.

1. At 9am: “What's the deal with getting pizza today?” Twelve expected facts cover both people, restaurants, streets, dates, times and current confirmation.
2. At 9:02am: “For those two pizza plans, which one comes first, and what changed from the original plans?” Four expected facts cover order and the original times/venue. The judge receives the prior public conversation to resolve “those”, but credits facts conveyed in this answer.

| Native provider | Today, first answer | Order/changes, first answer | Recovery | Retention points across both questions |
|---|---:|---:|---|---:|
| Memcal | 12/12 | 4/4 | None needed | 0/16: first answer opened original sources |
| Holographic | 12/12 | 3/4 | One message; original 6pm not recovered | 15/16 |
| Local OSS Mem0 | 12/12 | 3/4 | One message; original 6pm not recovered | 15/16 |

Memcal used two event opens and two `memcal_open_source` calls for the first answer, then answered the second question without tools. Semantic correctness remains 16/16, but original-source access fails retention and contaminates the later shared-context answer. This is a real provider behavior diagnosis, not a clock regression: the direct Eastern clock fix remains in Memcal commit `523e76b`.

Holographic's first question used one `fact_store` probe and two feedback calls; its second used two probes and two feedback calls. Recovery used one probe plus one feedback call, 8.57 seconds. It claimed the time stayed at 7pm. Mem0 used two searches on the first question and no tools on the second; recovery used one search, 18.82 seconds, and also claimed the original time was 7pm. Both initial second-answer judgments missed the original 6pm; both recovery judgments marked that fact misrepresented. Initial grades are preserved.

Holographic's recovery question was broad (“What changed from the original Devon pizza plan?”), although its reviewer accepted it. Treat this as a prompting weakness when interpreting the recovery result. Mem0's recovery specifically asked for the original agreed time. The separate live Spark regression rejected that broad question and approved “What was originally agreed for the Devon pizza time, and did it change?” under a short specificity rule; it never adds a second provider retry or rewrites the recorded trial. New policies bind this wording revision as `specific-missing-facts-v2`; earlier policies retain their previous author instructions.

Private artifacts:

- `runs/pizza-shared-context-prepared-20261006`: preparation audit passes; frozen bundle hash `a4a0773c4fcd7940df89813a0633cf0cd8313c3f05d4aacf45b44f8259f734d5`.
- `runs/pizza-shared-context-native-20261006`: Memcal and Mem0 completed. This launch also selected unconfigured Honcho; its explicit blocker makes the combined run incomplete and its root audit fail. Both completed arms' shared-context audits pass. Preserve the original failed root audit. `post-fix-native-audit.json` now continues auditing later arms after the blocker, rather than aborting on missing Honcho trace files.
- `runs/pizza-shared-context-holographic-native-20261006`: completed; full native audit passes.
- `runs/pizza-shared-context-recheck-prepared-20261006`: changed second prompt to “Which of those pizza meetups happens first, and how did each plan change?” against the exact same frozen dialogue, without regeneration. New contracts and preparation audit pass; this condition was not run through provider arms.
- `runs/shared-context-final-budget-report.json`: cumulative usage-derived upper bound at snapshot $0.5742805, including $0.1081344 still reserved from earlier unknown usage. Never refund this hold without usage evidence. Final snapshot after the isolated specificity check: $0.5751122, including $0.1081344 reserved; see `runs/shared-context-specificity-final-budget-report.json`.

The web UI indexes all native/prepared runs and shows both check contracts, per-answer verdicts and evidence, exact judge prompts/responses/model identity, recovery answers, tools and retention accounting. Live Chromium checks found no page errors: the combined run shows 16 planned facts, five evaluation/recovery messages, 33 verdict rows and seven tool cards; Holographic shows 16 planned facts, three messages, 17 verdict rows and nine tool cards. Each shows the missed initial time and wrong recovery time separately. Source lookup failure is visible beside Memcal's correct answers.

Offline validation: 240 passed, two skipped; JavaScript syntax and artifact builder pass. Synthetic integrity tests verify session continuity, exact prompts/criteria, source contamination across shared turns, token deltas, tampering rejection, follow-up subsets and private-artifact exclusion. Real native traces establish provider behavior; unit tests do not.

Next bounded trial: increase to roughly 100 real messages, with one cancellation or tentative replacement and a question that distinguishes current commitments from abandoned plans. Keep two or three shared questions and verify elicitation before ingestion. Test that stage repeatedly before growing toward 1,000 messages. Provider setup blockers remain blockers until a normal documented isolated backend is configured; they do not count as tested providers.
