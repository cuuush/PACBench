"""Audited Spark calls for private scene construction and semantic evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL_TIMEOUT_S = 180
ENCRYPTED_REDACTION = "<encrypted reasoning retained in private run artifact>"
DIRECT_RESPONSE_EVIDENCE_VERSION = "direct-response-evidence-v1"
RESPONSE_HEADER_ALLOWLIST = (
    "content-type", "content-encoding", "date", "x-request-id",
    "x-openrouter-request-id", "x-generation-id",
)


def direct_response_metadata(response):
    """Retain correlation hints, never authentication/cookie headers."""
    headers = getattr(response, 'headers', {}) or {}
    selected = {}
    for name in RESPONSE_HEADER_ALLOWLIST:
        value = headers.get(name)
        if isinstance(value, str):
            selected[name] = value[:1024]
    status = getattr(response, 'status', None)
    return {'version': DIRECT_RESPONSE_EVIDENCE_VERSION,
            'http_status': status if type(status) is int else None,
            'headers': selected,
            'scope': 'Response correlation hints; headers do not establish a generation ID or charge.'}


def save_failed_response_body(record_path, body):
    """Keep exact failed bytes private instead of losing them during parsing."""
    private = record_path.parent / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    target = private / (record_path.name + '.response-body.bin')
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
    return {'relative_path': 'private/' + target.name,
            'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
            'scope': 'Private raw HTTP body; not a usable judgment or verified cost receipt.'}


def read_direct_response(response, started, timing=None, body_sink=None, *, stop_at_done=False, timeout_s=None):
    """Bound the whole direct response, including whitespace keepalives.

    HTTP socket timeouts measure inactivity. A slow non-streaming model can
    otherwise keep a judge alive indefinitely by sending whitespace. read1
    returns available bytes so each read can use the remaining real deadline.
    The direct accounting wrapper captures these reads and retains the bound
    when an incomplete response is closed. No uncertain request is repeated.
    """
    timeout_s=MODEL_TIMEOUT_S if timeout_s is None else timeout_s
    timing=timing if timing is not None else {}
    timing['headers_after_s']=round(time.monotonic()-started,6)
    chunks=[];tail=b''
    while True:
        remaining=timeout_s-(time.monotonic()-started)
        if remaining<=0:
            raise RuntimeError('Direct model request wall deadline exceeded; outcome uncertain, do not retry')
        sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
        if sock is not None:sock.settimeout(remaining)
        chunk=response.read1(65536)
        if not chunk:return b''.join(chunks)
        if body_sink is not None:body_sink.extend(chunk)
        timing.setdefault('first_body_after_s',round(time.monotonic()-started,6))
        timing['last_body_after_s']=round(time.monotonic()-started,6)
        timing['body_bytes']=timing.get('body_bytes',0)+len(chunk)
        chunks.append(chunk)
        if stop_at_done:
            check=tail+chunk
            if re.search(rb'(?:^|\n)data:[ \t]*\[DONE\]\r?\n\r?\n',check):
                return b''.join(chunks)
            tail=check[-128:]


def public_record(value: Any) -> Any:
    """Keep readable prompts, answers and usage; exclude encrypted replay blobs."""
    if isinstance(value, dict):
        return {key: (ENCRYPTED_REDACTION if key == "encrypted_content" and item
                      else public_record(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [public_record(item) for item in value]
    return value


def _read_record(path: Path) -> dict:
    from .evidence_store import read_json
    private = path.parent / "private" / path.name
    return read_json(private if private.exists() else path)


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
                 streaming: bool | None = None,
                 timeout_s: int = MODEL_TIMEOUT_S,
                 max_attempts: int = 2,
                 url: str = "http://127.0.0.1:8099/p/pacbench-authoring/v1/chat/completions"):
        self.key = key
        self.model = model
        self.record_dir = Path(record_dir)
        self.temperature = temperature
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.reasoning_effort = reasoning_effort
        self.json_mode = json_mode
        self.streaming = streaming
        if type(timeout_s) is not int or not 0<timeout_s<=900:raise ValueError('Invalid model wall deadline')
        self.timeout_s=timeout_s
        if type(max_attempts) is not int or max_attempts not in (1,2):raise ValueError('Invalid model attempt limit')
        self.max_attempts=max_attempts
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
        streaming=(self.streaming if self.streaming is not None else
                   os.environ.get('PACBENCH_DIRECT')=='1' and os.environ.get('PACBENCH_TRANSPORT')=='openrouter')
        if streaming:
            request_body.update(stream=True,stream_options={'include_usage':True})
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
            saved = _read_record(path)
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
                saved = _read_record(cached)
                if (saved.get("status") == "ok" and saved.get("request") == request_body
                        and saved.get("url") == self.url):
                    saved["cache_hit"] = True
                    saved["cost_this_run_usd"] = 0
                    _write_record(path, saved)
                    return ModelOutput(saved["text"], saved["model"],
                                       saved.get("usage") or {}, str(path), key)
        for attempt in range(1,self.max_attempts+1):
            request_started=time.monotonic()
            response_timing={}
            raw: dict[str, Any] | None = None
            response_body = None
            received_body = bytearray()
            stream_proof = None
            accounting_receipt = None
            response = None
            response_metadata = None
            error_response: str | None = None
            error = ""
            transient = False
            spending_refused = False
            access_blocked = False
            try:
                from .gateway_client import validate_route
                validate_route(self.url, self.key)
                req = urllib.request.Request(
                    self.url, data=canonical.encode(),
                    headers={"Authorization": f"Bearer {self.key}",
                             "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
                    if os.environ.get('PACBENCH_DIRECT') == '1' or request_body.get('stream') is True:
                        response_metadata = direct_response_metadata(response)
                        response_body = read_direct_response(response, request_started, response_timing, received_body,
                                                             stop_at_done=request_body.get('stream') is True,timeout_s=self.timeout_s)
                        response_metadata.update(body_bytes=len(response_body),
                            body_sha256=hashlib.sha256(response_body).hexdigest())
                        if request_body.get('stream') is True:
                            from .chat_stream import parse_chat_stream
                            raw,stream_proof=parse_chat_stream(response_body,self.model)
                            capture=getattr(response,'capture',None)
                            if os.environ.get('PACBENCH_DIRECT')=='1' and capture is None:
                                raise ValueError('Direct judge stream has no accounting receipt')
                            if capture is not None:
                                capture.finish()
                                accounting_receipt=next(row for row in capture.receipt['budget'].snapshot()
                                                        if row['id']==capture.receipt['id'])
                                if accounting_receipt['state']!='settled':
                                    raise ValueError('Judge stream cost and token usage did not settle')
                        else:
                            decoded = json.loads(response_body)
                            if not isinstance(decoded, dict):
                                raise ValueError('model response must be a JSON object')
                            raw = decoded
                    else:
                        raw = json.loads(response.read())
                if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
                    if isinstance(raw.get('usage'), dict) and 'cost' in raw['usage']:
                        raw['usage']['cost_usd'] = raw['usage']['cost']
                content = (_response_text(raw) if protocol == "responses" else
                           raw["choices"][0]["message"]["content"])
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty model content")
            except urllib.error.HTTPError as exc:
                error_response = exc.read().decode("utf-8", errors="replace")[:8000]
                error = f"HTTP {exc.code}: {exc.reason}"
                transient = exc.code == 429 or exc.code >= 500
                access_blocked = exc.code in (401,403)
                try:
                    refusal_type = json.loads(error_response).get('error', {}).get('type')
                    if refusal_type in ('budget_refused','request_refused'):
                        transient = False
                        spending_refused = refusal_type == 'budget_refused'
                    if refusal_type == 'upstream_auth_blocked':
                        access_blocked = True
                        transient = False
                except (ValueError, AttributeError):
                    pass
                content = ""
            except Exception as exc:
                from .chat_stream import IncompleteChatStream
                if isinstance(exc,IncompleteChatStream):raw=exc.response
                if received_body and response_body is None:response_body=bytes(received_body)
                error = f"{type(exc).__name__}: {exc}"
                transient = isinstance(exc, (urllib.error.URLError, ConnectionError,
                                             TimeoutError, OSError))
                if os.environ.get('PACBENCH_DIRECT')=='1':
                    transient=False  # A disconnect can conceal a charged request.
                content = ""
            if accounting_receipt is None and response is not None:
                capture=getattr(response,'capture',None)
                if capture is not None:
                    accounting_receipt=next((row for row in capture.receipt['budget'].snapshot()
                                              if row['id']==capture.receipt['id']),None)
            record = {"created_at": datetime.now(timezone.utc).isoformat(),
                      "client_wall_deadline_s": self.timeout_s,
                      "client_max_attempts": self.max_attempts,
                      "status": "failed" if error else "ok", "model": self.model,
                      "url": self.url, "protocol": protocol, "attempt": attempt,
                      "request": request_body, "response": raw,
                      "text": content, "usage": (raw or {}).get("usage") or {},
                      "cost_usd": (raw or {}).get("usage", {}).get("cost_usd"),
                      "cost_status": ("reported" if (raw or {}).get("usage", {}).get("cost_usd") is not None
                                      else "not reported by API"),
                      "error": error, "error_response": error_response}
            if accounting_receipt is not None and accounting_receipt['state']=='settled':
                record['cost_usd']=float(accounting_receipt['accounted'])
                record['cost_status']='reported; retained in the linked accounting receipt'
            if os.environ.get('PACBENCH_DIRECT')=='1':
                record['direct_timing']={**response_timing,
                    'wall_duration_s':round(time.monotonic()-request_started,6),
                    'wall_deadline_s':self.timeout_s,
                    'scope':'Client-observed HTTP timing; does not identify upstream queue or model compute'}
                if response_metadata is not None:
                    if response_body is not None:response_metadata.update(body_bytes=len(response_body),body_sha256=hashlib.sha256(response_body).hexdigest())
                    record['direct_response'] = response_metadata
                if request_body.get('stream') is True:
                    from .chat_stream import VERSION as STREAM_PROTOCOL
                    record['response_transport']=STREAM_PROTOCOL
                    if stream_proof is not None:record['stream_completion']=stream_proof
                    if accounting_receipt is not None:
                        record['accounting_receipt']={k:accounting_receipt.get(k) for k in
                            ('id','state','accounted','reserved','input_tokens','output_tokens','upstream_request_id')}
                    if response_body is not None:
                        wire=save_failed_response_body(path,response_body)
                        wire['scope']='Private original SSE response; completion and usage are validated separately.'
                        record['stream_wire_response']=wire
                        if error:record['failed_response_body']=wire
                elif error and response_body is not None:
                    record['failed_response_body'] = save_failed_response_body(path, response_body)
            _write_record(path, record)
            if spending_refused:
                from .gateway_client import SpendingBlocked
                raise SpendingBlocked(f'model call blocked by daily spending gateway; audit record {path}: {error}')
            if access_blocked:
                from .gateway_client import ModelAccessBlocked
                raise ModelAccessBlocked(f'upstream model authentication blocked; audit record {path}: {error}')
            if not error:
                if self.cache_dir is not None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cached = self.cache_dir / f"{key}.json"
                    if not cached.exists():
                        _write_record(cached, record)
                return ModelOutput(content, self.model, record["usage"], str(path), key)
            if not transient or attempt == self.max_attempts:
                raise RuntimeError(f"model call failed; audit record {path}: {error}")
            while path.exists():
                path = self.record_dir / f"{key}-{suffix}.json"
                suffix += 1
            time.sleep(attempt)
        raise AssertionError("unreachable model retry state")


def _write_record(path: Path, record: dict) -> None:
    projected = public_record(record)
    if projected != record:
        private = path.parent / "private" / path.name
        private.parent.mkdir(exist_ok=True)
        _write_json(private, record)
    _write_json(path, projected)


def _write_json(path: Path, record: dict) -> None:
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
    """Stateless Spark writer with public traces and private encrypted replay."""

    def __init__(self, *, key: str, model: str, record_dir: Path,
                 temperature: float = 0.7, cache_dir: Path | None = None,
                 reasoning_effort: str = 'low',
                 url: str = "http://127.0.0.1:8099/p/pacbench-authoring/v1/responses"):
        super().__init__(key=key, model=model, record_dir=record_dir,
                         temperature=temperature, cache_dir=cache_dir, url=url,
                         reasoning_effort=reasoning_effort)

    def __call__(self, prompt: str) -> ModelOutput:
        return self._responses([{"role": "user", "content": [
            {"type": "input_text", "text": prompt}]}])

    def continue_from(self, prior: ModelOutput, initial_prompt: str,
                      feedback: str) -> ModelOutput:
        path = Path(prior.record_path).resolve()
        if not path.is_relative_to(self.record_dir.resolve()):
            raise ValueError("writer continuation must use this run's model record")
        saved = _read_record(path)
        if (saved.get("status") != "ok" or saved.get("protocol") != "responses" or
                saved.get("model") != self.model or saved.get("url") != self.url):
            raise ValueError("writer continuation record does not match this model")
        output = saved["response"].get("output")
        if (not isinstance(output, list) or not any(
                isinstance(item, dict) and item.get("type") == "reasoning" and
                item.get("encrypted_content") and item["encrypted_content"] != ENCRYPTED_REDACTION
                for item in output)):
            raise ValueError("writer continuation requires private encrypted reasoning")
        return self._responses([
            {"role": "user", "content": [{"type": "input_text", "text": initial_prompt}]},
            *output,
            {"role": "user", "content": [{"type": "input_text", "text": feedback}]},
        ])

    def _responses(self, items: list[dict]) -> ModelOutput:
        return self._request({"model": self.model, "temperature": self.temperature,
                              "store": False,
                              "include": ["reasoning.encrypted_content"],
                              "reasoning": {"effort": self.reasoning_effort, "summary": "auto"},
                              "max_output_tokens": 8192, "input": items},
                             protocol="responses")
