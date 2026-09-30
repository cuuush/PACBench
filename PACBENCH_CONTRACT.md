# PACBench Benchmark Contract

**Status:** Draft · **Version:** 0.1
**Pilot:** 3 synthetic lives · **Comparative target:** 30 independent lives · **Scored horizon:** 7 consecutive simulated days per life

## Purpose

PACBench tests whether Hermes with one memory provider can track a person's changing life across noisy communication: current and past plans, commitments, opportunities, tasks, and reminders. It scores behavior, not storage architecture.

The primary comparison is **Hermes + provider**. A Memcal loss remains in the benchmark and becomes a product finding.

## Experimental unit and corpus

A **life** is one synthetic person's coherent, overlapping stories. Seven consecutive fictional days are scored; about a month of older history may precede them.

The pilot has three development lives; the comparative target has 30 independent lives split into development, calibration, and held out sets. Related variants stay together. A life inspected or used for tuning becomes regression coverage, not held out evidence.

Vary life size and distribute substantial traffic across threads and channels. Important evidence should sometimes resemble noise. Report corpus volume; do not assume full history fits in a question prompt.

Streams include email, direct texts, WhatsApp, group chat, Hermes conversations, and workplace messages when relevant. Preserve content, sender, channel, thread/reply links, source time, arrival time where relevant, and collection time.

## Truth, evidence, and authoring

Keep three layers separate:

1. **World truth:** what happened in the fictional life, including changes and unresolved states.
2. **Evidence:** messages and other records available to Hermes and the provider by a given fictional time.
3. **Expected behavior:** what an assistant should say or surface at that checkpoint, given only exposed evidence.

Authors provide a prose `scene` and short plain language `facts`; concrete `test_intent` remains supported. Optional `at` anchors relative dates. The writer chooses length; times and IDs are derived unless scenes share a thread. Larger lives may use a human reviewable LifeSpec for stories, transitions, evidence placement, difficulty, and checkpoints. Neither authoring form dictates provider storage.

A generation model, initially Spark, writes dialogue and ambient traffic. It chooses expression, not benchmark truth or expected answers. Generate connected conversation windows where useful. Reject consequential invented facts or deliberately add them to truth and grading before freezing.

Generation yields a short writer note and cited support. Verify that dialogue establishes the scene and every fact; review contradictions and ambiguities. Freeze messages, question, and evidence cited claim contract **before either provider runs**. Hide private scene and contract from both arms. Bind runs to content hashes, never edited fixtures by source ID alone.

Validate structure, chronology, exposure timing, and private data separation. Semantically verify cited facts. Humans review development lives and a meaningful release sample, including answer keys. Never infer truth from provider output.

## Capability coverage

Scenarios should combine ordinary and difficult cases across these dimensions:

| Dimension | Behavior under test |
| --- | --- |
| Current state and updates | Track reschedules, corrections, cancellation, reversal, replacement, completion, and expiry without reviving stale state. |
| History | Reconstruct original and intermediate states, order of changes, and why an old answer once seemed true. |
| Identity | Keep similar people, events, recurring occurrences, and threads distinct even when users say only “the game.” |
| Source authority | Consider speaker, role, direct knowledge, hearsay, tentativeness, and later confirmation using visible evidence. |
| Cross source joining | Combine facts spread across channels, such as a date, address, and attendance decision. |
| Future state | Distinguish an invitation or opportunity from a user commitment, and both from a task. |
| Proactivity | Surface a relevant unresolved task in an ordinary turn; avoid reminders when no task exists or it is done. |
| Information lifetime | Keep short lived state current only while valid while retaining appropriate history and durable facts. |
| Uncertainty | Express uncertainty before resolution and give a definite answer after decisive evidence arrives. |
| False memory | Avoid invented plans, false merges, wrong person or occurrence links, stale assertions, and assistant claims recycled as independent evidence. |
| Broad questions | Find relevant plans or tasks for “Anything fun this weekend?” or “What should I bring?” without a precise retrieval keyword. |

Noise includes unrelated and similar chatter, stale confirmations, near duplicates, tentative alternatives, hearsay, and retellings. Durable facts may appear casually.

Vary thread length, total history, update depth, entity collisions, required channels, conflict, and evidence age. Use matched controls: unchanged versus updated, one versus similar entities, concentrated versus distributed facts, unresolved versus resolved, and reminder needed versus not needed.

Test contextual reminders and matched negative controls. Add autonomous notifications only when providers share a controlled mechanism.

## Common runtime and fair comparison

Use isolated Hermes and the same outer model where practical. Give providers the **same frozen messages, question, collection schedule, and fictional clock**. Permit native ingestion, consolidation, retrieval, context, and lifecycle. Document necessary, fairness neutral harness accommodations; never add answer specific repairs.

