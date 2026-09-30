#!/usr/bin/env python3
"""Run a prose scene through frozen evidence, providers, and semantic judging."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacbench import harness  # noqa: E402
from pacbench.adapters import Usage  # noqa: E402
from pacbench.authoring import ScenePlan, build_scene_observations, realize_scene, scene_spec  # noqa: E402
from pacbench.creds import load_driver  # noqa: E402
from pacbench.daily_contract import freeze_daily_contracts  # noqa: E402
from pacbench.frozen import bundle_hash, write_bundle  # noqa: E402
from pacbench.model_calls import ModelClient, ResponsesWriter  # noqa: E402
from pacbench.run_audit import audit_run  # noqa: E402
from pacbench.semantic_contract import build_contract  # noqa: E402
from pacbench.semantic_grade import Claim, grade_semantic  # noqa: E402


RETRIEVAL_TOOLS = {
    "memcal": {"memcal_open", "memcal_open_source", "memcal_search_wiki",
               "memcal_search_archive", "memcal_conversation", "memcal_list_days",
               "memcal_list_month", "memcal_activity", "memcal_open_page"},
    "hindsight": {
        "mcp__hindsight__recall", "mcp__hindsight__reflect",
        "mcp__hindsight__list_memories", "mcp__hindsight__get_memory",
        "mcp__hindsight__list_documents", "mcp__hindsight__get_document",
        "mcp__hindsight__search_knowledge_base",
        "mcp__hindsight__get_knowledge_base_tree",
        "mcp__hindsight__get_knowledge_page",
        "mcp__hindsight__list_mental_models",
        "mcp__hindsight__get_mental_model",
        "mcp__hindsight__list_directives",
    },
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _human_time(value: str) -> time:
    if not isinstance(value, str):
        raise ValueError("time must be written like '7 a.m.' or '8:30 p.m.'")
    match = re.fullmatch(r"\s*(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?m\.?\s*",
                         value, re.IGNORECASE)
    if not match:
        raise ValueError("time must be written like '7 a.m.' or '8:30 p.m.'")
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "p" else 0)
    return time(hour, int(match.group(2) or 0))


def expand_week_spec(spec: dict) -> dict:
    """Turn the readable Monday–Sunday authoring form into scene plumbing."""
    if "days" not in spec:
        return spec
    days = spec["days"]
    if not isinstance(days, dict) or set(days) != set(WEEKDAYS):
        raise ValueError("days must contain Monday through Sunday exactly")
    try:
        monday = datetime.fromisoformat(spec["week_of"]).date()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("week_of must be an ISO Monday date") from exc
    if monday.weekday() != 0:
        raise ValueError("week_of must be a Monday")
    scenes = []
    for offset, day in enumerate(WEEKDAYS):
        rows = days[day]
        if not isinstance(rows, list):
            raise ValueError(f"{day} must be a list of chats")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or "id" in row or "at" in row or "thread" in row:
                raise ValueError(f"{day} chats use channel, chat, time, scene, and facts; IDs are derived")
            channel, chat = row.get("channel"), row.get("chat")
            if not isinstance(channel, str) or not isinstance(chat, str) or not chat.strip():
                raise ValueError(f"{day} chat needs channel and chat name")
            slug = re.sub(r"[^a-z0-9]+", "-", chat.casefold()).strip("-")
            if not slug:
                raise ValueError(f"{day} chat needs an alphabetic name")
            key = (channel, slug)
            if key in seen:
                raise ValueError(f"{day} repeats {channel} chat {chat}; combine its messages in one scene")
            seen.add(key)
            stamp = datetime.combine(monday + timedelta(days=offset),
                                     _human_time(row.get("time"))).isoformat(timespec="seconds")
            scenes.append({"id": f"{day.lower()}-{channel}-{slug}",
                           "channel": channel, "thread": slug, "at": stamp,
                           "scene": row.get("scene", ""),
                           "facts": row.get("facts", [])})
    expanded = {key: value for key, value in spec.items() if key not in ("days", "week_of")}
    expanded["scenes"] = scenes
    expanded["test_scene_id"] = next((s["id"] for s in reversed(scenes) if s["facts"]), None)
    if not expanded["test_scene_id"]:
        raise ValueError("week needs at least one scene with facts")
    next_morning = datetime.combine(monday + timedelta(days=7),
                                    _human_time(spec.get("check_time", "8 a.m.")))
    expanded["question_at"] = next_morning.isoformat(timespec="seconds")
    expanded["maintenance_at"] = (next_morning - timedelta(hours=1)).isoformat(timespec="seconds")
    expanded["week_of"] = monday.isoformat()
    return expanded


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def tool_behavior(provider: str, turn) -> dict:
    """Describe Hermes' observed tool choices, separately from capability."""
    names = RETRIEVAL_TOOLS[provider]
    attempted, executed, blocked, blocked_other = [], [], [], []
    for call, result in zip(turn.tool_calls, turn.tool_results):
        name = call.get("name")
        if name == "tool_call":
            entries = call.get("input", {}).get("calls", [])
            if not isinstance(entries, list):
                entries = []
            try:
                payload = json.loads(result.get("text", ""))
            except (TypeError, ValueError):
                payload = {}
            outcomes = payload.get("results") if isinstance(payload, dict) else None
            for index, entry in enumerate(entries):
                nested = entry.get("name") if isinstance(entry, dict) else None
                if nested not in names:
                    continue
                attempted.append(nested)
                outcome = outcomes[index] if isinstance(outcomes, list) and index < len(outcomes) else payload
                if result.get("policy_blocked") or "PACBENCH-BLOCKED" in str(outcome):
                    blocked.append(nested)
                elif (result.get("success") is True and
                      not (isinstance(outcome, dict) and "error" in outcome)):
                    executed.append(nested)
            if result.get("policy_blocked"):
                blocked_other.append(name)
        elif name in names:
            attempted.append(name)
            if result.get("policy_blocked") or "PACBENCH-BLOCKED" in str(result.get("text", "")):
                blocked.append(name)
            elif result.get("success") is True:
                executed.append(name)
        elif result.get("policy_blocked"):
            blocked_other.append(name)
    return {"provider_tool_attempts": attempted,
            "provider_tool_calls": executed,
            "blocked_provider_calls": blocked,
            "blocked_other_calls": blocked_other,
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
              "allowed": False, "recall_reached_bank": False,
              "query": query}
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


