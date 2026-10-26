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

This is a tool boundary, not OS isolation. Native provider internals can read their stores, run subprocesses and call models. All inference remains metered through the gateway; nested work is not assumed free.

For Hindsight 1.1.0, read its native `/llm-requests` trace API. Reflection's
internal `recall` may return `chunks.*.chunk_text` containing original messages;
the outer tool returning synthesized text does not make that a retained-facts
path. Log native arguments/results and apply the source penalty when those
chunks reach the reflection model. Observe the normal trace-writer buffer;
missing/truncated traces stay unclassified. Work completed after every Hermes
request input cannot supply that answer. Keep native token telemetry separate
from authoritative gateway costs, and omit model prompts/reasoning from public
trace records.
