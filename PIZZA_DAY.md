# Run Pizza Day end to end

Pizza Day generates two chats: a confirmed pizza plan with Devon and an unrelated, tentative ducks outing. The author supplies five required pizza facts in [`examples/pizza_day.json`](examples/pizza_day.json). PACBench verifies the rendered conversation, freezes its evidence and claim contract, ingests the same generated messages from two threads in one daily batch into Memcal and Hindsight, asks Hermes the same question for each, and judges each answer fact by fact. The writer chooses the message count; the 2026-09-24 native-path run generated 25.

## Prerequisites

1. Work from the repository root. Keep the existing `.env` private; it needs `MODEL` and `META` as shown in `.env.example`. `MODEL` must be an exact model ID served by Meta. PACBench creates isolated Hermes homes for this run and does not read a personal Hermes profile.
2. The sibling Memcal checkout must be at `../memcal`, or set `MEMCAL_SRC` to its path. The runner's Python must have Memcal's declared `pytypedstream` dependency (`python3 -c 'import typedstream'`); install Memcal's package dependencies if that check fails. The isolated Hermes executable must exist at `vendor/venv-hermes-isolated/bin/hermes`; its Python environment must have the MCP SDK. Hindsight starts its own scratch database and local MCP server.
3. Allow outbound access to Meta's API and local loopback connections for the provider tools. On this macOS Python installation, set the CA bundle shown below. The normal Python certificate path failed TLS verification during the 2026-09-23 run; certifi's bundle succeeded without disabling verification.

## Run

```sh
export SSL_CERT_FILE=$(/usr/local/bin/python3 -c 'import certifi; print(certifi.where())')
/usr/local/bin/python3 -m pacbench examples/pizza_day.json
```

The command prints a new `runs/semantic-<UTC timestamp>/` directory and exits successfully only when both provider arms finish validly. To choose a path, add `--output runs/my-pizza-run` with a path that does not already exist. Each invocation freezes its conversation; identical model requests may reuse audited responses from `runs/model-cache/`. The provider homes and ingestion are fresh. For a preparation without provider calls, add `--prepare-only`; that is **not** an end-to-end run.

The model and providers can incur API charges. Keep the run bounded to one invocation and inspect its saved `model-calls/` and provider usage records before repeating it. The Meta response may omit `cost_usd`; an absent value means the charge is unavailable, not zero.

## Check the result

Open `summary.json` in the printed run directory. It should say `state: complete` with both `memcal` and `hindsight` results marked `valid`. Then check:

- `bundle.json` and `bundle-provenance.json`: the messages given to both providers and their content hash. New bundles use `messages`; historical bundles retain their original `observations` key and hash.
- `scenes.json`, `scene-verifications/`, and `contract.json`: generated dialogue, verifier citations for the five authored facts, the frozen question and claim citations. These must exist before either provider arm.
- `<provider>/ingestion.json` and `maintenance.json`: the submitted daily batch, provider acknowledgment, simulated maintenance time, and readiness.
- `<provider>/preflight.json` and `status.json`: retrieval capability separately from whether Hermes called a memory tool. Hindsight must show SDK, connection, allowed namespaced recall, and a successful read-only recall against its bank. Memcal must show its installed plugin and fictional clock pin.
- `<provider>/turn.json`: the exact question, prefetch context if any, tool calls with arguments, tool results, and Hermes answer. Memcal's brief must reflect the fictional date. Hindsight's recall result should be visible when Hermes calls it.
- `<provider>/judgment.json` and `grade.json`: the saved judge prompt and response, followed by a separate verdict for each claim. A missing or malformed judgment leaves that arm ungraded.
- `run-audit.json`: a separate check of bundle and contract hashes, cited source IDs, exactly-once daily ingestion, matched input, fictional clocks, retrieval access, and saved judgments. It must say `state: pass` for a complete run. Recheck a saved run without changing its files using `python3 -m pacbench.run_audit runs/<run-id>`.

For a readable view, run:

```sh
/usr/local/bin/python3 serve_explorer.py
```