def group_threads(observations: list[dict]) -> dict[tuple[str, str], bool]:
    """Mark conversations with at least two speakers besides the owner."""
    speakers: dict[tuple[str, str], set[str]] = {}
    for obs in observations:
        sender = str(obs["sender"])
        if obs.get("from_owner") or sender.casefold() == "me":
            continue
        key = (str(obs["channel"]), str(obs["thread"]))
        speakers.setdefault(key, set()).add(sender.casefold())
    return {key: len(names) > 1 for key, names in speakers.items()}


def daily_batches(messages: list[dict]) -> list[tuple[str, list[dict]]]:
    """Give each provider new messages once, grouped by fictional date."""
    batches: dict[str, list[dict]] = {}
    for message in messages:
        day = datetime.fromisoformat(message["collected_at"]).date().isoformat()
        batches.setdefault(day, []).append(message)
    return [(day, sorted(batch, key=lambda m: (m["collected_at"], m["source_id"])))
            for day, batch in sorted(batches.items())]


def checkpoint_batches(messages: list[dict], contracts: dict) -> list[tuple[str, list[dict]]]:
    """Keep morning checks on quiet days without replaying old messages."""
    by_day = dict(daily_batches(messages))
    if set(by_day) - set(contracts):
        raise ValueError("an ingested day has no daily contract")
    return [(day, by_day.get(day, [])) for day in sorted(contracts)]


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


