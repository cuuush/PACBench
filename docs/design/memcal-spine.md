# Memcal spine

**Current rule: the public observation contract stays provider-neutral.
PACBench never copies Memcal-only metadata into observations to inflate
Memcal scores. `is_group` stays shared provenance** (computed by the runner
over the public batch, delivered to all adapters via `group_flags`).

Public fields available (neutral, no change): `source_id, channel,
written_at, arrived_at, collected_at, sender, thread, text, from_owner,
structured`. Every classification below is judged against exactly this set:
anything Memcal needs beyond it, Memcal derives itself.

## Classification

| Item | Class | New public field? |
|---|---|---|
| `counterpart` | provider-internal | No — `sender`+`from_owner`+`thread`+`channel` suffice |
| `addressed_to` | provider-internal | No |
| `top_tier` | out of scope | No — corpus must not depend on it |
| `meta` | provider-internal | No |
| owner-handle resolution | provider-internal | No |

No item classifies as (a): every genuine public semantic in this spine is
already expressible with existing `Observation` fields, so this audit
proposes zero contract additions.

### 1. `counterpart` — provider-internal (Memcal derives itself)

What it is: `deliver()`'s explicit "exchange participant regardless of
direction" (`base.py` lines 175, 207–215). For owner turns it is the
recipient; otherwise the author. It feeds `identity.resolve()` and, via
`other`, the `entity_for` bundle key, with `thread_person()` as fallback.

Rationale: the underlying semantics — who wrote this line, is the author the
owner, which thread is it in — are already public (`sender`, `from_owner`,
`thread`, `channel`). The counterpart-to-bundle-entity mapping is Memcal's
own grouping policy (`gate.entity_for`: person beats thread except in
groups). Handing PACBench a `counterpart` field would smuggle that policy
into the neutral contract.

Neutral representation: none new. `sender` + `from_owner` + `thread` +
`channel` suffice; any adapter can reproduce "author, or thread when the
author is the owner" without provider-specific input.

Current behavior pin: the adapter never reads an obs `counterpart` (no such
key is consumed). It derives `other = None if owner else sender` locally
(`memcal_adapter.py` lines 310–312) and calls
`gate.entity_for(person=other, thread=thread_key, channel, is_group)`
(lines 331–335), so owner turns key by `thread:{channel}:{thread}` and
non-owner turns by `person:{sender}`. `handle` is passed as `None` for owner
turns, else the sender string, with `person=sender` always set — so
`deliver()`-style resolution has nothing left to resolve.

### 2. `addressed_to` — provider-internal (Memcal derives itself)

What it is: recipient-type flag (`"person"` default / `"machine"`,
`base.py` line 176; `gate.gate_message` line 265). Its only behavioral
effect is inside `gate_message` lines 286–290: an owner-authored COMMIT_RE
directive passes either way, differing only in reason (`"directive"` vs
`"own-commitment"`). It does not change any pass/fail outcome, and it is
stored on the archive row as product-side bookkeeping.

Rationale: a pass/fail-neutral reason string and an archive-column default
are product internals. There is no corpus-visible distinction between
"addressed to a person" and "addressed to a machine" in any current channel,
so a PACBench-supplied value would be invented metadata serving only
Memcal's reason labels — precisely what P0-7 forbids.

Neutral representation: none. If a future corpus source genuinely models
human-vs-machine recipients as source structure (e.g. a bot thread type),
that arrives as a P0-1 source-semantics proposal with its own neutral field,
not via this flag.

Current behavior pin: the adapter's `gate.gate_message` call
(`memcal_adapter.py` lines 294–297) omits `addressed_to`, taking Memcal's
`"person"` default; its `archive.append` call (lines 314–319) likewise omits
it. Owner directives therefore gate as `"own-commitment"`, identical to a
live source that never sets the flag.

### 3. `top_tier` — out of scope

What it is: a caller-supplied VIP sender set, checked at `gate.py`
lines 303–304 (`"top-tier-sender"` pass). It is reachable only for
non-`PASS_ALL` channels after all content tests fail — and every PACBench
conversational channel (`imessage`, `whatsapp`, `groupme`, `slack`) is in
`PASS_ALL_STREAMS`, which returns before the check is ever reached
(`gate.py` lines 275–279). Only `email`/`text` observations could reach it.

Rationale: a benchmark-authored VIP list would be sender prioritization
written by the grader's own hand — the clearest form of the prohibited
"copy metadata to make Memcal perform better." Sender importance is a
contact-book/product concern, not source provenance.

