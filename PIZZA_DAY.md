# Current Pizza Week diagnostic

The latest run is [pizza-week-context-memcal-verified-20261010](runs/pizza-week-context-memcal-verified-20261010/summary.json): 72 messages, 13 low-noise scenes and 12 factual questions, using Memcal 0.8.2 plus product fixes on `main`. All twelve answers pass. Retention is **39/46 points**, with **19 Hermes tool calls**. Preparation and native trace audits pass. These are development diagnostics, not calibrated rankings.

| Check | Answer | Retention | Hermes tools |
| --- | --- | ---: | ---: |
| Initial duck-pond plan | Pass | 7/7 | 2 |
| Unnamed group cancellation | Pass | 0/3 | 5 |
| Separate Jen call and Alex coffee | Pass | 0/2 | 2 |
| Ben dinner count | Pass | 1/1 | 0 |
| Ben dinner after email reschedule | Pass | 3/3 | 1 |
| Initial pizza count | Pass | 1/1 | 0 |
| Initial pizza details | Pass | 10/10 | 2 |
| Updated pizza count | Pass | 1/1 | 0 |
| Updated pizza details | Pass | 11/11 | 2 |
| Leo shift cover | Pass | 0/2 | 3 |
| Fresh-session Devon venue | Pass | 2/2 | 1 |
| Fresh-session Ben dinner | Pass | 3/3 | 1 |

Source fallback loses retention credit even when the answer is right. The pond cancellation used archive search and a conversation read. The appointment question shares that assistant session and inherits its source access. Leo's shift-cover explanation also used original-message tools. No follow-up was needed in the final run. Only the provider's exact shipped tools were offered; no built-in filesystem, shell, browser or session-search tools were available.

## Native state and realism review

Monday creates four separate events: the pond outing, Jen's phone call, Alex's coffee and Ben's dinner. Tuesday's group messages say only “hey guys i can’t make it today” and “me too let’s just do this another day!”, followed by the owner's agreement. They name neither the outing nor the separate appointments. Native processing cancels only the pond outing, without inventing a replacement date.

Ben's iMessage plan is dinner Thursday at 7 pm at Juniper Cafe. His email asks “hey can we move our dinner to another day?”; the replies settle Saturday, “same time” and “same place”. The native reader associates two distinct accounts using a standard Contacts card. Before Hermes answers, one event has moved to Saturday while retaining 7 pm and Juniper Cafe. A fresh Saturday session recalls that plan correctly. The two Friday pizza outings also remain separate, and the Maya/Leo time and venue corrections survive.

The source messages do not establish attendance at the call or coffee. Memcal automatically ages past appointments to `happened`; Hermes says they “show as happened”. This test judges whether they were cancelled, not whether they actually occurred. Keep that distinction when inspecting the output. The corpus is a small synthetic stress test with minimal chatter, not a claim of full real-world coverage.

Messages use a provider-independent Apple Messages SQLite fixture, realistic sender/thread/time metadata and a separate Contacts source. Email uses RFC 5322 messages, Inbox/Sent folders and real TLS IMAP against Memcal's native reader. The IMAP fixture implements the text-reading protocol needed here; it is not Proton Bridge or a full email server. No reader methods are replaced, no facts are seeded, and Hermes does no ingestion. Normal nightly processing runs at 03:00 America/New_York; questions never see future batches. Named assistant conversations share context, with separate fresh sessions for later checks.

Before benchmarking, source review caught drafts that repeated the restaurant or added explicit appointment reminders to the group cancellation. Those drafts were replaced. The final frozen corpus preserves contextual references. Preparation also rejected overbroad count and yes/no grading requirements; expectations now match the questions' natural scope.

## Fixes, control and costs

Memcal commits [`98f1a8e`](https://github.com/cuuush/memcal/commit/98f1a8e) and [`4328c64`](https://github.com/cuuush/memcal/commit/4328c64), pushed to `main`, fix distinct same-day occasion merging and an uncited time-only nightly diff restoring an old venue. Product verification: 2477 unittest cases, 39 skipped; 400/400 temporal integration checks.

The [unchanged control](runs/pizza-week-memcal-verified-20261010/development-review.json) reruns the original 42 messages and six questions: **6/6 answer passes, 28/30 retention points, eight tool calls**. Its source bundle hash is unchanged. Leo's explanation remains the source-fallback failure. The expanded corpus has a different set of criteria, so its denominator is not directly comparable.

The first expanded attempt exposed a harness error: a safe follow-up review listed both missing fact IDs in reverse order, and validation rejected it before Hermes received the message. Coverage validation now accepts either order while rejecting omissions, duplicates and extra IDs. The unchanged corpus was rerun from fresh stores. The [development review](runs/pizza-week-context-memcal-verified-20261010/development-review.json) preserves the concrete rejected review and final result. All **192 harness tests** pass. Superseded runs and duplicate preparation were deleted.

Every inference request used Spark Contributor through the capped gateway. The final accounted upper bound is **$5.66 total / $4.75 today**, including retained unknown reservations, below the $10 total and $5 daily caps. The final run's captured memory-provider work used 12 model requests, 35,799 input and 34,073 output tokens, accounting for $0.0104. Hermes used 31 model requests, 249,577 input and 8,174 output tokens, accounting for $0.0266; this includes normal agent overhead. Judging is logged separately. Aggregate requests and token windows do not establish an exact price for an individual tool call. Native backend cost fields reporting zero are not used as evidence of free inference.

## Inspect and reuse

The explorer reads public evidence directly from `runs/`: messages, native daily ingestion, actual answers and tool results, expected criteria, judge decisions and model usage. Private homes, databases, credentials and encrypted replay remain ignored. The current run and necessary unchanged control ship with Git.

On your Mac, pull `work`, then:

```sh
python3 serve_explorer.py
```

Open `http://localhost:8137/?run=pizza-week-context-memcal-verified-20261010&day=2026-10-07&provider=memcal-repeat-1`. Cloud localhost is separate from your Mac. Fresh checkouts display the saved public evidence without private native stores.

Reuse [preparation/week.json](runs/pizza-week-context-memcal-verified-20261010/preparation/week.json) to run the exact corpus without writer or verifier calls. Native re-auditing requires the private runtime files; the saved audit and public traces remain inspectable after checkout.

Next, diagnose the remaining source-fallback paths in Memcal's retained change context and normal retrieval surface. Keep this frozen low-noise week as the control. After that, increase conversation noise gradually by about 100 messages and freeze that next corpus once; do not regenerate it for each provider run.
