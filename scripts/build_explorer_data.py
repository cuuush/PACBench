#!/usr/bin/env python3
"""Build the Pizza Day explorer from saved semantic experiments.

Every run uses its own verified frozen bundle. Encrypted reasoning stays in
private run artifacts and is redacted from the browser data.
"""

import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pacbench.frozen import bundle_hash  # noqa: E402
from pacbench.harness import _recover_tool_results  # noqa: E402
from pacbench.semantic_experiment import tool_behavior  # noqa: E402

OUT = ROOT / "explorer" / "data"
SCENE_FAMILY = "pizza-day"


def model_record_for_dashboard(value):
    if isinstance(value, dict):
        return {key: ("<encrypted reasoning retained in private run artifact>"
                      if key == "encrypted_content" and item else
                      model_record_for_dashboard(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [model_record_for_dashboard(item) for item in value]
    return value


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    dashboard_runs = []
    for run_dir in sorted((ROOT / "runs").glob("semantic-*"), reverse=True):
        if not run_dir.is_dir():
            continue
        spec = read_json(run_dir / "spec.json")
        if not spec or not str(spec.get("id", "")).startswith(SCENE_FAMILY):
            continue
        bundle = read_json(run_dir / "bundle.json")
        provenance = read_json(run_dir / "bundle-provenance.json")
        if bundle is not None:
            actual_hash = bundle_hash(bundle)
            if not provenance or provenance.get("hash") != actual_hash:
                raise SystemExit(f"ABORT semantic bundle mismatch: {run_dir}")
        else:
            actual_hash = None
        rerun_provenance = read_json(run_dir / "rerun-provenance.json")
        contract = read_json(run_dir / "contract.json")
        if rerun_provenance and (rerun_provenance.get("reused_bundle_hash") != actual_hash or
                                 rerun_provenance.get("reused_contract_hash") !=
                                 (contract or {}).get("contract_hash")):
            raise SystemExit(f"ABORT semantic rerun provenance mismatch: {run_dir}")
        life = (bundle or {}).get("lives", {}).get(spec.get("id"), {})
        messages = life.get("messages", life.get("observations", []))
        arms = {}
        for provider in spec.get("providers", ["memcal", "hindsight"]):
            if spec.get("prepare_only") is True:
                continue
            arm = run_dir / provider
            archive_sources = {}
            archive_db = arm / "provider-home" / "memcal.db"
            if provider == "memcal" and archive_db.exists():
                with sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True) as db:
                    archive_sources = {str(row[0]): row[1] for row in
                        db.execute("SELECT id, external_id FROM archive")}
            arms[provider] = {name: read_json(arm / (name + ".json")) for name in
                              ("ingestion", "maintenance", "retrieval", "preflight",
                               "turn", "judgment", "grade", "status")}
            exchanges = []
            for turn_dir in sorted((arm / "turns").glob("*")):
                if turn_dir.is_dir():
                    exchange = {name: read_json(turn_dir / (name + ".json"))
                                for name in ("turn", "judgment", "grade", "status")}
                    if exchange["turn"]:
                        trace = SimpleNamespace(
                            session_id=exchange["turn"].get("session_id"),
                            tool_results=exchange["turn"].get("tool_results", []))
                        if not all(result.get("capture") == "hermes_session_db"
                                   for result in trace.tool_results):
                            _recover_tool_results(trace, arm / "hermes-home" / "state.db")
                    exchange["day"] = turn_dir.name
                    exchange["contract"] = read_json(run_dir / "contracts" /
                                                     (turn_dir.name + ".json"))
                    exchanges.append(exchange)
            if not exchanges and arms[provider]["turn"]:
                exchanges = [{name: arms[provider][name]
                              for name in ("turn", "judgment", "grade")}]
            arms[provider]["exchanges"] = exchanges
            # Older semantic runs predate the separate capability/behavior
            # fields. Derive only what the saved trace actually demonstrates.
            old_status = arms[provider]["status"]
            if old_status is not None and arms[provider]["turn"]:
                turn = arms[provider]["turn"]
                derived = tool_behavior(provider, SimpleNamespace(
                    tool_calls=turn.get("tool_calls", []),
                    tool_results=turn.get("tool_results", []),
                    policy_blocks=turn.get("policy_blocks", 0)))
                derived["basis"] = "derived from saved turn"
                recorded = old_status.get("behavior")
                if recorded and any(recorded.get(k) != derived.get(k) for k in
                                    ("provider_tool_attempts", "provider_tool_calls",
                                     "blocked_provider_calls", "retrieval_called")):
                    old_status["behavior_disagreement"] = recorded
                old_status["behavior"] = derived
            if old_status is not None and "capability" not in old_status and arms[provider]["preflight"]:
                pf = arms[provider]["preflight"]
                available = (bool(pf.get("plugin_installed")) if provider == "memcal"
                             else all(pf.get(k) for k in
                                      ("sdk", "connected", "allowed", "recall_reached_bank")))
                old_status["capability"] = {"available": available,
                                            "basis": "derived from saved preflight"}
            arms[provider]["archive_sources"] = archive_sources
        model_calls = []
        for path in sorted((run_dir / "model-calls").glob("*.json")):
            model_calls.append({"file": path.name,
                                "record": model_record_for_dashboard(read_json(path))})
        verifications = []
        for path in sorted((run_dir / "scene-verifications").glob("*.json")):
            verifications.append({"file": path.name, "record": read_json(path)})
        calibrations = []
        for report_path in sorted(run_dir.glob("calibration*/report.json")):
            folder = report_path.parent
            calibrations.append({
                "name": folder.name,
                "report": read_json(report_path),
                "cases": {path.stem: read_json(path) for path in folder.glob("*.json")
                          if path.name != "report.json"},
            })
        dashboard_runs.append({"kind": "semantic", "id": run_dir.name,
                               "spec": spec, "summary": read_json(run_dir / "summary.json"),
                               "resolved_spec": read_json(run_dir / "resolved-spec.json"),
                               "experiment_status": read_json(run_dir / "experiment-status.json"),
                               "audit": read_json(run_dir / "run-audit.json"),
                               "bundle": bundle, "bundle_hash": actual_hash,
                               "provenance": provenance, "messages": messages,
                               "rerun_provenance": rerun_provenance,
                               "scenes": read_json(run_dir / "scenes.json"),
                               "contract": contract,
                               "daily_facts": {p.stem: read_json(p) for p in
                                               sorted((run_dir / "daily-facts").glob("*.json"))
                                               if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)},
                               "verifications": verifications, "model_calls": model_calls,
                               "calibrations": calibrations,
                               "arms": arms})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dashboard.json").write_text(json.dumps({
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "runs": dashboard_runs}, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT / 'dashboard.json'} ({len(dashboard_runs)} runs)")


if __name__ == "__main__":
    main()
