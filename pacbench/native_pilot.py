"""Bounded, native Hermes memory diagnostics; no SDK ingestion or oracle writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import harness
from .creds import load_driver
from .frozen import load_bundle, messages_for
from .model_calls import ModelClient
from .semantic_contract import load_contract
from .semantic_grade import Claim, grade_semantic
from .semantic_experiment import daily_batches, save
from .gateway_client import client_settings, require_live_cost_boundary
from .followups import freeze_policy, run_followups

MEMCAL_SRC = harness.REPO.parent / "memcal"
# These use profile-local stores without requiring a shared remote namespace.
ISOLATED_PROVIDERS = {"holographic", "memcal", "mem0"}

PROBE = '''
import json, sys
from plugins.memory import load_memory_provider
p = load_memory_provider(sys.argv[1], register_skills=False)
if p is None:
    result = {"available": False, "reason": "native provider failed to load", "tools": []}
else:
    available = p.is_available()
    result = {"available": available,
              "reason": "" if available else (p.unavailable_reason() or "native setup incomplete"),
              "tools": [s.get("name", s.get("function", {}).get("name"))
                        for s in p.get_tool_schemas()]}
print(json.dumps(result))
'''

READ_ONLY_SMOKE = '''
import json, sys
from hermes_constants import get_hermes_home
from plugins.memory import load_memory_provider
p = load_memory_provider(sys.argv[1], register_skills=False)
p.initialize("pacbench-read-only-smoke", hermes_home=str(get_hermes_home()), platform="cli")
try:
    tool = {"holographic": "fact_store", "memcal": "memcal_list_days", "mem0": "mem0_search"}[p.name]
    args = ({"action": "list"} if p.name == "holographic" else
            {"query": "native read-only capability check"} if p.name == "mem0" else {})
    output = p.handle_tool_call(tool, args)
    print(json.dumps({"tool": tool, "arguments": args, "output": output}))
finally:
    p.shutdown()
'''


def provider_names() -> list[str]:
    """Inventory the pinned checkout, rather than maintaining a vendor wish list."""
    root = harness.ISOLATED_TREE / "plugins" / "memory"
    names = sorted(p.name for p in root.iterdir()
                   if p.is_dir() and (p / "plugin.yaml").is_file())
    return names + ["memcal"]


def provision(root: Path, provider: str, model: str = "unconfigured") -> dict:
    home = root / "hermes-home"
    driver_url, client_key = client_settings(f"pacbench-{provider}-driver")
    harness.provision_home(
        home, provider_plugin_src=(MEMCAL_SRC / "integrations/hermes/memcal"
                                   if provider == "memcal" else None),
        provider_name=provider, model=model, model_provider="meta-ai",
        builtin_memory=True, model_base_url=driver_url)
    # Preserve the normal additive built-in memory, but isolate all HOME fallback paths.
    fallback = root / "fallback-home"
    fallback.mkdir()
    env = {"HOME": str(fallback.resolve()), "PACBENCH_NO_BUILTIN_MEMORY": "0",
           "MEMCAL_HOME": str((root / "memcal-home").resolve()),
           "MEMCAL_SRC": str(MEMCAL_SRC), "HERMES_ENABLE_PROJECT_PLUGINS": "0"}
    env.update({"META": "", "META_API_KEY": client_key, "MODEL_API_KEY": client_key,
                "META_MODEL_API_KEY": client_key, "META_BASE_URL": driver_url,
                "OPENAI_API_KEY": client_key, "MEM0_API_KEY": "",
                "MEM0_MODE": "oss", "MEM0_HOST": "", "MEM0_TELEMETRY": "false",
                "MEM0_DIR": str((root / "mem0-state").resolve())})
    if provider == "mem0":
        identity = "pacbench-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
        extraction_url, _ = client_settings("mem0-local-extraction")
        configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
        # Invoke the documented agent-driven OSS setup, not a custom SDK shim.
        command = [str(harness.ISOLATED_BIN), "memory", "setup", "mem0", "--mode", "oss",
                   "--oss-llm", "openai", "--oss-llm-model", configured_model,
                   "--oss-llm-url", extraction_url, "--oss-embedder", "ollama",
                   "--oss-embedder-model", "nomic-embed-text", "--oss-embedder-url", "http://127.0.0.1:11435",
                   "--oss-vector", "qdrant", "--oss-vector-path", str((root / "qdrant").resolve()),
                   "--user-id", identity]
        proc = subprocess.run(command, env={**os.environ, **env, "HERMES_HOME": str(home.resolve())},
                              capture_output=True, text=True, timeout=90)
        save(root / "native-setup.json", {"command": command, "returncode": proc.returncode,
                                         "stdout": proc.stdout, "stderr": proc.stderr})
        if proc.returncode or not (home / "mem0.json").exists():
            # The pinned top-level argparse rejects flags documented by its plugin.
            # Use the README's manual mem0.json path and the plugin's own config builder.
            builder = '''
import json,sys
from pathlib import Path
from plugins.memory.mem0._setup import build_oss_config
from plugins.memory.mem0 import Mem0MemoryProvider
flags=json.loads(sys.argv[1])
oss,_=build_oss_config(flags)
Mem0MemoryProvider().save_config({"mode":"oss","user_id":sys.argv[2],"oss":oss},sys.argv[3])
'''
            flags = {"oss_llm": "openai", "oss_llm_model": configured_model,
                     "oss_llm_url": extraction_url, "oss_embedder": "ollama",
                     "oss_embedder_model": "nomic-embed-text", "oss_embedder_url": "http://127.0.0.1:11435",
                     "oss_vector": "qdrant", "oss_vector_path": str((root / "qdrant").resolve())}
            proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c", builder,
                                   json.dumps(flags), identity, str(home.resolve())],
                                  env={**os.environ, **env, "HERMES_HOME": str(home.resolve())},
                                  cwd=harness.ISOLATED_TREE, capture_output=True, text=True, timeout=30)
            save(root / "native-manual-setup.json", {"route": "README manual OSS mem0.json",
                 "flags": flags, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            if proc.returncode or not (home / "mem0.json").exists():
                raise RuntimeError("documented manual Mem0 setup failed; see native-manual-setup.json")
        config = json.loads((home / "mem0.json").read_text())
        # README recommends 6000 characters for 8k-context embedders.
        config["sync_max_chars"] = 6000
        save(home / "mem0.json", config)
        os.chmod(home / "mem0.json", 0o600)
        (home / ".env").write_text("OPENAI_API_KEY=" + client_key + "\n")
        os.chmod(home / ".env", 0o600)
    if provider == "memcal":
        memcal_url, _ = client_settings("memcal-models")
        configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
        env.update({"MEMCAL_LLM_PROVIDER": "openai-compatible", "MEMCAL_OPENAI_BASE_URL": memcal_url,
                    "OPENAI_COMPAT_API_KEY": client_key, "MEMCAL_PROPOSE_MODEL": configured_model,
                    "MEMCAL_SWEEP_MODEL": configured_model, "MEMCAL_MATCH_MODEL": configured_model})
        # Native first-install bootstrap, with no facts seeded into the store.
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from memcal import config, db, brief; "
            "cfg=config.load(); cfg.ensure_dirs(); "
            "conn=db.open_db(cfg.db_path); brief.write(conn,cfg); conn.close()")
        proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c",
                               bootstrap, str(MEMCAL_SRC)],
                              env={**os.environ, **env}, capture_output=True, timeout=30)
        if proc.returncode:
            raise RuntimeError("Memcal native scratch-store bootstrap failed")
    return env


def probe(root: Path, provider: str, env: dict, *, smoke: bool = False) -> dict:
    child_env = harness.turn_env("2026-09-11T09:00:00-04:00",
                                 root / "hermes-home", "", env)
    try:
        proc = subprocess.run(
            [str(harness.ISOLATED_BIN.parent / "python"), "-c", PROBE, provider],
            cwd=harness.ISOLATED_TREE, env=child_env, text=True,
            capture_output=True, timeout=60)
        result = json.loads(proc.stdout.splitlines()[-1])
        if proc.returncode:
            raise ValueError("probe process failed")
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        result = {"available": False, "reason": f"native probe failed: {type(exc).__name__}",
                  "tools": []}
    result["provider"] = provider
    result["live_verified"] = False
    if provider not in ISOLATED_PROVIDERS:
        result["execution_blocker"] = "run-owned backend/remote namespace not configured"
    if smoke and result["available"] and provider in ISOLATED_PROVIDERS:
        try:
            proc = subprocess.run(
                [str(harness.ISOLATED_BIN.parent / "python"), "-c", READ_ONLY_SMOKE, provider],
                cwd=harness.ISOLATED_TREE, env=child_env, text=True,
                capture_output=True, timeout=30)
            if proc.returncode:
                raise ValueError("native smoke failed")
            result["read_only_smoke"] = json.loads(proc.stdout.splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            result["read_only_smoke"] = {"error": type(exc).__name__}
    return result


def exposure_prompt(messages: list[dict]) -> str:
    """The same ordinary transcript handoff for every provider, without private facts."""
    return ("Here are my recent messages. Catch up on them for me.\n\n" +
            "\n".join(f"[{m['written_at']}] {m['channel']} / {m['thread']} / "
                      f"{m['sender']}: {m['text']}" for m in messages))


def smoke_succeeded(capability: dict) -> bool:
    try:
        output = json.loads(capability["read_only_smoke"]["output"])
    except (KeyError, ValueError, TypeError):
        return False
    return isinstance(output, dict) and "error" not in output and output.get("success") is not False


def read_prepared(source: Path, max_messages: int) -> tuple[list[dict], object, dict]:
    summary = json.loads((source / "summary.json").read_text())
    bundle = load_bundle(source / "bundle.json", summary["bundle_hash"])
    if len(bundle["lives"]) != 1:
        raise ValueError("native pilot requires one frozen life")
    messages = messages_for(bundle, next(iter(bundle["lives"])))
    if not messages or len(messages) > max_messages:
        raise ValueError(f"message budget exceeded or empty: {len(messages)} / {max_messages}")
    contract = load_contract(source / "contract.json")
    evidence_hash = hashlib.sha256(json.dumps(messages, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    if evidence_hash != contract.evidence_hash or contract.contract_hash != summary["contract_hash"]:
        raise ValueError("frozen contract does not match exposure")
    resolved = json.loads((source / "resolved-spec.json").read_text())
    if resolved.get("week_of"):
        raise ValueError("native pilot currently supports one final question; use a scene spec")
    return messages, contract, resolved


def run(source: Path | None, output: Path, max_messages: int = 150,
        repetitions: int = 1, *, smoke: bool = False, providers: list[str] | None = None) -> dict:
    if not 1 <= repetitions <= 3 or not 1 <= max_messages <= 1200:
        raise ValueError("pilot limits: 1–3 repetitions, 1–1200 messages")
    selected = providers if providers is not None else provider_names()
    if not selected or len(set(selected)) != len(selected) or any(p not in provider_names() for p in selected):
        raise ValueError("invalid or duplicate provider selection")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    report = {"state": "inventory", "results": results, "mode": "native-hermes",
              "builtin_memory": True, "session_search": False}
    save(output / "summary.json", report)
    driver = None
    judge = plan = None
    messages = contract = resolved = None
    if source:
        messages, contract, resolved = read_prepared(source, max_messages)
        try:
            report["gateway_before"] = require_live_cost_boundary()
        except RuntimeError as exc:
            report.update(state="blocked", reason=str(exc))
            save(output / "summary.json", report)
            raise
        driver = load_driver()
        for name in ("bundle.json", "contract.json", "resolved-spec.json"):
            shutil.copyfile(source / name, output / name)
        report.update(input_hash=contract.evidence_hash, contract_hash=contract.contract_hash,
                      bundle_hash=json.loads((source / "summary.json").read_text())["bundle_hash"],
                      message_count=len(messages), source=str(source.resolve()))
        judge = ModelClient(key=os.environ["META_API_KEY"], model=driver.model_id,
                            url=client_settings("pacbench-judge")[0] + "/chat/completions",
                            record_dir=output / "model-calls")
        plan = freeze_policy(contract, output / "followup-plan.json")
        report["followup_plan_hash"] = plan["plan_hash"]
        report["followup_policy"] = "one combined message for all initially failed facts; half recovery credit; source fallback fails retention"
    for repetition in range(1, repetitions + 1):
        for provider in selected:
            root = output / f"repeat-{repetition}" / provider
            env = provision(root, provider, driver.model_id if driver else "unconfigured")
            capability = probe(root, provider, env, smoke=smoke or source is not None)
            if (smoke or source is not None) and provider in ISOLATED_PROVIDERS:
                capability["wiring_verified"] = smoke_succeeded(capability)
            save(root / "preflight.json", capability)
            result = {"provider": provider, "repetition": repetition,
                      "capability": capability, "state": (
                          "available-unverified" if capability["available"] and
                          not capability.get("execution_blocker") else "blocked")}
            results.append(result)
            if capability.get("wiring_verified") is False:
                result.update(state="invalid", reason="native read-only smoke failed")
            if (not source or not capability["available"] or capability.get("execution_blocker")
                    or result["state"] == "invalid"):
                save(output / "summary.json", report)
                continue
            allowlist = ",".join(["memory", "tool_search", "tool_describe", "tool_call"] +
                                 [n for n in capability["tools"] if n])
            failed = False
            for day, batch in daily_batches(messages):
                at = max(m["collected_at"] for m in batch)
                turn = harness.run_turn(exposure_prompt(batch), at=at,
                                        home=root / "hermes-home", allowlist=allowlist,
                                        extra_env=env, timeout_s=180)
                save(root / f"exposure-{day}.json", vars(turn))
                if turn.returncode or not turn.answer.strip() or turn.policy_blocks:
                    failed = True
                    break
            if failed:
                result.update(state="invalid", reason="exposure turn failed or policy blocked")
            else:
                # Deliberately omit --resume: recall must cross a real session boundary.
                turn = harness.run_turn(contract.question, at=resolved["question_at"],
                                        home=root / "hermes-home", allowlist=allowlist,
                                        extra_env=env, timeout_s=180)
                save(root / "recall.json", vars(turn))
                result["behavior"] = {
                    "provider_tool_calls": [c.get("name") for c in turn.tool_calls
                                            if c.get("name") in capability["tools"]],
                    "prefetch_context_present": bool(turn.prefetch_context)}
                result.update(state="invalid", reason="recall turn failed or policy blocked")
                if not turn.returncode and turn.answer.strip() and not turn.policy_blocks:
                    grade = grade_semantic(resolved["id"], turn.prompt, turn.answer,
                                           tuple(Claim(c.id, c.statement, c.evidence)
                                                 for c in contract.claims), judge,
                                           record_path=root / "judgment.json")
                    save(root / "grade.json", {"correct": grade.correct, "missed": grade.missed,
                                              "misrepresented": grade.misrepresented,
                                              "claims": [vars(s) for s in grade.slots]})
                    result.update(state="diagnostic", reason="", input_hash=contract.evidence_hash)
                    capability["live_verified"] = True
                    recovery = run_followups(contract=contract, plan=plan, grade=grade,
                        initial_turn=turn, output=root / "followups", judge=judge,
                        home=root / "hermes-home", allowlist=allowlist, extra_env=env,
                        question_dir=output / "combined-questions")
                    result["recovery"] = {"state": recovery["state"], "score": recovery["score"],
                        "attempts": [{"claim_ids": a["claim_ids"], "verdicts": a["verdicts"],
                                      "metrics": a["metrics"]} for a in recovery["attempts"]]}
                    if recovery["state"] != "complete":
                        result.update(state="invalid", reason="follow-up diagnostics incomplete")
            save(root / "status.json", result)
            save(output / "summary.json", report)
    report["state"] = ("diagnostic" if all(r["state"] == "diagnostic" for r in results)
                       else "incomplete") if source else "inventory"
    if source:
        report["gateway_after"] = require_live_cost_boundary()
    save(output / "summary.json", report)
    if source:
        from .native_audit import audit
        report["audit"] = audit(output)
        if report["audit"]["state"] != "pass":
            report["state"] = "incomplete"
        save(output / "native-audit.json", report["audit"])
        save(output / "summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-messages", type=int, default=150)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--smoke", action="store_true", help="free native read-only local tool checks")
    parser.add_argument("--providers", nargs="+", help="select native arms; omitted inventories every provider")
    args = parser.parse_args()
    report = run(args.prepared, args.output, args.max_messages, args.repetitions, smoke=args.smoke, providers=args.providers)
    for r in report["results"]:
        print(r["provider"], r["state"], r["capability"].get("execution_blocker") or
              r["capability"].get("reason", ""))
    return 1 if (report["state"] == "incomplete" or any(r["state"] == "invalid" for r in report["results"]) or
                 args.prepared and any(r["state"] != "diagnostic" for r in report["results"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
