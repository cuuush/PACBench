Yes. Exit Q&A mode and turn the blocker list into tracked work items.

One correction before you do: the blocker list is missing the biggest Hindsight ingestion change we just agreed on.

The current per-message `document_id = source_id` design is not the intended final Hindsight adapter.

We want provider-native ingestion based on the natural source document or conversation.

So organize the next work around the following milestones.

### P0-1: Define natural source-document semantics

Before changing the Hindsight adapter, write and test an explicit source-document policy.

Use these initial semantics:

* iMessage: one conversation/chat is one evolving logical document.
* WhatsApp: one conversation/chat is one evolving logical document.
* GroupMe: one group/thread is one evolving logical document.
* Slack DM: one DM conversation is one evolving logical document.
* Slack threaded conversation: one actual Slack thread is one logical document.
* Slack channel messages that are not part of a thread should retain their normal channel/conversation grouping rather than being artificially grouped around PACBench stories.
* Email: preserve actual email structure. Do not group all mail from the same sender. Individual emails remain individual documents unless the corpus explicitly models a real email thread, in which case preserve that thread relationship.
* Hermes conversations: one Hermes session/conversation is one logical conversation.

The rule is:

**natural source boundaries, never benchmark-story boundaries.**

Do not use `life_id`, checkpoint IDs, expected entity IDs, or collection timestamps as provider document identity.

If any of these source semantics are ambiguous in the current corpus schema, document the ambiguity before inventing a mapping.

### P0-2: Stable public source identity

Replace Hindsight's current per-message `document_id = source_id` scheme for conversational sources.

Create stable logical document IDs derived solely from public source provenance.

They should be stable across:

* collection cycles;
* resumes;
* provider repetitions;
* checkpoint execution.

They must not encode:

* life IDs;
* checkpoint IDs;
* benchmark story/family names;
* grading metadata;
* timestamps merely used as batching boundaries.

Add collision tests.

Also add an adapter-configuration/version digest to every run manifest now.

That digest must include at least:

* source-document mapping version;
* Hindsight document-ID scheme;
* Hindsight update mode;
* packetization strategy;
* operation-ID strategy;
* Memcal ingestion recipe version.

Reports must refuse to silently aggregate provider runs with different adapter-config digests.

This solves the Q26 problem before it becomes historical debt.

### P0-3: Specify Hindsight conversation updates before implementing them

Do not blindly choose append or full replacement.

Check the pinned Hindsight version's supported behavior and use the most native safe mechanism for evolving conversations.

The desired semantics are:

Day 1:
retain the conversation available through that collection cycle.

Day 2:
deliver only newly exposed information through the provider's supported evolving-document mechanism.

Day 3:
same.

Do not expose future messages early.

Do not make every new message a standalone conversation document.

Before implementation, explicitly specify behavior for:

* new messages;
* late-arriving older messages;
* edited messages;
* deleted messages;
* source reorder;
* previously missing/backfilled messages.

PACBench does not necessarily need all of these cases in v1, but the adapter must not have undefined behavior silently masquerading as correctness.

If edits/deletes are out of scope for v1, say so explicitly and prevent the corpus from generating them.

### P0-4: Preserve message structure inside conversation documents

When several conversational messages are delivered together, serialize them chronologically, not by `source_id`.

Preserve:

* sender;
* written timestamp;
* original rendered text;
* message order;
* thread identity.

Do not add benchmark-authored summaries.

Add a unit test where lexicographic source-ID order deliberately disagrees with chronological order so the test proves the implementation, rather than merely matching current authoring convention.

### P0-5: Separate collection time from provider ingestion time

Freeze the semantic definition:

`collected_at` = the simulated time PACBench's source collector discovers and exposes the observation.

It must never be changed merely because several observations are transported to a provider together later.

Introduce/record a separate `ingested_at` or equivalent run-artifact timestamp for when the adapter actually submits the batch.

Provider batching must not rewrite source chronology.

Audit every use of `collected_at` in Memcal, Hindsight, runner manifests, operation IDs, readiness, and simulated-clock setup.

### P0-6: Exposure-equivalence proof

Add an automated provider-blind exposure ledger.

At every collection boundary, record a canonical digest over the newly exposed public observations, including at least:

* source identity;
* channel;
* thread;
* sender;
* text;
* written time;
* arrival time;
* collected time.

The Memcal and Hindsight runs for the same life/configuration must prove that the same public evidence was offered before the corresponding checkpoint.

Different provider packaging is allowed.

Different evidence is not.

This should become a comparative-validity gate.

### P0-7: Fix Memcal's remaining source-spine divergence

Keep the current policy of entering after OS-specific source reading. Do not fake `chat.db` or touch live source databases.

Use the real shared Memcal ingestion spine.

Review the remaining differences you identified:

* `counterpart`;
* `addressed_to`;
* `top_tier`;
* `meta`;
* owner handle resolution.

Classify each as:

* meaningful public source semantics PACBench should supply;
* provider-internal behavior that Memcal should derive itself;
* out of scope.

Do not simply copy Memcal-specific metadata into PACBench to make Memcal perform better.

The public observation contract must remain provider-neutral.

The recently added `is_group` correction should remain shared provenance, not Memcal-only information.

### P0-8: Resume/idempotency protocol

Specify and test crash recovery before scale.

For every provider write, preserve enough information to know whether an interrupted operation is:

* definitely not submitted;
* submitted but completion unknown;
* completed;
* failed.

For Hindsight, use supported operation-status/idempotency mechanisms where available.

On resume:

