"""Spark surface-language generation, configured by `.env`.

Life authoring is specified by PACBench (`pacbench.authoring`); Spark only writes
surface language through :func:`pacbench.render.render`, which rejects any rewrite
that drops a required fact token. This module binds that rewrite hook to the
configured Spark model.

Configuration comes from the repo `.env` (never committed, never printed):

- ``MODEL`` — the Spark model id (v0.1: ``muse-spark-1.3-contributor``).
- ``META`` — the credential for the generation gateway. Treated as a secret:
  loaders never include it in reprs, logs, or errors.

If the gateway call fails, the error is reported to the caller. The model is never
silently swapped: a failed generation aborts the rendering it was asked for.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .render import Generate

GATEWAY_URL = "https://openrouter.ai/api/v1/chat/completions"


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
    """Build a `generate(prompt) -> text` bound to the configured Spark model."""

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
    """One-step hook for authoring: config from `.env`, ready for `build_life`."""
    config, credential = load_config(env_path)
    return spark_generator(config, credential, **kwargs)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check Spark generation config.")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--render-sample", action="store_true",
                        help="render one sample beat through Spark (network)")
    args = parser.parse_args()
    try:
        config, _credential = load_config(args.env)
    except GenerationError as exc:
        print(f"config error: {exc}")
        raise SystemExit(1)
    print(f"model: {config.model}")
    print(f"credential: present (<redacted>, {len(_credential)} chars)")
    if args.render_sample:
        from .render import Beat, render
        generate = generator_from_env(args.env)
        beat = Beat(channel="groupme", sender="Jordan Vance",
                    draft="moving poker to Saturday 8pm at my place, 44 Birch Ave",
                    must_include=("Saturday", "8pm", "44 Birch"))
        try:
            print(f"rendered: {render(beat, generate=generate)}")
        except Exception as exc:
            print(f"generation failed (reported, not substituted): {exc}")
            raise SystemExit(2)
