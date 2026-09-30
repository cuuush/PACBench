import hashlib
import json
import sys

import pytest

from pacbench.frozen import bundle_hash, write_bundle
from pacbench.run_audit import audit_run, main


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def frozen_week(tmp_path):
    message = {"source_id": "chat/0", "channel": "imessage", "thread": "devon",
               "sender": "Me", "text": "Pizza Friday at seven.",
               "written_at": "2026-09-07T18:00:00-04:00",
               "collected_at": "2026-09-07T18:00:00-04:00"}
    bundle = {"bundle_version": "0.3.0", "lives": {"pizza": {"messages": [message]}}}
    write_bundle(bundle, tmp_path / "bundle.json")
    save(tmp_path / "bundle-provenance.json", {"hash": bundle_hash(bundle)})
    body = {"claims": [{"id": "fact-1", "statement": "Pizza is at seven.",
                        "evidence": ["chat/0"]}],
            "question": "What's the pizza plan?",
            "evidence_hash": hashlib.sha256(json.dumps(
                [message], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "author_fact_count": 1}
    contract_hash = hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    contract = {**body, "contract_hash": contract_hash}
    save(tmp_path / "contracts/2026-09-07.json", contract)
    save(tmp_path / "spec.json", {"id": "pizza", "providers": ["memcal"]})
    save(tmp_path / "summary.json", {"state": "prepared", "results": [],
                                   "bundle_hash": bundle_hash(bundle),
                                   "contract_hash": contract_hash})
    return message, contract


def test_audit_detects_contract_tampering_before_provider_run(tmp_path):
    _, contract = frozen_week(tmp_path)
    assert audit_run(tmp_path)["state"] == "pass"
    contract["claims"][0]["evidence"] = ["absent/0"]
    save(tmp_path / "contracts/2026-09-07.json", contract)
    report = audit_run(tmp_path)
    assert report["state"] == "fail"
    assert any("unavailable message" in error for error in report["errors"])
    contract["claims"] = ["broken"]
    save(tmp_path / "contracts/2026-09-07.json", contract)
    assert audit_run(tmp_path)["state"] == "fail"


def test_recheck_does_not_replace_saved_audit(tmp_path, monkeypatch):
    frozen_week(tmp_path)
    saved = tmp_path / "run-audit.json"
    saved.write_text("original audit\n")
    monkeypatch.setattr(sys, "argv", ["run-audit", str(tmp_path)])
    assert main() == 0
    monkeypatch.setattr(sys, "argv", ["run-audit", str(tmp_path), "--save"])
    with pytest.raises(SystemExit):
        main()
    assert saved.read_text() == "original audit\n"


def test_audit_detects_duplicate_daily_ingestion(tmp_path):
    message, contract = frozen_week(tmp_path)
    at = "2026-09-08T08:00:00-04:00"
    save(tmp_path / "summary.json", {"state": "complete", "results": [
                                   {"provider": "memcal", "state": "valid"}],
                                   "bundle_hash": bundle_hash({"bundle_version": "0.3.0",
                                       "lives": {"pizza": {"messages": [message]}}}),
                                   "contract_hash": contract["contract_hash"]})
    arm = tmp_path / "memcal"
    batch = {"day": "2026-09-07", "source_ids": ["chat/0"]}
    save(arm / "ingestion.json", {"packet": [message],
         "input_hash": bundle_hash({"messages": [message]}), "daily_batches": [batch]})
    save(arm / "maintenance.json", {"daily_passes": [{"day": "2026-09-07",
         "at": "2026-09-08T07:00:00-04:00", "readiness": {"ready": True}}]})
    save(arm / "preflight.json", {"plugin_installed": True})
    save(arm / "status.json", {"state": "valid"})
    turn_dir = arm / "turns/2026-09-07"
    save(turn_dir / "status.json", {"state": "valid", "at": at,
         "contract_hash": contract["contract_hash"],
         "capability": {"available": True}, "retrieval_outcome": "not_called"})
    save(turn_dir / "turn.json", {"at": at, "prompt": contract["question"],
         "answer": "Seven.", "prefetch_context": "MEMCAL SNAPSHOT " + at,
         "tool_results": []})
    save(turn_dir / "judgment.json", {"question": contract["question"],
         "answer": "Seven.", "claims": contract["claims"],
         "response": json.dumps({"verdicts": [
             {"id": "fact-1", "verdict": "correct", "reason": "Says seven."}]}),
         "model_call": "model-calls/test.json"})
    save(turn_dir / "grade.json", {"correct": 1, "missed": 0,
         "misrepresented": 0, "claims": [{"id": "fact-1", "verdict": "correct",
                                           "reason": "Says seven."}]})
    assert audit_run(tmp_path)["state"] == "pass"
    batch["source_ids"].append("chat/0")
    save(arm / "ingestion.json", {"packet": [message],
         "input_hash": bundle_hash({"messages": [message]}), "daily_batches": [batch]})
    report = audit_run(tmp_path)
    assert report["state"] == "fail"
    assert any("not ingested exactly once" in error for error in report["errors"])
