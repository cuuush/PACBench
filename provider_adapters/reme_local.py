"""Run-owned native ReMe service and shipped HTTP Hermes integration."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from pacbench.followups import save
from pacbench.gateway_client import validate_route
from pacbench.reme_memos_setup import PINS
from provider_adapters.reme_memos_documents import request

REPO = Path(__file__).resolve().parents[1]


def plan_config(root, model, base, key):
    return {"LLM_BACKEND": "openai", "LLM_MODEL_NAME": model,
            "LLM_BASE_URL": base, "LLM_API_KEY": key}


def start(root, env, model, base, key):
    validate_route(base, key)
    root = Path(root).resolve()
    source = REPO / "vendor/reme"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != PINS["reme"][1]:
        raise ValueError("ReMe source differs from the pinned integration")
    python = REPO / "vendor/venv-reme/bin/python"
    if not python.exists():
        raise RuntimeError("Install ReMe's isolated core runtime before execution")
    plugin = source / "integrations/hermes_agent"
    destination = root / "hermes-home/plugins/reme"
    if destination.is_symlink():
        if destination.resolve() != plugin.resolve():
            raise ValueError("Existing ReMe plugin points at a different source")
    elif destination.exists():
        raise ValueError("Existing ReMe plugin is not the pinned symlink")
    else:
        destination.symlink_to(plugin, target_is_directory=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    workspace = root / "reme-workspace"
    receipts = root / "reme-native-resource-results"
    child = {**os.environ, **env, **plan_config(root, model, base, key)}
    child["PYTHONPATH"] = str(REPO) + os.pathsep + child.get("PYTHONPATH", "")
    from pacbench.openrouter_direct import runtime_environment
    runtime_environment(child)
    for name in ("DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_API_KEY"):
        child.pop(name, None)
    command = [str(python), "-m", "provider_adapters.reme_service", "--workspace", str(workspace),
               "--receipts", str(receipts), "--port", str(port)]
    log_path = root / "reme-native-service.private.log"
    with log_path.open("ab") as log:
        log_offset = log.tell()
        log_path.chmod(0o600)
        process = subprocess.Popen(command, cwd=root, env=child, stdin=subprocess.DEVNULL, stdout=log, stderr=log)

    shutdown = None
    def stop():
        nonlocal shutdown
        if shutdown is not None:
            return dict(shutdown)
        began = time.monotonic()
        forced = False
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                forced = True
                process.kill()
                process.wait(timeout=5)
        segment = log_path.read_bytes()[log_offset:]
        shutdown = {'pid': process.pid, 'returncode': process.returncode,
                    'forced_kill': forced, 'wall_s': time.monotonic() - began,
                    'native_shutdown_complete': (b'Application shutdown complete.' in segment and
                        f'Finished server process [{process.pid}]'.encode() in segment),
                    'log_slice': {'path': str(log_path), 'offset': log_offset, 'length': len(segment),
                                  'sha256': hashlib.sha256(segment).hexdigest()}}
        return dict(shutdown)

    atexit.register(stop)
    deadline = time.monotonic() + 90
    health = None
    while time.monotonic() < deadline and process.poll() is None:
        try:
            candidate = request(endpoint, "/health_check", {}, 2)
            if candidate.get("success") is True and candidate.get("metadata", {}).get("health", {}).get("healthy") is True:
                health = candidate
                break
        except Exception:
            pass
        time.sleep(.2)
    if health is None:
        stop()
        raise RuntimeError("Native ReMe service failed health check; inspect its private log")
    config = {"mode": "http", "endpoint": endpoint, "workspace_dir": str(workspace)}
    save(root / "hermes-home/reme/config.json", config)
    save(root / "native-server-setup.json", {"route": "Unmodified native ReMe HTTP service and Hermes HTTP backend",
        "provider_revision": revision, "endpoint": endpoint, "pid": process.pid, "model": model,
        "health": health, "memory_backend": "run-owned local files and default BM25",
        "native_resource_watcher": True, "native_defaults": True,
        "resource_observer": "reme-native-resource-observer-v1; read-only callback; no manual job invocation",
        "provider_clock": "real wall clock; historical raw documents retain scenario dates in resource paths and source text",
        "plugin_source_sha256": hashlib.sha256((plugin / "__init__.py").read_bytes()).hexdigest()})
    save(root / "reme-resource-config.json", {"workspace": str(workspace), "receipts": str(receipts)})
    env.update(plan_config(root, model, base, key))
    return stop
