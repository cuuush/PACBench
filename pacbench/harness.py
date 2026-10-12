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
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ISOLATED_TREE = REPO / "vendor" / "hermes-isolated"
ISOLATED_BIN = REPO / "vendor" / "venv-hermes-isolated" / "bin" / "hermes"
PATCH_RECORD = REPO / "patches" / "hermes-isolated.diff"

#: Interpreter that owns the hindsight_api install (proven: 3.12.8 at this
#: path; 3.14 lacks the deps → ModuleNotFoundError mid-run). The Hindsight
#: MCP server MUST launch under this binary — never bare sys.executable
#: (inherits whatever launched the run), never a new hardcoded literal
#: (rots on the next machine). Override with PACBENCH_PYTHON when the
#: install provably moves; the override is recorded, never silent.
PYTHON_ENV_OVERRIDE = "PACBENCH_PYTHON"
DEFAULT_PYTHON = "/usr/local/bin/python3"


def python_bin() -> str:
    """Interpreter for provider server subprocesses (audit surface)."""
    override = os.environ.get(PYTHON_ENV_OVERRIDE, "").strip()
    return override or DEFAULT_PYTHON


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
    # so per-turn spend is the delta. None means the clone
    # reported no token stats for this turn — unknown stays unknown, never zero.
    session_input_tokens: int | None = None
    session_output_tokens: int | None = None
    provider_traces: list[dict] = dataclasses.field(default_factory=list)
    prefetch_context: str | None = None
    context_isolation: dict | None = None


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
                   provider_name: str, model: str = "muse-spark-1.3-contributor",
                   model_provider: str = "meta-ai",
                   model_base_url: str | None = None) -> dict:
    """Build a private provider home, with gateway inference and no live auth.

    Copy the selected plugin rather than symlinking a personal profile. The
    provider process receives only task-owned gateway credentials. Native
    dispatch enforces the exact provider allowlist; built-in memory stays off.
    """
    if model_provider != 'meta-ai':
        raise ValueError('Only gateway-routed Spark inference is supported')
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

    from .gateway_client import client_settings, validate_route
    base_url = model_base_url or client_settings("pacbench-driver")[0]
    validate_route(base_url)
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


