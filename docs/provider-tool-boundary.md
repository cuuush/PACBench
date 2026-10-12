# Provider tools and source penalties

New trials use `provider-memory-with-source-penalty-v3`. Hermes receives only the selected provider's exact shipped tools. Native message search, archive search and source opening are permitted, including Honcho's message readers, Memcal's archive readers and OpenViking's mixed `viking_read`.

Answer correctness and retention are separate. A correct source-assisted answer keeps its semantic grade, but the turn earns zero retention credit. That source path also carries into dependent turns sharing its context. Log actual calls, arguments/results, latency, observed model requests and tokens. A penalty is not a dollar price; unknown costs remain unknown. Background source work without proven query attribution remains uncredited pending review.

Memcal's normal `memcal_open` is retained event detail and receives normal credit. Native memory pages, generated summaries and attached citations remain unchanged. Apply the same rule to every provider. No generic messaging connector, filesystem, shell, browser, discovery/dispatch, built-in memory or Hermes session-search tools are added.

`tool-policy.json` freezes the grant and penalty. `context.jsonl.tools.jsonl` records actual offered schemas. Dispatch guards reject all tools outside the exact native grant before execution; the audit checks schemas, calls and native saved prompt isolation. A prohibited-tool attempt still invalidates the check.

The current published diagnostic used the earlier `retained-memory-only-v2` condition, which hid dedicated source readers and `viking_read`. Its saved permissions and grades are unchanged; it is not a live test of the new permission policy. The auditor recognizes that frozen receipt.

This is a tool boundary, not OS isolation. Native provider internals can read their stores, run subprocesses and call models. All inference remains metered through the gateway; nested work is not assumed free.
