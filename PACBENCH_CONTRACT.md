# PACBench Benchmark Contract

**Status:** Draft benchmark contract  
**Version:** 0.1  
**Purpose:** Standalone specification for PACBench, a benchmark for memory systems used by persistent personal assistants  
**Initial development target:** 3 synthetic lives  
**Target comparative corpus:** 30 independent synthetic lives  
**Scored horizon per life:** 7 consecutive simulated days  
**Historical context:** Older synthetic history may be delivered before the scored week, including approximately a month of prior email, messaging, and assistant activity where useful

---

## 1. Background

PACBench is a benchmark for a specific kind of memory problem: maintaining a useful, trustworthy model of a person's life while that life changes across many noisy communication streams.

The target is not a chatbot that remembers a few durable user preferences. The target is closer to a persistent personal assistant: an assistant that sees a person's email, texts, group chats, work messages, and conversations with the assistant itself; notices plans, tasks, commitments, opportunities, reminders, relationships, documents, and changing facts; and can later answer questions or surface relevant information at the right moment.

Examples include:

- "Where is poker this weekend?"
- "Didn't my dentist appointment change?"
- "Walk me through what happened to that appointment."
- "Anything fun I can do this weekend?"
- "What do I need to sort out before my trip?"
- "What did Maya recommend for dinner?"
- "Where is the document I need?"
- "I'm heading over to Eric's."  
  A useful assistant may respond: "Remember to bring the coat you said you'd return to him."

The benchmark is motivated by a practical distinction between ordinary long-term memory and personal state management.

A conventional memory system may be good at facts such as:

- the user's dog is a cattle-dog mix;
- the user's partner's name is Taylor;
- the user dislikes early flights.

Those are useful memories, but they are not enough to maintain a changing model of a person's current life.

A personal assistant must also deal with facts such as:

- poker was at Sam's, then moved to Jordan's;
- one poker game was canceled while a different poker game moved;
- a dentist appointment changed four times;
- an invitation exists, but the user never committed to attending;
- the user promised to bring chips;
- a task matters only until Tuesday;
- a piece of information should remain historically available but stop influencing current recommendations;
- a casual suggestion must not become a confirmed plan;
- the assistant's own stale answer must not become new evidence.

PACBench exists to measure those capabilities.

---

## 2. Benchmark thesis

PACBench asks:

> **Can a memory-backed assistant maintain an accurate, actionable, provenance-aware model of a person's current life as information arrives, changes, conflicts, expires, and becomes relevant across many channels?**

The benchmark is designed around the needs of a real personal assistant rather than around the internal data model of any particular memory provider.

PACBench does **not** require a provider to use a calendar schema, wiki, state table, temporal database, vector store, knowledge graph, or any other specific representation.

A system receives credit for producing correct assistant behavior, regardless of how it stores or retrieves the information internally.

This is essential to benchmark fairness.

Memcal is one system PACBench is intended to evaluate, but PACBench must behave as though it does not know which system is supposed to win.

If another provider handles a task better than Memcal, that result remains in the benchmark and should be reported.

If PACBench exposes a Memcal failure, that is useful: the failure becomes a concrete product-improvement target.

---

## 3. What PACBench measures

PACBench measures the complete memory-supported assistant stack.

The primary object being evaluated is:

> **Hermes + one selected memory provider**

The benchmark evaluates whether the resulting assistant can use a realistic personal history to behave correctly.

The major capability families are:

1. **Current-state tracking**
2. **Historical-state reconstruction**
3. **Entity and occurrence resolution**
4. **Cross-source information joining**
5. **Commitment tracking**
6. **Opportunity tracking**
7. **Task and to-do tracking**
8. **Proactive reminder surfacing**
9. **Temporary and durable fact handling**
10. **Uncertainty handling**
11. **Correction, cancellation, reversal, and deletion**
12. **Source and authorship handling**
13. **Noise resistance**
14. **False-memory resistance**
15. **Efficient question-time retrieval and reasoning**

PACBench also measures operational properties such as latency, tool usage, model calls, tokens, archive reads, and cost.

---

## 4. What PACBench is not

PACBench is not primarily:

- a benchmark for remembering isolated biographical preferences;
- a generic long-context question-answering benchmark;
- a pure vector-search benchmark;
- a calendar API benchmark;
- a database benchmark;
- a benchmark that requires one exact memory-tool call sequence;
- a benchmark that scores internal storage layout;
- a test of whether a provider imitates Memcal's architecture;
- a test where every important fact is explicitly marked as important;
- a collection of tiny three-message toy scenarios.

PACBench may include optional diagnostic baselines that resemble these tasks, but they are not the primary experiment.

---

## 5. Core experimental principle

The benchmark must separate three things:

### 5.1 World truth

What is actually true in the fictional person's life.

