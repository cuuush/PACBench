"""Task-owned Spark driver configuration. The configured Contributor model is
resolved through the shared gateway. Clients receive only the gateway token;
upstream credential files never become inference credentials or fallbacks.
"""

from __future__ import annotations

import dataclasses
import json
import os
import urllib.request
from pathlib import Path

from .generation import GenerationError, load_config

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ENV = REPO / ".env"

PROVIDER = "meta-ai"
BASE_URL = ""
KEY_ENV_VAR = "META_API_KEY"


@dataclasses.dataclass(frozen=True)
class DriverConfig:
    """Resolved driver model. ``repr`` never carries the credential."""

    configured_model: str
    model_id: str
    provider: str = PROVIDER
    base_url: str = BASE_URL

    def __repr__(self) -> str:
        return (f"DriverConfig(configured_model={self.configured_model!r}, "
                f"model_id={self.model_id!r}, provider={self.provider!r})")


def available_model_ids(credential: str, base_url: str = BASE_URL,
                        timeout: float = 30.0) -> list[str]:
    """Model ids the Meta API currently serves (credential kept out of errors).

    Bounded retries (3) cover transient gateway blips during setup resolution.
    Scored native turns never retry.
    """
    from .gateway_client import client_settings, validate_route
    base_url = base_url or client_settings("pacbench-driver")[0]
    validate_route(base_url, credential)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {credential}"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
            break
        except Exception as exc:
            last = exc
            import time
            time.sleep(2 * (attempt + 1))
    else:
        raise GenerationError(
            f"driver model resolution failed: cannot list Meta API models "
            f"({type(last).__name__})") from last
    try:
        return [m["id"] for m in data["data"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise GenerationError(
            "driver model resolution failed: unusable model list payload") from exc


def resolve_model_id(configured: str, available: list[str]) -> str:
    """The configured MODEL must be served verbatim. No silent fallback."""
    if configured in available:
        return configured
    raise GenerationError(
        f"driver model {configured!r} is not served by the Meta API "
        f"(checked {len(available)} ids). "
        f"Fix PACBench .env MODEL; will not substitute another model.")


def load_driver(env_path: str | Path = DEFAULT_ENV) -> DriverConfig:
    """Resolve the driver from PACBench-owned `.env` and install the key.

    Reads ``MODEL``/``META`` via the existing generation loader (same parsing,
    same errors), validates the id against the live Meta model list, exports
    ``META_API_KEY`` into this process only, and returns the config.
    Raises ``GenerationError`` on any problem — callers fail the run, never
    fall back to another model or to live-profile credentials.
    """
    if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        from .gateway_client import client_settings, require_live_cost_boundary
        from .openrouter_route import MODEL
        require_live_cost_boundary()
        base_url, client_key = client_settings('pacbench-driver')
        os.environ[KEY_ENV_VAR] = client_key
        return DriverConfig(configured_model=MODEL, model_id=MODEL,
                            provider='openrouter', base_url=base_url)
    config, credential = load_config(env_path)
    from .gateway_client import client_settings
    base_url, client_key = client_settings("pacbench-driver")
    model_id = resolve_model_id(config.model, available_model_ids(client_key, base_url))
    os.environ[KEY_ENV_VAR] = client_key
    os.environ["META_BASE_URL"] = base_url
    return DriverConfig(configured_model=config.model, model_id=model_id, base_url=base_url)
