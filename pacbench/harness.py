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
    policy_blocks: int
    latency_s: float
    returncode: int
    stderr_tail: str
    raw_events: int


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
                   live_home: Path | None = None) -> dict:
    """Build a scratch HERMES_HOME: minimal config, copied auth, provider plugin.

    - ``provider_plugin_src`` is copied (never symlinked) to
      ``<home>/plugins/<provider_name>/``.
    - ``auth.json`` is copied from the live home with 0600 permissions so driver
      model calls authenticate; it never enters artifacts (see ``redact``).
    - Built-in memory stays off; every toolset except ``memory`` is disabled in
      config (the dispatch patch in the clone enforces the rest).
    Returns a record of what was provisioned (no secrets).
    """
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

    config = {
        "model": {"default": model, "provider": model_provider,
                  "base_url": ("https://api.x.ai/v1"
                               if model_provider == "xai-oauth" else "")},
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
    _write_yaml(home / "config.yaml", config)
    record["config_written"] = True
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
    """Drive one noninteractive turn through the isolated clone and parse traces."""
    _check_paths(home)
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                          env=env, cwd=str(REPO))
    latency = time.monotonic() - started
    return _parse_turn(prompt, at, proc, latency)


def _parse_turn(prompt: str, at: str, proc: subprocess.CompletedProcess,
                latency: float) -> TurnResult:
    answer_parts: list[str] = []
    tool_calls: list[dict] = []
    blocks = 0
    events = 0
    text_deltas: list[str] = []
    result_text = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        kind = event.get("type", "")
        if kind in ("tool_call", "tool_use", "function_call"):
            tool_calls.append(event)
        elif kind in ("tool_result", "tool_response"):
            text = json.dumps(event)
            if "PACBENCH-BLOCKED" in text:
                blocks += 1
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
        for line in reversed((proc.stdout or "").splitlines()):
            stripped = line.strip()
            if not stripped or stripped.startswith("{"):
                continue
            if "tirith security scanner" in stripped:
                continue
            answer_parts.append(stripped)
            break
    return TurnResult(prompt=prompt, at=at, answer="\n".join(answer_parts),
                      tool_calls=tool_calls, policy_blocks=blocks,
                      latency_s=round(latency, 2), returncode=proc.returncode,
                      stderr_tail="\n".join((proc.stderr or "").splitlines()[-8:]),
                      raw_events=events)


def redact(obj):
    """Strip anything that looks like a credential from an artifact structure."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in
                                        ("token", "secret", "auth", "key", "bearer",
                                         "credential", "password")) else redact(v))
                for k, v in obj.items()}
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
