"""Run-owned local Cognee server with the unmodified native HTTP plugin."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

from pacbench.followups import save
from pacbench.gateway_client import validate_route
from pacbench.cognee_memobase_setup import PINS

REPO = Path(__file__).resolve().parents[1]


def plan_config(root, model, base, key):
    # LiteLLM removes this transport prefix, leaving the exact OpenRouter model
    # identifier on the wire. Without it LiteLLM guesses the DeepSeek API.
    return {"LLM_PROVIDER": "openai", "LLM_MODEL": "openai/" + model,
        "LLM_ENDPOINT": base, "LLM_API_KEY": key,
        "EMBEDDING_PROVIDER": "ollama", "EMBEDDING_MODEL": "nomic-embed-text",
        "EMBEDDING_ENDPOINT": "http://127.0.0.1:11435/api/embed", "EMBEDDING_DIMENSIONS": "768",
        # These are the pinned plugin's own ollama_embedding_pins defaults.
        "EMBEDDING_MAX_COMPLETION_TOKENS": "2048",
        "HUGGINGFACE_TOKENIZER": "nomic-ai/nomic-embed-text-v1.5",
        "DATA_ROOT_DIRECTORY": str(root / "cognee-data"),
        "SYSTEM_ROOT_DIRECTORY": str(root / "cognee-system"),
        "CACHE_ROOT_DIRECTORY": str(root / "cognee-cache"),
        "CACHING": "true", "AUTO_FEEDBACK": "true",
        "LLM_INSTRUCTOR_MODE": "json_schema_mode", "COGNEE_IMPROVE_SUBMIT_TIMEOUT": "420",
        "DEFAULT_USER_EMAIL": "default_user@example.com", "DEFAULT_USER_PASSWORD": "default_password",
        "TELEMETRY_DISABLED": "1"}


def start(root, env, model, base, key):
    validate_route(base, key)
    root = Path(root).resolve()
    source = REPO / "vendor/cognee-integrations"
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != PINS["cognee"]["commit"]:
        raise ValueError("Cognee integration differs from pinned source")
    python = REPO / "vendor/venv-cognee/bin/python"
    from pacbench import harness
    from provider_adapters.embedding_health import check
    save(root / "native-embedding-preflight.json", check(
        plan_config(root,model,base,key)['EMBEDDING_ENDPOINT'],interface='ollama'))
    plugin_source = source / "integrations/hermes-agent"
    child = {**os.environ, **env, **plan_config(root, model, base, key)}
    from pacbench.openrouter_direct import runtime_environment
    runtime_environment(child)
    from provider_adapters.tokenizer_cache import configure as configure_tokenizers
    if not configure_tokenizers(root,child):
        raise RuntimeError('Install the verified native tokenizer assets before Cognee processing')
    install_env = {**child, "PYTHONPATH": str(plugin_source) + os.pathsep + child.get("PYTHONPATH", "")}
    installation = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-m",
        "cognee_integration_hermes.installer", "--home", str(root / "hermes-home")],
        cwd=harness.ISOLATED_TREE, env=install_env, capture_output=True, text=True, timeout=30)
    if installation.returncode:
        raise RuntimeError("Native Cognee plugin installer failed")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    scope = "pacbench_" + hashlib.sha256(str(root).encode()).hexdigest()[:24]
    for name in ("LLM_EXTRACTION_API_KEY", "LLM_QUERY_API_KEY", "LLM_SUMMARIZATION_API_KEY",
                 "LLM_EXTRACTION_MODEL", "LLM_QUERY_MODEL", "LLM_SUMMARIZATION_MODEL",
                 "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "COGNEE_API_KEY", "EMBEDDING_API_KEY"):
        child.pop(name, None)
    child["HTTP_API_PORT"] = str(port)
    log_path = root / "cognee-native-server.private.log"
    with log_path.open("ab") as log:
        log_path.chmod(0o600)
        process = subprocess.Popen([str(python), "-m", "uvicorn", "cognee.api.client:app",
            "--host", "127.0.0.1", "--port", str(port)], cwd=root, env=child,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)

    def stop():
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    atexit.register(stop)
    deadline = time.monotonic() + 240
    health = None
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urlopen(endpoint + "/health", timeout=2) as response:
                candidate = json.load(response)
            if candidate.get("version") == "1.6.6":
                health = candidate
                break
        except Exception:
            pass
        time.sleep(.25)
    if health is None:
        stop()
        raise RuntimeError("Native Cognee server did not become healthy at pinned version; inspect private log")
    save(root / "hermes-home/cognee.json", {"service_url": endpoint, "embedded": False,
        "transport": "http", "dataset": scope, "recall_mode": "pinned"})
    save(root / "cognee-document-config.json", {"service_url": endpoint, "dataset": scope})
    env.update(COGNEE_BASE_URL=endpoint, COGNEE_SERVICE_URL="", COGNEE_API_KEY="",
        COGNEE_TRANSPORT="http", COGNEE_EMBEDDED="false", COGNEE_PLUGIN_DATASET=scope,
        COGNEE_PLUGIN_RECALL_MODE="pinned")
    from pacbench.prefetch_provenance import VERSION
    declaration={"protocol":VERSION,"provider":"cognee","state":"unverified",
        "reason":"Native hybrid graph/chunk prefetch can contain original source; independent passage provenance remains pending"}
    save(root/'prefetch-provenance.json',declaration)
    env['PACBENCH_PREFETCH_PROVENANCE']=json.dumps(declaration)
    save(root / "native-server-setup.json", {"route": "Native Cognee1.6.6 local API; native Hermes HTTP transport to loopback",
        "provider_revision": revision, "endpoint": endpoint, "dataset": scope, "pid": process.pid,
        "health": health, "model": model, "sdk_model": "openai/" + model,
        "memory_backend": "local native SQLite, graph and vector stores; isolated roots",
        "hosted_memory_service": False, "embedding": "local Ollama nomic-embed-text",
        "bootstrap_defaults": "CACHING, AUTO_FEEDBACK, instructor mode, default local user and embedding pins match shipped plugin bootstrap",
        "provider_clock": "real processing clock; historical raw source retains original dates",
        "plugin_source_sha256": hashlib.sha256((plugin_source / "cognee_integration_hermes/provider.py").read_bytes()).hexdigest()})
    return stop
