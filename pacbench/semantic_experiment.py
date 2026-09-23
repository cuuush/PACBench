#!/usr/bin/env python3
"""Run a prose scene through frozen evidence, providers, and semantic judging."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacbench import harness  # noqa: E402
from pacbench.authoring import ScenePlan, build_scene_observations, realize_scene, scene_spec  # noqa: E402
from pacbench.creds import load_driver  # noqa: E402
from pacbench.frozen import bundle_hash, write_bundle  # noqa: E402
from pacbench.live import _free_loopback_port  # noqa: E402
from pacbench.model_calls import ModelClient  # noqa: E402
from pacbench.semantic_contract import build_contract  # noqa: E402
from pacbench.semantic_grade import Claim, grade_semantic  # noqa: E402


RETRIEVAL_TOOLS = {
    "memcal": {"memcal_open", "memcal_open_source", "memcal_search_wiki",
               "memcal_search_archive", "memcal_conversation", "memcal_list_days",
               "memcal_list_month", "memcal_activity", "memcal_open_page"},
    "hindsight": {"mcp__hindsight__recall", "mcp__hindsight__reflect",
                  "mcp__hindsight__list_memories", "mcp__hindsight__search_knowledge_base"},
}


def tool_behavior(provider: str, turn) -> dict:
    """Describe Hermes' observed tool choices, separately from capability."""
    calls = [c.get("name") for c in turn.tool_calls]
    names = RETRIEVAL_TOOLS[provider]
    attempted = [n for n in calls if n in names]
    executed = [n for n, result in zip(calls, turn.tool_results)
                if n in names and result.get("success") is True
                and "PACBENCH-BLOCKED" not in str(result.get("text", ""))]
    blocked = [n for n, result in zip(calls, turn.tool_results)
               if n in names and (result.get("policy_blocked") or
                                  "PACBENCH-BLOCKED" in str(result.get("text", "")))]
    return {"provider_tool_attempts": attempted,
            "provider_tool_calls": executed,
            "blocked_provider_calls": blocked,
            "retrieval_called": bool(executed),
            "policy_blocks": turn.policy_blocks}

def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               default=str) + "\n")


def hindsight_preflight(adapter, home: Path, at: str, seam: dict,
                        mcp_spec: dict, query: str) -> dict:
    """Verify isolated SDK, MCP discovery, allowlist, and bank-scoped recall."""
    result = {"sdk": False, "connected": False, "discovered": [],
              "allowed": False, "recall_reached_bank": False}
    check = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"),
                            "-c", "import mcp; print(mcp.__version__ if hasattr(mcp, '__version__') else 'installed')"],
                           capture_output=True, text=True, timeout=30)
    result["sdk"] = check.returncode == 0
    if not result["sdk"]:
        result["error"] = check.stderr[-1000:]
        return result
    # This command uses the same isolated home and config as scored turns.
    env = harness.turn_env(at, home, ",".join(seam["tool_allowlist"]), seam.get("env"))
    probe = subprocess.run([str(harness.ISOLATED_BIN), "mcp", "test", "hindsight"],
                           env=env, capture_output=True, text=True, timeout=60)
    result["hermes_probe"] = {"returncode": probe.returncode,
                              "stdout": probe.stdout[-8000:],
                              "stderr": probe.stderr[-3000:]}
    result["connected"] = probe.returncode == 0
    # Call the same bank-scoped server through the isolated SDK, without
    # relying on Hermes' decision to use it.
    code = '''import asyncio,json,sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
 async with streamable_http_client(sys.argv[1]) as (read,write):
  async with ClientSession(read,write) as session:
   await session.initialize()
   tools=(await session.list_tools()).tools
   recall=next(t for t in tools if t.name=="recall")
   print(json.dumps({"tools":[t.name for t in tools],"recall_schema":recall.input_schema}),flush=True)
   required=recall.input_schema.get("required",[])
   args={"query":sys.argv[3]}
   if "bank_id" in required: args["bank_id"]=sys.argv[2]
   if "bankId" in required: args["bankId"]=sys.argv[2]
   answer=await session.call_tool("recall",args)
   print(json.dumps({"is_error":answer.is_error,"content":[getattr(x,"text",str(x)) for x in answer.content]}))
asyncio.run(main())'''
    direct = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c",
                             code, mcp_spec["url"], adapter._bank, query],
                            env=env, capture_output=True, text=True, timeout=90)
    result["direct_probe"] = {"returncode": direct.returncode,
                              "stdout": direct.stdout[-12000:],
                              "stderr": direct.stderr[-3000:]}
    try:
        lines = [json.loads(x) for x in direct.stdout.splitlines()
                 if x.startswith("{")]
        names = lines[0]["tools"]
        result["discovered"] = [f"mcp__hindsight__{n}" for n in names]
        result["allowed"] = ("mcp__hindsight__recall" in seam["tool_allowlist"]
                             and "mcp__hindsight__recall" in result["discovered"])
        result["recall_reached_bank"] = (direct.returncode == 0 and
                                         lines[1]["is_error"] is False)
        result["recall_result"] = lines[1]
    except (IndexError, KeyError, ValueError):
        pass
    return result


