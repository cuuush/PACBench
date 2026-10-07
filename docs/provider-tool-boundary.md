# Provider-only recall

New native trials use `provider-only-v1`. Hermes remains the evaluation agent,
using the provider's normal plugin, prefetch and tools. Switching agent runtimes
would create a different benchmark condition.

The runner discovers tool names from the selected provider's native schemas.
Only those exact names are exposed to Hermes. Sequential, concurrent and registry
dispatch block other tools before execution. Built-in memory, session search,
generic tool discovery, shell, filesystem and browser tools are disabled. Empty
allowlists fail closed; prefix wildcards are not supported in this condition.

A disallowed attempt invalidates the trial even if the final answer is correct.
The artifact retains the attempted call and the block. Missing/wrong answers made
using only allowed tools receive their ordinary fact judgments. Reading original
messages through a provider-shipped tool remains possible and visible, but fails
retention under the existing source-fallback rule.

`tool-policy.json` records the exact tool grant. `context.jsonl.tools.jsonl`
records the schemas exposed each turn. The native audit independently checks both
and every saved initial/follow-up call. Development results are disposable;
older trials impose no compatibility requirement. Regenerate results when the
implementation or test contract changes.

This is an agent tool boundary, not OS isolation. Provider implementations may use
files, subprocesses and model calls internally as part of normal operation. Their
internal behavior must be recorded when observable; unknown nested work is not
reported as free or as verified retention. Docker is not required for this
benchmark rule. If protection against malicious provider code becomes necessary,
add an OS sandbox as a separate, explicitly recorded condition.
