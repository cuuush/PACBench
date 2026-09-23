#!/usr/bin/env python3
"""Build explorer/data/*.json: frozen corpus + run-result summaries.

Reads available frozen bundles, fail-closed verifies each embedded bundle_hash
via pacbench.frozen.bundle_hash (aborts on mismatch), and extracts per-arm
summaries from every runs/life-*/ dir present at build time (grades.json +
manifest.json + slimmed turns.json). Run dirs without grades.json are listed
as pending, not fatal.

Regenerate: /usr/local/bin/python3 scripts/build_explorer_data.py
Serve:      /usr/local/bin/python3 serve_explorer.py
"""

import datetime
import importlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pacbench.frozen import bundle_hash  # noqa: E402

BUNDLES = ["runs/race-week-bundle.json", "runs/revoke-week-bundle.json",
           "runs/tiny-toy-bundle.json", "runs/tiny-pizza-bundle.json"]
# Corpus modules exposing lives() with Life.checkpoints carrying the rubrics.
CORPUS_MODULES = ["life_g_toyday", "poker_move", "weekend_plans"]
FIELDS = ("source_id", "text", "channel", "sender", "thread",
          "written_at", "collected_at", "from_owner")
OUT = ROOT / "explorer" / "data"

# Keep retrieved evidence inspectable in the UI; label genuinely truncated data.
TOOL_TEXT_CAP = 12000


def slot_dict(s) -> dict:
    kind = getattr(s.kind, "value", s.kind)
    norm = getattr(s.norm, "value", s.norm)
    return {"id": s.id, "kind": str(kind), "norm": str(norm),
            "expected": s.expected, "alternatives": list(s.alternatives),
            "forbidden": list(s.forbidden),
            "supported_by": list(s.supported_by),
            "required": bool(s.required), "error": s.error or ""}


def _cp_dict(cp, source: str) -> dict:
    return {"question": cp.question, "at": cp.at,
            "mode": cp.mode, "family": cp.family,
            "difficulty_tags": list(cp.difficulty_tags),
            "required_fields": list(cp.required_fields),
            "slots": [slot_dict(s) for s in cp.slots],
            "supported": [slot_dict(s) for s in cp.supported],
            "forbidden_options":
                [slot_dict(s) for s in cp.forbidden_options],
            "source": source}


def load_criteria() -> dict:
    """Checkpoint rubrics available in the current corpus."""
    criteria: dict = {}
    for mod in CORPUS_MODULES:
        try:
            lives = importlib.import_module(
                f"pacbench.corpus.{mod}").lives()
        except Exception as exc:
            print(f"criteria: worktree {mod} unimportable: {exc}")
            continue
        for life in lives:
            for cp in life.checkpoints:
                criteria[cp.id] = _cp_dict(cp, f"worktree:{mod}")
    print(f"criteria: {len(criteria)} checkpoints from corpus modules")
    return criteria


def slim_turn(t: dict) -> dict:
    tools = []
    for c in t.get("tool_calls", []) or []:
        tools.append({"name": c.get("name"), "input": c.get("input")})
    results = []
    for r in t.get("tool_results", []) or []:
        txt = r.get("text", "")
        results.append({"tool": r.get("tool"), "success": r.get("success"),
                        "truncated": bool(r.get("truncated")) or len(str(txt)) > TOOL_TEXT_CAP,
                        "text": str(txt)[:TOOL_TEXT_CAP]})
    return {"at": t.get("at"), "kind": t.get("kind"),
            "prompt": t.get("prompt"), "answer": t.get("answer"),
            "source_id": t.get("source_id"),
            "session_id": t.get("session_id"),
            "latency_s": t.get("latency_s"), "returncode": t.get("returncode"),
            "policy_blocks": t.get("policy_blocks"),
            "turn_input_tokens": t.get("turn_input_tokens"),
            "turn_output_tokens": t.get("turn_output_tokens"),
            "tool_calls": tools, "tool_results": results}


