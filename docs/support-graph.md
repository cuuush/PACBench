# Support-graph semantics (P0-12)

## 1. `supported_by` is cited-only — frozen

`Slot.supported_by` (and `SupportEntry.observation_ids` in the manifest)
means exactly one thing:

> The listed observations are **cited as relevant evidence** for the
> slot/fact. They were available before the checkpoint and carry (or were
> intended to carry) the required value.

It does **NOT** mean:

- that the set is **sufficient** (that these observations, together, settle
  the answer);
- that any one of them is **necessary** (that removing it would change the
  answer);
- that a checkpoint citing N channels **depends on** N channels.

This meaning is frozen. No validator, grader, difficulty function, or report
may silently upgrade a citation into a sufficiency or necessity claim.
`required_support_ids` (`pacbench/difficulty.py`) is likewise a citation
closure — the ids the rubric cites — not a minimal set. Any stronger claim
must use the explicit concepts below and pass ablation (§4).

## 2. Sufficient evidence set

A **sufficient set** for a checkpoint is a set of observations, all collected
at or before the checkpoint time, such that every required slot's expected
value (or an accepted alternative) is deterministically carried by the set
under the literal-carry rule (`evidence_carries` in
`pacbench/validate.py`: casefolded containment, plus time-variant and
bare-hour matching for `TIME`; `YESNO` is behavior, not a cited literal,
and is outside the rule).

Schema sketch (authoring-time claim, not a scored-run input):

```python
@dataclass(frozen=True)
class SufficientSet:
    checkpoint_id: str
    observation_ids: tuple[str, ...]  # subset of cited ids, all pre-checkpoint
    # Claim: every required literal slot of the checkpoint is carried
    # by the texts of observation_ids under the literal-carry rule.
```

A sufficient set is **minimal** if removing any member breaks carriage for
at least one required slot. Minimality is checked by ablation, one removal
at a time — never asserted by inspection.

## 3. Required multi-source dependency

A checkpoint has a **required multi-source dependency** on a set of
sources/channels only if it is genuinely jointly determined by them:

> There exist two (or more) disjoint source groups G1, G2, each cited by the
> checkpoint, such that removing either group leaves at least one required
> slot uncarried under the literal-carry rule — i.e. the answer changes or
> becomes uncertain.

Schema sketch:

```python
@dataclass(frozen=True)
class MultiSourceClaim:
    checkpoint_id: str
    essential_groups: tuple[tuple[str, ...], ...]  # e.g. (("email",), ("slack",))
    # Claim: ablating any one group flips ablation_check(...).answer_changed_or_uncertain
```

`multi_source_dependency(life)` lists checkpoints whose *citations* span
more than one channel. That is a candidacy list, not a proof: a checkpoint
can cite two channels while depending on one (redundant citation). Proof is
per-group ablation via `ablation_check`.

## 4. Ablation procedure (`ablation_check`)

`ablation_check(life, checkpoint, channel_or_source)`:

1. Resolve the removal: a channel name removes every observation on that
   channel; otherwise the argument is one `source_id` and removes that
   observation. Unknown names raise `ValueError`.
2. For each required slot with an expected value under a literal norm
   (`LITERAL_NORMS`, non-`ABSTAIN` kind), recompute carriage twice over
   pre-checkpoint cited evidence: with the full cited set (baseline) and
   with the ablated set.
3. Any slot carried at baseline but uncarried after ablation is affected;
   `answer_changed_or_uncertain` is true iff at least one slot is affected.
4. Slots outside the literal rule (`YESNO`, slots with no expected value,
   `ABSTAIN`) are reported as undecidable-by-ablation in `detail`, never
   silently passed or failed.

## 5. Claim discipline

Until a checkpoint's multi-source claim passes ablation, reports must not
make quantitative claims about "number of required channels" for it.
`required_channels` remains a descriptive citation-span measure. Qualitative
audit use is fine provided the unproven status is stated.
