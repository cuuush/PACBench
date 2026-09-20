# Gates for `comparative_claim_valid = True` (contract co-author)

Thirty lives, proper splits, nonempty held-out, and repetitions are
necessary but NOT sufficient. Before a genuine comparative quality claim
gets the flag, all of the following hold — checked, not asserted:

1. Corpus and rubric version frozen before the scored comparison.
2. Provider recipes and relevant model configurations frozen.
3. Genuinely held-out lives present (nonempty held-out split).
4. Story-family leakage checks passed (no close variants across splits).
5. At least one **real external memory provider** runs — a lexical or
   prompt baseline does not satisfy this; it is a diagnostic (§45.5).
6. Identical public evidence delivered to both systems.
7. Provider isolation and the Hermes memory boundary verified.
8. Simulated time verified across both systems.
9. Hidden answer-key isolation verified.
10. Invalid or incomplete runs preserved, never silently discarded.
11. Semantic residuals handled by a calibrated judge or explicitly
    human-adjudicated under a frozen procedure (no ceremonial judge runs:
    if a corpus has no residuals, what matters is that every residual has
    a frozen, validated adjudication path).
12. Retrieval/tool traces captured sufficiently to audit answers.
13. Stochastic repetitions run according to the declared experiment recipe.
14. No held-out results used to tune Memcal or provider configuration
    before the final run (exposed lives move to regression coverage, §39).

Token/pricing accounting is NOT a gate for accuracy comparisons: the
contract allows provider-side usage to stay unknown, and an opaque hosted
provider must not block a legitimate product-quality claim. Validity is
therefore split:

- `comparative_claim_valid` — factual/product-quality comparisons.
- `efficiency_claim_valid` — latency, token, and cost comparisons (needs
  measured, comparable telemetry on both sides).

Current status: gate 5 is open (lexical diagnostic only), gate 3 is open
(held-out empty). Milestone: **Memcal + lexical diagnostic: complete;
Memcal + real external memory provider: pending.**
