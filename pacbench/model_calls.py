"""Audited Spark calls for private scene construction and semantic evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_TIMEOUT_S = 180


@dataclass(frozen=True)
class ModelOutput:
    text: str
    model: str
    usage: dict[str, Any]
    record_path: str
    request_sha: str = ""


class ModelClient:
    def __init__(self, *, key: str, model: str, record_dir: Path,
                 temperature: float = 0.0,
                 cache_dir: Path | None = None,
                 reasoning_effort: str | None = None,
                 json_mode: bool = False,
                 url: str = "http://127.0.0.1:8099/p/pacbench-authoring/v1/chat/completions"):
        self.key = key
        self.model = model
        self.record_dir = Path(record_dir)
        self.temperature = temperature
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.reasoning_effort = reasoning_effort
        self.json_mode = json_mode
        self.url = url

    def __call__(self, prompt: str) -> ModelOutput:
        return self.complete([{"role": "user", "content": prompt}])

    def complete(self, messages: list[dict[str, str]]) -> ModelOutput:
        request_body = {"model": self.model, "temperature": self.temperature,
                        "messages": messages}
        if self.reasoning_effort is not None:
            request_body["reasoning_effort"] = self.reasoning_effort
        if self.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        return self._request(request_body, protocol="chat")

    def _request(self, request_body: dict, *, protocol: str) -> ModelOutput:
        canonical = json.dumps(request_body, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(json.dumps({"url": self.url, "body": request_body},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.record_dir.mkdir(parents=True, exist_ok=True)
        base = self.record_dir / f"{key}.json"
        prior = [base, *sorted(self.record_dir.glob(f"{key}-*.json"))]
        for path in prior:
            if not path.exists():
                continue
            saved = json.loads(path.read_text())
            if (saved.get("status") == "ok" and saved.get("request") == request_body
                    and saved.get("url") == self.url):
                return ModelOutput(saved["text"], saved["model"],
                                   saved.get("usage") or {}, str(path), key)
        path = base
        suffix = 2
        while path.exists():
            path = self.record_dir / f"{key}-{suffix}.json"
            suffix += 1
        if self.cache_dir is not None:
            cached = self.cache_dir / f"{key}.json"
            if cached.exists():
                saved = json.loads(cached.read_text())
                if (saved.get("status") == "ok" and saved.get("request") == request_body
                        and saved.get("url") == self.url):
                    saved["cache_hit"] = True
                    saved["cost_this_run_usd"] = 0
                    _write_record(path, saved)
                    return ModelOutput(saved["text"], saved["model"],
                                       saved.get("usage") or {}, str(path), key)
        for attempt in (1, 2):
            raw: dict[str, Any] | None = None
            error_response: str | None = None
            error = ""
            transient = False
            try:
                from .gateway_client import validate_route
                validate_route(self.url, self.key)
                req = urllib.request.Request(
                    self.url, data=canonical.encode(),
                    headers={"Authorization": f"Bearer {self.key}",
                             "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT_S) as response:
                    raw = json.loads(response.read())
                content = (_response_text(raw) if protocol == "responses" else
                           raw["choices"][0]["message"]["content"])
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty model content")
            except urllib.error.HTTPError as exc:
                error_response = exc.read().decode("utf-8", errors="replace")[:8000]
                error = f"HTTP {exc.code}: {exc.reason}"
                transient = exc.code == 429 or exc.code >= 500
                content = ""
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                transient = isinstance(exc, (urllib.error.URLError, ConnectionError,
                                             TimeoutError, OSError))
                content = ""
            record = {"created_at": datetime.now(timezone.utc).isoformat(),
                      "status": "failed" if error else "ok", "model": self.model,
                      "url": self.url, "protocol": protocol, "attempt": attempt,
                      "request": request_body, "response": raw,
                      "text": content, "usage": (raw or {}).get("usage") or {},
                      "cost_usd": (raw or {}).get("usage", {}).get("cost_usd"),
                      "cost_status": ("reported" if (raw or {}).get("usage", {}).get("cost_usd") is not None
                                      else "not reported by API"),
                      "error": error, "error_response": error_response}
            _write_record(path, record)
            if not error:
                if self.cache_dir is not None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cached = self.cache_dir / f"{key}.json"
                    if not cached.exists():
                        _write_record(cached, record)
                return ModelOutput(content, self.model, record["usage"], str(path), key)
            if not transient or attempt == 2:
                raise RuntimeError(f"model call failed; audit record {path}: {error}")
            while path.exists():
                path = self.record_dir / f"{key}-{suffix}.json"
                suffix += 1
            time.sleep(attempt)
        raise AssertionError("unreachable model retry state")


def _write_record(path: Path, record: dict) -> None:
    data = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)


def _response_text(raw: dict) -> str:
    if raw.get("status") != "completed":
        raise ValueError(f"response status {raw.get('status')!r}")
    return "\n".join(
        part["text"] for item in raw.get("output", [])
        if item.get("type") == "message" for part in item.get("content", [])
        if part.get("type") == "output_text" and isinstance(part.get("text"), str))


class ResponsesWriter(ModelClient):
    """Stateless Spark writer whose saved turns retain encrypted reasoning."""

    def __init__(self, *, key: str, model: str, record_dir: Path,
                 temperature: float = 0.7, cache_dir: Path | None = None,
                 url: str = "http://127.0.0.1:8099/p/pacbench-authoring/v1/responses"):
        super().__init__(key=key, model=model, record_dir=record_dir,
                         temperature=temperature, cache_dir=cache_dir, url=url)

    def __call__(self, prompt: str) -> ModelOutput:
        return self._responses([{"role": "user", "content": [
            {"type": "input_text", "text": prompt}]}])

    def continue_from(self, prior: ModelOutput, initial_prompt: str,
                      feedback: str) -> ModelOutput:
        path = Path(prior.record_path).resolve()
        if not path.is_relative_to(self.record_dir.resolve()):
            raise ValueError("writer continuation must use this run's model record")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if (saved.get("status") != "ok" or saved.get("protocol") != "responses" or
                saved.get("model") != self.model or saved.get("url") != self.url):
            raise ValueError("writer continuation record does not match this model")
        output = saved["response"].get("output")
        if (not isinstance(output, list) or not any(
                isinstance(item, dict) and item.get("type") == "reasoning" and
                item.get("encrypted_content") for item in output)):
            raise ValueError("writer continuation has no encrypted reasoning")
        return self._responses([
            {"role": "user", "content": [{"type": "input_text", "text": initial_prompt}]},
            *output,
            {"role": "user", "content": [{"type": "input_text", "text": feedback}]},
        ])

    def _responses(self, items: list[dict]) -> ModelOutput:
        return self._request({"model": self.model, "temperature": self.temperature,
                              "store": False,
                              "include": ["reasoning.encrypted_content"],
                              "reasoning": {"effort": "medium", "summary": "auto"},
                              "max_output_tokens": 8192, "input": items},
                             protocol="responses")
