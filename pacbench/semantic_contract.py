"""Build and freeze an evidence-backed contract before provider execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model_calls import ModelOutput


def _text(result: str | ModelOutput) -> str:
    return result.text if isinstance(result, ModelOutput) else result


def _json(result: str | ModelOutput) -> dict:
    value = json.loads(_text(result))
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


def _dialogue(observations: list[dict]) -> str:
    return "\n".join(f"{o['source_id']} | written {o['written_at']} | "
                     f"{o['sender']}: {o['text']}"
                     for o in observations)


@dataclass(frozen=True)
class ContractClaim:
    id: str
    statement: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationContract:
    claims: tuple[ContractClaim, ...]
    question: str
    evidence_hash: str
    contract_hash: str

    def public_judge_context(self) -> dict:
        return {"claims": [vars(c) for c in self.claims],
                "question": self.question, "contract_hash": self.contract_hash}


def build_contract(*, scene: str, test_intent: str,
                   observations: list[dict], generate: Callable,
                   verify: Callable, question_generate: Callable,
                   output_path: Path,
                   author_facts: tuple[str, ...] = ()) -> EvaluationContract:
    """Derive, independently verify, and freeze claims before either arm runs."""
    if not observations:
        raise ValueError("contract needs frozen dialogue")
    dialogue = _dialogue(observations)
    ids = {o["source_id"] for o in observations}
    if len(ids) != len(observations):
        raise ValueError("duplicate dialogue source ids")
    fact_instruction = (
        "The author supplied the following required facts. Make exactly one "
        "independently assessable claim for each, in the same order, with IDs "
        "fact-1, fact-2, and so on. Cite dialogue evidence for each. Do not "
        "merge facts, add claims, or treat intended but absent details as true. "
        "If a supplied fact combines independently testable details, flag "
        "that in the review rather than silently splitting it.\nRequired facts:\n" +
        "\n".join(f"{i}. {fact}" for i, fact in enumerate(author_facts, 1)) + "\n"
        if author_facts else "")
    proposed = _json(generate(
        "Identify independently assessable factual claims that cover every "
        "detail required by the test intent. Separate details that an answer "
        "could get right or wrong independently, such as a date and a time, "
        "or a venue name and its street. Resolve relative days from each "
        "message's written timestamp where the dialogue makes that clear. "
        "Cite exact dialogue source IDs for "
        "each. "
        "A proposed plan is different from an agreed plan. Do not include "
        "scenario details absent from the dialogue. Return only JSON: "
        '{"claims":[{"id":"short-id","statement":"current fact",'
        '"evidence":["source-id"]}]}.\n\n'
        f"Private scenario: {scene}\nTest intent: {test_intent}\n"
        f"{fact_instruction}"
        f"Frozen dialogue:\n{dialogue}"))
    rows = proposed.get("claims")
    if not isinstance(rows, list) or not rows:
        raise ValueError("contract generator returned no claims")
    if author_facts and len(rows) != len(author_facts):
        raise ValueError("contract must have exactly one claim per required fact")
    claims = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("malformed contract claim")
        cid, statement, evidence = (row.get("id"), row.get("statement"),
                                    row.get("evidence"))
        if (not isinstance(cid, str) or not cid or cid in seen or
                (author_facts and cid != f"fact-{len(claims) + 1}") or
                not isinstance(statement, str) or not statement.strip() or
                not isinstance(evidence, list) or not evidence or
                any(not isinstance(e, str) or e not in ids for e in evidence)):
            raise ValueError(f"malformed or unsupported citation in claim {cid!r}")
        seen.add(cid)
        claims.append(ContractClaim(cid, statement.strip(), tuple(evidence)))

    reviewed = _json(verify(
        "Review each proposed claim against the dialogue and test intent. "
        "When required facts are supplied, verify that each matching claim "
        "faithfully covers exactly that one fact, that the authored fact is "
        "independently assessable, and that no current-state detail was "
        "invented from an old or tentative mention. Reject a compound fact "
        "that should be split by its author. "
        "Also decide whether the set covers every intent-required detail "
        "that the dialogue actually establishes, with independent claims "
        "where one detail could be omitted or wrong while another is right. "
        "A calendar date can be established by a relative day paired with "
        "a message's written timestamp; do not ignore those timestamps. "
        "Reject unsupported, irrelevant, or ambiguous claims, including a "
        "commitment inferred from a mere suggestion. Return only JSON: "
        '{"complete":true,"coverage_reason":"brief explanation",'
        '"reviews":[{"id":"claim-id","supported":true,'
        '"relevant":true,"unambiguous":true,"reason":"brief explanation"}]}. '
        "One review per claim in order.\n\n"
        f"Test intent: {test_intent}\n{fact_instruction}Frozen dialogue:\n{dialogue}\n"
        f"Proposed claims: {json.dumps([vars(c) for c in claims])}"))
    reviews = reviewed.get("reviews")
    if (reviewed.get("complete") is not True or
            not isinstance(reviews, list) or len(reviews) != len(claims) or
            any(not isinstance(r, dict) or r.get("id") != c.id or
                any(r.get(k) is not True for k in
                    ("supported", "relevant", "unambiguous")) or
                not isinstance(r.get("reason"), str)
                for r, c in zip(reviews, claims))):
        raise ValueError(f"contract review rejected claims: {reviewed}")

    question_prompt = (
        "Write one natural user question that tests these claims together. "
        "Do not reveal any answer, person, venue, time, or other claim value. "
        "Do not invent who performed an action or make an unsupported "
        "ownership or commitment claim in the question. For tickets bought "
        "by someone else, ask about the tickets we discussed, not tickets "
        "I purchased. "
        'Return only JSON: {"question":"..."}.\n\n'
        f"Test intent: {test_intent or 'Ask about all required facts.'}\n"
        f"Claims: {json.dumps([vars(c) for c in claims])}")
    question_review_prompt = (
        "Check the proposed user question against the frozen dialogue and "
        "claims. Does it reveal an answer to a claim? Does it assert or "
        "presuppose anything the dialogue does not establish, including who "
        "bought, booked, chose, or agreed to something? Return only JSON: "
        '{"safe":true,"faithful":true,"reason":"brief explanation"}. '
        "Safe means no answer is disclosed; faithful means every factual "
        "presupposition is supported by the dialogue. For example, 'tickets "
        "I purchased' is unfaithful if a companion bought them.\n\n"
        f"Frozen dialogue:\n{dialogue}\nQuestion: {{question}}\nClaims: "
        f"{json.dumps([vars(c) for c in claims])}")
    question = None
    question_review = None
    correction = ""
    for _ in range(2):
        question_doc = _json(question_generate(question_prompt + correction))
        question = question_doc.get("question")
        if not isinstance(question, str) or not question.strip().endswith("?"):
            raise ValueError("question generator returned no usable question")
        question_review = _json(verify(question_review_prompt.replace("{question}", question)))
        if question_review.get("safe") is True and question_review.get("faithful") is True:
            break
        correction = (f"\nThe previous question was rejected: {question}. "
                      f"Reason: {question_review.get('reason', 'unspecified')}. "
                      "Write a different question that fixes this problem.")
    if question_review.get("safe") is not True or question_review.get("faithful") is not True:
        raise ValueError(f"question unsafe or unfaithful: {question_review}")
    evidence_hash = hashlib.sha256(json.dumps(observations, sort_keys=True,
                                              ensure_ascii=False).encode()).hexdigest()
    body = {"claims": [vars(c) for c in claims], "question": question.strip(),
            "evidence_hash": evidence_hash, "review": reviewed,
            "question_review": question_review,
            "author_fact_count": len(author_facts)}
    contract_hash = hashlib.sha256(json.dumps(body, sort_keys=True,
                                              ensure_ascii=False).encode()).hexdigest()
    body["contract_hash"] = contract_hash
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"contract already frozen: {output_path}")
    output_path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    return EvaluationContract(tuple(claims), question.strip(), evidence_hash,
                              contract_hash)


def load_contract(path: Path) -> EvaluationContract:
    body = json.loads(path.read_text())
    claimed = body.pop("contract_hash")
    actual = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    if actual != claimed:
        raise ValueError(f"contract hash mismatch: {path}")
    return EvaluationContract(
        tuple(ContractClaim(r["id"], r["statement"], tuple(r["evidence"]))
        for r in body["claims"]), body["question"], body["evidence_hash"],
        claimed)
