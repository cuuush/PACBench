# Retained-memory-only recall

New trials use `retained-memory-only-v1`. Hermes uses the selected provider's
normal plugin and prefetch, with an exact subset of its shipped tools.

Dedicated archive search, original-source opening, conversation history and
message activity readers are excluded. For Memcal this blocks
`memcal_search_archive`, `memcal_open_source`, `memcal_conversation` and
`memcal_activity`. Retained event/page opening and memory search remain available.
Their native payloads, including attached citations, are unchanged. This condition
measures retained memory and its attached evidence, rather than strictly facts-only
storage. A mixed memory/resource reader such as `viking_read` is excluded wholesale
until an argument-aware boundary can safely permit only memory reads.

Only exact allowed names are offered to Hermes. Existing sequential, concurrent
and registry guards reject excluded tools before execution. Built-in memory,
session search, generic discovery, shell, filesystem and browser remain disabled.
Any disallowed attempt fails the check even if the final answer is correct.
Independent conversations continue. Missing or wrong answers using allowed tools
receive their normal fact judgments and natural omission recovery rules.

`tool-policy.json` records allowed and blocked source tools.
`context.jsonl.tools.jsonl` records actual schemas exposed each turn. The native
audit verifies these against the provider's discovered schemas and saved calls.
Older comparison runs retain their recorded `provider-only-v1` condition.

This is an agent tool boundary, not OS isolation. Native provider internals may
read files, run subprocesses and call models. The gateway still meters inference;
unknown nested work is not reported as free.
