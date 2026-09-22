"""Deterministic report aggregation over saved grades.

v0.1 aggregates what the deterministic grader already produces — no model, no
network. Later stages feed it per-checkpoint grades from live Hermes runs
(regrading saved answers never needs re-ingestion); here it is exercised on
hand-written answers in tests and examples.

Reported (never collapsed into one number):

- task success rate (complete-plan occasions fully correct);
- factual precision / recall / F1 over answerable fact slots;
- current-state vs historical-state accuracy (split by slot kind);
- abstention accuracy on unanswerable checkpoints;
- open-ended opportunity recall and forbidden-suggestion precision;
- error counts: missed, misrepresented (stale/invented/wrong-person), and
  forbidden suggestions surfaced;
- per-difficulty-tag breakdowns (accuracy vs. difficulty curves start here).

Unanswered answerable slots count as missed; timeouts, tool-policy failures, and
refusals of answerable questions are failures and stay in the denominator — the
aggregation only sees the grades it is given, so callers must pass one grade per
executed checkpoint including failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grade import Grade
from .rubric import Checkpoint, Kind, Verdict
from .exposure import compare_exposure


@dataclass
class Summary:
    checkpoints: int = 0
    slots: int = 0
    correct: int = 0
    missed: int = 0
    misrepresented: int = 0
    #: §36 error taxonomy: MISREPRESENTED verdicts broken out by the slot's
    #: declared error class (rubric.ERROR_CLASSES). Slots without a class
    #: (none should exist — validation requires one wherever forbidden
    #: values exist) count under "unlabeled" rather than vanishing.
    errors: dict[str, int] = field(default_factory=dict)
    current_correct: int = 0
    current_total: int = 0
    historical_correct: int = 0
    historical_total: int = 0
    complete_plan_occasions: int = 0
    complete_plan_correct: int = 0
    abstention_checkpoints: int = 0
    abstention_sum: float = 0.0
    options_found: int = 0
    options_total: int = 0
    suggested_forbidden: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    by_tag: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "checkpoints": self.checkpoints,
            "slots": self.slots,
            "correct": self.correct,
            "missed": self.missed,
            "misrepresented": self.misrepresented,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "current_accuracy": (self.current_correct / self.current_total
                                 if self.current_total else None),
            "historical_accuracy": (self.historical_correct / self.historical_total
                                    if self.historical_total else None),
            "complete_plan_accuracy": (self.complete_plan_correct / self.complete_plan_occasions
                                       if self.complete_plan_occasions else None),
            "abstention_accuracy": (self.abstention_sum / self.abstention_checkpoints
                                    if self.abstention_checkpoints else None),
            "opportunity_recall": (self.options_found / self.options_total
                                   if self.options_total else None),
            "forbidden_suggested": self.suggested_forbidden,
            "errors": dict(self.errors),
            "by_difficulty_tag": self.by_tag,
        }


def check_adapter_digests(digests: list[str | None]) -> str | None:
    """Fail closed on mixed adapter-config digests (plan.md P0-2).

    Returns the single agreed digest (``None`` when every run predates
    the digest field). Raises ``RuntimeError`` naming each distinct
    digest when runs ingested under different adapter configs would
    otherwise blend silently.
    """
    distinct = sorted({d for d in digests if d is not None})
    if len(distinct) > 1:
        raise RuntimeError(
            "refusing to aggregate runs with different "
            f"adapter-config digests: {', '.join(distinct)}")
    return distinct[0] if distinct else None


def manifest_digest(manifest: dict) -> str | None:
    """One manifest's ``adapter_config_digest`` (``None`` if absent)."""
    recipe = manifest.get("provider_recipe", None) or {}
    return recipe.get("adapter_config_digest")


def check_manifest_digests(manifests: list[dict]) -> str | None:
    """Refuse to aggregate manifests ingested under mixed digests."""
    return check_adapter_digests([manifest_digest(m) for m in manifests])


#: Run-name prefixes that are diagnostic-only by construction (P0-11):
#: the checkpoint debugging aid never had a frozen-bundle path.
_DIAGNOSTIC_RUN_PREFIXES = ("pacbench-w4-live",)


def require_scored_manifest(manifest: dict) -> bool:
    """Refuse draft-text / diagnostic-only runs at the scoring gate.

    Returns True for a manifest that may enter scored aggregation: a
    frozen-bundle life episode. Raises ``RuntimeError`` for
    diagnostic-only checkpoint runs (``diagnostic_only`` or a
    ``pacbench-w4-live*`` run name, with or without the flag for
    pre-P0-11 manifests) and for any manifest whose ``evidence_source``
    is present but is not ``"frozen-bundle"``.
    """
    run = str(manifest.get("run", ""))
    if manifest.get("diagnostic_only", False):
        raise RuntimeError(
            f"refusing diagnostic-only run {run!r}: never a scored result "
            f"(scored runs require run-life/run-pilot on a frozen bundle)")
    if any(run.startswith(prefix) for prefix in _DIAGNOSTIC_RUN_PREFIXES):
        raise RuntimeError(
            f"refusing diagnostic-only run {run!r}: the checkpoint "
            f"debugging aid has no frozen-bundle path and is never scored")
    source = manifest.get("evidence_source")
    if source is not None and source != "frozen-bundle":
        raise RuntimeError(
            f"refusing non-frozen evidence source {source!r} in run "
            f"{run!r}: scored runs consume hash-verified frozen bundles (§5)")
    return True