Example:

- Eric's poker game exists.
- It was originally Saturday at 7 PM.
- Eric later moved it to 8 PM.
- John runs a different poker game.
- John canceled his game.

### 5.2 Evidence

What messages, emails, assistant turns, or source records make that truth knowable.

Example:

- Eric posts the move in one WhatsApp thread.
- A participant later repeats the stale 7 PM time.
- John cancels his own game in an unrelated GroupMe thread.
- The user tells Hermes they are still going to Eric's game.

### 5.3 Expected assistant behavior

What the assistant should correctly say or do at a checkpoint given only evidence that has been exposed by that time.

Example:

- "John's game is canceled."
- "Eric's game is still on and moved to 8 PM."
- If asked for the old time, "Eric's game was originally at 7 PM."
- If the evidence is genuinely ambiguous, the assistant should ask or express uncertainty rather than inventing certainty.

These three layers must remain distinct.

---

## 6. Unit of evaluation: a life

The main independent unit in PACBench is a **life**.

A life represents one synthetic person and their communication environment.

Each scored life covers seven consecutive simulated days.

A life may also include older synthetic history, including approximately a month of prior email, messaging, assistant conversations, and other records where older evidence is useful.

The older history exists because a real personal assistant is not born on Monday morning. Stale confirmations, prior locations, old recommendations, and previous plans can all compete with current information.

### 6.1 Target corpus size

The target comparative corpus is **30 independent lives**.

The first implementation phase begins with **3 development lives**.

The 3-life development corpus exists to validate:

- corpus generation;
- ingestion;
- simulated time;
- provider isolation;
- assistant execution;
- grading;
- tracing;
- proactive tests;
- reporting.

The first three lives are not treated as final held-out evidence of superiority after they have been repeatedly inspected and used for debugging.

Before a comparative claim is made, the complete set of lives must be assigned declared roles such as development, calibration, and held-out evaluation.

Once detailed results from a held-out life are used to tune Memcal, provider configuration, prompts, or benchmark code, that life becomes exposed and should move into regression coverage.

---

## 7. Life composition

A life should feel like one coherent person's week, not a bag of unrelated test questions.

Each life contains multiple ongoing story threads.

A life may contain:

- appointments;
- recurring social events;
- work projects;
- travel;
- invitations;
- errands;
- documents;
- relationship context;
- restaurant recommendations;
- purchases;
- household tasks;
- favors;
- reminders;
- cancellations;
- tentative plans;
- overlapping events;
- stale information;
- casual conversation;
- automated email;
- newsletters;
- receipts;
- work noise;
- memes;
- unrelated chatter.

Most observations do not need to matter to a scored question.

The benchmark should contain substantial realistic noise.

The important information should sometimes look almost identical to the surrounding noise.

---

## 8. Channels

PACBench begins with five broad information streams:

- email;
- iMessage or direct text messaging;
- WhatsApp;
- GroupMe or similar group chat;
- direct user conversations with Hermes.

Work-oriented Slack or equivalent workplace messaging should also be included where the life calls for it.

The benchmark should preserve:

- actual sender identity;
- thread identity;
- channel identity;
- source timestamp;
- source-arrival timestamp where relevant;
- collection timestamp;
- original message content;
- reply or thread relationships where available.

Different channels are not separate people or separate memory namespaces.

They are different views into the same person's life.

---

## 9. The LifeSpec

PACBench should use a relatively simple structured object called a **LifeSpec**.

The LifeSpec is the authoring source of truth for a fictional life.

It does not need to model every message.

It describes the meaningful stories, facts, state transitions, difficulty settings, people, threads, and checkpoint questions that the generated corpus must realize.

The goal is to avoid manually writing ten thousand messages while also preventing the generation model from inventing benchmark truth.

A LifeSpec may contain:

- people and identities;
- threads and channels;
- events;
- tasks;
- opportunities;
- commitments;
- temporary facts;
- durable facts;
- documents;
- reminders;
- initial state;
- state transitions;
- uncertainty;
- source authority;
- required evidence placements;
- noise budgets;
- difficulty parameters;
- benchmark checkpoints;
- hidden expected facts.

A LifeSpec should remain human-reviewable.

It should not become a giant formal ontology unless implementation experience shows that one is necessary.

---

## 10. Corpus generation

PACBench uses a generation model, initially Spark, to turn a LifeSpec into realistic message traffic.

The generation model writes the **surface language** of the life.

It does not decide the ground truth.

### 10.1 Generation pipeline

The intended pipeline is:

**LifeSpec → prompt builder → Spark → structured observations + support manifest → validation → frozen corpus**

### 10.2 Spark's role

Spark may decide:

- wording;
- slang;
- punctuation;
- typos;
- message fragmentation;
- reactions;
- conversational rhythm;
- casual topic transitions;
- realistic filler;
- harmless jokes;
- stylistic differences between people;
- how a required fact is naturally stated.