Corpus must-not-depend note: the corpus must not include sender tiers, VIP
lists, contact-priority fixtures, or any expectation whose satisfaction
depends on a `"top-tier-sender"` gate reason. A checkpoint answerable only
because a sender was tiered is an invalid checkpoint.

Current behavior pin: the adapter never passes `top_tier` (absent from the
`gate_message` call), so it is `None` and the check is dead code for this
path — matching a live source with no tier list configured.

### 4. `meta` — provider-internal (Memcal derives itself)

What it is: free-form dict (`base.py` line 171) archived verbatim on the row
and read for `seen_name` during contact resolution
(`link_by_name`/`adopt_seen_name`, lines 193–200). It is the live ingesters'
side-channel for display-name variants and source-specific extras.

Rationale: contact resolution against a private identity store is Memcal's
own subsystem. PACBench's `sender` field already supplies the identity the
contract guarantees; shipping `seen_name` variants or any other
Memcal-shaped keys inside `meta` would be coaching Memcal's resolver with
answers no other provider receives. The adapter namespace starts each run
with an empty contact store (scratch home + `reset`), which is the correct
neutral starting point — resolution quality differences from a populated
user address book are a deployment fact, not benchmark signal.

Corpus must-not-depend note: the corpus must not depend on contact-book
pre-population, display-name-variant resolution, or any `meta`-keyed
behavior. Name-variant expectations (e.g. "Devon" vs "devon") belong to
thread-identity normalization, already handled neutrally by lowercase
`thread_key` in the adapter — not by resolver coaching.

Current behavior pin: the adapter passes no `meta` to `archive.append` and
no `seen_name` anywhere; `handle`/`person` are set directly from the public
`sender`/`from_owner` fields (lines 310–319), so the `identity.resolve`
fallback chain in `deliver()` is trivially satisfied with nothing unresolved.

### 5. Owner-handle resolution — provider-internal (Memcal derives itself)

What it is: `deliver()`'s handle→person pipeline (`base.py` lines 192–202):
normalize, `identity.resolve`, `link_by_name`, `adopt_seen_name`,
`note_unresolved` + `report.unknown_handles` for the residue, plus
`_is_bulk_address` Sender-table checks. It exists because live sources hand
Memcal raw platform handles that may not match any contact.

Rationale: PACBench observations already carry the resolved public identity
in `sender` with authorship in `from_owner`. There is no raw-handle layer in
the contract to resolve — the corpus models senders, not phone-number or
platform-ID strings. Re-introducing synthetic handles for Memcal to resolve
would manufacture work that measures nothing about memory, and pre-seeding
answers (contacts, owner handles) would leak benchmark-side information into
the provider's store.

Corpus must-not-depend note: the corpus must not contain raw platform
handles, owner-handle fixtures, or expectations that depend on unresolved-
handle queue behavior. `sender` strings are opaque identifiers; grading may
rely on their stability within a life, never on their resolvability in any
product's contact store.

Current behavior pin: the adapter sets `handle=None` for owner turns and
`handle=sender` otherwise, with `person=sender` always (`memcal_adapter.py`
lines 314–319). No `unknown_handles` path is reachable from benchmark input;
`from_owner_flag(obs)` (`memcal_adapter.py` line 523–524) is the sole
authorship signal, read from the public `from_owner` field.

## `is_group` provenance (unchanged, pinned)

`is_group` remains shared provenance: computed by
`pacbench/live.py::_thread_group_flags` over the public batch (3+ distinct
non-owner senders on a thread), delivered to adapters via `group_flags`, and
consumed identically by the Memcal adapter (`memcal_adapter.py` lines 287–293)
for both `gate_message` and `entity_for`. It is not Memcal-only information
and no per-provider group signal may replace it.

## History

Plan.md P0-7, merged from `docs/memcal-spine-audit.md`. Doc-first audit:
**no code changes were made and none are proposed** — implementation follows
P0-1 approval, and current adapter behavior already matches every
classification. References pinned 2026-09-22: `~/code/memcal/memcal/sources/
base.py` `deliver()` (~line 159, body 159–257); `~/code/memcal/memcal/gate.py`
`gate_message` (257–305), `PASS_ALL_STREAMS` (213–220),
`entity_for`/`bundle_entity` (380–400);
`provider_adapters/memcal_adapter.py` `ingest` (lines 236–359, read-only for
the audit); `pacbench/observation.py` public fields;
`pacbench/live.py` `_thread_group_flags` (lines 54–72).