def saved_hermes_contexts(run_dir: Path, turns: list[dict]) -> None:
    """Recover context actually sent with historical turns, when saved."""
    db = run_dir / "hermes-home" / "state.db"
    if not db.exists():
        return
    try:
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
            messages = conn.execute(
                "SELECT COALESCE(api_content, content), timestamp "
                "FROM messages WHERE role = 'user' ORDER BY id"
            ).fetchall()
    except sqlite3.Error as exc:
        print(f"run {run_dir.name}: Hermes context unreadable: {exc}")
        return
    cursor = 0
    for turn in turns:
        prompt = turn.get("prompt")
        if not prompt:
            continue
        for index in range(cursor, len(messages)):
            content = messages[index][0]
            if not isinstance(content, str):
                continue
            before_context = content.split("<memory-context>", 1)[0].rstrip()
            if not before_context.endswith(prompt):
                continue
            cursor = index + 1
            if isinstance(messages[index][1], (int, float)):
                turn["hermes_received_at"] = datetime.datetime.fromtimestamp(
                    messages[index][1], datetime.timezone.utc).isoformat()
            start = content.find("<memory-context>", len(before_context))
            end = content.find("</memory-context>", start)
            if start >= 0 and end >= 0:
                turn["prefetch_context"] = content[start:end + len("</memory-context>")]
                turn["prefetch_source"] = "saved Hermes session"
            break


def historical_temporal_audit(turns: list[dict], batches: list[dict]) -> dict | None:
    """Flag source evidence ingested before a turn but collected after it."""
    findings = []
    for index, turn in enumerate(turns, 1):
        if turn.get("kind") != "user-turn-replay" or not turn.get("hermes_received_at"):
            continue
        for batch in batches:
            if (batch.get("collected_at", "") > (turn.get("at") or "") and
                    batch.get("ingested_at", "") < turn["hermes_received_at"]):
                findings.append({"turn": index, "at": turn["at"],
                                 "future_batch": batch.get("collected_at"),
                                 "observations": batch.get("observations", [])})
    if not findings:
        return None
    return {"valid_for_comparison": False,
            "finding": "Future observations were ingested before earlier Hermes turns.",
            "temporal_exposure": findings}