Spark may not independently decide:

- whether a plan exists;
- whether a plan is confirmed;
- whether the user committed to it;
- whether something is canceled;
- event identity;
- final event time;
- final event location;
- durable preferences;
- durable relationships;
- benchmark-relevant addresses;
- benchmark-relevant tasks;
- which source is authoritative;
- expected answers.

Those are specified by the LifeSpec.

### 10.3 Whole-thread generation

Where useful, Spark should render short windows or entire conversational segments rather than rendering every important message independently.

This allows realistic:

- callbacks;
- interruptions;
- message fragments;
- replies;
- reactions;
- misunderstandings;
- repeated stale information;
- uneven conversational style.

### 10.4 Ambient traffic

Spark may also create ordinary filler traffic.

Ambient generation should be constrained so that it does not accidentally create new benchmark-relevant durable facts.

For example, ambient text should not casually invent:

- a new allergy;
- a new appointment;
- a new trip;
- a new family relationship;
- a new commitment;
- an address that later collides with a scored task.

If ambient generation introduces meaningful new facts, the life should either be rejected or those facts must be deliberately incorporated into the LifeSpec and rubric before the corpus is frozen.

---

## 11. Support manifest

Generated observations should include a generation-time support manifest.

For each important fact or state transition, Spark should identify the message or messages that are intended to establish it.

Example:

- `eric_poker_move`
- supporting messages: `msg_438`, `msg_439`

The support manifest is a corpus-construction aid.

It is not automatically accepted as ground truth.

The benchmark validates that the cited observations actually support the intended fact.

The support manifest is never exposed to the memory provider or Hermes during a scored run.

---

## 12. Corpus validation

Perfect deterministic semantic validation of thousands of natural-language messages is not expected.

PACBench instead uses multiple layers of validation.

### 12.1 Deterministic structural validation

Check:

- schema validity;
- unique message IDs;
- valid timestamps;
- valid channels;
- valid sender references;
- valid thread references;
- chronology;
- collection chronology;
- message-count targets;
- no future evidence delivered early;
- required threads exist;
- required participants exist;
- required literal values appear when the task requires exact values;
- hidden grading data is inaccessible to adapters.

### 12.2 Fact-realization validation

For each benchmark-relevant fact or transition, inspect the support messages cited by the generation manifest.

A narrow model-based validator may answer questions such as:

> Do these messages establish that Eric moved his poker game to Saturday at 8 PM?

The validator should not be the same thing as the final benchmark grader.

This is authoring-time corpus quality assurance.

### 12.3 Contradiction scan

The generated corpus should be checked for accidental contradictions to protected benchmark facts.

This can be partly deterministic and partly model-assisted.

### 12.4 Human review

A sample of every corpus release should be manually reviewed.

All development lives should receive substantial manual inspection.

Calibration and held-out lives should also receive sufficient review to verify that the answer key follows the actual evidence.

The benchmark must never derive ground truth from the memory system being tested.

---

## 13. Difficulty dimensions

PACBench should not produce only one aggregate score.

The corpus should deliberately vary several independent dimensions so performance curves can be measured.

### 13.1 Conversation-size difficulty

Relevant threads vary in size.

Illustrative buckets:

- small;
- medium;
- large;
- extra large.

The exact message-count boundaries should be declared in the corpus version.

A key fact may be buried in:

- a 20-message conversation;
- a 100-message conversation;
- a 300-message conversation;
- a 500-plus-message conversation.

The benchmark should report task success against conversation size.

This answers questions such as:

> Does a provider degrade as the relevant conversation grows?

### 13.2 Total-history difficulty

A life contains many unrelated threads and channels.

PACBench should also report performance against total exposed observation volume.

The full history may be too large for brute-force prompt inclusion.

That is intentional.

### 13.3 Update-depth difficulty

The same real-world state may change repeatedly.

Examples:

- zero changes;
- one change;
- two changes;
- four changes;
- eight changes.

The benchmark should measure:

- current-state accuracy;
- historical-state accuracy;
- timeline reconstruction;
- false resurrection of stale states.

Example:

A dentist appointment may be:

1. Friday at 2;
2. moved to Monday at 11;
3. moved to Tuesday at 3;
4. canceled;
5. rebooked Thursday at 4:30.

Questions may ask:

- "When is my dentist now?"
- "What was the original appointment?"
- "When did it change?"
- "Was it ever canceled?"
- "Walk me through how the appointment changed."

### 13.4 Entity-collision difficulty

Several similar real-world entities may exist simultaneously.

Example:

- John's poker game;
- Eric's poker game.

John cancels his game.

Eric moves his game.

A weak memory system may:

