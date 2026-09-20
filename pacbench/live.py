"""Live checkpoint execution: collection + isolated Hermes turns + artifacts.

W4 runtime slice (contract §§28/30/31/32/41/42). Generic orchestration only:
evidence reaches the provider through the adapter's ``ingest`` at declared
collection times, ``hermes``/``agent``-channel observations are replayed as real
isolated Hermes turns (never adapter DB writes, §31), and the checkpoint
question itself is asked through isolated Hermes and graded afterwards (§32).

This module never imports a provider. Provider specifics arrive as an already
constructed duck-typed adapter (``reset`` / ``ingest`` / ``retrieval`` /
``usage`` / ``readiness`` / ``maintenance`` / ``env``) plus scratch paths owned
by the caller. Corpus truth, rubrics, and support manifests never cross the
adapter boundary — only :func:`runner.public_exposure`-equivalent public
payloads do.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import CORPUS_VERSION
from . import harness
from .freeze import digest
from .life import Life
from .rubric import Checkpoint
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
    """
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
                      "answer": result.answer, "tool_calls": result.tool_calls,
                      "policy_blocks": result.policy_blocks,
                      "latency_s": result.latency_s,
                      "returncode": result.returncode,
                      "stderr_tail": result.stderr_tail,
                      "raw_events": result.raw_events})
        if result.returncode != 0:
            raise RuntimeError(
                f"user-turn replay {obs['source_id']} failed "
                f"(exit {result.returncode}): {result.stderr_tail[-500:]}")

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
                  "answer": scored.answer, "tool_calls": scored.tool_calls,
                  "policy_blocks": scored.policy_blocks,
                  "latency_s": scored.latency_s, "returncode": scored.returncode,
                  "stderr_tail": scored.stderr_tail,
                  "raw_events": scored.raw_events})
    if scored.returncode != 0:
        raise RuntimeError(
            f"checkpoint turn failed (exit {scored.returncode}): "
            f"{scored.stderr_tail[-500:]}")

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
    """One bare turn through the isolated clone (wiring proof, no provider)."""
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
        "prompt": result.prompt, "at": result.at, "answer": result.answer,
        "tool_calls": result.tool_calls, "policy_blocks": result.policy_blocks,
        "latency_s": result.latency_s, "returncode": result.returncode,
        "stderr_tail": result.stderr_tail, "raw_events": result.raw_events,
    })
    if result.returncode != 0:
        raise RuntimeError(
            f"smoke turn failed (exit {result.returncode}): "
            f"{result.stderr_tail[-500:]}")
    return {"answer": result.answer, "latency_s": result.latency_s,
            "raw_events": result.raw_events, "artifacts": str(run_dir)}