def main() -> None:
    criteria = load_criteria()
    lives: dict = {}
    bundle_rows = []
    hash_to_file = {}
    for rel in BUNDLES:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        bundle = json.loads(raw)
        claimed = bundle.get("bundle_hash", "")
        if not claimed or bundle_hash(bundle) != claimed:
            raise SystemExit(f"ABORT hash mismatch: {rel} "
                             f"(claims {claimed[:12] or 'none'})")
        hash_to_file[claimed] = rel.split("/")[-1]
        n_obs = 0
        for lid, life in bundle["lives"].items():
            obs = [{k: o.get(k, "") for k in FIELDS}
                   for o in life["observations"]]
            lives[lid] = {"bundle": rel.split("/")[-1], "hash": claimed,
                          "obs": obs}
            n_obs += len(obs)
        bundle_rows.append({"file": rel.split("/")[-1], "hash": claimed,
                            "lives": sorted(bundle["lives"]),
                            "obs": n_obs})
        print(f"verified {rel}: hash {claimed[:12]} "
              f"lives={len(bundle['lives'])} obs={n_obs}")

    run_rows = []
    pending = []
    for run_dir in sorted((ROOT / "runs").glob("life-*")):
        if not run_dir.is_dir():
            continue
        gpath = run_dir / "grades.json"
        if not gpath.exists():
            pending.append(run_dir.name)
            continue
        grades_doc = json.loads(gpath.read_text(encoding="utf-8"))
        cls = {c.get("checkpoint"): c
               for c in grades_doc.get("classifications", [])}
        grades = []
        for g in grades_doc.get("grades", []):
            c = cls.get(g.get("checkpoint"), {})
            cp_id = g.get("checkpoint")
            crit = criteria.get(cp_id)
            if crit is None:
                print(f"run {run_dir.name}: no corpus criteria for {cp_id}")
            grades.append({"checkpoint": cp_id,
                           "answer": g.get("answer"),
                           "f1": g.get("f1"), "precision": g.get("precision"),
                           "recall": g.get("recall"),
                           "correct": g.get("correct"),
                           "missed": g.get("missed"),
                           "misrepresented": g.get("misrepresented"),
                           "abstention": g.get("abstention"),
                           "complete_plan": g.get("complete_plan"),
                           "slots": g.get("slots", []),
                           "options_found": g.get("options_found"),
                           "options_total": g.get("options_total"),
                           "suggested_forbidden": g.get("suggested_forbidden"),
                           "class_basis": c.get("basis"),
                           "class_category": c.get("category"),
                           "class_reason": c.get("reason"),
                           "criteria": crit})
        manifest = {}
        mpath = run_dir / "manifest.json"
        if mpath.exists():
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        turns = []
        tpath = run_dir / "turns.json"
        if tpath.exists():
            tdoc = json.loads(tpath.read_text(encoding="utf-8"))
            turns = [slim_turn(t) for t in tdoc.get("turns", [])]
            saved_hermes_contexts(run_dir, turns)
        validity = {}
        vpath = run_dir / "validity.json"
        if vpath.exists():
            validity = json.loads(vpath.read_text(encoding="utf-8"))
        claimed_bh = str(manifest.get("bundle_hash", "") or "")
        exposure = json.loads((run_dir / "observations.json").read_text(
            encoding="utf-8")) if (run_dir / "observations.json").exists() else {}
        evidence_life = None
        if exposure.get("exposed"):
            evidence_life = "run:" + run_dir.name
            lives[evidence_life] = {
                "bundle": hash_to_file.get(claimed_bh) or "saved run exposure; original bundle not captured",
                "hash": claimed_bh,
                "obs": [{k: o.get(k, "") for k in FIELDS} for o in exposure["exposed"]]}
        audit = historical_temporal_audit(turns, manifest.get("import_batches", []))
        run_rows.append({
            "kind": "historical",
            "dir": run_dir.name,
            "life": manifest.get("life", ""),
            "provider": manifest.get("provider", ""),
            "run": manifest.get("run", ""),
            "recorded_at": manifest.get("recorded_at", ""),
            "bundle_hash": claimed_bh,
            "bundle_file": hash_to_file.get(claimed_bh),
            "bundle_match": claimed_bh in hash_to_file if claimed_bh else None,
            "evidence_life": evidence_life,
            "validity": validity.get("state", ""),
            "audit": audit,
            "summary": grades_doc.get("summary", {}),
            "import_batches": manifest.get("import_batches", []),
            "session_schedule": manifest.get("session_schedule", []),
            "adapter_usage": manifest.get("adapter_usage", {}),
            "maintenance": json.loads((run_dir / "maintenance.json").read_text(
                encoding="utf-8")) if (run_dir / "maintenance.json").exists() else {},
            "exposure": exposure,
            "grades": grades, "turns": turns})
        print(f"run {run_dir.name}: life={manifest.get('life')} "
              f"provider={manifest.get('provider')} "
              f"checkpoints={len(grades)} turns={len(turns)} "
              f"bundle={claimed_bh[:12] or 'none'}"
              f"{'' if claimed_bh in hash_to_file else ' UNMATCHED'}")

    def read_json(path: Path):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    for run_dir in sorted((ROOT / "runs").glob("semantic-*")):
        spec = read_json(run_dir / "spec.json")
        bundle = read_json(run_dir / "bundle.json")
        if not spec or not bundle:
            continue
        actual_hash = bundle_hash(bundle)
        provenance = read_json(run_dir / "bundle-provenance.json") or {}
        if provenance.get("hash") != actual_hash:
            raise SystemExit(f"ABORT semantic bundle mismatch: {run_dir}")
        life_id = spec["id"]
        evidence_life = "run:" + run_dir.name
        observations = bundle["lives"][life_id]["observations"]
        lives[evidence_life] = {"bundle": str(run_dir / "bundle.json"),
                                "hash": actual_hash, "obs": observations}
        for provider in spec.get("providers", ["memcal", "hindsight"]):
            arm = run_dir / provider
            if not arm.exists():
                continue
            archive_sources = {}
            archive_db = arm / "provider-home" / "memcal.db"
            if provider == "memcal" and archive_db.exists():
                with sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True) as db:
                    archive_sources = {str(row[0]): row[1] for row in
                        db.execute("SELECT id, external_id FROM archive")}
            run_rows.append({
                "kind": "semantic", "dir": run_dir.name + "/" + provider,
                "life": life_id, "provider": provider,
                "recorded_at": run_dir.name, "bundle_hash": actual_hash,
                "bundle_file": str(run_dir / "bundle.json"),
                "bundle_match": True, "evidence_life": evidence_life,
                "observations": observations,
                "archive_sources": archive_sources,
                "contract": read_json(run_dir / "contract.json"),
                "ingestion": read_json(arm / "ingestion.json"),
                "maintenance": read_json(arm / "maintenance.json"),
                "retrieval": read_json(arm / "retrieval.json"),
                "preflight": read_json(arm / "preflight.json"),
                "turn": read_json(arm / "turn.json"),
                "judgment": read_json(arm / "judgment.json"),
                "grade": read_json(arm / "grade.json"),
                "status": read_json(arm / "status.json")})

    # One record per experiment lets the dashboard trace both arms against
    # the same frozen bundle, including experiments that stopped before ingest.
    dashboard_runs = []
    for run_dir in sorted((ROOT / "runs").glob("semantic-*"), reverse=True):
        if not run_dir.is_dir():
            continue
        spec = read_json(run_dir / "spec.json")
        if not spec:
            continue
        bundle = read_json(run_dir / "bundle.json")
        provenance = read_json(run_dir / "bundle-provenance.json")
        if bundle is not None:
            actual_hash = bundle_hash(bundle)
            if not provenance or provenance.get("hash") != actual_hash:
                raise SystemExit(f"ABORT semantic bundle mismatch: {run_dir}")
        else:
            actual_hash = None
        observations = (bundle or {}).get("lives", {}).get(spec.get("id"), {}).get(
            "observations", [])
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
                    exchanges.append({name: read_json(turn_dir / (name + ".json"))
                                      for name in ("turn", "judgment", "grade")})
            if not exchanges and arms[provider]["turn"]:
                exchanges = [{name: arms[provider][name]
                              for name in ("turn", "judgment", "grade")}]
            arms[provider]["exchanges"] = exchanges
            # Older semantic runs predate the separate capability/behavior
            # fields. Derive only what the saved trace actually demonstrates.
            old_status = arms[provider]["status"]
            if old_status is not None and arms[provider]["turn"]:
                names = (["memcal_search_wiki", "memcal_search_archive", "memcal_open",
                          "memcal_open_source", "memcal_conversation", "memcal_list_days"]
                         if provider == "memcal" else ["mcp__hindsight__recall",
                          "mcp__hindsight__reflect", "mcp__hindsight__list_memories"])
                calls = [c.get("name") for c in arms[provider]["turn"].get("tool_calls", [])]
                results = arms[provider]["turn"].get("tool_results", [])
                attempts = [name for name in calls if name in names]
                used = [name for name, result in zip(calls, results)
                        if name in names and result.get("success") is True
                        and "PACBENCH-BLOCKED" not in str(result.get("text", ""))]
                blocked = [name for name, result in zip(calls, results)
                           if name in names and "PACBENCH-BLOCKED" in str(result.get("text", ""))]
                derived = {"provider_tool_attempts": attempts,
                           "provider_tool_calls": used, "blocked_provider_calls": blocked,
                           "retrieval_called": bool(used),
                           "policy_blocks": arms[provider]["turn"].get("policy_blocks"),
                           "basis": "derived from saved turn"}
                recorded = old_status.get("behavior")
                if recorded and (recorded.get("provider_tool_calls") != used or
                                 recorded.get("retrieval_called") != bool(used)):
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
            model_calls.append({"file": path.name, "record": read_json(path)})
        verifications = []
        for path in sorted((run_dir / "scene-verifications").glob("*.json")):
            verifications.append({"file": path.name, "record": read_json(path)})
        dashboard_runs.append({"kind": "semantic", "id": run_dir.name,
                               "spec": spec, "summary": read_json(run_dir / "summary.json"),
                               "resolved_spec": read_json(run_dir / "resolved-spec.json"),
                               "experiment_status": read_json(run_dir / "experiment-status.json"),
                               "audit": read_json(run_dir / "run-audit.json"),
                               "bundle": bundle, "bundle_hash": actual_hash,
                               "provenance": provenance, "observations": observations,
                               "scenes": read_json(run_dir / "scenes.json"),
                               "contract": read_json(run_dir / "contract.json"),
                               "verifications": verifications, "model_calls": model_calls,
                               "calibration": read_json(run_dir / "calibration-v2" / "report.json"),
                               "arms": arms})
    dashboard_runs.extend(run for run in run_rows if run["kind"] == "historical")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "corpus.json").write_text(json.dumps(lives), encoding="utf-8")
    (OUT / "runs.json").write_text(
        json.dumps({"runs": run_rows, "pending": pending}), encoding="utf-8")
    (OUT / "dashboard.json").write_text(
        json.dumps({"generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
                    "runs": dashboard_runs, "pending": pending},
                   ensure_ascii=False), encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps({
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "bundles": bundle_rows,
        "lives": sorted(lives),
        "obs_total": sum(len(v["obs"]) for v in lives.values()),
        "runs": [r["dir"] for r in run_rows],
        "runs_pending": pending}, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({len(lives)} lives, {len(run_rows)} runs, "
          f"{len(pending)} pending)")


if __name__ == "__main__":
    main()