def run_arm(provider: str, root: Path, observations: list[dict],
            contract, driver, judge: ModelClient, *, question_at: str,
            maintenance_at: str, life_id: str, local_timezone: str = "",
            daily_contracts: dict | None = None,
            daily_check_time: str = "8 a.m.") -> dict:
    arm = root / provider
    arm.mkdir(parents=True)
    if provider == "memcal":
        from provider_adapters.memcal_adapter import MemcalAdapter
        adapter = MemcalAdapter(
            arm / "provider-home", dream_model=driver.model_id,
            dream_base_url=driver.base_url,
            dream_api_key=os.environ.get("META_API_KEY"))
    else:
        from provider_adapters.hindsight_adapter import HindsightAdapter
        adapter = HindsightAdapter(arm / "provider-home", llm_model=driver.model_id)
    namespace = f"semantic-{life_id}-{root.name}-{provider}"
    result = {"provider": provider, "state": "incomplete", "reason": "not started",
              "contract_hash": contract.contract_hash}
    server = False
    hermes_home = arm / "hermes-home"
    mcp_spec = None

    def ask_checkpoint(scored_contract, at: str, output: Path) -> dict:
        nonlocal server, mcp_spec
        checkpoint = {"state": "incomplete", "reason": "not started",
                      "contract_hash": scored_contract.contract_hash, "at": at}
        if not hermes_home.exists():
            if provider == "hindsight":
                port = _free_loopback_port()
                adapter.start_server(port)
                server = True
                mcp_spec = adapter.mcp_server_spec(port)
            harness.provision_home(hermes_home, provider_plugin_src=(
                Path(seam["plugin_src"]) if seam.get("plugin_available") else None),
                provider_name=provider, model=driver.model_id,
                model_provider=driver.provider,
                mcp_servers=[mcp_spec] if mcp_spec else None)
            if provider == "hindsight":
                preflight = hindsight_preflight(adapter, hermes_home, at,
                                                seam, mcp_spec, "bank access preflight")
            else:
                preflight = {"plugin_installed": (hermes_home / "plugins" / "memcal").is_dir(),
                             "clock_pin": harness.turn_env(at, hermes_home,
                                                           "")["MEMCAL_TODAY"]}
            preflight["tool_allowlist"] = list(seam["tool_allowlist"])
            preflight["retrieval_tools_allowed"] = sorted(
                RETRIEVAL_TOOLS[provider].intersection(seam["tool_allowlist"]))
            save(arm / "preflight.json", preflight)
            if provider == "hindsight" and not all(preflight.get(k) for k in
                    ("sdk", "connected", "allowed", "recall_reached_bank")):
                checkpoint.update(state="invalid", reason="retrieval capability preflight failed",
                                  capability={"available": False, "preflight": str(arm / "preflight.json")})
                return checkpoint
            if provider == "memcal" and not preflight["plugin_installed"]:
                checkpoint.update(state="invalid", reason="Memcal plugin unavailable",
                                  capability={"available": False, "preflight": str(arm / "preflight.json")})
                return checkpoint
        checkpoint["capability"] = {"available": True,
                                    "preflight": str(arm / "preflight.json")}
        turn = harness.run_turn(scored_contract.question, at=at,
                                home=hermes_home,
                                allowlist=",".join(seam["tool_allowlist"]),
                                extra_env=seam.get("env"), timeout_s=180)
        save(output / "turn.json", vars(turn))
        checkpoint["behavior"] = tool_behavior(provider, turn)
        checkpoint["retrieval_outcome"] = (
            "used" if checkpoint["behavior"]["retrieval_called"] else
            "blocked" if checkpoint["behavior"]["blocked_provider_calls"] else
            "not_called")
        if turn.returncode != 0 or not turn.answer.strip():
            checkpoint["reason"] = f"Hermes turn failed: {turn.returncode}"
            return checkpoint
        if provider == "memcal" and (
                turn.prefetch_context is None or
                at[:10] not in turn.prefetch_context and
                datetime.fromisoformat(at).strftime("%-d %B %Y")
                not in turn.prefetch_context):
            checkpoint["reason"] = "Memcal fictional-clock prefetch missing or mismatched"
            return checkpoint
        claims = tuple(Claim(c.id, c.statement, c.evidence) for c in scored_contract.claims)
        try:
            grade = grade_semantic(life_id, scored_contract.question, turn.answer,
                                   claims, judge,
                                   record_path=output / "judgment.json")
        except Exception as exc:
            checkpoint["reason"] = f"semantic judgment failed: {exc}"
            return checkpoint
        save(output / "grade.json", {"correct": grade.correct,
                                      "missed": grade.missed,
                                      "misrepresented": grade.misrepresented,
                                      "claims": [vars(s) for s in grade.slots]})
        if checkpoint["retrieval_outcome"] == "blocked":
            checkpoint.update(state="invalid", reason="Hermes retrieval call blocked by harness policy")
            return checkpoint
        checkpoint.update(state="valid", reason="")
        return checkpoint
    try:
        adapter.reset(namespace)
        seam = adapter.retrieval()
        save(arm / "retrieval.json", {k: v for k, v in seam.items() if k != "env"})
        input_hash = bundle_hash({"messages": observations})
        batches = (checkpoint_batches(observations, daily_contracts)
                   if daily_contracts else daily_batches(observations))
        ingested = []
        maintained = []
        provider_packets = []
        seen = []
        checkpoints = []
        for index, (day, batch) in enumerate(batches):
            boundary = (max(o["collected_at"] for o in batch) if batch else
                        datetime.combine(datetime.fromisoformat(day).date() + timedelta(days=1),
                                         time.min,
                                         ZoneInfo(local_timezone) if local_timezone else None
                                         ).isoformat(timespec="seconds"))
            seen.extend(batch)
            current_groups = group_threads(seen)
            day_record = arm / f"ingestion-{day}.json"
            save(day_record, {"state": "started", "at": boundary,
                              "packet": batch})
            usage = (adapter.ingest(batch, boundary, group_flags=current_groups)
                     if batch else Usage(notes=("no new messages",)))
            if bundle_hash({"messages": observations}) != input_hash:
                raise RuntimeError(f"{provider} changed the frozen input messages")
            packets = getattr(adapter, "_last_packets", None) if batch else []
            if packets:
                provider_packets.extend(packets)
            ingested.append({"day": day, "at": boundary,
                             "source_ids": [o["source_id"] for o in batch],
                             "group_threads": [
                                 {"channel": channel, "thread": thread}
                                 for (channel, thread), grouped in sorted(current_groups.items())
                                 if grouped], "ack": vars(usage),
                             "provider_packets": packets})
            save(day_record, {"state": "complete", **ingested[-1],
                              "packet": batch})
            save(arm / "ingestion.json", {
                "at": boundary, "packet": observations,
                "input_hash": input_hash,
                "group_threads": ingested[-1]["group_threads"],
                "ack": ingested[-1]["ack"],
                "provider_packets": provider_packets,
                "daily_batches": ingested})
            if daily_contracts:
                next_day = datetime.fromisoformat(day).date() + timedelta(days=1)
                morning = datetime.combine(next_day, _human_time(daily_check_time),
                                           ZoneInfo(local_timezone) if local_timezone else None)
                maintenance_time = (morning - timedelta(hours=1)).isoformat()
                upcoming = [o for _, later in batches[index + 1:] for o in later]
                if upcoming:
                    next_first = min(datetime.fromisoformat(o["collected_at"])
                                     for o in upcoming)
                    if morning >= next_first:
                        raise ValueError("morning checkpoint overlaps the next batch")
            elif index + 1 == len(batches):
                maintenance_time = maintenance_at
            else:
                if local_timezone:
                    next_day = datetime.fromisoformat(boundary).date() + timedelta(days=1)
                    maintenance_time = datetime.combine(
                        next_day, time.min, ZoneInfo(local_timezone)).isoformat()
                else:
                    last_clock = datetime.fromisoformat(boundary)
                    maintenance_time = (last_clock.replace(
                        hour=0, minute=0, second=0, microsecond=0) +
                        timedelta(days=1)).isoformat()
                next_first = min(datetime.fromisoformat(o["collected_at"])
                                 for o in batches[index + 1][1])
                if datetime.fromisoformat(maintenance_time) >= next_first:
                    raise ValueError("daily maintenance would overlap the next batch")
            maintenance = adapter.maintenance(maintenance_time)
            readiness = adapter.readiness()
            maintained.append({"day": day, "at": maintenance_time,
                               "usage": vars(maintenance),
                               "readiness": vars(readiness),
                               "detail": getattr(adapter, "_maintenance", None)})
            save(arm / "maintenance.json", {**maintained[-1],
                                            "daily_passes": maintained})
            if not readiness.ready:
                break
            if daily_contracts:
                checkpoint_at = morning.isoformat(timespec="seconds")
                output = arm / "turns" / day
                checkpoint = ask_checkpoint(daily_contracts[day], checkpoint_at, output)
                checkpoint["day"] = day
                checkpoints.append(checkpoint)
                save(output / "status.json", checkpoint)
                result["checkpoints"] = checkpoints
                result["behavior"] = checkpoint.get("behavior")
                result["capability"] = checkpoint.get("capability")
                result["retrieval_outcome"] = checkpoint.get("retrieval_outcome")
                if checkpoint["state"] != "valid":
                    result.update(state=checkpoint["state"],
                                  reason=f"{day}: {checkpoint['reason']}")
                    return result
        if not readiness.ready:
            result["reason"] = f"maintenance not ready: {readiness.detail}"
            return result
        if daily_contracts:
            result.update(state="valid", reason="",
                          capability=checkpoints[0]["capability"],
                          behavior=checkpoints[-1]["behavior"])
            return result
        result.update(ask_checkpoint(contract, question_at, arm))
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
    spec = expand_week_spec(spec)
    rows = spec.get("scenes")
    if not isinstance(rows, list) or not rows:
        raise ValueError("experiment needs at least one scene")
    if "facts" in spec and (not isinstance(spec["facts"], list) or
                            not spec["facts"] or
                            any(not isinstance(f, str) or not f.strip()
                                for f in spec["facts"])):
        raise ValueError("top-level facts must be nonempty prose assertions")
    timezone_name = spec.get("timezone", "")
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
    maintenance_at = (local_clock(spec["maintenance_at"], zone)
                      if spec.get("maintenance_at") else question_at - timedelta(hours=1))
    question_at = question_at.isoformat(timespec="seconds")
    maintenance_at = maintenance_at.isoformat(timespec="seconds")
    if (datetime.fromisoformat(maintenance_at) <= latest or
            datetime.fromisoformat(question_at) <= datetime.fromisoformat(maintenance_at)):
        raise ValueError("maintenance must follow scenes; question must follow maintenance")
    providers = spec.get("providers", ["memcal", "hindsight"])
    if not providers or any(p not in ("memcal", "hindsight") for p in providers):
        raise ValueError("unsupported provider selection")
    return scenes, target, {
        "id": life_id, "test_scene_id": target.id,
        "week_of": spec.get("week_of"),
        "check_time": spec.get("check_time", "8 a.m."),
        "timezone": timezone_name,
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
    writer = ResponsesWriter(key=key, model=driver.model_id, record_dir=call_dir,
                             cache_dir=ROOT / "runs" / "model-cache")
    judge = ModelClient(key=key, model=driver.model_id, record_dir=call_dir,
                        cache_dir=ROOT / "runs" / "model-cache")
    daily_model = ModelClient(key=key, model=driver.model_id,
                              record_dir=call_dir,
                              cache_dir=ROOT / "runs" / "model-cache",
                              reasoning_effort="low", json_mode=True)
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
    daily_contracts = None
    if resolved["week_of"]:
        daily_contracts = freeze_daily_contracts(
            monday=datetime.fromisoformat(resolved["week_of"]).date(),
            scenes=scenes, messages=observations, model=daily_model, root=root,
            check_time=resolved["check_time"])
        contract = daily_contracts[max(daily_contracts)]
        (root / "contract.json").write_bytes(
            (root / "contracts" / f"{max(daily_contracts)}.json").read_bytes())
    else:
        contract = build_contract(
            scene=target.scene, test_intent=target.test_intent,
            author_facts=tuple(spec.get("facts", target.facts)),
            observations=observations, generate=judge, verify=judge,
            question_generate=judge, output_path=root / "contract.json")
    progress["contract_frozen_at"] = datetime.now(timezone.utc).isoformat()
    progress["stage"] = "provider arms"
    save(root / "experiment-status.json", progress)
    if spec.get("prepare_only") is True:
        summary = {"state": "prepared", "results": [],
                   "contract_hash": contract.contract_hash,
                   "daily_contract_hashes": ({day: c.contract_hash for day, c in
                                             daily_contracts.items()} if daily_contracts else None),
                   "bundle_hash": bundle_hash(bundle)}
        save(root / "summary.json", summary)
        return summary
    results = [run_arm(p, root, json.loads(json.dumps(observations)), contract, driver, judge,
                       question_at=question_at, maintenance_at=maintenance_at,
                       life_id=life_id, local_timezone=resolved["timezone"],
                       daily_contracts=daily_contracts,
                       daily_check_time=resolved["check_time"])
               for p in providers]
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
        audit = audit_run(root)
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
