"""Isolated-run harness: scratch Hermes homes, patched-clone driving, artifacts.

This is the generic side of a live benchmark run. It provisions scratch
``HERMES_HOME`` directories (never the user's live profile), drives turns
through the PACBench-owned Hermes clone with the benchmark patches applied, and
writes redacted artifacts. Provider specifics (which plugin, which store) arrive
as parameters — this module never imports a provider.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISOLATED_TREE = REPO / "vendor" / "hermes-isolated"
ISOLATED_BIN = REPO / "vendor" / "venv-hermes-isolated" / "bin" / "hermes"
PATCH_RECORD = REPO / "patches" / "hermes-isolated.diff"


@dataclasses.dataclass
class TurnResult:
    prompt: str
    at: str
    answer: str
    tool_calls: list[dict]
    tool_results: list[dict]
    policy_blocks: int
    latency_s: float
    returncode: int
    stderr_tail: str
    raw_events: int
    session_id: str | None = None
    # M0 full diagnostics (§§41/42): complete stderr (never truncated — the
    # root cause lives here) plus the last non-empty stdout lines (quiet-text
    # answers and harness warnings print outside stream-json).
    stderr_full: str = ""
    stdout_tail: str = ""
    # §35 usage accounting: session-cumulative token counters from the clone's
    # terminal stream-json `result` event (`tokens.input/output`). Cumulative,
    # NOT per-turn: continuing sessions (`--resume`) grow these across turns,
    # so per-turn spend is the delta (see pacbench.live). None means the clone
    # reported no token stats for this turn — unknown stays unknown, never zero.
    session_input_tokens: int | None = None
    session_output_tokens: int | None = None


def _check_paths(home: Path) -> None:
    """Refuse to run against a live profile. Fail closed, loudly."""
    text = str(home)
    for forbidden in ("/.hermes", ".hermes-agent"):
        if forbidden in text and "hermes-homes" not in text and "pacbench" not in text.lower():
            raise RuntimeError(f"refusing live-looking HERMES_HOME: {home}")
    home_str = str(home.resolve()) if home.exists() else text
    if home_str.rstrip("/") in (str(Path.home() / ".hermes"),):
        raise RuntimeError(f"refusing live HERMES_HOME: {home}")


def provision_home(home: Path, *, provider_plugin_src: Path | None,
                   provider_name: str, model: str = "grok-4.6",
                   model_provider: str = "xai-oauth",
                   live_home: Path | None = None,
                   mcp_servers: list[dict] | None = None) -> dict:
    """Build a scratch HERMES_HOME: minimal config, provider plugin, no live auth.

    - ``provider_plugin_src`` is copied (never symlinked) to
      ``<home>/plugins/<provider_name>/``.
    - ``mcp_servers`` (provider-blind): [{name, url?, command?, args?,
      env?}] written as enabled entries under ``mcp_servers:`` in the
      isolated config — how MCP-native providers (Hindsight) expose
      memory tools to scored turns. No secrets: env values here land in
      the isolated config file, so callers pass only non-secret env
      (ports, bank paths, model names); credentials stay in process env.
    - Driver auth comes from PACBench-owned ``.env`` (``MODEL`` + ``META`` via
      ``pacbench.creds.load_driver``, exported as ``META_API_KEY``) and flows
      through the process environment into ``harness.run_turn``. This function
      never copies live-profile ``auth.json``: ``live_home`` is a fail-closed
      legacy parameter — any non-None value raises (§45.19, §42).
    - Built-in memory stays off; every toolset except ``memory`` is disabled in
      config (the dispatch patch in the clone enforces the rest).
    Returns a record of what was provisioned (no secrets).
    """
    if live_home is not None:
        raise RuntimeError(
            "live-profile auth copy disabled (§45.19): driver credential comes "
            "from PACBench .env only via pacbench.creds.load_driver")
    _check_paths(home)
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    (home / "plugins").mkdir()

    record: dict = {"home": str(home), "provider": provider_name, "model": model,
                    "model_provider": model_provider,
                    "plugin_copied": False, "auth_copied": False}
    if provider_plugin_src is not None:
        dest = home / "plugins" / provider_name
        shutil.copytree(provider_plugin_src, dest)
        record["plugin_copied"] = True
        record["plugin_files"] = sum(1 for _ in dest.rglob("*") if _.is_file())

    if live_home is not None:
        src_auth = Path(live_home) / "auth.json"
        if src_auth.exists():
            dest_auth = home / "auth.json"
            shutil.copyfile(src_auth, dest_auth)
            os.chmod(dest_auth, 0o600)
            record["auth_copied"] = True

    # Canonical base URLs per provider: some runtime env-key rungs only send a
    # key to its authoritative host, so an empty base_url would leave the key
    # unsent (401). Same table every provider resolves against.
    base_url = {"xai-oauth": "https://api.x.ai/v1",
                "openrouter": "https://openrouter.ai/api/v1",
                "meta-ai": "https://api.meta.ai/v1"}.get(model_provider, "")
    config = {
        "model": {"default": model, "provider": model_provider,
                  "base_url": base_url},
        "memory": {"memory_enabled": False, "user_profile_enabled": False,
                   "provider": provider_name},
        "agent": {"disabled_toolsets": [
            "browser", "clarify", "code_execution", "coding", "computer_use",
            "connections", "context_engine", "cronjob", "debugging", "delegation",
            "desktop_ui", "discord", "discord_admin", "feishu_doc", "feishu_drive",
            "file", "homeassistant", "image_gen", "kanban", "project", "safe",
            "search", "session_search", "skills", "spotify", "terminal", "todo",
            "tts", "video", "video_gen", "vision", "web", "x_search",
        ]},
    }
    if mcp_servers:
        entries = {}
        for spec in mcp_servers:
            name = spec["name"]
            entry: dict = {"enabled": True}
            if spec.get("url"):
                entry["url"] = spec["url"]
            if spec.get("command"):
                entry["command"] = spec["command"]
            if spec.get("args"):
                entry["args"] = list(spec["args"])
            if spec.get("env"):
                entry["env"] = dict(spec["env"])
            entries[name] = entry
        config["mcp_servers"] = entries
    _write_yaml(home / "config.yaml", config)
    record["config_written"] = True
    record["mcp_servers"] = sorted(
        s["name"] for s in (mcp_servers or []))
    return record


def _write_yaml(path: Path, data: dict) -> None:
    try:
        import yaml  # type: ignore
        path.write_text(yaml.safe_dump(data, default_flow_style=False))
    except ImportError:
        path.write_text(_to_yaml(data))


def _to_yaml(data: dict, indent: int = 0) -> str:
    lines = []
    pad = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                lines.append(f"{pad}  - {item}")
        else:
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines) + "\n"


def run_turn(prompt: str, *, at: str, home: Path, allowlist: str,
             extra_env: dict | None = None, timeout_s: float = 600.0,
             session_id: str | None = None) -> TurnResult:
    """Drive one noninteractive turn through the isolated clone and parse traces.

    Every turn carries its simulated now in chat context (not only in the
    PACBENCH_NOW tool-time shim): the model otherwise anchors "today" on
    the host date — demonstrated by the adversarial clock probe answering
    the host date under a fictional clock. The prefix is recorded in the
    turn artifact, never hidden.
    """
    _check_paths(home)
    prompt = (f"[Simulated now: {at}. Treat this as the current date and "
              f"time for everything in this turn.]\n\n{prompt}")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({
        "HERMES_HOME": str(home),
        "PACBENCH_NOW": at,
        "PACBENCH_TOOL_ALLOWLIST": allowlist,
        "PACBENCH_NO_BUILTIN_MEMORY": "1",
        "PACBENCH_NO_SESSION_SEARCH": "1",
        "TERM": "dumb",
    })
    if extra_env:
        env.update(extra_env)
    cmd = [str(ISOLATED_BIN), "chat", "-q", prompt, "-Q", "--format", "stream-json"]
    if session_id:
        cmd += ["--resume", session_id]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, env=env, cwd=str(REPO))
    except subprocess.TimeoutExpired as exc:
        # A timeout is a failed turn, not a missing one (§41: failures are
        # required artifacts). Synthesize the same diagnostic TurnResult a
        # nonzero exit would carry so no error path is record-less (P1: kill
        # error-only turns). Partial child output rides along when present.
        latency = time.monotonic() - started
        note = (f"Hermes turn timed out after {timeout_s}s "
                f"(cmd: {' '.join(str(c) for c in cmd)})")
        part_out = str(exc.stdout or "")
        part_err = str(exc.stderr or "")
        return TurnResult(
            prompt=prompt, at=at, answer="",
            tool_calls=[], tool_results=[], policy_blocks=0,
            latency_s=round(latency, 2), returncode=-1,
            stderr_tail=note,
            stderr_full=(note + (f"\n--- partial stderr ---\n{part_err}"
                                 if part_err else "")),
            stdout_tail="\n".join(
                [ln for ln in part_out.splitlines() if ln.strip()][-60:]),
            raw_events=0, session_id=None)
    latency = time.monotonic() - started
    return _parse_turn(prompt, at, proc, latency)


def _parse_turn(prompt: str, at: str, proc: subprocess.CompletedProcess,
                latency: float) -> TurnResult:
    answer_parts: list[str] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    blocks = 0
    events = 0
    text_deltas: list[str] = []
    result_text = ""
    session_id: str | None = None
    session_in: int | None = None
    session_out: int | None = None
    stdout_text = proc.stdout or ""
    stderr_text = proc.stderr or ""
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        if not session_id and isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        kind = event.get("type", "")
        if kind in ("tool_call", "tool_use", "function_call"):
            tool_calls.append(event)
        elif kind in ("tool_result", "tool_response"):
            text = json.dumps(event)
            if "PACBENCH-BLOCKED" in text:
                blocks += 1
            tool_results.append(_summarize_tool_result(event))
        elif kind == "text":
            # Streaming delta of the assistant message.
            delta = event.get("text") or ""
            if isinstance(delta, str) and delta:
                text_deltas.append(delta)
        elif kind == "result":
            # Final turn payload: full answer text (preferred over deltas).
            text = event.get("text") or ""
            if isinstance(text, str) and text:
                result_text = text
            # §35: session-cumulative token counters (see TurnResult). The
            # terminal result carries the session totals; per-turn spend is
            # the delta across turns in one session (pacbench.live). A missing
            # `tokens` mapping means unreported (None), never zero.
            tokens = event.get("tokens")
            if isinstance(tokens, dict):
                try:
                    if tokens.get("input") is not None:
                        session_in = int(tokens["input"])
                    if tokens.get("output") is not None:
                        session_out = int(tokens["output"])
                except (TypeError, ValueError):
                    pass
        elif kind in ("assistant", "message", "response", "final"):
            text = event.get("text") or event.get("content") or event.get("message") or ""
            if isinstance(text, list):
                text = " ".join(str(p.get("text", p)) if isinstance(p, dict) else str(p)
                                for p in text)
            if text:
                answer_parts.append(str(text))
    if result_text:
        answer_parts = [result_text]
    elif text_deltas and not answer_parts:
        answer_parts = ["".join(text_deltas)]
    if not answer_parts:
        # Fall back to the last non-JSON line (quiet text mode still prints it).
        # Skip harness warnings (e.g. the tirith notice), which are not answers.
        for line in reversed(stdout_text.splitlines()):
            stripped = line.strip()
            if not stripped or stripped.startswith("{"):
                continue
            if "tirith security scanner" in stripped:
                continue
            answer_parts.append(stripped)
            break
    stdout_tail = "\n".join(
        [ln for ln in stdout_text.splitlines() if ln.strip()][-60:])
    stderr_lines = stderr_text.splitlines()
    return TurnResult(prompt=prompt, at=at, answer="\n".join(answer_parts),
                      tool_calls=tool_calls, tool_results=tool_results,
                      policy_blocks=blocks,
                      latency_s=round(latency, 2), returncode=proc.returncode,
                      stderr_tail="\n".join(stderr_lines[-8:]),
                      stderr_full=stderr_text,
                      stdout_tail=stdout_tail,
                      session_input_tokens=session_in,
                      session_output_tokens=session_out,
                      raw_events=events, session_id=session_id)


def _summarize_tool_result(event: dict, limit: int = 2000) -> dict:
    """Compact a tool-result event for artifacts (name + truncated payload).

    Results can be large (archive dumps); the text is synthetic corpus evidence,
    truncated with an explicit flag so reviewers know the cap. Used for §34
    failure classification (did retrieved evidence reach Hermes?).
    """
    payload = event.get("result")
    if payload is None:
        payload = event.get("output")
    if payload is None:
        payload = event.get("text", "")
    text = payload if isinstance(payload, str) else json.dumps(payload)
    truncated = len(text) > limit
    return {
        "tool": event.get("tool") or event.get("name") or "",
        "success": event.get("success", event.get("error") is None),
        "text": text[:limit],
        "truncated": truncated,
    }


#: Usage-accounting keys that must survive artifact redaction. Token *counts*
#: (ints/None) are cost telemetry, never bearer secrets — but every one of
#: these names contains the substring "token", so the secret-substring rule
#: below would scrub them (it once turned a whole pilot's usage block into
#: "<redacted>"). Exact-match allowlist: a real credential never lives under
#: exactly these keys in PACBench artifacts (driver auth comes only from the
#: process environment, never from artifact structures).
_TOKEN_COUNT_KEYS = frozenset({
    "tokens",
    "input_tokens", "output_tokens",
    "prompt_tokens", "completion_tokens",
    "total_tokens", "cached_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
    "session_input_tokens", "session_output_tokens",
    "turn_input_tokens", "turn_output_tokens",
    "turns_with_tokens", "turns_total",
})


def redact(obj: object) -> object:
    """Strip anything that looks like a credential from an artifact structure."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _TOKEN_COUNT_KEYS:
                out[k] = redact(v)
            elif any(s in k.lower() for s in
                     ("token", "secret", "auth", "key", "bearer",
                      "credential", "password")):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 24 and obj[:4] in ("sk-", "xai", "ghu_"):
        return "<redacted>"
    return obj


def run_manifest(record: dict) -> dict:
    """Assemble the immutable run manifest (patches + versions + selections)."""
    manifest = {
        **record,
        "pacbench_repo": str(REPO),
        "hermes_tree": str(ISOLATED_TREE),
        "hermes_pin": _read_pin(),
        "hermes_patches_sha256": _sha(PATCH_RECORD),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return redact(manifest)


def _read_pin() -> str:
    pin = REPO / "vendor" / "HERMES_PIN"
    if pin.exists():
        for line in pin.read_text().splitlines():
            if line.startswith("HERMES_PIN="):
                return line.split("=", 1)[1].strip()
    return "unknown"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
