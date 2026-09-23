"""Audited Spark calls for private scene construction and semantic evaluation."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelOutput:
    text: str
    model: str
    usage: dict[str, Any]
    record_path: str


class ModelClient:
    def __init__(self, *, key: str, model: str, record_dir: Path,
                 temperature: float = 0.0,
                 cache_dir: Path | None = None,
                 url: str = "https://api.meta.ai/v1/chat/completions"):
        self.key = key
        self.model = model
        self.record_dir = Path(record_dir)
        self.temperature = temperature
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.url = url

    def __call__(self, prompt: str) -> ModelOutput:
        return self.complete([{"role": "user", "content": prompt}])

    def complete(self, messages: list[dict[str, str]]) -> ModelOutput:
        request_body = {"model": self.model, "temperature": self.temperature,
                        "messages": messages}
        canonical = json.dumps(request_body, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(canonical.encode()).hexdigest()
        self.record_dir.mkdir(parents=True, exist_ok=True)
        base = self.record_dir / f"{key}.json"
        prior = [base, *sorted(self.record_dir.glob(f"{key}-*.json"))]
        for path in prior:
            if not path.exists():
                continue
            saved = json.loads(path.read_text())
            if saved.get("status") == "ok" and saved.get("request") == request_body:
                return ModelOutput(saved["text"], saved["model"],
                                   saved.get("usage") or {}, str(path))
        path = base
        suffix = 2
        while path.exists():
            path = self.record_dir / f"{key}-{suffix}.json"
            suffix += 1
        if self.cache_dir is not None:
            cached = self.cache_dir / f"{key}.json"
            if cached.exists():
                saved = json.loads(cached.read_text())
                if saved.get("status") == "ok" and saved.get("request") == request_body:
                    saved["cache_hit"] = True
                    saved["cost_this_run_usd"] = 0
                    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
                    return ModelOutput(saved["text"], saved["model"],
                                       saved.get("usage") or {}, str(path))
        raw: dict[str, Any] | None = None
        error = ""
        try:
            req = urllib.request.Request(
                self.url, data=canonical.encode(),
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as response:
                raw = json.loads(response.read())
            content = raw["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model content")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            content = ""
        record = {"created_at": datetime.now(timezone.utc).isoformat(),
                  "status": "failed" if error else "ok", "model": self.model,
                  "request": request_body, "response": raw,
                  "text": content, "usage": (raw or {}).get("usage") or {},
                  "cost_usd": (raw or {}).get("usage", {}).get("cost_usd"),
                  "cost_status": ("reported" if (raw or {}).get("usage", {}).get("cost_usd") is not None
                                  else "not reported by API"),
                  "error": error}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        if not error and self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / path.name).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        if error:
            raise RuntimeError(f"model call failed; audit record {path}: {error}")
        return ModelOutput(content, self.model, record["usage"], str(path))