* do not blindly append again;
* do not guess;
* query status when supported;
* retry only operational uncertainty using the same idempotent operation identity;
* rebuild an isolated realization from a known prefix if safe reconciliation is impossible.

A successful operation that semantically extracted nothing must never receive a second semantic attempt merely because PACBench disliked the result.

Add a simulated crash test around the dangerous “provider accepted write, runner died before recording completion” point.

### P0-9: Multi-day Hindsight evolution proof

This is a live-engine validity test and therefore may require approved spend, but write the test harness now.

The test should create one conversation and update it across at least three simulated collection cycles.

Verify:

* one stable logical Hindsight document identity;
* no accidental duplicate ingestion of earlier messages;
* later deltas are associated with the same conversation;
* all intended messages remain recoverable;
* old content is not repeatedly multiplied because of the update mechanism;
* source chronology remains intact.

This test must pass before the large C4 Hindsight run.

The paid execution itself can wait for explicit spend approval.

### P0-10: Temporal characterization

Do not mark Hindsight temporal fidelity globally “working.”

Maintain the four-state ledger you described:

* proven;
* likely but untested;
* known wall-clock dependency;
* unsupported.

Add the two-fact adversarial test where:

* simulated evidence ages differ substantially;
* real ingestion times are nearly identical.

Always use supported fictional `event_date` / query-time controls.

Determine whether wall-clock mention/ingestion timing changes ranking or interpretation after those controls are supplied.

Until characterized, flag affected families explicitly:

* stale-versus-current state;
* opportunity expiration;
* temporary-state/half-life behavior;
* potentially historical reconstruction.

Do not silently downgrade those findings into “bookkeeping.”

### P0-11: Canonical frozen-bundle runtime

Finish the frozen-corpus boundary before the next meaningful paid pilot.

All scored execution should consume canonical, hash-verified frozen bundles.

LifeSpecs and drafts are authoring inputs only.

Remove or hard-label any scored-looking runtime route that consumes draft text.

Prefer eventually having one runtime path for hand-authored and Spark-rendered lives alike:

author → validate/render → freeze → hash → run.

The partial race-week render is not yet a canonical corpus artifact.

Do not treat it as one until it finishes validation and freeze.

### P0-12: Support-graph semantics

The current `supported_by` meaning is too weak for some claims.

Do not silently redefine it yet.

Instead introduce explicit concepts where needed.

At minimum distinguish:

* cited/relevant evidence;
* sufficient evidence set;
* evidence required for a claimed multi-source dependency.

For a checkpoint advertised as requiring multiple channels, we need to be able to say that the answer genuinely depends on those sources rather than merely citing messages from several channels.

Add source-removal/ablation validation for multi-source checkpoints:

remove one claimed-essential source;
recompute what is knowable;
verify that the answer changes or becomes uncertain.

Until this exists, do not make strong quantitative claims about “number of required channels.”

### P0-13: Difficulty measurements

Keep existing measured properties, but do not present author-declared tags as measured difficulty.

Before final curves, add freeze-time computation/validation for:

* actual thread size;
* total exposed observation count;
* actual update depth;
* evidence age;
* source/channel count;
* entity/occurrence collision count where measurable.

A LifeSpec requesting depth 8 but rendering only depth 7 must not silently enter the “depth 8” bucket.

This is not required to run the immediate C4 development audit if the report is honest, but it is required before comparative difficulty curves.

### P0-14: Style/difficulty QA

The current generation screens catch semantic corruption but not “benchmark voice.”

Add an offline report comparing scored support messages with ambient messages on simple provider-blind surface properties such as:

* message length;
* sentence count;
* explicit date/time frequency;
* proper-name repetition;
* event-name repetition;
* punctuation/formality;
* channel-specific length distributions.

Do not turn this into a magic automated naturalness score.

It is an instrument for human review.

The human review packet remains authoritative for deciding whether important messages look suspiciously easier than ambient traffic.

This is desirable before C4 but does not block implementation of the ingestion fixes.

### P0-15: Lifecycle cost report

Add one per-life lifecycle rollup:

* ingestion;
* maintenance/background processing;
* question-time provider work;
* Hermes driver work;
* retries;
* opaque/unpriced work;
* total known cost.

Unknown remains unknown, never zero.

Do not compare cost per API call across providers because provider call shapes intentionally differ.

This is observability and does not block the next development run.

### C4 gate

Before launching the next meaningful paid Memcal-versus-Hindsight large-life run, I require these specific validity items closed:

1. natural source-document policy defined;
2. stable Hindsight document identity implemented;
3. chronological conversation serialization implemented;
4. Hindsight update semantics defined and implemented;
5. `collected_at` separated from adapter ingestion time;
6. exposure-equivalence ledger/test passing;
7. Memcal shared-ingestion-spine audit resolved;
8. resume/idempotency policy implemented sufficiently for the run;
9. multi-day Hindsight evolution test passes against the real provider;
10. frozen-bundle runtime is actually being used;
11. temporal-risk ledger is explicit and the adversarial probe has been run.

Support-graph ablation is required before claiming multi-channel difficulty quantitatively, but it need not prevent a qualitative development C4 audit if those claims are clearly withheld.

Style instrumentation, lifecycle cost rollup, domain diversity, full measured-difficulty enforcement, fairness-log polish, and the 30-life/held-out/judge work can follow the first large development audit.

Please turn these into tracked work items with dependencies and acceptance tests.

Then show me the proposed execution order before implementing the Hindsight source-document redesign, because that redesign changes the meaning of previous Hindsight results and I want the transition captured explicitly.
