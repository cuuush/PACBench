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


def _call_json(call: Callable, prompt: str) -> dict:
    for attempt in range(2):
        try:
            return _json(call(prompt))
        except (TypeError, json.JSONDecodeError, ValueError):
            if attempt == 0:
                prompt += ("\nThe previous response was not a valid JSON object. "
                           "Return one complete JSON object with no surrounding text.")
    raise ValueError("contract model returned malformed JSON twice")


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
                   author_facts: tuple[str, ...] = (),
                   user_question: str | None = None,
                   question_at: str = "", behavior: bool = False) -> EvaluationContract:
    """Derive, independently verify, and freeze claims before either arm runs."""
    if not observations:
        raise ValueError("contract needs frozen dialogue")
    dialogue = _dialogue(observations)
    ids = {o["source_id"] for o in observations}
    if len(ids) != len(observations):
        raise ValueError("duplicate dialogue source ids")
    if behavior:
        if not author_facts:
            draft=_call_json(generate,
                'Turn the English expectation into independently assessable criteria for the assistant answer. '
                'Describe observable answer behavior; acknowledgement alone does not prove persistence. '
                'Use the expectation as written; do not add requirements. Return JSON {"criteria":["..."]}.\n'
                f'User: {user_question}\nExpectation: {test_intent}')
            criteria=draft.get('criteria')
            if not isinstance(criteria,list) or not criteria or any(not isinstance(c,str) or not c.strip() for c in criteria):raise ValueError('No usable behavioral criteria')
            author_facts=tuple(criteria)
        reviewed = _call_json(verify,
            'Review this scripted assistant interaction before running it. Expected criteria describe observable '
            'answer behavior, not proof of external execution. Check each criterion is observable, objective, '
            'fairly requested, and supported by the available source and scripted user instructions. '
            'An acknowledgement does not prove persistence. Cite source IDs supporting each '
            'expectation. Return JSON {"complete":true,"reviews":[{"id":"fact-1","supported":true,'
            '"relevant":true,"unambiguous":true,"reason":"...","evidence":["source-id"]}]}.\n'
            f'Question time: {question_at}\nUser: {user_question}\n'
            f'Criteria: {json.dumps(author_facts)}\nAvailable source and planned user turns:\n{dialogue}')
        reviews=reviewed.get('reviews',[])
        if (reviewed.get('complete') is not True or len(reviews)!=len(author_facts) or
            any(r.get('id')!=f'fact-{i}' or any(r.get(k) is not True for k in ('supported','relevant','unambiguous')) or
                not r.get('reason') or not r.get('evidence') or set(r['evidence'])-ids for i,r in enumerate(reviews,1))):
            raise ValueError(f'Behavior criteria rejected: {reviewed}')
        claims=tuple(ContractClaim(r['id'],fact,tuple(r['evidence'])) for fact,r in zip(author_facts,reviews))
        body={'claims':[vars(c) for c in claims],'question':user_question,
            'evidence_hash':hashlib.sha256(json.dumps(observations,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'review':reviewed,'question_review':{'safe':True,'faithful':True,'elicits_all':True,'reason':'Reviewed behavioral interaction'},
            'author_fact_count':len(author_facts),'question_source':'supplied','question_at':question_at,'kind':'behavior','test_intent':test_intent}
        hashed=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        output_path.parent.mkdir(parents=True,exist_ok=True)
        with output_path.open('x') as f:json.dump({**body,'contract_hash':hashed},f,indent=2,ensure_ascii=False)
        return EvaluationContract(claims,user_question,body['evidence_hash'],hashed)
    fact_instruction = (
        "The author supplied the following required facts. Return one claim "
        "per fact, in order, with IDs fact-1, fact-2, and so on. Supply exact "
        "dialogue source IDs as evidence. Do not merge facts, add claims, or "
        "treat intended but absent details as true. The authored fact text is "
        "the claim statement; do not rewrite it or add another fact's value. "
        "If a supplied fact combines details that can be graded separately, "
        "flag that in the review rather than silently splitting it.\nRequired facts:\n" +
        "\n".join(f"{i}. {fact}" for i, fact in enumerate(author_facts, 1)) + "\n"
        if author_facts else "")
    proposed = _call_json(generate,
        "Identify independently assessable factual claims that cover every "
        "detail required by the test intent. Separate details that an answer "
        "could get right or wrong independently, such as a date and a time, "
        "or a venue name and its street. Resolve relative days from each "
        "message's written timestamp where the dialogue makes that clear. "
        "Cite exact dialogue source IDs (including any assistant-user: prefix) for "
        "each. "
        "A proposed plan is different from an agreed plan. Do not include "
        "scenario details absent from the dialogue. Return only JSON: "
        '{"claims":[{"id":"short-id","statement":"current fact",'
        '"evidence":["source-id"]}]}.\n\n'
        f"Private scenario: {scene}\nTest intent: {test_intent}\n"
        f"{fact_instruction}"
        f"Frozen dialogue:\n{dialogue}")
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
        cid, evidence = row.get("id"), row.get("evidence")
        statement = (author_facts[len(claims)] if author_facts and
                     len(claims) < len(author_facts) else row.get("statement"))
        if (not isinstance(cid, str) or not cid or cid in seen or
                (author_facts and cid != f"fact-{len(claims) + 1}") or
                not isinstance(statement, str) or not statement.strip() or
                not isinstance(evidence, list) or not evidence or
                any(not isinstance(e, str) or e not in ids for e in evidence)):
            raise ValueError(f"malformed or unsupported citation in claim {cid!r}")
        seen.add(cid)
        claims.append(ContractClaim(cid, statement.strip(), tuple(evidence)))

    reviewed = _call_json(verify,
        "Review each proposed claim against the dialogue and test intent. "
        "When required facts are supplied, verify that each matching claim "
        "faithfully covers exactly that one fact. Try answering one detail "
        "correctly while omitting another detail in the same claim: if this "
        "is possible, reject that compound authored fact and ask for it to "
        "be split. Check that no current-state detail was "
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
        f"Proposed claims: {json.dumps([vars(c) for c in claims])}")
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
        "Write one short, casual question a person would actually text to "
        "their assistant. It should invite a useful recap that covers the "
        "claims, but must not read like an evaluation checklist or enumerate "
        "who/where/when fields. Give the assistant a useful scope, such as "
        "this weekend's plans; 'where do things stand?' alone is too vague. "
        "If the claims include completed events, ask about what happened as "
        "well as what is coming up. If a plan changed, a natural question can "
        "ask what changed. "
        "Do not reveal any answer, person, venue, time, or other claim value. "
        "Do not presuppose unsupported facts. "
        'Return only JSON: {"question":"..."}.\n\n'
        f"Test intent: {test_intent or 'Ask about all required facts.'}\n"
        f"Claims: {json.dumps([vars(c) for c in claims])}")
    question_review_prompt = (
        "Check the proposed user question against the frozen dialogue and "
        "claims. Does it give a useful topic or time scope without being a "
        "checklist? A question with no identifiable scope, such as 'where do "
        "things stand?', should be rejected. Does it reveal an answer to a "
        "claim? Does it assert or "
        "presuppose anything the dialogue does not establish, including who "
        "bought, booked, chose, or agreed to something? Return only JSON: "
        '{"safe":true,"faithful":true,"reason":"brief explanation"}. '
        "Safe means no answer is disclosed; faithful means the question has "
        "a useful scope and every factual presupposition is supported by the "
        "dialogue.\n\n"
        f"Frozen dialogue:\n{dialogue}\nQuestion: {{question}}\nClaims: "
        f"{json.dumps([vars(c) for c in claims])}")
    question = None
    question_review = None
    correction = ""
    if user_question is not None:
        if not isinstance(user_question, str) or not 1 <= len(user_question.strip()) <= 4000:
            raise ValueError("supplied evaluation prompt must be bounded nonempty text")
        question = user_question.strip()
        question_review = _call_json(verify,
            "Check this evaluation prompt against the dialogue and expected facts. "
            "Does it reasonably invite every expected fact, without supplying their answers "
            "or presupposing unsupported details? Respect current versus historical meaning. "
            "A topic or time scope does not itself establish which events belong in it. "
            'Return ONLY JSON {"safe":true,"faithful":true,"elicits_all":true,"reason":"explanation"}.\n'
            f"Question time: {question_at}\nPrompt: {question}\n"
            f"Expected facts: {json.dumps([vars(c) for c in claims])}\nDialogue:\n{dialogue}")
        if any(question_review.get(k) is not True for k in ("safe", "faithful", "elicits_all")):
            raise ValueError(f"supplied prompt cannot fairly elicit its facts: {question_review}")
    for _ in range(0 if user_question is not None else 2):
        question_doc = _call_json(question_generate, question_prompt + correction)
        question = question_doc.get("question")
        if not isinstance(question, str) or not question.strip().endswith("?"):
            raise ValueError("question generator returned no usable question")
        question_review = _call_json(verify, question_review_prompt.replace("{question}", question))
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
            "author_fact_count": len(author_facts),"test_intent":test_intent}
    if user_question is not None:
        body.update(question_source="supplied", question_at=question_at)
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
