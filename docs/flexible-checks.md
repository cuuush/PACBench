# Questions and shared conversations

Add `checks` to a bounded scene spec. Each check contains an `id`, the exact public `prompt`, expected plain-language `facts`, and optional `at` clock. Up to eight questions and fifty facts per question are accepted; begin small.

```json
{
  "evaluation_context": "shared",
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

These fields belong in a complete scene spec; see `examples/pizza_shared_context.json`. Each check freezes its own verified facts, citations, prompt, clock and recovery policy. The question is reviewed for answer leakage and whether it should elicit its facts. Unsupported assertions or unfair questions block preparation. Public wording is preserved.

`evaluation_context: shared` continues one actual evaluation session across questions and recovery. Its first question starts fresh after native source ingestion. `isolated` starts a new session per planned check. Built-in memory, session search and non-provider tools remain disabled. All source batches finish first; earlier checkpoints against later memory state are rejected.

Each answer is judged independently. Prior public exchanges resolve references, but facts appearing only in an earlier answer earn no credit in the current one. Source fallback or unknown tool paths in shared history also affect later retention. [Recovery](followups.md) preserves initial penalties and allows at most one combined message for eligible implicit omissions per answer.

New ideas can use existing verified dialogue without regenerating it:

```sh
python -m pacbench.evaluation --prepared runs/<prepared> --spec new-checks.json --output runs/<new-prepared>
python -m pacbench.native_pilot --prepared runs/<new-prepared> --providers mem0 --output runs/<current>
```

The recheck spec may contain only `checks`, `question_at` and `evaluation_context`. The explorer's **Criteria & judges** view shows planned criteria and separate initial/follow-up verdicts, cited source messages, exact judge prompt/response/model, actual tools and measured effort.
