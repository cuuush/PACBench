# Natural follow-ups and model usage

New trials use recovery protocol 5. The semantic judge evaluates the first answer and classifies each expected fact in the same model call:

- `provided`: correctly stated; no question needed.
- `incorrect`: wrong detail; fail it, with no rescue.
- `explicitly_requested`: missing a detail the user specifically asked for; fail it.
- `unavailable`: Hermes says it does not know or cannot find the detail; fail it.
- `implicit_omission`: a silent omission from a broad request; eligible for natural clarification.

A broad request such as “Tell me about the pizza plan” reasonably invites time, place and people but does not explicitly name each detail. “What time and place?” explicitly requests both. Meaning is judged by Spark, not keyword matching. Malformed or absent eligibility judgments leave the trial ungraded and cannot start a recovery turn.

The conservative rule applies to the whole answer: any wrong detail, admitted retrieval failure or missed explicitly requested fact cancels recovery for that answer. Zero correct facts also cancels recovery. Otherwise ask ONE short message for all implicitly omitted facts. Preserve the original grade; award at most half credit per recovered fact. Source/archive/message lookup and unknown operations still invalidate retention credit. Later planned evaluation questions continue normally. Existing trials retain their recorded protocols and questions; this rule never rewrites older scores.

Judge records save the per-fact category and its explanation alongside the unchanged verdicts. Audits bind these categories to the actual model response and verify that the follow-up targets exactly the eligible facts. The chat UI shows why recovery was skipped and displays the per-fact assessment with judge criteria.

## Cost evidence

Every native run uses isolated gateway projects for the Hermes model and the provider's own model calls. Mem0's OSS extraction model and Memcal's stage models use the memory-provider project; model configuration follows their documented setup. Memcal's private store `.env` makes the intended route explicit instead of inheriting a project fallback. Ordinary provider behavior is unchanged. All paid Spark inference uses the authenticated shared spending gateway; external model URLs or upstream client keys are rejected before transport. Gateway limits remain $5 per UTC day and $10 for the task.

Initial questions, catch-up turns and follow-ups save separate usage snapshots. They read only model-call metadata from the gateway's real ledger: model, request ID, project, start/finish, input/output tokens, state and accounted cost bound. No prompt or credential is read from that ledger. Hermes and provider model-call counts/tokens are shown separately in the chat. Each request is inspectable.

A reservation or missing usage means tokens remain **unknown** and the full reserved cost stays accounted. Background calls can outlive a turn; late settlements are logged separately, and a provider's final `model-usage.json` captures its run project at that moment. Do not reinterpret these records as precise causality or a final invoice. Run totals can settle after the last snapshot.

**Per-tool token cost remains unknown**: native traces record tool starts and results but do not identify which model request belongs causally to an individual tool. A Hermes model request may select multiple tools or consume several results, and a provider can run asynchronously. The UI therefore reports real per-request and per-turn tokens plus tool counts/latency, rather than splitting tokens among tools artificially. This measures the cost of the initial/recovery interaction while preserving that limit.

Spark's follow-up writer and reviewer are separate benchmark overhead. Their model identity, reported token usage and private call-record path are stored in the authored-question record. A reused question is marked reused; its original usage is not treated as a fresh charge for each arm.
