"""Check saved run invariants without rerunning models or providers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .frozen import bundle_hash, messages_for


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _contract_hash(contract: dict) -> str:
    body = {k: v for k, v in contract.items() if k != "contract_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _evidence_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _full_tool_trace(turn: dict, state_db: Path) -> bool:
    results = turn.get("tool_results") or []
    if not results:
        return True
    if all(item.get("capture") == "hermes_session_db" for item in results):
        return True
    session_id = turn.get("session_id")
    if not session_id or not state_db.exists():
        return False
    try:
        with sqlite3.connect(f"file:{state_db}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT tool_name, content FROM messages WHERE session_id = ? "
                "AND role = 'tool' ORDER BY id", (session_id,)).fetchall()
    except sqlite3.Error:
        return False
    return len(rows) == len(results) and all(
        name == result.get("tool") and isinstance(content, str)
        for result, (name, content) in zip(results, rows))


def audit_run(root: Path) -> dict:
    """Return a reproducible validity report for a saved semantic run."""
    root = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    spec = _read(root / "spec.json") or {}
    summary = _read(root / "summary.json") or {}
    bundle = _read(root / "bundle.json") or {}
    provenance = _read(root / "bundle-provenance.json") or {}
    life_id = spec.get("id")
    try:
        messages = messages_for(bundle, life_id)
    except (RuntimeError, TypeError):
        messages = []
        errors.append("frozen messages missing")
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        messages = []
        errors.append("frozen messages malformed")
    elif any(not isinstance(m.get(key), str) for m in messages
             for key in ("source_id", "written_at", "collected_at")):
        messages = []
        errors.append("frozen message fields malformed")
    actual_bundle_hash = bundle_hash(bundle) if bundle else None
    if not actual_bundle_hash or bundle.get("bundle_hash") != actual_bundle_hash:
        errors.append("frozen bundle hash mismatch")
    if provenance.get("hash") != actual_bundle_hash:
        errors.append("bundle provenance mismatch")
    if summary.get("bundle_hash") != actual_bundle_hash:
        errors.append("summary bundle hash mismatch")
    source_ids = [message.get("source_id") for message in messages]
    if (not source_ids or any(not isinstance(i, str) or not i for i in source_ids) or
            len(source_ids) != len(set(source_ids))):
        errors.append("source IDs missing or duplicated")

    contract_paths = sorted((root / "contracts").glob("????-??-??.json"))
    if not contract_paths:
        contract_paths = [root / "contract.json"]
    contracts: dict[str, dict] = {}
    for path in contract_paths:
        contract = _read(path)
        if not contract:
            errors.append(f"contract missing: {path.name}")
            continue
        day = path.stem if path.parent.name == "contracts" else "single"
        contracts[day] = contract
        if contract.get("contract_hash") != _contract_hash(contract):
            errors.append(f"contract hash mismatch: {day}")
        exposed = ([m for m in messages if m["written_at"][:10] <= day]
                   if day != "single" else messages)
        visible_ids = {m["source_id"] for m in exposed}
        if contract.get("evidence_hash") != _evidence_hash(exposed):
            errors.append(f"contract evidence hash mismatch: {day}")
        claims = contract.get("claims") or []
        if not isinstance(claims, list) or any(not isinstance(c, dict) for c in claims):
            claims = []
            errors.append(f"claims malformed: {day}")
        contract["claims"] = claims
        claim_ids = [c.get("id") for c in claims]
        if (not claims or any(not isinstance(i, str) or not i for i in claim_ids) or
                len(claim_ids) != len(set(claim_ids)) or
                not isinstance(contract.get("question"), str) or
                not contract["question"].strip()):
            errors.append(f"claims missing or duplicated: {day}")
        if any(not isinstance(c.get("evidence"), list) or not c["evidence"] or
               any(not isinstance(source, str) or source not in visible_ids
                   for source in c["evidence"])
               for c in claims):
            errors.append(f"claim cites unavailable message: {day}")
        if contract.get("author_fact_count") is not None and len(claims) != contract["author_fact_count"]:
            errors.append(f"authored fact count mismatch: {day}")
    final_contract = contracts[max(contracts)] if contracts else {}
    if summary.get("contract_hash") != final_contract.get("contract_hash"):
        errors.append("summary contract hash mismatch")

    expected_days = sorted(contracts)
    providers = spec.get("providers", ["memcal", "hindsight"])
    if (not isinstance(providers, list) or not providers or
            any(not isinstance(p, str) for p in providers) or
            len(providers) != len(set(providers))):
        errors.append("provider list missing or duplicated")
        providers = []
    arm_reports = {}
    if summary.get("state") == "prepared":
        if summary.get("results"):
            errors.append("prepared run has provider results")
    else:
        reported = summary.get("results") or []
        if ([item.get("provider") for item in reported if isinstance(item, dict)] !=
                providers):
            errors.append("summary provider arms differ from spec")
        for provider in providers:
            arm = root / provider
            ingest = _read(arm / "ingestion.json") or {}
            maintenance = _read(arm / "maintenance.json") or {}
            preflight = _read(arm / "preflight.json") or {}
            status = _read(arm / "status.json") or {}
            expected_input_hash = bundle_hash({"messages": messages})
            if ingest.get("packet") != messages or (ingest.get("input_hash") is not None and
                                                     ingest["input_hash"] != expected_input_hash):
                errors.append(f"{provider}: input differs from frozen messages")
            if ingest.get("input_hash") is None:
                warnings.append(f"{provider}: original input hash not captured; packet checked against bundle")
            batches = ingest.get("daily_batches") or []
            if not isinstance(batches, list) or any(not isinstance(b, dict) for b in batches):
                errors.append(f"{provider}: daily batches malformed")
                batches = []
            if expected_days == ["single"] and not batches:
                batches = [{"day": "single", "source_ids": [m.get("source_id")
                                                               for m in ingest.get("packet", [])]}]
            seen = [source for batch in batches for source in batch.get("source_ids", [])]
            if len(seen) != len(source_ids) or set(seen) != set(source_ids) or len(seen) != len(set(seen)):
                errors.append(f"{provider}: messages not ingested exactly once")
            if expected_days != [batch.get("day") for batch in batches] and expected_days != ["single"]:
                errors.append(f"{provider}: daily batches differ from contracts")
            for batch in batches:
                day = batch.get("day")
                expected = (source_ids if day == "single" else
                            [m["source_id"] for m in messages
                             if m["collected_at"][:10] == day])
                if batch.get("source_ids") != expected:
                    errors.append(f"{provider}: wrong daily input: {day}")
            passes = maintenance.get("daily_passes") or []
            if not isinstance(passes, list) or any(not isinstance(p, dict) for p in passes):
                errors.append(f"{provider}: maintenance passes malformed")
                passes = []
            if expected_days == ["single"] and not passes:
                passes = [maintenance]
            if len(passes) != len(batches) or not all(
                    item.get("readiness", {}).get("ready") is True for item in passes):
                errors.append(f"{provider}: maintenance/readiness incomplete")
            if status.get("state") != "valid":
                errors.append(f"{provider}: arm is not valid")
            if provider == "hindsight" and not all(preflight.get(k) for k in
                    ("sdk", "connected", "allowed", "recall_reached_bank")):
                errors.append("hindsight: retrieval preflight failed")
            if provider == "memcal" and preflight.get("plugin_installed") is not True:
                errors.append("memcal: plugin preflight failed")
            day_reports = []
            for day, contract in contracts.items():
                turn_dir = arm / "turns" / day if day != "single" else arm
                question = contract.get("question") if isinstance(contract.get("question"), str) else ""
                checkpoint = _read(turn_dir / "status.json") if day != "single" else status
                turn = _read(turn_dir / "turn.json") or {}
                judgment = _read(turn_dir / "judgment.json") or {}
                grade = _read(turn_dir / "grade.json") or {}
                checkpoint = checkpoint or {}
                checkpoint_at = (turn.get("at") if day == "single" else
                                 checkpoint.get("at"))
                claim_ids = [c["id"] for c in contract.get("claims", [])]
                verdicts = grade.get("claims") or []
                if not isinstance(verdicts, list) or any(not isinstance(v, dict) for v in verdicts):
                    errors.append(f"{provider}: verdicts malformed: {day}")
                    verdicts = []
                judged_claims = judgment.get("claims") or []
                if not isinstance(judged_claims, list) or any(not isinstance(c, dict) for c in judged_claims):
                    judged_claims = []
                if checkpoint.get("state") != "valid" or checkpoint.get("contract_hash") != contract.get("contract_hash"):
                    errors.append(f"{provider}: invalid checkpoint or contract: {day}")
                if not checkpoint.get("capability", {}).get("available"):
                    errors.append(f"{provider}: retrieval unavailable: {day}")
                if checkpoint.get("retrieval_outcome") == "blocked":
                    errors.append(f"{provider}: retrieval blocked: {day}")
                if checkpoint.get("retrieval_outcome") is None:
                    warnings.append(f"{provider}: retrieval outcome not separately captured: {day}")
                if day != "single":
                    expected_date = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                    at = checkpoint.get("at", "")
                    if at[:10] != expected_date:
                        errors.append(f"{provider}: wrong checkpoint day: {day}")
                    day_pass = next((p for p in passes if p.get("day") == day), {})
                    try:
                        if datetime.fromisoformat(day_pass["at"]) >= datetime.fromisoformat(at):
                            errors.append(f"{provider}: maintenance after question: {day}")
                    except (KeyError, ValueError, TypeError):
                        errors.append(f"{provider}: missing fictional clock: {day}")
                    if provider == "memcal" and f"MEMCAL SNAPSHOT {at}" not in (turn.get("prefetch_context") or ""):
                        errors.append(f"memcal: missing or stale brief: {day}")
                if (turn.get("at") != checkpoint_at or
                        not str(turn.get("prompt") or "").endswith(question) or
                        judgment.get("question") != question or
                        judgment.get("answer") != turn.get("answer") or
                        [c.get("id") for c in judged_claims] != claim_ids):
                    errors.append(f"{provider}: question, answer, or judge input mismatch: {day}")
                counts = [grade.get(k) for k in ("correct", "missed", "misrepresented")]
                if ([v.get("id") for v in verdicts] != claim_ids or
                        any(v.get("verdict") not in {"correct", "missed", "misrepresented"}
                            or not isinstance(v.get("reason"), str) or not v["reason"].strip()
                            for v in verdicts) or
                        any(type(count) is not int or count < 0 for count in counts) or
                        sum(count for count in counts if type(count) is int) != len(claim_ids)):
                    errors.append(f"{provider}: missing or malformed grade: {day}")
                if not judgment.get("response") or not judgment.get("model_call"):
                    errors.append(f"{provider}: judge call not captured: {day}")
                else:
                    try:
                        judged = json.loads(judgment["response"])["verdicts"]
                    except (TypeError, ValueError, KeyError):
                        judged = None
                    if judged != verdicts:
                        errors.append(f"{provider}: grade differs from judge response: {day}")
                if not _full_tool_trace(turn, arm / "hermes-home" / "state.db"):
                    warnings.append(f"{provider}: full tool result unavailable: {day}")
                day_reports.append({"day": day, "state": checkpoint.get("state"),
                                    "claims": len(claim_ids),
                                    "correct": grade.get("correct"),
                                    "missed": grade.get("missed"),
                                    "misrepresented": grade.get("misrepresented"),
                                    "retrieval": checkpoint.get("retrieval_outcome")})
            arm_reports[provider] = {"input_hash": ingest.get("input_hash") or expected_input_hash,
                                     "input_hash_saved": ingest.get("input_hash") is not None,
                                     "days": day_reports}
        hashes = {arm_reports[p]["input_hash"] for p in arm_reports}
        if len(hashes) != 1:
            errors.append("provider input hashes differ")
        if summary.get("state") != "complete":
            errors.append("summary does not report complete")
    return {"state": "pass" if not errors else "fail", "errors": errors,
            "warnings": warnings, "bundle_hash": actual_bundle_hash,
            "days": expected_days, "providers": arm_reports}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--save", action="store_true",
                        help="write run-audit.json only if it does not exist")
    args = parser.parse_args()
    report = audit_run(args.run)
    if args.save:
        path = args.run / "run-audit.json"
        if path.exists():
            parser.error(f"audit already exists: {path}")
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(path)
    print(f"{report['state']}: {len(report['errors'])} errors, "
          f"{len(report['warnings'])} warnings")
    for item in report["errors"]:
        print(f"error: {item}")
    for item in report["warnings"]:
        print(f"not captured: {item}")
    return 0 if report["state"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