- cancel both;
- merge the two events;
- move the wrong event;
- return only one;
- attach Eric's time to John's game.

PACBench should vary the number and similarity of competing entities or occurrences.

### 13.5 Cross-channel distribution difficulty

Some answers are fully stated in one source.

Other answers require joining evidence across:

- two channels;
- three channels;
- four or more channels.

Example:

- GroupMe establishes the new day;
- iMessage provides the address;
- the user tells Hermes that they are still attending;
- email contains the original confirmation.

### 13.6 Conflict difficulty

Cases should vary from:

- clean evidence;
- stale evidence;
- tentative suggestions;
- participant confusion;
- authoritative corrections;
- old confirmations arriving late;
- contradictory statements;
- different occurrences that only appear contradictory.

### 13.7 Evidence-age difficulty

Important evidence may be:

- minutes old;
- hours old;
- days old;
- weeks old.

Old but still valid facts should remain available.

Old superseded facts should remain historical without being presented as current.

---

## 14. Entity and occurrence resolution

Thread identity is an important source of evidence, but PACBench tests something deeper:

> Can the memory system preserve the identity of the real-world thing being discussed?

Evidence for identity may include:

- thread;
- sender;
- participants;
- recurring schedule;
- location;
- organizer;
- prior conversation;
- surrounding context;
- event history.

A benchmark question should not always use the exact formal entity name.

For example, the group may call a poker event simply "game."

The assistant must still distinguish it from a different recurring game.

This capability family should include matched controls where only one event exists.

---

## 15. Source authority and provenance

PACBench should test whether systems correctly handle who said something and how authoritative the statement is.

Source metadata is not merely decorative.

Example:

- Eric, a random participant, says John's game is canceled.
- John, the organizer, later says it is still happening.

The correct interpretation may depend on:

- who is speaking;
- whether the statement is direct knowledge or hearsay;
- whether the statement is tentative;
- whether a more authoritative source confirms or rejects it.

A useful scenario is a deliberate fake-out:

> Eric casually claims John's poker game is canceled, but John later confirms that it is still on.

The benchmark should test whether the system stores or surfaces Eric's statement as fact without sufficient support.

Source authority must be determined from evidence available in the fictional life, not from hidden benchmark metadata that the provider cannot see.

---

## 16. Commitments, opportunities, and tasks

PACBench must distinguish between different kinds of future-relevant state.

### 16.1 Commitment

The user has committed to doing something.

Example:

> "Yeah, I'm coming. I'll bring chips."

### 16.2 Opportunity

A possibility exists, but the user has not committed.

Example:

> A friend invites the user to a concert Saturday.

The assistant may later mention it in response to:

> "Anything fun this weekend?"

But it should not claim:

> "You're going to a concert Saturday."

### 16.3 Task

The user needs or intends to do something.

Example:

> "I need to send the insurance form by Tuesday."

These types must not be casually merged.

The benchmark should specifically penalize converting opportunities into commitments.

---

## 17. Proactive reminder and to-do evaluation

PACBench should test whether the assistant can surface relevant stored information **without the user explicitly asking for that information**.

This is central to a useful personal assistant.

### 17.1 Contextual proactive reminder

Example prior evidence:

> The user borrowed Eric's coat after a party and later said they need to return it.

Later user turn:

> "I'm heading over to Eric's around six."

A strong assistant may naturally add:

> "Remember to bring his coat."

The user did not ask:

> "What should I remember?"

This tests whether the assistant can connect a current situation with an unresolved task.

### 17.2 Matched negative control

A similar user turn should exist where no relevant outstanding task exists.

The assistant should not invent a reminder.

This prevents a system from scoring by constantly surfacing vaguely related memories.

### 17.3 Reminder metrics

Measure:

- relevant reminder surfaced;
- reminder omitted;
- irrelevant reminder surfaced;
- stale completed task resurfaced;
- wrong-person reminder;
- wrong-occurrence reminder;
- latency;
- tool calls;
- evidence read.

### 17.4 Autonomous notification

Truly autonomous scheduled notifications may be tested later where every provider can be placed behind a common controlled notification mechanism.

The first PACBench version should prioritize contextual proactivity during ordinary Hermes turns, because it is easier to compare fairly.

---

## 18. Temporary facts, durable facts, and information half-life

Personal information has different useful lifetimes.

PACBench should test whether systems treat all extracted information as permanent undifferentiated memory.

Examples:

### Very short-lived

> "I'm at the dentist right now."

### Short-lived current state

> "My car is at the shop until Thursday."

### Event state that later becomes history

> "My dentist appointment moved to Thursday."

### Durable personal fact

> "My dentist is Dr. Patel."

### Durable preference

> "I hate 6 AM flights."

The benchmark should test whether information:

- remains current while it should;
- expires from current state when it should;
- remains historically retrievable where appropriate;
- does not continue polluting unrelated future answers.

