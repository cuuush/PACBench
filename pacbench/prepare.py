"""Prepare verified source messages and question contracts without provider execution."""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .authoring import ScenePlan, build_scene_observations, realize_scene, scene_spec
from .creds import load_driver
from .gateway_client import client_settings
from .frozen import bundle_hash, write_bundle
from .model_calls import ModelClient, ResponsesWriter
from .semantic_contract import build_contract, load_contract
from .followups import freeze_policy, save
from .preparation_audit import audit_prepared

def local_clock(value: str, zone: ZoneInfo | None) -> datetime:
    """Interpret an offset-free authored clock as local wall time for its day."""
    clock = datetime.fromisoformat(value)
    if zone is None:
        return clock
    if clock.tzinfo is not None:
        wall = clock.replace(tzinfo=None)
        valid_offsets = {wall.replace(tzinfo=zone, fold=fold).utcoffset()
                         for fold in (0, 1)}
        if clock.utcoffset() not in valid_offsets:
            raise ValueError(f"offset conflicts with local time zone: {value}")
        converted = clock.astimezone(zone)
        if converted.replace(tzinfo=None) != wall:
            raise ValueError(f"nonexistent local time: {value}")
        return converted
    clock = clock.replace(tzinfo=zone)
    # ZoneInfo silently permits nonexistent spring-forward wall times.
    if clock.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != clock.replace(tzinfo=None):
        raise ValueError(f"nonexistent local time: {value}")
    if clock.replace(fold=0).utcoffset() != clock.replace(fold=1).utcoffset():
        raise ValueError(f"ambiguous local time: {value}")
    return clock

def resolve_spec(spec: dict, root: Path) -> tuple[list[ScenePlan], ScenePlan, dict]:
    """Fill operational clocks and identifiers without changing author prose."""
    rows = spec.get("scenes")
    if not isinstance(rows, list) or not rows:
        raise ValueError("experiment needs at least one scene")
    if "facts" in spec and (not isinstance(spec["facts"], list) or
                            not spec["facts"] or
                            any(not isinstance(f, str) or not f.strip()
                                for f in spec["facts"])):
        raise ValueError("top-level facts must be nonempty prose assertions")
    timezone_name = spec.get("timezone", "America/New_York")
    if timezone_name and not isinstance(timezone_name, str):
        raise ValueError("timezone must be an IANA time zone name")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else None
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown time zone: {timezone_name}") from exc
    seed = (local_clock(spec["question_at"], zone) - timedelta(days=1)
            if spec.get("question_at") else datetime.now(zone or timezone.utc))
    scenes = []
    for index, authored in enumerate(rows, 1):
        row = dict(authored)
        row.setdefault("id", f"scene-{index:02d}")
        if not row.get("at") and not row.get("window_start"):
            row["at"] = (seed.replace(hour=18, minute=0, second=0,
                                       microsecond=0) +
                         timedelta(minutes=21 * (index - 1))).isoformat(timespec="seconds")
        if zone:
            for field in ("at", "window_start", "window_end"):
                if row.get(field):
                    row[field] = local_clock(row[field], zone).isoformat(timespec="seconds")
            row["timezone"] = timezone_name
        scenes.append(ScenePlan(**row))
    if len({s.id for s in scenes}) != len(scenes):
        raise ValueError("scene IDs must be unique")
    for previous, current in zip(scenes, scenes[1:]):
        if datetime.fromisoformat(current.window_start) < datetime.fromisoformat(previous.window_start):
            raise ValueError("scenes must be authored in fictional time order")
    last_thread_end: dict[tuple[str, str], datetime] = {}
    for scene in scenes:
        key = (scene.channel, scene.thread)
        start = datetime.fromisoformat(scene.window_start)
        if key in last_thread_end and start < last_thread_end[key]:
            raise ValueError(f"overlapping windows in thread {scene.thread}")
        last_thread_end[key] = datetime.fromisoformat(scene.window_end)
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
    next_day = latest.date() + timedelta(days=1)
    default_question = (datetime.combine(next_day, time(hour=9), zone)
                        if zone else (latest + timedelta(days=1)).replace(
                            hour=9, minute=0, second=0, microsecond=0))
    question_at = local_clock(spec["question_at"], zone) if spec.get("question_at") else default_question
    question_at = question_at.isoformat(timespec="seconds")
    if datetime.fromisoformat(question_at) <= latest:
        raise ValueError("evaluation must follow the source scenes")
    if any(key in spec for key in ('providers', 'prepare_only', 'exposure_context', 'exposure_delivery', 'week_of', 'days')):
        raise ValueError("authoring specs contain scenes and evaluation checks; select providers in native_pilot")
    from .evaluation import resolve_checks
    return scenes, target, {
        "id": life_id, "test_scene_id": target.id,
        "timezone": timezone_name,
        "question_at": question_at, "scenes": [scene_spec(s) for s in scenes],
        **resolve_checks(spec, question_at, timezone_name)}

