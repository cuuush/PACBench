"""Live pilot execution: collection + isolated Hermes turns + artifacts.

W4/W5 runtime (contract §§28/30/31/32/41/42). Generic orchestration only:
evidence reaches the provider through the adapter's ``ingest`` at declared
collection times, ``hermes``/``agent``-channel observations are replayed as real
isolated Hermes turns (never adapter DB writes, §31), checkpoint questions are
asked through isolated Hermes and graded afterwards (§32), nightly provider
maintenance runs at declared simulated times with verified readiness, and
failures are classified from trace evidence only (§34).

This module never imports a provider. Provider specifics arrive as an already
constructed duck-typed adapter (``reset`` / ``ingest`` / ``retrieval`` /
``usage`` / ``readiness`` / ``maintenance`` / ``env``) plus scratch paths owned
by the caller. Corpus truth, rubrics, and support manifests never cross the
adapter boundary — only public observation payloads do.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import CORPUS_VERSION
from . import harness
from .freeze import digest
from .grade import Grade, GradedSlot
from .life import Life
from .report import summarize
from .rubric import Checkpoint, Verdict
from .runner import exposed_observations, grade_answer

#: Channels that are live user turns, not ingested evidence (§31). ``agent`` is
#: the historical alias of ``hermes`` (see pacbench.observation).
USER_TURN_CHANNELS = ("hermes", "agent")


def split_collection(payloads: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split exposed public payloads into (evidence, user_turns), order kept.

    Pure function (offline-testable): evidence goes to ``adapter.ingest``,
    user turns go to ``harness.run_turn``. Nothing here answers or grades.
    """
    evidence = [p for p in payloads if p.get("channel") not in USER_TURN_CHANNELS]
    turns = [p for p in payloads if p.get("channel") in USER_TURN_CHANNELS]
    return evidence, turns


