"""Model-only semantic verdicts for a natural-language checkpoint contract.

The model sees the question, answer, and each independently testable claim.
Python validates the response shape and aggregates verdicts, but never matches
answer text or chooses a semantic verdict. A failed judge call cannot pass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .model_calls import ModelOutput

Generate = Callable[[str], str]


@dataclass(frozen=True)
class Claim:
    id: str
    description: str
    evidence: tuple[str, ...] = ()


@dataclass
class GradedClaim:
    id: str
    verdict: str
    reason: str


@dataclass
class Grade:
    checkpoint: str
    slots: list[GradedClaim] = field(default_factory=list)
    correct: int = 0
    missed: int = 0
    misrepresented: int = 0
    claim_coverage: float = 0.0
    all_claims_correct: bool = False


VERDICTS = {"correct", "missed", "misrepresented"}


def judge_prompt(question: str, answer: str, claims: tuple[Claim, ...]) -> str:
    spec = [{"id": c.id, "claim": c.description,
             "evidence": list(c.evidence)} for c in claims]
    return (
        "You are a semantic memory-benchmark judge. Evaluate each claim "
        "independently against the assistant's answer to the user's question. "
        "Use meaning and context, not string matching or a demand for "
        "verbatim wording. Grade only the details stated in each claim. "
        "A fact mentioned as "
        "historical does not count as an assertion about the current plan. "
        "If absent or explicitly "
        "unknown, mark missed; if contradicted or a wrong value is asserted "
        "for that fact, mark misrepresented; if correctly conveyed, mark "
        "correct. Do not infer facts from the question or claim alone. "
        "Return ONLY one JSON object with 'verdicts', an array in the same "
        "order, each containing id, verdict (correct/missed/misrepresented), "
        "and a short reason grounded in the answer.\n\n"
        f"Question: {question}\nAnswer: {answer}\n"
        f"Claims (hidden from the assistant): {json.dumps(spec, ensure_ascii=False)}"
    )


def grade_semantic(checkpoint_id: str, question: str, answer: str,
                   claims: tuple[Claim, ...], generate: Generate,
                   *, record_path: Path | None = None) -> Grade:
    """Judge every claim with a model; reject missing/malformed verdicts."""
    if not claims or len({c.id for c in claims}) != len(claims):
        raise ValueError("claims must be nonempty with unique IDs")
    prompt = judge_prompt(question, answer, claims)
    response = generate(prompt)
    output = response.text if isinstance(response, ModelOutput) else response
    if record_path is not None:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps({
            "checkpoint": checkpoint_id, "question": question,
            "answer": answer, "claims": [vars(c) for c in claims],
            "prompt": prompt, "response": output,
            "model_call": (response.record_path if isinstance(response, ModelOutput)
                           else None)}, indent=2, ensure_ascii=False) + "\n")
    try:
        parsed = json.loads(output)
        rows = parsed["verdicts"]
        valid = (isinstance(rows, list) and len(rows) == len(claims) and
                 all(isinstance(r, dict) and r.get("id") == c.id and
                     r.get("verdict") in VERDICTS and
                     isinstance(r.get("reason"), str) and r["reason"].strip()
                     for r, c in zip(rows, claims)))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"unusable semantic judge response: {output[:200]!r}") from exc
    if not valid:
        raise ValueError(f"unusable semantic judge verdicts: {output[:200]!r}")
    result = Grade(checkpoint=checkpoint_id)
    result.slots = [GradedClaim(r["id"], r["verdict"], r["reason"]) for r in rows]
    result.correct = sum(r.verdict == "correct" for r in result.slots)
    result.missed = sum(r.verdict == "missed" for r in result.slots)
    result.misrepresented = sum(r.verdict == "misrepresented" for r in result.slots)
    result.claim_coverage = result.correct / len(claims)
    result.all_claims_correct = result.correct == len(claims)
    return result