This capability is particularly important for systems that otherwise accumulate large amounts of low-value memory.

---

## 19. Uncertainty

PACBench should reward uncertainty when the evidence is genuinely uncertain.

Example:

> "Dinner might be Saturday or Sunday. I'll confirm later."

Before confirmation, the correct assistant behavior may be:

- express uncertainty;
- ask for clarification if needed;
- avoid asserting one day as definite.

After a decisive update arrives, continued uncertainty becomes a failure.

The benchmark should distinguish:

- appropriate abstention;
- inappropriate certainty;
- inappropriate abstention after resolution.

---

## 20. Cancellation, reversal, deletion, and invalidation

PACBench should test more than simple rescheduling.

Examples include:

- an event is canceled;
- a cancellation is reversed;
- a plan is replaced;
- a task is completed;
- an invitation expires;
- the user says the information is no longer relevant;
- the user explicitly asks the assistant to forget or disregard a piece of information.

Important questions include:

- Does the old fact remain incorrectly active?
- Is historical information still available when appropriate?
- Does a completed task continue triggering reminders?
- Does a reversed cancellation create duplicate active events?
- Can the assistant distinguish deletion of current relevance from destruction of useful historical context?

---

## 21. False-memory resistance

A personal assistant must be penalized for inventing actions or commitments.

PACBench should track at least:

- invented commitments;
- invented appointments;
- resurrected canceled plans;
- opportunities incorrectly promoted to commitments;
- tentative suggestions promoted to confirmed plans;
- wrong-person associations;
- wrong-occurrence associations;
- false merges;
- duplicate commitments;
- stale information presented as current;
- assistant-generated claims later treated as source evidence.

For a personal assistant, confident false state can be more harmful than an omission.

The benchmark should therefore report false-positive behavior prominently rather than hiding it inside aggregate accuracy.

---

## 22. Assistant-authorship contamination

A special failure occurs when the assistant's own incorrect answer becomes later evidence.

Example:

1. Source evidence says poker moved to Sunday.
2. User asks where poker is.
3. Assistant incorrectly says Saturday.
4. Memory system stores the assistant's Saturday answer.
5. Later the system "remembers" Saturday because the assistant reinforced its own mistake.

PACBench should test whether systems preserve role and authorship correctly.

A genuine user correction should survive.

A source-backed action result should survive.

An assistant hallucination should not become independent evidence merely because the assistant said it.

---

## 23. Historical reconstruction

PACBench must separately evaluate the ability to reconstruct how state changed.

This includes questions such as:

- "What was the original date?"
- "When did it move?"
- "How many times did it change?"
- "Was it ever canceled?"
- "Why did I think it was Friday?"
- "Walk me through the changes."

A system that knows only the final state may pass current-state questions while failing historical reconstruction.

Both capabilities matter and should be reported separately.

---

## 24. Broad personal-assistant questions

PACBench should include questions with no obvious retrieval keyword.

Examples:

- "Anything fun I can do this weekend?"
- "What should I sort out before my trip?"
- "Is there anything I'm forgetting?"
- "Who might I want to invite?"
- "What do I need to bring tonight?"

These questions test whether the memory system can prepare or reconstruct useful context rather than depending entirely on one explicit lexical query.

The rubric for broad questions should identify:

- supported relevant items;
- expired items;
- declined items;
- wrong-person items;
- forbidden unsupported suggestions.

The benchmark grades factual support and coverage, not writing style.

---

## 25. Noise design

Noise should not be treated as arbitrary token padding.

PACBench should include several kinds of noise.

### Pure irrelevant noise

Memes, jokes, routine work chatter, newsletters, receipts.

### Topically similar noise

Other poker games, another dentist, another Eric, another "Saturday at 8."

### Stale relevant evidence

Old but once-correct plans.

### Near duplicates

People repeating the stale plan.

### Tentative alternatives

"Maybe Saturday?"

### Hearsay

"I think John canceled?"

### Harmless retellings

Messages that repeat current information without changing it.

### Casual durable facts

Important facts may appear in ordinary chatter and should still be retained.

The benchmark must not accidentally make every important message visually distinctive.

The needle should sometimes look like hay.

---

## 26. Conversation size and corpus scale

PACBench should be large enough that a full personal history is not realistically copied into every assistant prompt.

The final corpus may contain well over ten thousand messages across all lives.

The benchmark should not require one enormous 10,000-message life.

Instead, message volume should be distributed across independent lives and channels.

Some lives should be comparatively small.

Others should contain large message histories.

This supports meaningful performance curves rather than one arbitrary "large context" number.

The exact corpus volume should be measured and reported in every release.

---

## 27. Optional brute-force and oracle baselines

A brute-force raw-history baseline is **not** required as a primary comparison.

