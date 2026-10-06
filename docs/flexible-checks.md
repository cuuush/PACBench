# Flexible questions and shared conversations

Add `checks` to any bounded author spec. Each check has an `id`, your exact `prompt`, a list of plain-language expected `facts`, and an optional `at` clock. No event slots or fixed topic schema are required. Up to eight questions and fifty facts per question are accepted; start small.

```json
{
  "evaluation_context": "shared",
  "exposure_context": "shared",
  "exposure_delivery": "scenes",
  "checks": [
    {"id": "plans", "prompt": "What's the deal with getting pizza today?",
     "facts": ["The pizza outing with Maya is Friday September 11 at 1pm."],
     "at": "2026-09-11T09:00:00-04:00"},
    {"id": "changes", "prompt": "What changed from the original plan?",
     "facts": ["The original outing with Maya was at noon."],
     "at": "2026-09-11T09:02:00-04:00"}
  ]
}
```

The example fields above belong in a full scene spec; `examples/pizza_shared_context.json` is runnable. Dialogue is generated and verified first. Spark then verifies that every expected fact has evidence and the supplied prompt reasonably invites it. An unsupported fact or unfair question stops preparation before a provider runs. The user's prompt is preserved, never rewritten to improve a provider's score. Every check freezes its own claims, citations, prompt, clock and recovery policy.

Prepare with the existing semantic authoring command and `--prepare-only`, then run `python -m pacbench.native_pilot --prepared runs/<prepared> --output runs/<native> --max-messages 60 --providers memcal holographic mem0`. This path uses native Hermes tools and ordinary memory writes, with the same frozen messages for every provider.

`exposure_delivery: scenes` delivers one scene per Hermes turn. `exposure_context: shared` resumes one real ingestion session across scenes and days. `evaluation_context: shared` resumes one separate evaluation session across the questions and any recovery attempts. Its first question always starts fresh after ingestion; session search stays disabled. Either context can be `isolated` for comparison. These are Hermes-plus-provider tests: normal built-in memory remains enabled.

Each question is graded separately. Prior public questions and answers let the judge resolve references such as “those plans”; facts only present in an earlier answer do not earn credit in the latest answer. One combined recovery question asks for all facts missed or wrong at that checkpoint. Initial grades remain unchanged, with half recovery credit tracked separately. Source/archive/message lookup fails retention. A source lookup or unknown operation contaminates subsequent answers in that shared evaluation context; starting an isolated context clears that inherited path. Tool calls, arguments, results, session continuity, latency, token deltas and gateway cost windows are saved and audited.

To try a new idea against existing verified dialogue, create a small JSON file containing `checks` and optional clocks/context modes, then run:

```sh
python -m pacbench.evaluation --prepared runs/<prepared> --spec new-checks.json --output runs/<new-prepared>
```

This copies the unchanged frozen bundle and its authoring evidence, records provenance, and reviews new criteria. It does not regenerate dialogue or alter previous trials. Run the new prepared directory through `native_pilot` to get an authentic new comparison. Changing a question is a new condition, never a regrade of an old answer.

Start `python serve_explorer.py`, select the run, and open **Criteria & judges**. Planned checks appear even before provider answers exist. Each answer shows Passed, Failed · missed, or Failed · wrong for its exact expected facts, plus evidence links, the exact judge prompt/response and model identity. Initial and recovery answers remain separate. Provider pages expose ingestion turns, session IDs, memory briefs and actual tool arguments/results. The browser index includes native runs and repetitions; credentials and encrypted reasoning remain private.
