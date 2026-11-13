# Natural recovery and cost

The initial semantic judge classifies each fact as provided, silently omitted, explicitly requested but absent, incorrect, or unavailable after a failed lookup. Any wrong detail, admitted lookup failure or missing explicit request cancels recovery for that answer. Zero correct facts also gets no recovery. Otherwise, ask once for all silent implicit omissions; all-correct answers receive none.

Freeze the eligibility policy with the contract before testing. When needed, Spark receives the original question, eligible missing facts and the actual answer as event context in a stateless call. Hidden criteria are not "known" context: their descriptions may contain other omitted values. It writes one short message asking for missing values, preserving current versus historical meaning, without answers or tool instructions. One independent brief review checks coverage, event scope and leakage. Bind wording to the contract, missing fact set and authoring revision, then reuse it across matching provider arms. Save the original answer used for authoring. No up-front per-fact questions are generated.

Hermes continues its actual recall session at the same fictional clock. A failed follow-up or malformed judge is not retried. The original grade remains unchanged. Separate diagnostic retention points give one point per initially correct fact and half a point per recovered fact; wrong or ungraded recovery gives zero. These points are uncalibrated and establish no ranking.

Opening retained event/page detail may recover a fact. Native archive, original-source or message lookup is permitted in new trials, but earns no retention credit even if the answer is correct. Outside messaging connectors remain blocked. Attempted source access counts even if blocked. Initial source fallback disqualifies the answer's retention; follow-up fallback disqualifies all targeted facts. Unknown operations require review and receive no retention credit. Normal evidence returned with a retained event does not automatically mean source fallback.

`followups/recovery.json` records eligibility, skip reason, initial effort, the single attempt and score. `followups/combined/` stores the question plan, actual turn, judgment and grade. Log arguments/results, top-level and nested operations, failures, repeats, latency and reported session token deltas. Saved native session data supplies full tool outputs, including resumed turns.

Gateway windows separate agent inference, provider inference and benchmark overhead. Background work may settle later. Missing/reset token counters stay unknown; per-tool token prices and causal attribution are not invented. Native provider internal traces are shown separately when available, including prefetch. Audits verify eligibility, one-message coverage, question hashes, continuity, clocks, tool/token metrics and preserved initial penalties.

Direct OpenRouter Chat Completions use `complete-chat-stream-v1`: streamed replies
must contain a stable generation ID, a successful `stop` finish, `[DONE]`, answer
text, and usable token counts and cost. The direct ledger must settle before a
reply is accepted. JSON judges request JSON object mode; semantic validation still
requires every expected verdict and field. Exact SSE bytes are retained privately
with public hashes and completion receipts. Empty HTTP 200 bodies, interrupted
streams and incomplete usage remain failures without automatic paid replay.
A corrective judgment of a saved answer is a separately versioned overlay bound
to the original turn, frozen criteria and failed request; it never replaces the
original ungraded result or releases its uncertain charge.

Initial pass/fail/ungraded counts are disjoint. If recovery authoring or review fails, preserve the completed initial verdict and report the recovery error separately. A question asking what happened to a specific object is valid; asking about a boolean status does not assert that status. Current scope-review artifacts flag overly literal criteria and vague event references without changing the original grades. Future contracts must identify the intended event rather than rely on author-only labels such as “first outing.”