def run_arm(provider: str, root: Path, observations: list[dict],
            contract, driver, judge: ModelClient, *, question_at: str,
            maintenance_at: str, life_id: str) -> dict:
    arm = root / provider
    arm.mkdir(parents=True)
    if provider == "memcal":
        from provider_adapters.memcal_adapter import MemcalAdapter
        adapter = MemcalAdapter(arm / "provider-home", dream_model=driver.model_id)
    else:
        from provider_adapters.hindsight_adapter import HindsightAdapter
        adapter = HindsightAdapter(arm / "provider-home", llm_model=driver.model_id)
    namespace = f"semantic-{life_id}-{root.name}-{provider}"
    result = {"provider": provider, "state": "incomplete", "reason": "not started",
              "contract_hash": contract.contract_hash}
    server = False
    try:
        adapter.reset(namespace)
        seam = adapter.retrieval()
        save(arm / "retrieval.json", {k: v for k, v in seam.items() if k != "env"})
        boundary = max(o["collected_at"] for o in observations)
        usage = adapter.ingest(observations, boundary)
        save(arm / "ingestion.json", {"at": boundary, "packet": observations,
                                      "ack": vars(usage),
                                      "provider_packets": getattr(adapter, "_last_packets", None)})
        maintenance = adapter.maintenance(maintenance_at)
        readiness = adapter.readiness()
        save(arm / "maintenance.json", {"at": maintenance_at,
                                        "usage": vars(maintenance),
                                        "readiness": vars(readiness)})
        if not readiness.ready:
            result["reason"] = f"maintenance not ready: {readiness.detail}"
            return result
        spec = None
        if provider == "hindsight":
            port = _free_loopback_port()
            adapter.start_server(port)
            server = True
            spec = adapter.mcp_server_spec(port)
        hermes_home = arm / "hermes-home"
        harness.provision_home(hermes_home, provider_plugin_src=(
            Path(seam["plugin_src"]) if seam.get("plugin_available") else None),
            provider_name=provider, model=driver.model_id,
            model_provider=driver.provider,
            mcp_servers=[spec] if spec else None)
        if provider == "hindsight":
            preflight = hindsight_preflight(adapter, hermes_home, question_at,
                                            seam, spec, contract.question)
        else:
            preflight = {"plugin_installed": (hermes_home / "plugins" / "memcal").is_dir(),
                         "clock_pin": harness.turn_env(question_at, hermes_home,
                                                       "")["MEMCAL_TODAY"]}
        preflight["tool_allowlist"] = list(seam["tool_allowlist"])
        preflight["retrieval_tools_allowed"] = sorted(
            RETRIEVAL_TOOLS[provider].intersection(seam["tool_allowlist"]))
        save(arm / "preflight.json", preflight)
        if provider == "hindsight" and not all(preflight.get(k) for k in
                ("sdk", "connected", "allowed", "recall_reached_bank")):
            result.update(state="invalid", reason="retrieval capability preflight failed",
                          capability={"available": False, "preflight": str(arm / "preflight.json")})
            return result
        if provider == "memcal" and not preflight["plugin_installed"]:
            result.update(state="invalid", reason="Memcal plugin unavailable",
                          capability={"available": False, "preflight": str(arm / "preflight.json")})
            return result
        turn = harness.run_turn(contract.question, at=question_at,
                                home=hermes_home,
                                allowlist=",".join(seam["tool_allowlist"]),
                                extra_env=seam.get("env"), timeout_s=180)
        save(arm / "turn.json", vars(turn))
        result["behavior"] = tool_behavior(provider, turn)
        result["capability"] = {"available": True,
                                "preflight": str(arm / "preflight.json")}
        if turn.returncode != 0 or not turn.answer.strip():
            result["reason"] = f"Hermes turn failed: {turn.returncode}"
            return result
        if provider == "memcal" and (
                turn.prefetch_context is None or
                question_at[:10] not in turn.prefetch_context and
                datetime.fromisoformat(question_at).strftime("%-d %B %Y")
                not in turn.prefetch_context):
            result["reason"] = "Memcal fictional-clock prefetch missing or mismatched"
            return result
        claims = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
        try:
            grade = grade_semantic(life_id, contract.question, turn.answer,
                                   claims, judge,
                                   record_path=arm / "judgment.json")
        except Exception as exc:
            result["reason"] = f"semantic judgment failed: {exc}"
            return result
        save(arm / "grade.json", {"correct": grade.correct,
                                  "missed": grade.missed,
                                  "misrepresented": grade.misrepresented,
                                  "claims": [vars(s) for s in grade.slots]})
        result.update(state="valid", reason="")
        return result
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        save(arm / "status.json", result)
        if server:
            adapter.stop_server()
        if hasattr(adapter, "close"):
            adapter.close()


