# Retained-memory-only recall

New trials use `retained-memory-only-v2`. Hermes uses the selected provider's
normal plugin and prefetch, with an exact subset of its shipped tools.

Dedicated archive search, original-source opening, conversation history and
message activity readers are excluded. For Memcal this blocks
`memcal_search_archive`, `memcal_open_source`, `memcal_conversation` and
`memcal_activity`. Retained event/page opening and memory search remain available.
Their native payloads, including attached citations, are unchanged. This condition
measures retained memory and its attached evidence, rather than strictly facts-only
storage. OpenViking's mixed `viking_read` is excluded entirely from Hermes's
exposed schemas, including memory and generated-summary reads. Hermes uses the
remaining shipped OpenViking tools and normal provider context. It receives no
instruction to work around a reader that it can see but cannot use. URI guards
remain defense in depth for unexpected calls; they do not grant tool exposure.
Honcho's `honcho_search` and `honcho_context` return raw message excerpts and
are excluded; native profile, conclusions and dialectic reasoning remain.
Provider-internal retrieval during dialectic reasoning is native provider work,
with gateway cost recorded where observable; it is not an exposed archive tool.

Only exact allowed names are offered to Hermes. Existing sequential, concurrent
and registry guards reject excluded tools before execution. Built-in memory,
session search, generic discovery, shell, filesystem and browser remain disabled.
Any disallowed attempt fails the check even if the final answer is correct.
Independent conversations continue. Missing or wrong answers using allowed tools
receive their normal fact judgments and natural omission recovery rules.

`tool-policy.json` records allowed tools, blocked source tools and argument rules.
`context.jsonl.tools.jsonl` records actual schemas exposed each turn. The native
audit verifies these against the provider's discovered schemas, actual turn argument
rules and saved calls.
Superseded development runs are removed.

This is an agent tool boundary, not OS isolation. Native provider internals may
read files, run subprocesses and call models. The gateway still meters inference;
unknown nested work is not reported as free.
