"""Freeze one evidence-backed claim per authored fact after each fictional day."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from .authoring import ScenePlan
from .model_calls import ModelOutput
from .semantic_contract import ContractClaim, EvaluationContract


DAILY_QUESTION = (
    "Can you catch me up on the plans and outings I've discussed lately, "
    "including what changed or happened?"
)


def _response(call, prompt: str) -> tuple[dict, str, str | None]:
    for attempt in range(2):
        result = call(prompt)
        raw = result.text if isinstance(result, ModelOutput) else result
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value, raw, (result.record_path if isinstance(result, ModelOutput)
                                    else None)
        except (TypeError, json.JSONDecodeError):
            pass
        if attempt == 0:
            prompt += ("\nYour last response was malformed JSON. Return one complete "
                       f"JSON object. Last response: {raw}")
    raise ValueError("daily fact model returned malformed JSON twice")


def _valid_proposal(rows: object, events: list[dict], source_ids: set[str]) -> bool:
    if not isinstance(rows, list) or len(rows) != len(events):
        return False
    for row, event in zip(rows, events):
        if (not isinstance(row, dict) or row.get("id") != event["id"] or
                row.get("state") not in {"current", "superseded", "historical"} or
                not isinstance(row.get("claim"), str) or not row["claim"].strip() or
                not isinstance(row.get("reason"), str) or
                not isinstance(row.get("evidence"), list) or not row["evidence"] or
                any(source not in source_ids for source in row["evidence"])):
            return False
    return len({row["claim"].strip() for row in rows}) == len(rows)


def _review_approved(review: dict, rows: list[dict]) -> bool:
    checks = review.get("checks")
    return (review.get("approved") is True and
            isinstance(review.get("reason"), str) and
            isinstance(checks, list) and len(checks) == len(rows) and
            all(isinstance(check, dict) and check.get("id") == row["id"] and
                check.get("correct") is True and
                check.get("state") == row["state"] and
                isinstance(check.get("reason"), str)
                for check, row in zip(checks, rows)))


def freeze_daily_contracts(*, monday: date,
                           scenes: list[ScenePlan], messages: list[dict],
                           model, root: Path,
                           check_time: str = "8 a.m.") -> dict[str, EvaluationContract]:
    """Review each fact's meaning and temporal state, then freeze all seven days."""
    contracts = {}
    for offset in range(7):
        day = monday + timedelta(days=offset)
        question_day = day + timedelta(days=1)
        exposed = [m for m in messages if m["written_at"][:10] <= day.isoformat()]
        source_ids = {m["source_id"] for m in exposed}
        events = [{"id": f"{scene.id}-fact-{index}", "day": scene.at[:10],
                   "text": fact}
                  for scene in scenes if scene.at[:10] <= day.isoformat()
                  for index, fact in enumerate(scene.facts, 1)]
        if not events:
            raise ValueError(f"no authored facts established by {day}")
        dialogue = "\n".join(f"{m['source_id']} | {m['written_at']} | "
                             f"{m['sender']}: {m['text']}" for m in exposed)
        prompt = (
            "Track these authored facts through the end of the fictional day. "
            "Return exactly one row per fact, in the supplied order. Do not add, "
            "merge, or drop facts. For each, classify its state and write ONE "
            "short claim that preserves its concrete value, with dialogue source "
            "IDs. Current means an ongoing future or unresolved plan. Once an "
            "outing happens, its earlier plan details become historical. A "
            "superseded old value must be described as past, not current. "
            "A relative weekday can establish a calendar date using the "
            "message timestamp; the numeral need not appear in the chat. "
            "Do not add a year unless the authored fact requires one. "
            "Use another fact's text only for context, never to combine claims. "
            "Return JSON: {\"facts\":[{\"id\":\"authored-fact-id\",\"state\":"
            "\"current|superseded|historical\",\"claim\":\"one fact\","
            "\"evidence\":[\"source-id\"],\"reason\":\"brief\"}]}.\n"
            f"Fictional day: {day.isoformat()}. The question is asked "
            f"{question_day.isoformat()} at {check_time}.\n"
            f"Authored facts: {json.dumps(events, ensure_ascii=False)}\n"
            f"Visible dialogue:\n{dialogue}")
        ledger_path = root / "daily-facts" / f"{day.isoformat()}.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        attempts = []
        feedback = ""
        for attempt in (1, 2, 3):
            proposal_prompt = prompt + feedback
            proposed, proposal_raw, proposal_record = _response(model, proposal_prompt)
            rows = proposed.get("facts")
            if not _valid_proposal(rows, events, source_ids):
                raise ValueError(f"malformed daily fact proposal for {day}")
            review_prompt = (
                "Independently review every proposed fact against the visible "
                "dialogue and authored facts. For EACH row, check evidence, "
                "the exact authored value and precision, atomicity, and "
                "temporal state. Current means future/unresolved; completed "
                "plan details are historical. A superseded detail must not be "
                "asserted as current. Resolve relative weekdays from message "
                "timestamps. Check that no claim duplicates or combines another "
                "authored fact. Return one check per fact in order, with your "
                "independent state. Reject if any check fails. JSON only: "
                '{"approved":true,"reason":"brief","checks":'
                '[{"id":"authored-fact-id","correct":true,'
                '"state":"current|superseded|historical","reason":"brief"}]}.\n'
                f"Fictional day: {day.isoformat()}\n"
                f"Authored facts: {json.dumps(events, ensure_ascii=False)}\n"
                f"Proposed facts: {json.dumps(rows, ensure_ascii=False)}\n"
                f"Visible dialogue:\n{dialogue}")
            review, review_raw, review_record = _response(model, review_prompt)
            record = {"day": day.isoformat(), "events": events, "facts": rows,
                      "statuses": [{"id": r["id"], "state": r["state"],
                                    "reason": r["reason"], "evidence": r["evidence"]}
                                   for r in rows],
                      "claims": [{"statement": r["claim"], "evidence": r["evidence"]}
                                 for r in rows],
                      "question": DAILY_QUESTION, "review": review,
                      "proposal_prompt": proposal_prompt,
                      "proposal_response": proposal_raw,
                      "proposal_model_call": proposal_record,
                      "review_prompt": review_prompt,
                      "review_response": review_raw,
                      "review_model_call": review_record}
            attempts.append(record)
            attempt_path = ledger_path.with_name(f"{day.isoformat()}-attempt-{attempt}.json")
            attempt_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            if _review_approved(review, rows):
                break
            problems = [{"id": c.get("id"), "reason": c.get("reason"),
                         "state": c.get("state")}
                        for c in review.get("checks", []) if isinstance(c, dict)
                        and (c.get("correct") is not True or
                             not any(r["id"] == c.get("id") and
                                     r["state"] == c.get("state") for r in rows))]
            feedback = ("\nThe independent review rejected the last proposal. "
                        f"Problems: {json.dumps(problems, ensure_ascii=False)}. "
                        f"Review summary: {review.get('reason', 'unspecified')}. "
                        "Return all fact rows again, correcting these problems.")
        ledger_path.write_text(json.dumps({**record, "attempts": attempts},
                                          indent=2, ensure_ascii=False) + "\n")
        if not _review_approved(review, rows):
            raise ValueError(f"daily fact state rejected for {day}: {review}")
        frozen_claims = tuple(ContractClaim(f"fact-{index}", row["claim"].strip(),
                                            tuple(row["evidence"]))
                              for index, row in enumerate(rows, 1))
        evidence_hash = hashlib.sha256(json.dumps(
            exposed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        body = {"claims": [vars(claim) for claim in frozen_claims],
                "question": DAILY_QUESTION, "evidence_hash": evidence_hash,
                "review": review,
                "question_review": {"safe": True, "method": "fixed broad question"},
                "fact_map": {claim.id: row["id"] for claim, row in
                             zip(frozen_claims, rows)},
                "author_fact_count": len(events)}
        contract_hash = hashlib.sha256(json.dumps(
            body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        contract_path = root / "contracts" / f"{day.isoformat()}.json"
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        if contract_path.exists():
            raise FileExistsError(f"contract already frozen: {contract_path}")
        contract_path.write_text(json.dumps({**body, "contract_hash": contract_hash},
                                            indent=2, ensure_ascii=False) + "\n")
        contracts[day.isoformat()] = EvaluationContract(
            frozen_claims, DAILY_QUESTION, evidence_hash, contract_hash)
    return contracts