def turn_env(at: str, home: Path,
             allowlist: str,
             extra_env: dict | None = None) -> dict:
    """Environment for one isolated turn.

    MEMCAL_TODAY pins memcal's native clock (brief windows, write
    precedence) to the fictional now via the product's documented
    benchmark seam (`MEMCAL_TODAY`, memcal.db clock header). Without it
    the memcal brief renders the host week — observed 2026-09-21: brief
    said "(nothing known)" for host-week Sep 18–28 while the fictional
    2026-09-10 evidence sat in archive, and Hermes trusted the empty
    brief. Accepts the full ISO `at` (memcal splits day + time).
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    from .source_policy import SOURCE_ARGUMENT_RULES
    env.update({
        "PACBENCH_SOURCE_ARGUMENT_RULES": json.dumps(SOURCE_ARGUMENT_RULES),
        "HERMES_HOME": str(home),
        "PACBENCH_NOW": at,
        "MEMCAL_TODAY": at,
        "PACBENCH_TOOL_ALLOWLIST": allowlist,
        "PACBENCH_NO_BUILTIN_MEMORY": "1",
        "PACBENCH_NO_SESSION_SEARCH": "1",
        "PACBENCH_CONTEXT_TRACE": str(home.parent / "context.jsonl"),
        "TERM": "dumb",
    })
    if extra_env:
        env.update(extra_env)
    return env


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
    home = home.resolve()
    _check_paths(home)
    prompt = (f"[Simulated now: {at}. Treat this as the current date and "
              f"time for everything in this turn.]\n\n{prompt}")
    env = turn_env(at, home, allowlist, extra_env)
    from .context_isolation import actual_context, workspace_for
    workspace = workspace_for(home)
    env['TERMINAL_CWD'] = str(workspace)
    cmd = [str(ISOLATED_BIN), "chat", "-q", prompt, "-Q", "--format", "stream-json"]
    if session_id:
        cmd += ["--resume", session_id]
    provider_before = provider_trace_snapshot(env)
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, env=env, cwd=str(workspace))
    except subprocess.TimeoutExpired as exc:
        # A timeout is a failed turn, not a missing one (§41: failures are
        # required artifacts). Synthesize the same diagnostic TurnResult a
        # nonzero exit would carry so no error path is record-less (P1: kill
        # error-only turns). Partial child output rides along when present.
        latency = time.monotonic() - started
        note = (f"Hermes turn timed out after {timeout_s}s "
                f"(cmd: {' '.join(str(c) for c in cmd)})")
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        part_out, part_err = decode(exc.stdout), decode(exc.stderr)
        partial = subprocess.CompletedProcess(cmd, -1, part_out, note + "\n" + part_err)
        result = _parse_turn(prompt, at, partial, latency)
        result.stderr_tail = note
        result.context_isolation = actual_context(home, result.session_id, workspace)
        _recover_tool_results(result, home / "state.db")
        capture_provider_traces(result, env, provider_before)
        enforce_provider_tools(result, env)
        return result
    latency = time.monotonic() - started
    result = _parse_turn(prompt, at, proc, latency)
    result.context_isolation = actual_context(home, result.session_id, workspace)
    if result.context_isolation['state'] != 'pass':
        result.policy_blocks += 1
    _recover_tool_results(result, home / "state.db")
    trace = home.parent / "context.jsonl"
    if trace.exists():
        try:
            latest = json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])
            result.prefetch_context = latest.get("prefetch")
        except (IndexError, ValueError, OSError):
            pass
    capture_provider_traces(result, env, provider_before)
    enforce_provider_tools(result, env)
    return result



def provider_trace_snapshot(env: dict) -> dict:
    root = env.get('PACBENCH_PROVIDER_TRACE_ROOT')
    records={str(p):p.read_bytes() for p in Path(root).glob('projects/*/task-history/**/tsk-*.json')} if root else {}
    honcho=env.get('PACBENCH_HONCHO_TRACE_FILE')
    if honcho and Path(honcho).exists():records[honcho]=Path(honcho).read_bytes()
    return records


def honcho_tool_trace(data, turn):
    """Publish native tool evidence only; private prompts/reasoning stay private."""
    if data.get('task_type')!='dialectic_chat':return None
    messages=data.get('input',{}).get('messages',[])
    text=messages[-1].get('content','') if messages else ''
    query=(text.split('\n\n## Relevant Observations',1)[0].removeprefix('Query: ').strip()
           if isinstance(text,str) and text.startswith('Query: ') else None)
    queries={(c.get('input',c.get('arguments',{})) or {}).get('query')
             for c in turn.tool_calls if c.get('name')=='honcho_reasoning'}
    calls=[{'toolName':c['tool_name'],'args':c.get('tool_input',{}),
            'result':c.get('tool_result'),'metadata':c.get('tool_result_metadata',{})}
           for c in data.get('output',{}).get('tool_calls',[]) or []]
    return {'provider':'honcho','attribution':'explicit-query-match' if query in queries else 'background-or-prefetch',
            'record':{'timestamp':data['timestamp'],'model':data.get('model'),'query':query,'toolCalls':calls},
            'scope':'Native reasoning completed during the turn. Query matching identifies explicit Hermes requests; other work may be asynchronous.'}


def capture_provider_traces(turn: TurnResult, env: dict, before: dict) -> None:
    """Observe native provider task journals, including prefetch, without changing tools."""
    for filename, content in provider_trace_snapshot(env).items():
        if before.get(filename) == content:
            continue
        try:
            text = content.decode()
            for key in ('OPENAI_API_KEY', 'OPENAI_COMPATIBLE_API_KEY', 'SUPERMEMORY_API_KEY'):
                token = env.get(key)
                if token: text = text.replace(token, '<redacted>')
            if filename==env.get('PACBENCH_HONCHO_TRACE_FILE'):
                prior=before.get(filename,b'')
                if not content.startswith(prior):raise ValueError('Native journal rotated during turn')
                fresh=content[len(prior):].decode()
                for key in ('OPENAI_API_KEY','OPENAI_COMPATIBLE_API_KEY','SUPERMEMORY_API_KEY'):
                    if env.get(key):fresh=fresh.replace(env[key],'<redacted>')
                for line in fresh.splitlines():
                    trace=honcho_tool_trace(json.loads(line),turn)
                    if trace:turn.provider_traces.append(trace)
                continue
            record = json.loads(text)
            trace = {'file': filename, 'record': record}
            project = next((p for p in Path(filename).parents if p.parent.name == 'projects'), None)
            if project and record.get('type') == 'query':
                query_records = []
                for p in (project / 'query-log').glob('qry-*.json'):
                    query_text = p.read_text()
                    for key in ('OPENAI_API_KEY', 'OPENAI_COMPATIBLE_API_KEY', 'SUPERMEMORY_API_KEY'):
                        token = env.get(key)
                        if token: query_text = query_text.replace(token, '<redacted>')
                    data = json.loads(query_text)
                    if data.get('taskId') == record.get('taskId'):
                        query_records.append(data)
                trace['query_records'] = query_records
            turn.provider_traces.append(trace)
        except (ValueError, UnicodeError, TypeError, AttributeError, KeyError):
            turn.provider_traces.append({'file': filename, 'provider': 'honcho' if filename==env.get('PACBENCH_HONCHO_TRACE_FILE') else None,
                                         'error': 'Native task journal could not be decoded'})


def enforce_provider_tools(turn: TurnResult, env: dict) -> None:
    """Fail any non-provider attempt, even if Hermes rejected it before dispatch."""
    if env.get("PACBENCH_PROVIDER_ONLY") != "1":
        return
    allowed = {n.strip() for n in env.get("PACBENCH_TOOL_ALLOWLIST", "").split(",")
               if n.strip() and "*" not in n}
    from .source_policy import SOURCE_ARGUMENT_RULES, source_read_blocked
    rules = json.loads(env.get('PACBENCH_SOURCE_ARGUMENT_RULES', json.dumps(SOURCE_ARGUMENT_RULES)))
    forbidden = [c for c in turn.tool_calls if c.get("name") not in allowed or
                 source_read_blocked(c.get("name"),c.get("input",c.get("arguments",{})),rules)]
    turn.policy_blocks = max(turn.policy_blocks, len(forbidden))


def _recover_tool_results(turn: TurnResult, state_db: Path) -> None:
    """Use Hermes's saved tool messages when stream-json clipped their output."""
    if not turn.session_id or not turn.tool_results or not state_db.exists():
        return
    try:
        with sqlite3.connect(f"file:{state_db}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT tool_name, content FROM messages WHERE session_id = ? "
                "AND role = 'tool' ORDER BY id", (turn.session_id,)).fetchall()
    except sqlite3.Error:
        return
    # A resumed session also contains prior turns' results. Recover only this
    # turn's trailing messages; do not discard full outputs after a follow-up.
    rows = rows[-len(turn.tool_results):]
    if (len(rows) != len(turn.tool_results) or
            any(name != result["tool"] or not isinstance(content, str)
                for result, (name, content) in zip(turn.tool_results, rows))):
        return
    for result, (_, content) in zip(turn.tool_results, rows):
        if len(result["text"]) == 5003 and result["text"].endswith("..."):
            result["stream_preview"] = result["text"]
        result["text"] = content
        result["truncated"] = False
        result["capture"] = "hermes_session_db"


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
            tool_results.append(_summarize_tool_result(event, limit=200_000))
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
            # the delta across turns in one session. A missing
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
    stream_clipped = len(text) == 5003 and text.endswith("...")
    truncated = len(text) > limit or stream_clipped
    policy_blocked = "PACBENCH-BLOCKED" in text
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    tool_error = isinstance(decoded, dict) and "error" in decoded
    return {
        "tool": event.get("tool") or event.get("name") or "",
        "success": bool(event.get("success", event.get("error") is None))
                   and not event.get("is_error", False) and not policy_blocked and not tool_error,
        "policy_blocked": policy_blocked,
        "text": text[:limit],
        "truncated": truncated,
        "capture": "stream_json_preview" if stream_clipped else "stream_json",
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