def resolve_spec(spec: dict, root: Path) -> tuple[list[ScenePlan], ScenePlan, dict]:
    """Fill operational clocks and identifiers without changing author prose."""
    rows = spec.get("scenes")
    if not isinstance(rows, list) or not rows:
        raise ValueError("experiment needs at least one scene")
    seed = (datetime.fromisoformat(spec["question_at"]) - timedelta(days=1)
            if spec.get("question_at") else datetime.now(timezone.utc))
    scenes = []
    for index, authored in enumerate(rows, 1):
        row = dict(authored)
        row.setdefault("id", f"scene-{index:02d}")
        if not row.get("at") and not row.get("window_start"):
            row["at"] = seed.replace(hour=18, minute=index - 1, second=0,
                                      microsecond=0).isoformat(timespec="seconds")
        scenes.append(ScenePlan(**row))
    if len({s.id for s in scenes}) != len(scenes):
        raise ValueError("scene IDs must be unique")
    if spec.get("test_scene_id"):
        target = next((s for s in scenes if s.id == spec["test_scene_id"]), None)
        if target is None:
            raise ValueError("test_scene_id does not identify a scene")
    else:
        eligible = [s for s in scenes if s.facts or s.test_intent.strip()]
        if len(eligible) != 1:
            raise ValueError("choose one test scene when multiple scenes have facts or intent")
        target = eligible[0]
    if not target.facts and not target.test_intent.strip():
        raise ValueError("test scene needs required facts or test intent")
    life_id = spec.get("id") or root.name
    latest = max(datetime.fromisoformat(s.window_end) for s in scenes)
    question_at = spec.get("question_at") or (latest + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    maintenance_at = spec.get("maintenance_at") or (
        datetime.fromisoformat(question_at) - timedelta(hours=1)).isoformat(timespec="seconds")
    if (datetime.fromisoformat(maintenance_at) <= latest or
            datetime.fromisoformat(question_at) <= datetime.fromisoformat(maintenance_at)):
        raise ValueError("maintenance must follow scenes; question must follow maintenance")
    providers = spec.get("providers", ["memcal", "hindsight"])
    if not providers or any(p not in ("memcal", "hindsight") for p in providers):
        raise ValueError("unsupported provider selection")
    return scenes, target, {
        "id": life_id, "test_scene_id": target.id,
        "question_at": question_at, "maintenance_at": maintenance_at,
        "providers": providers, "scenes": [scene_spec(s) for s in scenes]}


def _run_experiment_body(spec: dict, root: Path, progress: dict) -> dict:
    scenes, target, resolved = resolve_spec(spec, root)
    save(root / "resolved-spec.json", resolved)
    life_id = resolved["id"]
    question_at = resolved["question_at"]
    maintenance_at = resolved["maintenance_at"]
    providers = resolved["providers"]
    progress["stage"] = "scene generation and verification"
    save(root / "experiment-status.json", progress)
    driver = load_driver()
    key = os.environ["META_API_KEY"]
    call_dir = root / "model-calls"
    writer = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                         cache_dir=ROOT / "runs" / "model-cache",
                         temperature=0.7)
    judge = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                        cache_dir=ROOT / "runs" / "model-cache")
    observations = []
    scene_records = []
    for plan in scenes:
        realized = realize_scene(plan, writer, judge, model=driver.model_id,
                                 record_dir=root / "scene-verifications")
        observations.extend(o.public() for o in build_scene_observations(
            life_id, plan, list(realized.lines)))
        scene_records.append({"scene_id": plan.id, "lines": realized.lines,
                              "record": vars(realized.record),
                              "verdict": realized.verdict})
        save(root / "scenes.json", {"scenes": scene_records})
    bundle = {"bundle_version": "0.2.0", "lives": {life_id: {
        "lineage": life_id, "observations": observations}}}
    write_bundle(bundle, root / "bundle.json")
    save(root / "bundle-provenance.json", {"hash": bundle_hash(bundle),
                                           "source": "verified-scenes"})
    progress["stage"] = "contract generation and review"
    save(root / "experiment-status.json", progress)
    contract = build_contract(
        scene=target.scene, test_intent=target.test_intent,
        author_facts=target.facts,
        observations=observations, generate=judge, verify=judge,
        question_generate=judge, output_path=root / "contract.json")
    progress["contract_frozen_at"] = datetime.now(timezone.utc).isoformat()
    progress["stage"] = "provider arms"
    save(root / "experiment-status.json", progress)
    if spec.get("prepare_only") is True:
        summary = {"state": "prepared", "results": [],
                   "contract_hash": contract.contract_hash,
                   "bundle_hash": bundle_hash(bundle)}
        save(root / "summary.json", summary)
        return summary
    results = [run_arm(p, root, observations, contract, driver, judge,
                       question_at=question_at, maintenance_at=maintenance_at,
                       life_id=life_id) for p in providers]
    state = "complete" if all(r["state"] == "valid" for r in results) else "incomplete"
    summary = {"state": state, "results": results,
               "contract_hash": contract.contract_hash,
               "bundle_hash": bundle_hash(bundle)}
    save(root / "summary.json", summary)
    return summary