Select the new Pizza Day run. The Story page shows the authored facts, rendered chats, verifier decisions, and frozen contract. The Memcal and Hindsight pages show the question, memory context or recall calls, answer, and clickable claim verdicts. The Saved files page shows the full records. `scripts/build_explorer_data.py` verifies each saved semantic bundle hash when it builds the browser data.

## Validation after code changes

```sh
/usr/local/bin/python3 -m pytest -q
node --check explorer/dashboard.js
/usr/local/bin/python3 scripts/build_explorer_data.py
git diff --check
```

The MCP seam test requires loopback access. A green mocked test suite does not prove that either provider was reachable in the live run; inspect the new preflight and turn artifacts.

## Daily iMessage ingestion

The current Pizza Day spec uses iMessage for both chats. PACBench writes the new messages into a scratch `chat.db` with the tables read by Memcal's native iMessage source, then calls that reader once for the daily batch. It archives each message once and queues it under its conversation: the direct Devon chat and the group Ducks chat. The owner has no incoming handle on sent messages, so the reader uses the chat's sole other handle for direct-chat routing. Hindsight gets the same public sender, time, text, channel, thread, and group information in one `retain_batch_async` call containing two conversation packets. Neither adapter sees the authored facts.

The scratch database covers the message and chat fields Memcal reads for this benchmark. It does not simulate attachments, edits, read receipts, Contacts, or macOS permissions. The synthetic roster links public speaker names to handles so this test isolates message ingestion from contact discovery. Saved older runs still show their original adapter path and bundle; they are not rewritten.

`examples/pizza_day_correction.json` tests an earlier 6pm proposal replaced by 7pm in one chat window. `examples/pizza_day_two_days.json` tests an agreed 6pm plan on one fictional day and its move to 7pm the next day. For the two-day case, the runner sends only that day's new messages to each adapter, performs maintenance after each day, and saves each batch and pass under `daily_batches` and `daily_passes`. The top-level `ack` and `readiness` remain the final day's summaries for older explorer views.

## Seven-day checkpoints

[`examples/pizza_day_week.json`](examples/pizza_day_week.json) groups chats under Monday through Sunday. The author writes a chat name, human time, scene, and short concrete facts; PACBench derives the date, thread, and scene ID. Tuesday's Devon chat changes only the pizza time and establishes a separate bowling plan in the same thread. Later ordinary chat does not erase either plan. The Ducks chat remains separate.

At the end of each fictional day, PACBench maps every authored fact visible so far to one evidence-cited claim and a state: current, superseded, or historical. A separate model reviews every mapping. It freezes all seven daily contracts before either provider runs. Each provider ingests that day's new messages once, performs maintenance at 7 a.m. the next day, and answers the same broad question at 8 a.m. A day with no messages still gets maintenance and a question, without replaying earlier messages. An old time may be superseded while unchanged venue and date facts remain current. Daily records are under `daily-facts/`, `contracts/`, and each provider's `turns/YYYY-MM-DD/`.

The writer and scene verifier see the assigned local message window. An earlier Tuesday draft, timestamped at 7 p.m., opened as though it were 11 a.m.; it remains a failed realism diagnostic. Each daily contract takes one full fact mapping and one independent review. The reviewer checks support, precision, atomicity, duplicates, and temporal state for every authored fact. A rejected mapping gets up to two audited revisions; another rejection stops preparation. Malformed JSON gets one recorded retry. The daily question is fixed and does not expose an answer. Providers run only after all seven contracts freeze.

```sh
/usr/local/bin/python3 -m pacbench.semantic_experiment examples/pizza_day_week.json --prepare-only
/usr/local/bin/python3 -m pacbench.semantic_experiment examples/pizza_day_week.json
```

Use distinct run IDs for those commands. A prepared run cannot be silently reused as a new live run, since its frozen artifacts and hashes are immutable. Inspect the generated dialogue, every daily fact state and question, and the provider checkpoints before interpreting any result. The nine proposed calibration labels still need human review.