def _prepare_body(spec: dict, root: Path, progress: dict) -> dict:
    scenes, target, resolved = resolve_spec(spec, root)
    save(root / "resolved-spec.json", resolved)
    life_id = resolved["id"]
    progress["stage"] = "scene generation and verification"
    save(root / "experiment-status.json", progress)
    driver = load_driver()
    key = os.environ["META_API_KEY"]
    call_dir = root / "model-calls"
    writer = ResponsesWriter(key=key, model=driver.model_id, record_dir=call_dir,
                             url=client_settings("pacbench-authoring")[0] + "/responses",
                             cache_dir=root / "model-cache")
    judge = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                        url=client_settings("pacbench-judge")[0] + "/chat/completions",
                        cache_dir=root / "model-cache")
    observations = []
    scene_records = []
    thread_history: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for plan in scenes:
        key = (plan.channel, plan.thread)
        realized = realize_scene(plan, writer, judge, model=driver.model_id,
                                 record_dir=root / "scene-verifications",
                                 prior_lines=tuple(thread_history.get(key, ())))
        new_messages = build_scene_observations(life_id, plan, list(realized.lines))
        thread_history.setdefault(key, []).extend(
            (o.written_at, o.sender, o.text) for o in new_messages)
        observations.extend(o.public() for o in new_messages)
        scene_records.append({"scene_id": plan.id, "lines": realized.lines,
                              "record": vars(realized.record),
                              "verdict": realized.verdict})
        save(root / "scenes.json", {"scenes": scene_records})
    bundle = {"bundle_version": "0.3.0", "lives": {life_id: {
        "lineage": life_id, "messages": observations}}}
    write_bundle(bundle, root / "bundle.json")
    save(root / "bundle-provenance.json", {"hash": bundle_hash(bundle),
                                           "source": "verified-scenes"})
    progress["stage"] = "contract generation and review"
    save(root / "experiment-status.json", progress)
    evaluation_plan = None
    if resolved['checks']:
        from .evaluation import freeze_evaluations
        evaluation_plan = freeze_evaluations(resolved, observations, target, judge, root)
        first = evaluation_plan['checks'][0]['id']
        (root / 'contract.json').write_bytes((root / 'evaluations' / first / 'contract.json').read_bytes())
        contract = load_contract(root / 'contract.json')
    else:
        contract = build_contract(
            scene=target.scene, test_intent=target.test_intent,
            author_facts=tuple(spec.get("facts", target.facts)),
            observations=observations, generate=judge, verify=judge,
            question_generate=judge, output_path=root / "contract.json")
    progress["contract_frozen_at"] = datetime.now(timezone.utc).isoformat()
    freeze_policy(contract, root / "followup-plan.json")
    summary = {"state": "prepared", "results": [],
               "contract_hash": contract.contract_hash, "bundle_hash": bundle_hash(bundle)}
    if evaluation_plan:
        summary['evaluation_plan_hash'] = evaluation_plan['plan_hash']
    save(root / "summary.json", summary)
    return summary

def prepare(spec: dict, root: Path) -> dict:
    """Run once and leave a diagnosis even when authoring fails early."""
    root.mkdir(parents=True, exist_ok=False)
    save(root / "spec.json", spec)
    progress = {"state": "running", "stage": "spec validation",
                "started_at": datetime.now(timezone.utc).isoformat()}
    save(root / "experiment-status.json", progress)
    try:
        summary = _prepare_body(spec, root, progress)
        audit = audit_prepared(root)
        save(root / "run-audit.json", audit)
        if audit["state"] != "pass":
            summary["state"] = "incomplete"
            summary["audit_errors"] = audit["errors"]
            save(root / "summary.json", summary)
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
    parser = argparse.ArgumentParser(description="Generate, verify and freeze source messages; never run providers.")
    parser.add_argument("spec", type=Path, help="JSON scene and fact specification")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(json.loads(args.spec.read_text()), args.output.resolve())
    print(args.output)
    return 0 if result['state'] == 'prepared' else 1

if __name__ == "__main__":
    raise SystemExit(main())