For realistic email and messaging histories, the complete history may exceed practical context limits.

However, optional diagnostics may be useful on subsets that fit.

Examples:

- small-life full-history prompt;
- relevant-thread full-history prompt;
- oracle evidence only;
- lexical archive search.

These should be labeled as diagnostics or upper bounds, not as equivalent real memory systems.

The primary benchmark remains Hermes + installed memory provider operating over the complete exposed life.

---

## 28. Common Hermes runtime

Every primary provider is evaluated through Hermes.

The same outer assistant model should be used across compared systems where practical.

Hermes may use:

- the selected provider's native memory tools;
- the selected provider's automatic context;
- ordinary current-session conversation.

Hermes must not use:

- another memory provider;
- unrestricted filesystem reads;
- shell access to benchmark inputs;
- web search for benchmark answers;
- hidden answer keys;
- independent built-in long-term memory;
- old-session retrieval outside the selected provider.

The benchmark measures the complete Hermes + provider product behavior.

---

## 29. Provider fairness

Every provider receives the same public observations at the same declared collection times.

PACBench does not force every provider into Memcal's internal architecture.

Providers may use their supported:

- ingestion;
- extraction;
- consolidation;
- reasoning;
- retrieval;
- native context;
- lifecycle behavior.

The benchmark should preserve meaningful native capabilities.

It should not repair one provider with hidden benchmark logic.

Harness modifications are allowed only when they are:

- fairness-neutral;
- shared in intent across providers;
- unrelated to specific answer keys;
- necessary to create an isolated controlled experiment.

---

## 30. Simulated time

Time is part of the benchmark.

The runner must maintain a fictional date and time consistently across:

- Hermes prompts;
- provider operations;
- source collection;
- event timestamps;
- session handling;
- memory tools;
- nightly processing;
- checkpoint questions.

Advancing fictional time must not accidentally give one provider unlimited real background-processing time.

The real processing allowance between simulated events should be declared and recorded.

---

## 31. Direct user conversations

User conversations are real Hermes turns.

The benchmark sends natural messages such as:

- "Yeah I'm still going."
- "Actually poker moved to Saturday."
- "I'm heading to Eric's."
- "What am I doing this weekend?"

Hermes decides which memory tools to use.

The benchmark does not secretly replace normal user turns with direct database mutations.

The assistant's real answer becomes part of the continuing life where normal persistence applies.

This allows PACBench to test whether wrong assistant answers contaminate future memory.

---

## 32. Checkpoints

A checkpoint is a scored assistant situation.

A checkpoint contains:

- simulated time;
- user turn or proactive trigger;
- hidden expected atomic facts;
- accepted alternatives;
- required evidence IDs;
- forbidden stale or unsupported claims;
- task family;
- difficulty annotations.

Checkpoints may test:

- direct recall;
- current state;
- history;
- cross-source reconstruction;
- complete plans;
- broad opportunities;
- uncertainty;
- proactivity;
- deletions;
- task completion;
- false-memory resistance.

---

## 33. Ground truth and grading contract

Ground truth belongs to the corpus.

It does not belong to the memory provider or grader.

Each checkpoint defines atomic expectations.

Example:

Question:

> "What's happening with poker this weekend?"

Atoms:

- John's game: canceled.
- Eric's game: active.
- Eric's game: Saturday.
- Eric's game: 8 PM.
- Forbidden: John's game moved to 8.
- Forbidden: Eric's game canceled.

Structured values such as times, dates, counts, status, and addresses should be normalized deterministically where possible.

Only semantic cases that cannot be decided reliably by normalization should go to a narrow blinded model judge.

The judge should answer small questions such as:

> Does this assistant answer assert that Eric's game is at 8 PM?

The judge should not see:

- provider identity;
- aggregate score;
- hidden corpus beyond the relevant atom;
- whether a particular verdict would help Memcal.

---

## 34. Separate memory failure from assistant failure

A wrong final answer is not enough to diagnose what went wrong.

PACBench should capture and classify the path from stored evidence to final answer.

At minimum, the benchmark should distinguish:

1. **Evidence ingestion failure**  
   The necessary observation never entered the provider correctly.

2. **Memory representation or consolidation failure**  
   The evidence arrived but was lost, corrupted, merged, or incorrectly reconciled.

3. **Retrieval failure**  
   The provider retained the evidence but did not return or inject it when needed.

4. **Tool-selection failure**  
   Hermes had an appropriate memory capability available but chose not to use it or used the wrong tool.

5. **Evidence-reading failure**  
   Correct evidence reached Hermes but the driver misunderstood or ignored it.

6. **Final-answer failure**  
   Hermes understood the evidence but expressed the answer incorrectly.

7. **Tool-policy or runtime failure**  
   The task failed because of benchmark infrastructure or a forbidden route.

