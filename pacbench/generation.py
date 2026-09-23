"""Spark credentials and a small prompt-to-text gateway helper.

Scene generation and audit records live in `pacbench.authoring` and
`pacbench.model_calls`. This module retains the shared credential loader used
by the driver and provider setup.

Configuration comes from the repo `.env` (never committed, never printed):

- ``MODEL`` — the Spark model id (v0.1: ``muse-spark-1.3-contributor``).
- ``META`` — the bearer credential for Meta's Model API
  (``https://api.meta.ai/v1``), which serves the Spark family natively.
  Treated as a secret: loaders never include it in reprs, logs, or errors.
  This is the same file and credential ``pacbench.creds.load_driver`` uses
  for the scored-run driver — one gateway, one credential.

If the gateway call fails, the error is reported to the caller. The model is never
silently swapped: a failed generation aborts the rendering it was asked for.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from typing import Callable

Generate = Callable[[str], str]

GATEWAY_URL = "https://api.meta.ai/v1/chat/completions"


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
    model, credential = None, None
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

    Posts to Meta's Model API (``gateway_url``, default ``GATEWAY_URL``) with
    the ``META`` credential as a Bearer header. The credential never appears
    in errors or logs; failures raise :class:`GenerationError` naming only
    the model."""

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
