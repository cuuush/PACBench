# Current Pizza Day test

The current run is `runs/pizza-clean-context-mem0-20261007`. Its preparation evidence is inside `preparation/`; the input has 25 verified messages and five facts. Both are private generated artifacts, outside Git. Old trials and caches were deleted during development cleanup.

Mem0 OSS ingested the raw daily transcript with its native extraction, using local Qdrant and Ollama embeddings. A fresh Hermes session called `mem0_search` once and answered:

> You and Devon are getting pizza at Luigi's on 5th tonight — Friday at 7pm. He's meeting you there.

The judge marked all five facts correct: companion, restaurant, street, date and time. Retention was 5/5, with no follow-up or source fallback. This is one small diagnostic, not a comparative result.

Recall launches from an empty temporary directory outside the repository. The audit reads Hermes's actual saved system prompt and working directory, verifying that benchmark project instructions were not loaded. Provider context and normal memory hooks remain enabled; built-in memory and non-provider tools are disabled.

```sh
python -m pacbench.preparation_audit runs/pizza-clean-context-mem0-20261007/preparation
python -m pacbench.native_audit runs/pizza-clean-context-mem0-20261007
python scripts/build_explorer_data.py
python serve_explorer.py
```

Both integrity audits pass. The frozen input hash is `62153788eaee6207430d5726c97967a09f1d653530e865435bb3252f0b3e2b21`. Task spending was $3.2266252 as an accounted upper bound including reservations, not an invoice. Cleanup and offline verification need no new paid calls.

The next development case is [Pizza Week](examples/pizza_week.json): two outings, DM/group updates, an ambiguous cancellation, and fresh-session persistence checks. Its weekday author format is implemented and covered offline; its live authoring was paused during the schema revision, so no new provider accuracy is asserted. Start with low noise, inspect small-case failures, then increase message counts.