A classification should only be assigned when the trace supports it.

The benchmark must not guess an internal cause from the final answer alone.

---

## 35. Question-time work

Correctness is necessary but not sufficient.

PACBench should measure how much work is required to answer.

Record:

- memory tool calls;
- archive reads;
- retrieval calls;
- returned items;
- evidence text read;
- model calls;
- input tokens;
- output tokens;
- provider reasoning calls where observable;
- answer latency;
- background processing cost;
- total lifecycle cost.

This enables comparisons such as:

- one provider answers a timeline question with one memory call;
- another provider requires five searches and several reads;
- both are correct, but their user experience and cost differ materially.

---

## 36. Primary metrics

PACBench should report multiple metrics rather than collapsing everything into one number.

### Semantic metrics

- task success rate;
- factual precision;
- factual recall;
- factual F1;
- current-state accuracy;
- historical-state accuracy;
- timeline-reconstruction accuracy;
- complete-plan accuracy;
- opportunity recall;
- commitment precision;
- task/reminder success;
- uncertainty or abstention accuracy.

### Error metrics

- stale assertions;
- invented facts;
- invented commitments;
- false merges;
- wrong-person associations;
- wrong-occurrence associations;
- duplicate commitments;
- missed updates;
- expired-state resurfacing;
- proactive false positives.

### Efficiency metrics

- median and tail answer latency;
- memory tool calls per task;
- evidence read per task;
- model calls;
- tokens;
- provider cost;
- total weekly lifecycle cost;
- nightly/background cost.

### Operational metrics

- ingestion failures;
- readiness failures;
- timeouts;
- tool-policy failures;
- unsupported tasks;
- incomplete runs.

---

## 37. Difficulty curves

PACBench should publish performance as curves where possible.

Examples:

### Accuracy vs. conversation size

X-axis: messages in relevant thread  
Y-axis: task success or fact accuracy  
One line per provider.

### Accuracy vs. update depth

X-axis: number of changes to the same entity  
Y-axis: current-state or timeline accuracy.

### Accuracy vs. entity collisions

X-axis: number of similar simultaneous events or people  
Y-axis: correct association rate.

### Accuracy vs. source distribution

X-axis: number of channels required  
Y-axis: task success.

### Question-time work vs. difficulty

X-axis: one of the difficulty measures  
Y-axis: tool calls, evidence read, latency, or cost.

These curves may reveal architecture behavior that one overall score would hide.

---

## 38. Matched controls

Each architecture-targeted challenge should have a matched control where possible.

Examples:

### Update case

Hard case: dentist moved four times.  
Control: dentist never moved.

### Entity collision

Hard case: two poker games.  
Control: one poker game.

### Cross-source join

Hard case: day, location, and commitment split across channels.  
Control: same factual content stated together.

### Uncertainty

Hard case: Saturday or Sunday unresolved.  
Control: later message resolves it.

### Proactive reminder

Hard case: unresolved coat-return task when visiting Eric.  
Control: visit Eric with no outstanding task.

Matched controls help distinguish a real capability from a benchmark that simply contains scenarios favorable to one architecture.

---

## 39. Development, calibration, and held-out use

PACBench begins with three development lives.

These are aggressively inspected and used to debug the full system.

Before comparative claims, the corpus should be expanded to thirty independent lives.

The final release must declare which lives are:

- development;
- grader or pipeline calibration;
- held-out comparison.

Close variants of the same story family must remain in the same split.

A renamed version of the same poker incident is not independent held-out evidence.

Repeated detailed inspection of a held-out result exposes that life.

---

## 40. Repetitions and stochasticity

Memory systems and assistant models may behave stochastically.

The runner should support repeated realizations of a fixed life and provider configuration.

The report should preserve every realization, including failures.

It should not silently retry until a preferred outcome occurs.

Uncertainty should be estimated over independent lives or story families rather than pretending that hundreds of facts about one person are hundreds of independent experiments.

---

## 41. Run artifacts

Every scored run should save enough information for an independent reviewer to reconstruct what happened.

Required artifacts include:

- immutable run manifest;
- corpus version and hash;
- selected lives;
- provider and version;
- Hermes version;
- driver model;
- grader version;
- provider model configuration where known;
- exact exposed observations;
- import batches;
- simulated timestamps;
- Hermes transcripts;
- exposed tool definitions;
- tool calls and arguments;
- tool results;
- automatic memory context;
- raw assistant answers;
- per-atom grades;
- grader explanations;
- latency records;
- usage and cost ledger;
- failures;
- retries;
- readiness events;
- maintenance events.

Hidden answer keys must remain inaccessible to Hermes and providers.

---

## 42. Valid and invalid runs

A run is valid only if:

