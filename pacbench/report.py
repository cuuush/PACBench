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


@dataclass
class Summary:
    checkpoints: int = 0
    slots: int = 0
    correct: int = 0
    missed: int = 0
    misrepresented: int = 0
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
            "by_difficulty_tag": self.by_tag,
        }


def summarize(grades: list[Grade], checkpoints: list[Checkpoint]) -> Summary:
    """Aggregate grades. `checkpoints[i]` is the checkpoint `grades[i]` answers."""
    summary = Summary()
    tag_stats: dict[str, list[int]] = {}  # tag -> [correct, total]
    for result, checkpoint in zip(grades, checkpoints):
        summary.checkpoints += 1
        scored = checkpoint.slots or checkpoint.supported
        kinds = {s.id: s.kind for s in scored}
        for slot in result.slots:
            kind = kinds.get(slot.id)
            if kind is None:  # forbidden-option extra slot on open-ended grading
                continue
            summary.slots += 1
            ok = slot.verdict == Verdict.CORRECT
            summary.correct += ok
            summary.missed += slot.verdict == Verdict.MISSED
            summary.misrepresented += slot.verdict == Verdict.MISREPRESENTED
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