def run_experiment(spec: dict, root: Path) -> dict:
    """Run once and leave a diagnosis even when authoring fails early."""
    root.mkdir(parents=True, exist_ok=False)
    save(root / "spec.json", spec)
    progress = {"state": "running", "stage": "spec validation",
                "started_at": datetime.now(timezone.utc).isoformat()}
    save(root / "experiment-status.json", progress)
    try:
        summary = _run_experiment_body(spec, root, progress)
    except Exception as exc:
        progress.update({"state": "incomplete", "error": f"{type(exc).__name__}: {exc}",
                         "finished_at": datetime.now(timezone.utc).isoformat()})
        save(root / "experiment-status.json", progress)
        save(root / "summary.json", {"state": "incomplete", "failed_stage": progress["stage"],
                                      "error": progress["error"], "results": []})
        raise
    progress.update(state=summary["state"], stage="finished",
                    finished_at=datetime.now(timezone.utc).isoformat())
    save(root / "experiment-status.json", progress)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path, help="JSON prose scene specification")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare-only", action="store_true",
                        help="freeze scene and contract without running provider arms")
    args = parser.parse_args()
    root = args.output or ROOT / "runs" / (
        "semantic-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    spec = json.loads(args.spec.read_text())
    if args.prepare_only:
        spec["prepare_only"] = True
    summary = run_experiment(spec, root)
    print(root)
    print(json.dumps(summary["results"], indent=2))
    return 0 if all(r["state"] == "valid" for r in summary["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
