import json
from datetime import date

import pytest

from pacbench.authoring import ScenePlan
from pacbench.daily_contract import DAILY_QUESTION, _response, freeze_daily_contracts
from pacbench.semantic_contract import _call_json, load_contract


def test_malformed_daily_model_output_gets_one_auditable_retry():
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        return '{broken' if len(prompts) == 1 else '{"approved":true}'

    value, raw, record = _response(model, "Return JSON")
    assert (value, raw, record) == ({"approved": True}, '{"approved":true}', None)
    assert "Last response: {broken" in prompts[1]


def test_malformed_contract_output_gets_one_retry():
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        return '{broken' if len(prompts) == 1 else '{"claims":[]}'

    assert _call_json(model, "Return claims") == {"claims": []}
    assert len(prompts) == 2


def test_daily_contract_carries_each_fact_across_quiet_days(tmp_path):
    scenes = [
        ScenePlan(id="monday", at="2026-09-07T18:00:00",
                  scene="Pizza plan.", facts=("Pizza is Friday, September 11.",)),
        ScenePlan(id="tuesday", at="2026-09-08T18:00:00",
                  scene="Bowling plan.", facts=("Bowling is Saturday.",)),
    ]
    messages = [
        {"source_id": "pizza", "written_at": scenes[0].at,
         "sender": "Devon", "text": "Pizza Friday?"},
        {"source_id": "bowl", "written_at": scenes[1].at,
         "sender": "Devon", "text": "Bowling Saturday?"},
    ]
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        ids = ["monday-fact-1"]
        if "tuesday-fact-1" in prompt:
            ids.append("tuesday-fact-1")
        if prompt.startswith("Track"):
            return json.dumps({"facts": [
                {"id": fid, "state": "current",
                 "claim": "Pizza is Friday, September 11." if fid == ids[0]
                          else "Bowling is Saturday.",
                 "evidence": ["pizza" if fid == ids[0] else "bowl"],
                 "reason": "still agreed"} for fid in ids]})
        return json.dumps({"approved": True, "reason": "supported",
                           "checks": [{"id": fid, "correct": True,
                                       "state": "current", "reason": "supported"}
                                      for fid in ids]})

    freeze_daily_contracts(monday=date(2026, 9, 7), scenes=scenes,
                           messages=messages, model=model, root=tmp_path)
    monday_prompt = next(p for p in prompts if p.startswith("Track") and
                         "Fictional day: 2026-09-07" in p)
    assert "Bowling Saturday?" not in monday_prompt
    assert "2026-09-08 at 8 a.m." in monday_prompt
    first = load_contract(tmp_path / "contracts" / "2026-09-07.json")
    last = load_contract(tmp_path / "contracts" / "2026-09-13.json")
    assert first.question == last.question == DAILY_QUESTION
    assert [c.statement for c in first.claims] == ["Pizza is Friday, September 11."]
    assert [c.statement for c in last.claims] == [
        "Pizza is Friday, September 11.", "Bowling is Saturday."]
    assert json.loads((tmp_path / "contracts" / "2026-09-13.json").read_text())[
        "fact_map"] == {"fact-1": "monday-fact-1", "fact-2": "tuesday-fact-1"}


def test_rejected_daily_review_never_freezes_contract(tmp_path):
    scene = ScenePlan(id="monday", at="2026-09-07T18:00:00",
                      scene="Pizza plan.", facts=("Pizza is Friday.",))
    messages = [{"source_id": "pizza", "written_at": scene.at,
                 "sender": "Devon", "text": "Pizza Friday?"}]

    def model(prompt):
        if prompt.startswith("Track"):
            return json.dumps({"facts": [{"id": "monday-fact-1", "state": "current",
                "claim": "Pizza is Friday.", "evidence": ["pizza"],
                "reason": "proposed"}]})
        return json.dumps({"approved": False, "reason": "Only a proposal",
                           "checks": [{"id": "monday-fact-1", "correct": False,
                                       "state": "current", "reason": "not agreed"}]})

    with pytest.raises(ValueError, match="daily fact state rejected"):
        freeze_daily_contracts(monday=date(2026, 9, 7), scenes=[scene],
                               messages=messages, model=model, root=tmp_path)
    assert (tmp_path / "daily-facts" / "2026-09-07.json").exists()
    assert len(list((tmp_path / "daily-facts").glob("*attempt*.json"))) == 3
    assert not (tmp_path / "contracts" / "2026-09-07.json").exists()


def test_reviewer_feedback_repairs_a_fact_without_extra_claims(tmp_path):
    scene = ScenePlan(id="monday", at="2026-09-07T18:00:00",
                      scene="Pizza plan.", facts=("Pizza is Friday.",))
    messages = [{"source_id": "pizza", "written_at": scene.at,
                 "sender": "Devon", "text": "Pizza Friday?"}]

    def model(prompt):
        if prompt.startswith("Track"):
            corrected = "independent review rejected" in prompt
            return json.dumps({"facts": [{"id": "monday-fact-1", "state": "current",
                "claim": "Pizza is Friday." if corrected else "Pizza is Saturday.",
                "evidence": ["pizza"], "reason": "revised"}]})
        correct = '"claim": "Pizza is Friday."' in prompt
        return json.dumps({"approved": correct, "reason": "checked",
                           "checks": [{"id": "monday-fact-1", "correct": correct,
                                       "state": "current", "reason": "checked"}]})

    freeze_daily_contracts(monday=date(2026, 9, 7), scenes=[scene],
                           messages=messages, model=model, root=tmp_path)
    contract = load_contract(tmp_path / "contracts" / "2026-09-07.json")
    assert [c.statement for c in contract.claims] == ["Pizza is Friday."]
    ledger = json.loads((tmp_path / "daily-facts" / "2026-09-07.json").read_text())
    assert len(ledger["attempts"]) == 2
