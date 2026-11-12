"""Isolated, keyless local configuration for the native ReMe/MemOS plugins.

This prepares runtime state, not a benchmark result. It does not install software,
start inference, invent ingestion turns, or claim that processing has completed.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

PINS = {
    "reme": ("https://github.com/agentscope-ai/ReMe.git", "37395034cf06f6c56268f2e738fab2a14779e513"),
    "memos": ("https://github.com/MemTensor/MemOS.git", "3a16e5fe3048a209f722806f4123fa2e533588fd"),
}


def local_endpoint(value: str) -> str:
    """Require a literal loopback address, not a potentially remote DNS alias."""
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid local endpoint port") from error
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if (parsed.scheme not in {"http", "https"} or not loopback
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("A credential-free loopback HTTP(S) endpoint is required")
    return value.rstrip("/")


def settings(provider: str, root: Path, endpoint: str, model: str) -> dict:
    endpoint = local_endpoint(endpoint)
    if not model.strip():
        raise ValueError("An explicit local model name is required")
    root = root.resolve()
    if provider == "reme":
        return {
            "plugin_name": "reme",
            "plugin_config_path": "hermes-home/reme/config.json",
            "plugin_config": {"mode": "embedded", "workspace_dir": str(root / "reme-workspace"), "reme_config": "default"},
            "environment": {
                "LLM_BACKEND": "openai", "LLM_MODEL_NAME": model,
                "LLM_BASE_URL": endpoint, "LLM_API_KEY": "local-not-a-secret",
            },
        }
    if provider == "memos":
        return {
            "plugin_name": "memtensor",
            "plugin_config_path": "memos-home/config.yaml",
            "plugin_config": {
                "version": 1,
                "llm": {"provider": "openai_compatible", "endpoint": endpoint, "model": model,
                        "apiKey": "", "fallbackToHost": False},
                "embedding": {"provider": "local", "local": {"offline": True}},
                "hub": {"enabled": False}, "telemetry": {"enabled": False},
            },
            "environment": {
                "MEMOS_HOME": str(root / "memos-home"), "MEMOS_HERMES_BRIDGE_MODE": "shared",
                "LLM_PROVIDER": "openai_compatible", "LLM_ENDPOINT": endpoint,
                "LLM_MODEL": model, "LLM_API_KEY": "", "LLM_FALLBACK_TO_HOST": "false",
                "L3_LLM_PROVIDER": "openai_compatible", "L3_LLM_ENDPOINT": endpoint,
                "L3_LLM_MODEL": model, "L3_LLM_API_KEY": "",
                "SKILL_EVOLVER_PROVIDER": "openai_compatible", "SKILL_EVOLVER_ENDPOINT": endpoint,
                "SKILL_EVOLVER_MODEL": model, "SKILL_EVOLVER_API_KEY": "",
                "EMBEDDING_PROVIDER": "local", "EMBEDDING_API_KEY": "",
                "TELEMETRY_ENABLED": "false", "HUB_ENABLED": "false",
            },
        }
    raise ValueError(f"Unsupported provider: {provider}")


def prepare(provider: str, root: Path, source: Path, endpoint: str, model: str) -> dict:
    """Prepare a new run-owned home; refuse to replace existing runtime state."""
    source = source.resolve()
    pin = PINS[provider][1]
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != pin:
        raise ValueError(f"Provider source pin differs: expected {pin}, got {actual}")
    cfg = settings(provider, root, endpoint, model)
    plugin = source / ("integrations/hermes_agent" if provider == "reme" else "apps/memos-local-plugin/adapters/hermes/memos_provider")
    if not (plugin / "__init__.py").is_file():
        raise ValueError("Native provider package is missing")
    root = root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    home = root / "hermes-home"
    home.mkdir(mode=0o700)
    plugins = home / "plugins"
    plugins.mkdir(mode=0o700)
    (plugins / cfg["plugin_name"]).symlink_to(plugin, target_is_directory=True)
    path = root / cfg["plugin_config_path"]
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg["plugin_config"], indent=2) + "\n")
    path.chmod(0o600)
    (home / "config.yaml").write_text(json.dumps({"memory": {"provider": cfg["plugin_name"]}}, indent=2) + "\n")
    (home / "config.yaml").chmod(0o600)
    env = {**cfg["environment"], "HERMES_HOME": str(home)}
    report = {"provider": provider, "provider_revision": actual, "environment": env,
              "qualification": "configuration-only", "ingestion_verified": False,
              "fresh_session_recall_verified": False}
    (root / "runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=tuple(PINS))
    parser.add_argument("root", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.provider, args.root, args.source, args.endpoint, args.model), indent=2))


if __name__ == "__main__":
    main()
