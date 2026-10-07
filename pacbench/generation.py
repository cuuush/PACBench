"""Spark configuration and generation through the authenticated spending gateway.
MODEL/META can come from task-owned files or managed environment bindings.
Inference always uses the local gateway token and rejects external endpoints.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from typing import Callable

Generate = Callable[[str], str]

GATEWAY_URL = "http://127.0.0.1:8099/p/pacbench-authoring/v1/chat/completions"


class GenerationError(RuntimeError):
    """A Spark generation call failed. Reported, never silently retried elsewhere."""


@dataclass(frozen=True)
class SparkConfig:
    """Resolved generation config. `repr` redacts the credential by construction."""

    model: str

    def __repr__(self) -> str:
        return f"SparkConfig(model={self.model!r}, credential=<redacted>)"


def load_config(env_path: str | Path = ".env") -> tuple[SparkConfig, str]:
    """Read MODEL and META from a `.env` file.

    Returns ``(config, credential)`` with the credential kept out of the config
    object so it cannot leak through logging. Raises :class:`GenerationError` if
    either value is missing.
    """
    # Managed runtimes bind these directly; never read a personal agent profile.
    import os
    model, credential = os.environ.get("MODEL"), os.environ.get("META")
    path = Path(env_path).expanduser()
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key == "MODEL" and value:
                model = value
            elif key == "META" and value:
                credential = value
    missing = [k for k, v in (("MODEL", model), ("META", credential)) if not v]
    if missing:
        raise GenerationError(f"{path}: missing {', '.join(missing)} "
                              f"(want MODEL=<spark model> and META=<credential>)")
    return SparkConfig(model=model or ""), credential or ""


def spark_generator(config: SparkConfig, credential: str, *,
                    gateway_url: str = GATEWAY_URL,
                    temperature: float = 0.8, timeout: float = 60.0) -> Generate:
    """Build a `generate(prompt) -> text` bound to the configured Spark model.

    Posts through the spending gateway (``gateway_url``, default ``GATEWAY_URL``)
    with its local credential as a Bearer header. The credential never appears
    in errors or logs; failures raise :class:`GenerationError` naming only
    the model."""

    from .gateway_client import client_settings, validate_route
    _, credential = client_settings("pacbench-authoring")
    validate_route(gateway_url, credential)

    def generate(prompt: str) -> str:
        body = json.dumps({
            "model": config.model,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = urllib.request.Request(
            gateway_url, data=body,
            headers={"Authorization": f"Bearer {credential}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
        except Exception as exc:
            raise GenerationError(
                f"spark generation with model {config.model!r} failed: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GenerationError(
                f"spark generation with model {config.model!r} returned "
                f"an unusable payload") from exc

    return generate


def generator_from_env(env_path: str | Path = ".env", **kwargs) -> Generate:
    """One-step Spark generator from the repository credential file."""
    config, credential = load_config(env_path)
    return spark_generator(config, credential, **kwargs)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check Spark generation config.")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--render-sample", action="store_true",
                        help="send one sample prompt through Spark (network)")
    args = parser.parse_args()
    try:
        config, _credential = load_config(args.env)
    except GenerationError as exc:
        print(f"config error: {exc}")
        raise SystemExit(1)
    print(f"model: {config.model}")
    print(f"credential: present (<redacted>, {len(_credential)} chars)")
    if args.render_sample:
        generate = generator_from_env(args.env)
        print(generate("Write one sentence about a fictional weekend plan."))