def _batched_by_collection(evidence: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group evidence by collection instant, earliest first (import batches)."""
    batches: dict[str, list[dict]] = {}
    for obs in evidence:
        batches.setdefault(obs["collected_at"], []).append(obs)
    return sorted(batches.items(), key=lambda kv: kv[0])


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(harness.redact(obj), indent=2, sort_keys=True) + "\n")


def _record_of(result: Any) -> dict:
    """JSON-serializable turn record with FULL diagnostics (M0, §§41/42).

    ``stderr_full`` + ``stdout_tail`` are always persisted so a failed turn
    keeps its root cause; ``stderr_tail`` stays for terse CLI output.
    """
    return {"answer": result.answer, "tool_calls": result.tool_calls,
            "tool_results": result.tool_results,
            "session_id": result.session_id,
            "policy_blocks": result.policy_blocks,
            "latency_s": result.latency_s, "returncode": result.returncode,
            "stderr_tail": result.stderr_tail,
            "stderr_full": result.stderr_full,
            "stdout_tail": result.stdout_tail,
            "raw_events": result.raw_events}


def _failure_error(result: Any) -> str:
    """Full-cause error string for a nonzero turn (no truncation — M0)."""
    cause = (result.stderr_full.strip() or result.stdout_tail.strip()
             or "(no output)")
    return f"exit {result.returncode}: {cause}"


def run_checkpoint_live(*, life: Life, checkpoint: Checkpoint, adapter: Any,
                        hermes_home: Path, run_dir: Path,
                        model: str = "grok-4.6",
                        model_provider: str = "xai-oauth",
                        timeout_s: float = 600.0,
                        auth_src: Path | None = None,
                        with_maintenance: bool = False,
                        namespace: str | None = None) -> dict:
    """Execute one checkpoint through Hermes + provider (§32) and grade it.

    Returns a JSON-serializable summary (also persisted under ``run_dir``).
    Raises ``RuntimeError`` on any setup/Hermes defect — a defect is reported,
    never converted into a passing grade (§42).

    Driver auth is PACBench .env only (§45.19): ``auth_src`` is a fail-closed
    legacy parameter and must stay None; any value raises.
    """
    if auth_src is not None:
        raise RuntimeError(
            "live-profile auth copy disabled (§45.19): driver credential comes "
            "from PACBench .env only via pacbench.creds.load_driver")
    run_dir.mkdir(parents=True, exist_ok=True)
    ns = namespace or (
        f"{life.id}-{checkpoint.id.replace('/', '-')}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    seam = adapter.retrieval()
    provider_name = seam.get("provider", getattr(adapter, "name", "unknown"))
    allowlist = ",".join(seam.get("tool_allowlist", ()))
    plugin_src = seam.get("plugin_src")
    plugin_path = Path(plugin_src) if seam.get("plugin_available") and plugin_src else None
    extra_env = dict(seam.get("env", {}) or {})
    if hasattr(adapter, "env"):
        try:
            extra_env.update(adapter.env())
        except Exception:
            pass

    adapter.reset(ns)
    home_record = harness.provision_home(
        hermes_home, provider_plugin_src=plugin_path,
        provider_name=provider_name, model=model,
        model_provider=model_provider, live_home=auth_src)

    exposed = exposed_observations(life, checkpoint.at)
    payloads = [o.public() for o in exposed]
    evidence, user_turns = split_collection(payloads)

    import_batches: list[dict] = []
    for collected_at, batch in _batched_by_collection(evidence):
        usage = adapter.ingest(batch, collected_at)
        import_batches.append({
            "collected_at": collected_at,
            "observations": [o["source_id"] for o in batch],
            "usage": usage.__dict__ if hasattr(usage, "__dict__") else str(usage),
        })

    turns: list[dict] = []
    for obs in user_turns:
        # §31: natural user message through real isolated Hermes (§28 runtime,
        # §30 fictional clock). The provider persists it via its own hook.
        result = harness.run_turn(
            obs["text"], at=obs["collected_at"], home=hermes_home,
            allowlist=allowlist, extra_env=extra_env, timeout_s=timeout_s)
        turns.append({"kind": "user-turn-replay", "source_id": obs["source_id"],
                      "at": obs["collected_at"], "prompt": obs["text"],
                      **_record_of(result)})
        if result.returncode != 0:
            raise RuntimeError(
                f"user-turn replay {obs['source_id']} failed "
                f"{_failure_error(result)}")

    maintenance_record: dict | None = None
    if with_maintenance:
        usage = adapter.maintenance(checkpoint.at)
        maintenance_record = {
            "at": checkpoint.at,
            "usage": usage.__dict__ if hasattr(usage, "__dict__") else str(usage),
            "readiness": adapter.readiness().__dict__,
        }

    # §32: the scored question goes through Hermes, not grade_answer(prewritten).
    scored = harness.run_turn(
        checkpoint.public_prompt(), at=checkpoint.at, home=hermes_home,
        allowlist=allowlist, extra_env=extra_env, timeout_s=timeout_s)
    turns.append({"kind": "checkpoint", "checkpoint": checkpoint.id,
                  "at": checkpoint.at, "prompt": checkpoint.public_prompt(),
                  **_record_of(scored)})
    if scored.returncode != 0:
        raise RuntimeError(
            f"checkpoint turn failed (exit {scored.returncode}): "
            f"{_failure_error(scored)}")

    grade = grade_answer(checkpoint, scored.answer)

    manifest = harness.run_manifest({
        "run": "pacbench-w4-live",
        "namespace": ns,
        "started_at": started,
        "corpus_version": CORPUS_VERSION,
        "corpus_hash": digest([life]),
        "life": life.id,
        "checkpoint": checkpoint.id,
        "checkpoint_at": checkpoint.at,
        "provider": provider_name,
        "driver_model": model,
        "grader": "deterministic (no judge)",
        "provision": home_record,
        "import_batches": import_batches,
        "maintenance": maintenance_record,
        "adapter_usage": (adapter.usage().__dict__
                          if hasattr(adapter, "usage") else {}),
    })

    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "observations.json", {"exposed": payloads})
    _write_json(run_dir / "turns.json", {"turns": turns})
    _write_json(run_dir / "grade.json", grade.to_dict())
    _write_json(run_dir / "retrieval.json", seam)

    return {
        "namespace": ns,
        "life": life.id,
        "checkpoint": checkpoint.id,
        "answer": scored.answer,
        "grade": grade.to_dict(),
        "user_turns_replayed": len(user_turns),
        "evidence_ingested": len(evidence),
        "tool_calls": len(scored.tool_calls),
        "latency_s": scored.latency_s,
        "artifacts": str(run_dir),
    }


def run_smoke(*, hermes_home: Path, run_dir: Path,
              model: str = "grok-4.6", model_provider: str = "xai-oauth",
              timeout_s: float = 600.0,
              at: str = "2026-09-10T12:00:00",
              auth_src: Path | None = None) -> dict:
    """One bare turn through the isolated clone (wiring proof, no provider).

    Driver auth is PACBench .env only (§45.19): ``auth_src`` is fail-closed.
    """
    if auth_src is not None:
        raise RuntimeError(
            "live-profile auth copy disabled (§45.19): driver credential comes "
            "from PACBench .env only via pacbench.creds.load_driver")
    run_dir.mkdir(parents=True, exist_ok=True)
    home_record = harness.provision_home(
        hermes_home, provider_plugin_src=None,
        provider_name="none", model=model, model_provider=model_provider,
        live_home=auth_src)
    result = harness.run_turn(
        "Reply with exactly: SMOKE OK", at=at, home=hermes_home,
        allowlist="", timeout_s=timeout_s)
    manifest = harness.run_manifest({
        "run": "pacbench-w4-smoke",
        "at": at,
        "driver_model": model,
        "provision": home_record,
    })
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "turn.json", {
        "prompt": result.prompt, "at": result.at, **_record_of(result)})
    if result.returncode != 0:
        raise RuntimeError(
            f"smoke turn failed (exit {result.returncode}): "
            f"{_failure_error(result)}")
    return {"answer": result.answer, "latency_s": result.latency_s,
            "raw_events": result.raw_events, "artifacts": str(run_dir)}


# ---------------------------------------------------------------------------
# W5: full-life pilot
# ---------------------------------------------------------------------------

def preflight(life: Life, hermes_home: Path, memcal_home: Path,
              adapter: Any) -> list[str]:
    """§42 validity gates checked before any scored turn. Non-empty = do not run.

    Verifies scratch homes (never live profiles), the isolated Hermes binary,
    the pinned clone, provider plugin availability, and corpus identity.
    """
    problems: list[str] = []
    try:
        harness._check_paths(hermes_home)
    except RuntimeError as exc:
        problems.append(f"hermes_home: {exc}")
    for label, home in (("hermes_home", hermes_home), ("memcal_home", memcal_home)):
        text = str(home)
        for live in (".hermes/auth.json", ".hermes/.env", ".memcal"):
            if live in text and "runs" not in text and "artifacts" not in text:
                problems.append(f"{label} looks like a live profile: {home}")
    if not harness.ISOLATED_BIN.exists():
        problems.append(f"isolated hermes binary missing: {harness.ISOLATED_BIN}")
    if not (harness.REPO / "vendor" / "HERMES_PIN").exists():
        problems.append("HERMES_PIN missing")
    try:
        seam = adapter.retrieval()
    except Exception as exc:
        problems.append(f"adapter.retrieval failed: {exc}")
        seam = {}
    if seam and not seam.get("plugin_available"):
        problems.append("provider plugin unavailable for isolated home install")
    if not life.checkpoints:
        problems.append(f"life {life.id} has no checkpoints")
    if not life.observations:
        problems.append(f"life {life.id} has no observations")
    return problems


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def classify(checkpoint: Checkpoint, grade: Grade | None, *,
             turn_ok: bool, ingested_ids: set[str], tool_names: list[str],
             result_blob: str, policy_blocks: int) -> dict:
    """Classify a checkpoint outcome per §34 — only when the trace supports it.

    Categories follow the contract numbering (1–7); ``unknown`` means the trace
    does not support a specific cause; ``null`` means no failure to classify.
    """
    if grade is not None and grade.misrepresented == 0 and grade.missed == 0:
        return {"category": None, "reason": "all required slots correct"}
    if not turn_ok:
        return {"category": 7,
                "reason": "Hermes turn failed (nonzero exit); infrastructure, not memory"}
    required_ids: set[str] = set()
    for slot in (*checkpoint.slots, *checkpoint.supported):
        if slot.required:
            required_ids.update(slot.supported_by)
    missing = sorted(required_ids - ingested_ids)
    if missing:
        return {"category": 1,
                "reason": f"required evidence never ingested: {missing}"}
    if policy_blocks and not tool_names:
        return {"category": 7,
                "reason": "memory tools blocked by the dispatch boundary"}
    if not tool_names:
        return {"category": 4,
                "reason": "no memory tool called at question time despite available tools"}
    blob = _norm(result_blob)
    wanted: list[str] = []
    for slot in (*checkpoint.slots, *checkpoint.supported):
        if slot.required:
            wanted.append(slot.expected)
            wanted.extend(slot.alternatives)
    if wanted and not any(_norm(w).strip() and _norm(w).strip() in blob for w in wanted):
        return {"category": 3,
                "reason": "evidence ingested but no tool result surfaced the expected value"}
    if wanted and any(_norm(w).strip() and _norm(w).strip() in blob for w in wanted):
        return {"category": 5,
                "reason": "tool results contained the expected value but the answer missed it"}
    return {"category": "unknown",
            "reason": "failure present but trace does not isolate a single cause"}


def _failed_grade(checkpoint: Checkpoint, reason: str) -> Grade:
    """All-MISSED grade for a checkpoint whose turn never produced an answer.

    Keeps failed checkpoints in the denominator (report.py contract) instead of
    dropping them or inventing a pass.
    """
    scored = checkpoint.slots or checkpoint.supported
    grade = Grade(checkpoint=checkpoint.id)
    for slot in scored:
        grade.slots.append(GradedSlot(slot.id, Verdict.MISSED, reason))
        if slot.required:
            grade.missed += 1
    grade.precision, grade.recall, grade.f1 = 0.0, 0.0, 0.0
    if checkpoint.mode == "complete-plan":
        grade.complete_plan = False
    return grade


def run_life(*, life: Life, provider: str, make_adapter: Callable[[Path], Any],
             life_dir: Path, model: str, model_provider: str,
             driver_configured_model: str,
             timeout_s: float = 900.0) -> dict:
    """Run one full 7-day life episode: ingest → turns → checkpoints → maintenance.

    A single chronological episode per (life, provider): evidence is ingested at
    declared collection times, hermes-channel observations run as continuing
    Hermes turns in per-thread sessions, each checkpoint is asked in the
    continuing episode session and graded, and one nightly maintenance pass runs
    per simulated date with verified readiness. Returns the life record
    (``state`` is ``valid``, ``incomplete``, or ``invalid``).
    """
    life_dir.mkdir(parents=True, exist_ok=True)
    hermes_home = life_dir / "hermes-home"
    adapter = make_adapter(life_dir / "memcal-home")
    namespace = f"{life.id}-rep0"

    problems = preflight(life, hermes_home, life_dir / "memcal-home", adapter)
    if problems:
        record = {"life": life.id, "state": "invalid",
                  "reasons": problems, "artifacts": str(life_dir)}
        _write_json(life_dir / "validity.json", record)
        return record

    seam = adapter.retrieval()
    allowlist = ",".join(seam.get("tool_allowlist", ()))
    plugin_src = seam.get("plugin_src")
    plugin_path = Path(plugin_src) if seam.get("plugin_available") and plugin_src else None
    extra_env = dict(seam.get("env", {}) or {})

    adapter.reset(namespace)
    home_record = harness.provision_home(
        hermes_home, provider_plugin_src=plugin_path,
        provider_name=provider, model=model,
        model_provider=model_provider, live_home=None)

    obs = sorted(life.observations, key=lambda o: (o.collected_at, o.source_id))
    checkpoints = sorted(life.checkpoints, key=lambda c: c.at)
    payloads = [o.public() for o in obs]

    turns: list[dict] = []
    grades: list[dict] = []
    grade_objs: list[Grade] = []
    classifications: list[dict] = []
    import_batches: list[dict] = []
    maintenance_events: list[dict] = []
    session_schedule: list[dict] = []
    sessions: dict[str, str] = {}
    primary_session: str | None = None
    ingested_ids: set[str] = set()
    incomplete_reasons: list[str] = []
    obs_idx = 0

    def session_for(key: str) -> tuple[str | None, bool]:
        if key in sessions:
            return sessions[key], True
        return primary_session, primary_session is not None

    def do_turn(prompt: str, at: str, session_key: str | None) -> dict:
        nonlocal primary_session
        resume, resumed = session_for(session_key or "__episode__")
        try:
            result = harness.run_turn(
                prompt, at=at, home=hermes_home, allowlist=allowlist,
                extra_env=extra_env, timeout_s=timeout_s, session_id=resume)
        except Exception as exc:
            # Full exception text (timeouts carry the command + elapsed time).
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if result.returncode != 0:
            return {"ok": False, "error": _failure_error(result),
                    "record": result}
        sid = result.session_id or resume
        if sid:
            if session_key:
                sessions[session_key] = sid
            if primary_session is None:
                primary_session = sid
        return {"ok": True, "record": result, "resumed": resumed,
                "session_id": sid}

    def ingest_until(until: str) -> None:
        nonlocal obs_idx
        pending = [p for p in payloads[obs_idx:] if p["collected_at"] <= until]
        evidence = [p for p in pending if p.get("channel") not in USER_TURN_CHANNELS]
        for collected_at, batch in _batched_by_collection(evidence):
            usage = adapter.ingest(batch, collected_at)
            ingested_ids.update(o["source_id"] for o in batch)
            import_batches.append({
                "collected_at": collected_at,
                "observations": [o["source_id"] for o in batch],
                "usage": usage.__dict__ if hasattr(usage, "__dict__") else str(usage),
            })
        obs_idx += len(pending)
        return pending

    def replay_turns(pending: list[dict]) -> None:
        for p in pending:
            if p.get("channel") not in USER_TURN_CHANNELS:
                continue
            out = do_turn(p["text"], p["collected_at"], session_key=p["thread"])
            if not out["ok"]:
                if p["thread"] in sessions:
                    del sessions[p["thread"]]
                incomplete_reasons.append(
                    f"user-turn {p['source_id']} failed: {out['error']}")
                turns.append({"kind": "user-turn-replay", "source_id": p["source_id"],
                              "at": p["collected_at"], "prompt": p["text"],
                              "error": out["error"]})
                continue
            r = out["record"]
            session_schedule.append({"event": p["source_id"], "at": p["collected_at"],
                                     "session_id": out["session_id"],
                                     "resumed": out["resumed"]})
            turns.append({"kind": "user-turn-replay", "source_id": p["source_id"],
                          "at": p["collected_at"], "prompt": p["text"],
                          "answer": r.answer, "tool_calls": r.tool_calls,
                          "tool_results": r.tool_results,
                          "session_id": r.session_id, "policy_blocks": r.policy_blocks,
                          "latency_s": r.latency_s, "returncode": r.returncode,
                          "stderr_tail": r.stderr_tail, "raw_events": r.raw_events})

    def maintain(day: str) -> None:
        stamp = f"{day}T23:59:00"
        usage = adapter.maintenance(stamp)
        readiness = adapter.readiness()
        maintenance_events.append({
            "at": stamp,
            "usage": usage.__dict__ if hasattr(usage, "__dict__") else str(usage),
            "readiness": readiness.__dict__,
        })
        state = adapter._maintenance.get("state") if hasattr(adapter, "_maintenance") else "unknown"
        if state != "complete" or not readiness.ready:
            incomplete_reasons.append(
                f"maintenance {stamp} not complete (state={state}, ready={readiness.ready})")

    dates = sorted({p["collected_at"][:10] for p in payloads}
                   | {c.at[:10] for c in checkpoints})
    maintained: set[str] = set()

    def maintain_completed_days(current_time: str) -> None:
        for day in dates:
            if day not in maintained and f"{day}T23:59:59" < current_time:
                maintain(day)
                maintained.add(day)

    for cp in checkpoints:
        pending = ingest_until(cp.at)
        replay_turns(pending)
        maintain_completed_days(cp.at)
        out = do_turn(cp.public_prompt(), cp.at, session_key=None)
        if not out["ok"]:
            incomplete_reasons.append(f"checkpoint {cp.id} turn failed: {out['error']}")
            failed = _failed_grade(cp, out["error"])
            grade_objs.append(failed)
            grades.append({**failed.to_dict(), "answer": None, "error": out["error"]})
            classifications.append({"checkpoint": cp.id, **classify(
                cp, None, turn_ok=False, ingested_ids=set(ingested_ids),
                tool_names=[], result_blob="", policy_blocks=0)})
            turns.append({"kind": "checkpoint", "checkpoint": cp.id,
                          "at": cp.at, "prompt": cp.public_prompt(),
                          "error": out["error"]})
            continue
        r = out["record"]
        session_schedule.append({"event": cp.id, "at": cp.at,
                                 "session_id": out["session_id"],
                                 "resumed": out["resumed"]})
        names = [t.get("function_name") or t.get("name") or t.get("tool") or ""
                 for t in r.tool_calls]
        blob = " ".join(x.get("text", "") for x in r.tool_results)
        grade = grade_answer(cp, r.answer)
        grade_objs.append(grade)
        grades.append({**grade.to_dict(), "answer": r.answer})
        classifications.append({"checkpoint": cp.id, **classify(
            cp, grade, turn_ok=True, ingested_ids=set(ingested_ids),
            tool_names=[n for n in names if n], result_blob=blob,
            policy_blocks=r.policy_blocks)})
        turns.append({"kind": "checkpoint", "checkpoint": cp.id,
                      "at": cp.at, "prompt": cp.public_prompt(),
                      "answer": r.answer, "tool_calls": r.tool_calls,
                      "tool_results": r.tool_results, "session_id": r.session_id,
                      "policy_blocks": r.policy_blocks, "latency_s": r.latency_s,
                      "returncode": r.returncode, "stderr_tail": r.stderr_tail,
                      "raw_events": r.raw_events})

    # Complete the week: deliver observations collected after the last checkpoint.
    if obs_idx < len(payloads):
        pending = ingest_until("9999-12-31T23:59:59")
        replay_turns(pending)
    for day in dates:
        if day not in maintained:
            maintain(day)
            maintained.add(day)

    summary = summarize(grade_objs, checkpoints)
    state = "valid" if not incomplete_reasons else "incomplete"
    manifest = harness.run_manifest({
        "run": "pacbench-w5-pilot-life",
        "namespace": namespace,
        "corpus_version": CORPUS_VERSION,
        "corpus_hash": digest([life]),
        "life": life.id,
        "provider": provider,
        "driver_model": model,
        "driver_configured_model": driver_configured_model,
        "grader": "deterministic (no judge)",
        "provision": home_record,
        "import_batches": import_batches,
        "adapter_usage": (adapter.usage().__dict__
                          if hasattr(adapter, "usage") else {}),
        "session_schedule": session_schedule,
    })
    _write_json(life_dir / "manifest.json", manifest)
    _write_json(life_dir / "observations.json",
                {"exposed": payloads, "ingested_ids": sorted(ingested_ids)})
    _write_json(life_dir / "turns.json", {"turns": turns})
    _write_json(life_dir / "grades.json",
                {"grades": grades, "classifications": classifications,
                 "summary": summary.to_dict()})
    _write_json(life_dir / "maintenance.json", {"events": maintenance_events})
    validity = {"life": life.id, "state": state,
                "reasons": incomplete_reasons, "artifacts": str(life_dir)}
    _write_json(life_dir / "validity.json", validity)
    return {**validity, "summary": summary.to_dict(),
            "checkpoints": len(checkpoints),
            "turns": len(turns), "maintenance_runs": len(maintenance_events)}


def run_pilot(*, lives: list[Life], provider: str,
              make_adapter: Callable[[Path], Any], pilot_dir: Path,
              model: str, model_provider: str, driver_configured_model: str,
              timeout_s: float = 900.0) -> dict:
    """Run the full development pilot: every life, every checkpoint, one episode each.

    No retry-until-pass: each life runs once; every failure is preserved in its
    artifacts and reflected in the pilot state. Returns the pilot record and
    writes ``report.json`` + ``REPORT.md`` under ``pilot_dir``.
    """
    pilot_dir.mkdir(parents=True, exist_ok=True)
    life_records: list[dict] = []
    all_grades: list[Grade] = []
    all_checkpoints: list[Checkpoint] = []
    for life in lives:
        record = run_life(
            life=life, provider=provider, make_adapter=make_adapter,
            life_dir=pilot_dir / life.id, model=model,
            model_provider=model_provider,
            driver_configured_model=driver_configured_model,
            timeout_s=timeout_s)
        life_records.append(record)
        life_dir = pilot_dir / life.id
        grades_path = life_dir / "grades.json"
        if grades_path.exists():
            saved = json.loads(grades_path.read_text())["grades"]
            for entry, cp in zip(saved, sorted(life.checkpoints, key=lambda c: c.at)):
                grade = Grade(checkpoint=cp.id)
                for slot in entry["slots"]:
                    grade.slots.append(GradedSlot(
                        slot["id"], slot["verdict"], slot["reason"]))
                grade.correct = entry["correct"]
                grade.missed = entry["missed"]
                grade.misrepresented = entry["misrepresented"]
                grade.precision = entry["precision"]
                grade.recall = entry["recall"]
                grade.f1 = entry["f1"]
                grade.complete_plan = entry.get("complete_plan")
                grade.abstention = entry.get("abstention")
                grade.options_found = entry.get("options_found")
                grade.options_total = entry.get("options_total")
                grade.suggested_forbidden = entry.get("suggested_forbidden", 0)
                all_grades.append(grade)
                all_checkpoints.append(cp)
    aggregate = summarize(all_grades, all_checkpoints).to_dict()
    states = [r["state"] for r in life_records]
    pilot_state = ("valid" if states and all(s == "valid" for s in states)
                   else "incomplete" if "incomplete" in states else "invalid")
    tool_calls_total = 0
    latency_total = 0.0
    turn_total = 0
    for life in lives:
        turns_path = pilot_dir / life.id / "turns.json"
        if turns_path.exists():
            for turn in json.loads(turns_path.read_text())["turns"]:
                if "error" in turn:
                    continue
                turn_total += 1
                tool_calls_total += len(turn.get("tool_calls", []))
                latency_total += turn.get("latency_s", 0.0) or 0.0
    summary = {
        "pilot": "pacbench-w5-3-life-memcal",
        "pilot_state": pilot_state,
        "provider": provider,
        "driver_model": model,
        "driver_configured_model": driver_configured_model,
        "corpus_version": CORPUS_VERSION,
        "lives": life_records,
        "aggregate": aggregate,
        "efficiency": {
            "hermes_turns": turn_total,
            "memory_tool_calls": tool_calls_total,
            "answer_latency_s_total": round(latency_total, 1),
        },
        "comparative_claim_valid": False,
        "comparative_gap": ("§45.5 unmet: no external provider has received the same "
                            "observations yet; single-provider pilot only, no comparison valid."),
    }
    _write_json(pilot_dir / "report.json", summary)
    (pilot_dir / "REPORT.md").write_text(render_report(summary, pilot_dir))
    return {"summary": summary, "lives": life_records}


def render_report(summary: dict, pilot_dir: Path) -> str:
    """Human-readable aggregate report (§§35–37, §41)."""
    lines = [
        "# PACBench W5 pilot report — 3 development lives, Hermes + memcal",
        "",
        f"pilot_state: **{summary['pilot_state']}**",
        f"provider: {summary['provider']}",
        f"driver_model: {summary['driver_model']} "
        f"(configured: {summary['driver_configured_model']})",
        f"corpus_version: {summary['corpus_version']}",
        f"artifacts: {pilot_dir}",
        "",
        "## Lives",
    ]
    for record in summary["lives"]:
        lines.append(f"- {record['life']}: {record['state']} "
                     f"({record.get('checkpoints', '?')} checkpoints)")
        for reason in record.get("reasons", []):
            lines.append(f"  - {reason}")
    agg = summary["aggregate"]
    lines += [
        "",
        "## Aggregate metrics",
        f"checkpoints: {agg['checkpoints']}, slots: {agg['slots']}, "
        f"correct: {agg['correct']}, missed: {agg['missed']}, "
        f"misrepresented: {agg['misrepresented']}",
        f"precision: {agg['precision']:.3f}, recall: {agg['recall']:.3f}, "
        f"F1: {agg['f1']:.3f}",
        f"current-state accuracy: {agg['current_accuracy']}, "
        f"historical accuracy: {agg['historical_accuracy']}",
        f"complete-plan accuracy: {agg['complete_plan_accuracy']}, "
        f"abstention accuracy: {agg['abstention_accuracy']}",
        f"opportunity recall: {agg['opportunity_recall']}, "
        f"forbidden suggested: {agg['forbidden_suggested']}",
        "",
        "## Difficulty tags",
    ]
    for tag, stats in agg["by_difficulty_tag"].items():
        lines.append(f"- {tag}: {stats['correct']}/{stats['total']} "
                     f"({stats['accuracy'] if stats['accuracy'] is None else round(stats['accuracy'], 3)})")
    eff = summary["efficiency"]
    lines += [
        "",
        "## Question-time work (§35)",
        f"hermes turns: {eff['hermes_turns']}, memory tool calls: "
        f"{eff['memory_tool_calls']}, answer latency total: "
        f"{eff['answer_latency_s_total']}s",
        "",
        "## Validity",
        f"comparative_claim_valid: {summary['comparative_claim_valid']}",
        f"gap: {summary['comparative_gap']}",
        "",
        "Inspect one success and one failure end to end from the per-life "
        "artifacts: manifest.json → observations.json → turns.json "
        "(tool calls + results) → grades.json (per-atom grades + §34 "
        "classifications) → maintenance.json.",
    ]
    return "\n".join(lines) + "\n"
