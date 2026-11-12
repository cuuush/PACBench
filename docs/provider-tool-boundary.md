# Provider tools and source penalties

New trials use `provider-memory-with-source-penalty-v3`. Hermes receives only the selected provider's exact shipped tools. Native message search, archive search and source opening are permitted, including Honcho's message readers, Memcal's archive readers and OpenViking's mixed `viking_read`.

Answer correctness and retention are separate. A correct source-assisted answer keeps its semantic grade, but the turn earns zero retention credit. That source path also carries into dependent turns sharing its context. Log actual calls, arguments/results, latency, observed model requests and tokens. A penalty is not a dollar price; unknown costs remain unknown. Background source work without proven query attribution remains uncredited pending review.

Memcal's normal `memcal_open` is retained event detail and receives normal credit. Native memory pages, generated summaries and attached citations remain unchanged. Apply the same rule to every provider. No generic messaging connector, filesystem, shell, browser, discovery/dispatch, built-in memory or Hermes session-search tools are added.

For OpenViking, requesting `overview` alone does not prove a generated summary:
the native reader can fall back to original file content. A complete successful
result matching every requested URI and summary level, with no native fallback
or error, establishes summary access. Full reads, fallback receipts and incomplete
captures remain source-assisted. Preserve the actual result as evidence.

`tool-policy.json` freezes the grant and penalty. `context.jsonl.tools.jsonl` records actual offered schemas. Dispatch guards reject all tools outside the exact native grant before execution; the audit checks schemas, calls and native saved prompt isolation. A prohibited-tool attempt still invalidates the check.

The current Pizza Month cohort exercises v3. Audits validate each saved trial's frozen permissions; do not relabel an older policy or compare unlike tool grants as matched trials.

New trials use `native-question-v2`: Hermes receives the question verbatim,
without a PACBench instruction prefix or appended system prompt. Its stock
Hermes prompt and native provider prompt hooks remain enabled. The simulated
clock still supplies the date rendered by Hermes's native prompt and provider
clock interfaces; it adds no instruction text. Recall starts in an empty
directory outside Git projects, so repository instructions are not loaded.

Earlier trials used a question prefix, `[Simulated now: … Treat this as the
current date and time for everything in this turn.]`. Those results retain that
historical condition and do not establish performance with unmodified
questions. Removing the prefix requires a fresh recall trial before comparing
the two conditions; it does not retroactively change saved answers or grades.
The selected public month/canary evidence contains 51 prefixed Hermes turns
(15 each for Memcal, Mem0 and Hindsight, and 6 for Honcho). Their isolation
receipts retain prompt hashes, but the full saved native system prompts and
private session databases are unavailable in this checkout. Those receipts
establish the recorded project-context check, not a complete historical
byte-for-byte prompt provenance audit. The other integrated providers have no
recall turns in these selected trials.

This is a tool boundary, not OS isolation. Native provider internals can read their stores, run subprocesses and call models. All inference remains metered through the gateway; nested work is not assumed free.

For Hindsight 1.1.0, read its native `/llm-requests` trace API. Under historical
`source-provenance-v1`, any returned original chunk incurs the source penalty.
Under `source-provenance-v2`, raw-only recall incurs the penalty, while chunks
explicitly linked from returned retained records are attached citations and keep
normal credit. Mixed results without proven links remain uncredited pending
review; co-occurrence alone does not establish a link. Explicit expansion is
source access. Log native arguments and results. Observe the normal trace-writer buffer;
missing/truncated traces stay unclassified. Work completed after every Hermes
request input cannot supply that answer. Keep native token telemetry separate
from authoritative gateway costs, and omit model prompts/reasoning from public
trace records.

New Cognee/MemOS trials use `source-provenance-v3`: the same credit rules also
apply to automatic context injection, even when the provider exposes no tools
or Hermes makes no tool call. A run-owned native condition and SHA-256 bind the
provenance classification to the captured context. Confirmed source content
receives zero retention credit; unverified native hybrid passages remain
uncredited pending review. This adds evidence tracking, not claims or weights.
Older v1/v2 trials keep their original recorded conditions and grades.