def check_exposure_equivalence(manifest_a: dict, manifest_b: dict) -> dict:
    """Comparative-validity gate over two runs' exposure ledgers (P0-6).

    Returns the :func:`pacbench.exposure.compare_exposure` result on a
    match; raises ``RuntimeError`` naming every divergent boundary
    otherwise. Two providers' runs for the same life/configuration may
    be compared only through this gate.
    """
    result = compare_exposure(manifest_a, manifest_b)
    if not result["match"]:
        lines = [f"{m['boundary']}: {m['reason']} ({m['detail']})"
                 for m in result["mismatches"]]
        raise RuntimeError(
            "refusing to compare runs with different offered evidence: "
            + "; ".join(lines))
    return result


def summarize(grades: list[Grade], checkpoints: list[Checkpoint],
                adapter_digests: list[str | None] | None = None) -> Summary:
    """Aggregate grades. `checkpoints[i]` is the checkpoint `grades[i]` answers.

    `adapter_digests` carries one run-manifest ``adapter_config_digest``
    per aggregated run (plan.md P0-2): mixed digests refuse to aggregate
    (fail closed, naming the digests) instead of silently blending runs
    ingested under different adapter configs. ``None`` (default) keeps
    the legacy caller contract — pure grade aggregation with no
    provenance check.
    """
    if adapter_digests is not None:
        check_adapter_digests(list(adapter_digests))
    summary = Summary()
    tag_stats: dict[str, list[int]] = {}  # tag -> [correct, total]
    for result, checkpoint in zip(grades, checkpoints):
        summary.checkpoints += 1
        scored = checkpoint.slots or checkpoint.supported
        kinds = {s.id: s.kind for s in scored}
        errors = {s.id: (s.error or "unlabeled")
                  for s in (*checkpoint.slots, *checkpoint.supported,
                            *checkpoint.forbidden_options)}
        for slot in result.slots:
            kind = kinds.get(slot.id)
            if kind is None:  # forbidden-option extra slot on open-ended grading
                if slot.verdict == Verdict.MISREPRESENTED:
                    summary.misrepresented += 1
                    cls = errors.get(slot.id, "unlabeled")
                    summary.errors[cls] = summary.errors.get(cls, 0) + 1
                continue
            summary.slots += 1
            ok = slot.verdict == Verdict.CORRECT
            summary.correct += ok
            summary.missed += slot.verdict == Verdict.MISSED
            summary.misrepresented += slot.verdict == Verdict.MISREPRESENTED
            if slot.verdict == Verdict.MISREPRESENTED:
                cls = errors.get(slot.id, "unlabeled")
                summary.errors[cls] = summary.errors.get(cls, 0) + 1
            if kind is Kind.CURRENT:
                summary.current_total += 1
                summary.current_correct += ok
            elif kind is Kind.HISTORICAL:
                summary.historical_total += 1
                summary.historical_correct += ok
        if checkpoint.mode == "complete-plan":
            summary.complete_plan_occasions += 1
            summary.complete_plan_correct += bool(result.complete_plan)
        if result.abstention is not None:
            summary.abstention_checkpoints += 1
            summary.abstention_sum += result.abstention
        if checkpoint.mode == "open-ended":
            summary.options_found += result.options_found or 0
            summary.options_total += result.options_total or 0
            summary.suggested_forbidden += result.suggested_forbidden
        for tag in checkpoint.difficulty_tags:
            stats = tag_stats.setdefault(tag, [0, 0])
            for slot in result.slots:
                if slot.id in kinds:
                    stats[1] += 1
                    stats[0] += slot.verdict == Verdict.CORRECT
    tp = summary.correct
    fp = summary.misrepresented + summary.suggested_forbidden
    fn = summary.missed + summary.misrepresented
    summary.precision = tp / (tp + fp) if tp + fp else 1.0
    summary.recall = tp / (tp + fn) if tp + fn else 1.0
    summary.f1 = (2 * summary.precision * summary.recall
                  / (summary.precision + summary.recall)
                  if summary.precision + summary.recall else 0.0)
    summary.by_tag = {tag: {"correct": c, "total": t,
                            "accuracy": (c / t if t else None)}
                      for tag, (c, t) in sorted(tag_stats.items())}
    return summary