The first complete week is `runs/semantic-pizza-week-live-v15-20260928/`. Its frozen bundle hash matches the reviewed preparation `runs/semantic-pizza-week-days-prep-v14-20260928/`. Both arms ingested the same 116 source messages across six active days, skipped ingestion on quiet Sunday, passed seven morning checkpoints, and used their memory tools. Memcal's briefs were pinned to each fictional morning. Hindsight's SDK, MCP, namespaced allowlist, and read-only bank recall preflight all passed. The one claim missed by Memcal was the earlier undecided status of the duck outing on Monday morning; Hindsight missed one venue-street detail and the old 6pm history on three later mornings. These are diagnostic judge verdicts pending human calibration, not a provider ranking.

The saved week passes the independent run audit with no errors or warnings. Earlier saved runs keep their original files; an audit of an older artifact reports fields that were not captured at the time. Claim coverage and verdict counts are available, but the old `precision` and `F1` fields were removed: they ignored unsupported extra assertions and could mislead a comparative analysis.

[`calibration/PIZZA_WEEK_REVIEW.md`](calibration/PIZZA_WEEK_REVIEW.md) groups the week's proposed misses by underlying fact and checkpoint. The same 6pm history gap appears in six verdicts across three mornings; those are correlated observations, not six independent failures.

Hermes's `stream-json` feed clips displayed tool output at 5,000 characters. The full tool messages are retained in each isolated `hermes-home/state.db`. The explorer builder reads the full saved messages for the week run; newer `turn.json` artifacts capture those messages directly. The abbreviated stream remains marked as a preview. This matters when inspecting why a retrieved detail did or did not appear in an answer.

The Pizza Day examples declare `America/New_York` and write local wall times without an offset. PACBench resolves each fictional day's UTC offset before freezing messages and running maintenance. Thus 7pm on two different days means 7pm locally on each day, including across daylight saving changes. The zone is operational metadata, not a fact the assistant must recite. Old saved bundles keep their original timestamps and hashes.

For multi-day chats, the writer sees recent messages from the same thread. Each scene verifies only its new assertions; the final authored facts are checked against the entire frozen conversation before either provider runs. This lets an update say “let's move it to seven” without repeating the restaurant and date in every text.

## Current two-day diagnostic

`runs/semantic-pizza-natural-v10-live-20260924/` is the latest full run. The writer made 30 text messages across the Pizza and Ducks threads. Its second Pizza exchange changes only the time; the frozen contract cites both days. Memcal and Hindsight each received 11 new messages on September 9 and 19 on September 10, with the same input hash. Both retrieval preflights passed, both agents used memory tools, and the semantic judge marked all six claims correct. This is a pipeline diagnostic, not a provider ranking.

The preceding `runs/semantic-pizza-natural-two-days-live-20260924/` exposed a benchmark tool-policy bug. Hindsight's bank recall worked in preflight, but Hermes invoked it through the `tool_call` bridge, which the benchmark allowlist blocked. Hermes then said it could not access the plan; the judge marked six claims missed. The bridge is now allowed, while each underlying call still passes through the isolated tool boundary. In the latest run, Hermes first sent an invalid three-tool local batch, received the one-tool-per-call error, then called `mcp__hindsight__recall` successfully. The error and retry are both preserved in `turn.json`. A blocked retrieval attempt with no successful retrieval now makes an arm invalid for comparison even if its answer is graded for diagnosis.

## Earlier runs and findings

`runs/semantic-pizza-dedup-20260923T-test/` is a fresh two-provider run using
Memcal from `../memcal-pacbench-reasoning`. Both arms passed retrieval preflight,
called their memory tools, and received five correct claim verdicts. Memcal's
one dream pass read 21 frozen observations through four overlapping bundles
(23 bundle lines), made one
proposal call and one sweep call, and wrote one confirmed Pizza event plus one
tentative Ducks event. Its two Pizza proposals were combined before storage.
The Pizza row retains `Luigi's on 5th`, Devon, Friday September 11, and 7pm.
The proposal call used 9,429 output tokens, of which 8,817 were reasoning;
an 8,192-token ceiling would have truncated it. The browser shows one Pizza
brief row, Hermes' `memcal_open({"ref":"E1"})` result with cited chat lines,
the answer, and all five judge explanations. This is one diagnostic run, not a
provider ranking; calibration labels still need human review.