- provider isolation is enforced;
- built-in alternate memory is disabled;
- the correct corpus version is delivered;
- observations are delivered at the correct times;
- simulated time is consistent;
- the provider is actually active;
- required traces are captured;
- hidden answers are inaccessible;
- scored questions come through Hermes;
- the assistant cannot bypass the memory boundary.

A setup defect should create an invalid or incomplete run.

It must never be converted into a passing score.

---

## 43. Initial three-life development corpus

The first three lives should be chosen to exercise the widest possible amount of infrastructure rather than to maximize benchmark breadth.

A useful initial set would include:

### Development Life A: update depth and history

Core story:

- dentist appointment;
- multiple reschedules;
- cancellation;
- rebooking;
- historical timeline questions.

Also include:

- ordinary email;
- work noise;
- unrelated social plans;
- one opportunity;
- one task.

### Development Life B: entity collision and source authority

Core story:

- John's poker game;
- Eric's poker game;
- one is canceled;
- one is moved;
- a misleading participant claims the wrong game was canceled;
- organizer correction;
- stale repetition later.

Questions test:

- current state;
- entity resolution;
- historical state;
- source authority;
- false merge resistance.

### Development Life C: proactive tasks and broad context

Core story:

- user borrows Eric's coat;
- user says they must return it;
- later user says they are visiting Eric;
- assistant should proactively surface the reminder;
- negative-control visit where no reminder should fire.

Also include:

- several weekend opportunities;
- one declined invitation;
- one tentative invitation;
- one commitment;
- broad question such as "Anything fun this weekend?"

These three lives should include enough noise and multiple channels to exercise the complete pipeline.

---

## 44. Engineering workstreams

An engineering team can divide implementation into five major workstreams.

### A. Corpus

Build:

- LifeSpec schema;
- prompt builder;
- Spark generation;
- observation format;
- support manifest;
- validators;
- corpus freezing and versioning.

### B. Runtime

Build:

- isolated Hermes provisioning;
- simulated clock;
- conversation driving;
- memory-tool boundary;
- lifecycle and maintenance scheduling;
- provider namespaces;
- cleanup.

### C. Provider adapters

Build a small common contract for:

- reset;
- ingestion;
- readiness;
- scheduled processing;
- retrieval integration;
- cleanup;
- usage reporting.

### D. Evaluation

Build:

- checkpoint execution;
- deterministic normalization;
- narrow semantic grading;
- proactive grading;
- failure classification;
- difficulty annotations;
- regrading.

### E. Reporting

Build:

- run manifests;
- task-level review records;
- aggregate metrics;
- difficulty curves;
- cost and latency reports;
- provider comparisons;
- audit artifacts.

---

## 45. Definition of done for the three-life pilot

The three-life pilot is complete when:

1. All three lives can be generated and frozen.
2. Every scored fact has reviewed supporting evidence.
3. Noise and non-scored traffic are present.
4. Hermes runs in isolated simulated time.
5. At least Memcal and one external provider can receive the same observations.
6. Direct user turns run through real Hermes.
7. The benchmark captures actual memory tool calls and results.
8. Current-state questions are graded.
9. Historical timeline questions are graded.
10. Entity-collision questions are graded.
11. Opportunity versus commitment behavior is graded.
12. Contextual proactive reminders are graded.
13. False-positive proactive reminders are graded.
14. The report distinguishes retrieved evidence from final Hermes behavior.
15. Question-time tool calls, evidence read, latency, and cost are recorded.
16. A reviewer can inspect one failed answer end to end from source evidence to final grade.
17. A reviewer can inspect one successful answer end to end.
18. No provider can access the hidden answer key.
19. No run uses the user's real Hermes profile or real personal data.
20. The full pilot can be reproduced from a pinned manifest.

---

## 46. Success criteria for PACBench itself

PACBench is successful if it can answer questions more useful than:

> "Which system got the highest aggregate score?"

It should allow an engineer or researcher to say things such as:

- Provider A remains accurate as thread size increases but becomes slow.
- Provider B handles current state well but loses historical transitions.
- Provider C merges similar recurring events.
- Provider D retrieves the correct evidence but Hermes frequently misuses it.
- Provider E handles opportunities but over-promotes tentative plans into commitments.
- Provider F is strong on direct questions but weak at contextual reminders.
- Memcal performs well on eight-step state changes but has a failure mode around source authority.
- A particular Memcal failure can be reproduced as a regression test and fixed.

The benchmark should be useful even when Memcal loses.

---

## 47. The central product question

PACBench ultimately evaluates a broader capability than ordinary memory:

> **Can an assistant maintain a trustworthy model of what is true about my life now, preserve how it got that way, know what is merely possible versus committed, remember what still needs to happen, surface relevant context at the right moment, and explain the evidence without drowning in stale information?**

That is the personal-assistant capability PACBench is intended to measure.