Hermes may use only the selected provider and current conversation. Exclude other memory, hidden keys, benchmark files or shell, and web answers. User messages are real Hermes turns; Hermes chooses tools. Persist its answers normally but preserve authorship so hallucinations do not become independent evidence.

Pin fictional time across collection, Hermes, provider tools, prefetch, maintenance, and checkpoints. Declare real processing allowance between fictional events.

Check SDK/MCP access, discovery, allowlist, and read only recall separately from whether Hermes calls recall. Log both; do not turn access failures into answer verdicts.

Raw history, oracle evidence, and lexical baselines are optional diagnostics, not full life provider comparisons.

## Checkpoints and grading

A checkpoint specifies fictional time, turn or trigger, task family, difficulty, and private atomic claims with evidence, accepted alternatives, and forbidden assertions. Review its contract before matched provider runs.

Grade answer meaning with a blinded semantic judge given the question, frozen evidence cited claims, and answer, without provider identity or score. Python validates IDs, citations, shapes, and counts; no string matching, entity substrings, or date expansion decides meaning. Missing or malformed verdicts leave runs **ungraded**. Calibrate uncertainty and historical mentions before comparison.

Separate current state from history. Reward uncertainty before resolution, penalize it afterward, and report false positives prominently. Broad question rubrics distinguish supported items from expired, declined, misattributed, or unsupported ones. Score support and coverage, not style.

Trace ingestion, representation, retrieval, Hermes tool choice, evidence reading, final expression, and runtime failures. Assign causes only with supporting traces. Keep access, recall use, and answer quality separate.

## Measurement and reporting

Report multiple measures rather than one winner number:

- **Quality:** task success, factual precision and recall, current and historical state, timelines, complete plans, opportunity versus commitment handling, task reminders, uncertainty, and proactivity.
- **Errors:** stale or invented claims, missed updates, false merges, wrong identity or occurrence, duplicate commitments, expired task resurfacing, and proactive false positives.
- **Work:** median and tail latency, memory and model calls, archive reads, returned items and evidence text read, tokens, observable provider reasoning, background processing, and lifecycle cost.
- **Operations:** ingestion or readiness failures, timeouts, tool policy failures, unsupported tasks, incomplete and ungraded runs.

Publish quality and work against thread length, total history, update depth, entity collision, channel distribution, conflict, and evidence age where sample size supports it. Report denominators and uncertainty over **independent lives or story families**, not individual facts treated as independent experiments. Repeat fixed life and provider configurations to expose stochasticity; preserve every realization and failure. Never retry silently until a favorable result occurs.

Every run must retain an immutable manifest and hashes; corpus and model versions and configurations; exact exposed observations, batches, and fictional times; Hermes transcript; available tool definitions, calls, arguments, results, and automatic context; raw answers and per claim judgments; readiness and maintenance events; latency, token, usage, and cost records; and failures or retries. Keep full audited model requests and responses in private artifacts. Redact private reasoning payloads such as `encrypted_content` from explorer exports. If a model API omits a dollar charge, record cost as unavailable. The artifacts must let a reviewer follow a success or failure from source evidence to grade without exposing private keys to an arm.

A run is valid only when provider isolation, correct frozen corpus and schedule, consistent fictional time, active provider, Hermes checkpoint turns, trace capture, and answer key secrecy hold. A setup defect yields an invalid or incomplete run, never a pass. Missing judge output yields an ungraded run.

## Pilot and completion criteria

The three development lives should exercise different paths:

1. An appointment with repeated moves, cancellation or rebooking, and timeline questions, plus ordinary unrelated traffic.
2. Two similar events with different updates, misleading hearsay, organizer correction, stale repetition, and identity questions.
3. A contextual task reminder and negative control, plus tentative, declined, and committed opportunities and a broad question.

They should span channels, noise, current and historical state, and provider lifecycle behavior. Pizza Day and progressively harder variants may be used to build and debug the pipeline, but a single Pizza Day run does not complete the three life pilot or justify a provider comparison.

The pilot is done when all three lives are generated, reviewed, hashed, and frozen; relevant evidence supports each scored claim; noise is present; isolated Hermes and at least Memcal plus one external provider receive matched observations under the same simulated time; user turns and memory tools are real and traced; current state, history, identity, opportunity versus commitment, proactive success and false positives are graded; access, retrieval, Hermes use, and answer quality can be inspected separately; work and cost are recorded; one success and one failure can be audited end to end; no arm sees private truth or real personal data; and the full pilot is reproducible from pinned manifests.

Before comparative claims, complete the declared development, calibration, and held out split for the larger corpus; review calibration labels; run repeated matched trials; and report failures and difficulty curves alongside accuracy and cost. PACBench succeeds if it can explain *how* systems succeed or fail as personal assistants, including when Memcal loses.