The prior duplicate came from one ingestion and one dream pass, not two Pizza
Days. Person and thread bundles overlap by design. The generic completion
transport had discarded Memcal's JSON schema, so Spark returned `place` and
`people` while Memcal expected `location` and `participants`. Those fields
were lost, and the differently titled “Pizza” and “Meetup” proposals were not
nominated for merge. The transport now sends the schema, Memcal reserves more
reasoning and answer tokens as evidence grows, and same-time proposals from
overlapping source lines reach merge for comparison. A truncated sweep now
marks the pass incomplete instead of silently reporting readiness.

The earlier retry is in `runs/semantic-pizza-memcal-retry-20260923T234115Z/`. Memcal was rerun against the exact frozen bundle and contract from `runs/semantic-pizza-memcal-fixed-20260923T233633Z/`; the Hindsight trace was copied from that source run. `rerun-provenance.json` records this relationship. Both arms are valid, both called memory tools, and the answer judge marked all five pizza claims correct. This diagnostic pair is not a fresh simultaneous two-arm run or a provider ranking.

The source run found the original integration bug: Memcal's dream pass used the default Codex backend for the Meta Spark model. After routing it through the generic OpenAI-compatible endpoint, Spark reached Memcal but truncated at its roughly 2,100-token propose ceiling, leaving 19 of 21 observations queued. The adapter now marks either dream error or queued observations as failed readiness. Memcal's configurable propose output floor was set to 8,192 tokens for the retry. Its dream pass made six model requests, reported no error, and processed all 21 source observations before Hermes answered. The brief then showed the pizza plan at the fictional September 11 clock; Hermes opened the saved event evidence twice and answered from it.

The post-answer database contains one extra queued `person:me` row: Hermes' question, captured after maintenance. It is distinct from the 21 source observations and does not change the pre-answer readiness result.

The earlier traces exposed issues to track before larger or comparative runs:

1. **Hindsight advertised a blocked tool.** Discovery exposed 40 MCP tools, including `mcp__hindsight__list_memories`, while the benchmark allowlist permitted recall and reflect. Hermes described and attempted `list_memories`; the policy blocked it. Two recall calls had already succeeded, so this did not invalidate the answer, but it caused an avoidable extra call and should be visible as a policy event. The WebUI shows the blocked call.
2. **The fictional time zone is ambiguous for Hindsight.** The spec and frozen messages use offset-free times. Hindsight's saved pizza memory says 7pm but records `occurred_start` as `2026-09-11T19:00:00+00:00`. Memcal's brief uses `2026-09-11T09:00:00-04:00`. The answer's date and time were correct, but a future benchmark that compares instants or crosses midnight could be wrong unless the fictional zone is explicit and consistent.
3. **Hindsight added an unsupported pronoun in the distractor memory.** One recalled duck-outing fact says Devon “she wants fresh air”; the frozen dialogue does not establish Devon's gender. This did not affect the five scored pizza facts, but it is an observable extraction error.
4. **Dollar cost is not observable from these artifacts.** Eight of the 10 saved scene/contract/judge model calls were cache hits, and two were new calls. All 10 report `cost_usd` as unavailable. Provider usage records contain token counts but no dollar charge. The run cannot prove a precise spend total or enforce a daily dollar cap from these records.
5. **Memcal duplicated the pizza event in the retry.** Dream wrote both a confirmed “Pizza” row from Devon and a confirmed “Meetup” row from the conversation thread for the same Friday 7pm plan. The new matched run above stores one Pizza row after schema delivery and merge nomination were fixed.

In the earlier `semantic-pizza-live-20260923T142510Z` run, Memcal's brief was pinned to Friday, September 11 but said “nothing known.” Hermes recovered by searching the archive. The new retry shows why readiness must include the dream result, not merely a generated brief.
