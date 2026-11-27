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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

MODEL_TIMEOUT_S = 180
ENCRYPTED_REDACTION = "<encrypted reasoning retained in private run artifact>"
DIRECT_RESPONSE_EVIDENCE_VERSION = "direct-response-evidence-v1"
RESPONSE_HEADER_ALLOWLIST = (
    "content-type", "content-encoding", "date", "x-request-id",
    "x-openrouter-request-id", "x-generation-id",
)


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


def read_bounded_response(response, started, timing=None, body_sink=None, *, stop_at_done=False, timeout_s=None):
    """Limit total wall time even while the server continues sending keepalives.
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
        if self.streaming:
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
            response = None
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
                    if request_body.get('stream'):
                        response_body = read_bounded_response(response, request_started, response_timing,
                                                             received_body, stop_at_done=True, timeout_s=self.timeout_s)
                        raw, stream_proof = parse_chat_stream(response_body, self.model)
                    else:
                        raw = json.load(response)
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
                    if refusal_type in ('budget_refused','upstream_payment_required','request_refused'):
                        transient = False
                        spending_refused = refusal_type != 'request_refused'
                    if refusal_type == 'upstream_auth_blocked':
                        access_blocked = True
                        transient = False
                except (ValueError, AttributeError):
                    pass
                content = ""
            except Exception as exc:
                if isinstance(exc,IncompleteChatStream):raw=exc.response
                if received_body and response_body is None:response_body=bytes(received_body)
                error = f"{type(exc).__name__}: {exc}"
                transient = isinstance(exc, (urllib.error.URLError, ConnectionError,
                                             TimeoutError, OSError))
                # An ambiguous OpenRouter send may already have incurred a charge.
                if os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
                    transient = False
                content = ""
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
            if stream_proof is not None:
                record['stream_completion'] = stream_proof
            if error and received_body:
                record['failed_response_body'] = save_failed_response_body(path, bytes(received_body))
            _write_record(path, record)
            if spending_refused:
                from .gateway_client import SpendingBlocked
                reason = ('upstream model provider rejected payment' if refusal_type == 'upstream_payment_required'
                          else 'model call blocked by the spending limit')
                raise SpendingBlocked(f'{reason} ({error}). Audit record {path}.')
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


CHAT_STREAM_PROTOCOL = 'complete-chat-stream-v1'


class IncompleteChatStream(ValueError):
    def __init__(self, reason, response):
        super().__init__(reason)
        self.response=response


def parse_chat_stream(body: bytes, model: str) -> tuple[dict, dict]:
    """Require an actual end, successful finish, and usable cost/token usage.

    Whitespace, heartbeat comments, partial JSON, and a plausible partial verdict
    cannot substitute for a complete response. No network request is made here.
    """
    response={'model':model,'choices':[{'index':0,'message':{'role':'assistant','content':''},'finish_reason':None}]}
    proof={'protocol':CHAT_STREAM_PROTOCOL,'body_bytes':len(body),'body_sha256':hashlib.sha256(body).hexdigest(),
           'done':False,'finish_reason':None,'usage_complete':False,'events':0}
    content=[];reasoning=[];details=[];identifier=None;finished=None;done=False
    def fail(reason):
        response['choices'][0]['message']['content']=''.join(content)
        raise IncompleteChatStream(reason,response)
    try:text=body.decode('utf-8')
    except UnicodeError:fail('Judge stream is not UTF-8')
    events=[];data=[]
    for line in text.splitlines()+['']:
        if not line.strip():
            if data:events.append('\n'.join(data));data=[]
        elif line.startswith('data:'):
            data.append(line[5:].removeprefix(' '))
        elif line.startswith(':') or line.startswith(('event:','id:','retry:')):
            continue
        else:
            fail('Judge response contains non-SSE data')
    for payload in events:
        if done:fail('Judge stream contains data after its terminator')
        if payload=='[DONE]':done=True;proof['done']=True;continue
        try:event=json.loads(payload)
        except (ValueError,TypeError):fail('Judge stream contains malformed event JSON')
        if not isinstance(event,dict):fail('Judge stream event is not an object')
        proof['events']+=1
        if event.get('error'):fail('Judge stream reports an upstream error')
        if event.get('model') not in (None,model):fail('Judge stream served a different model')
        if isinstance(event.get('id'),str) and event['id']:
            if identifier is not None and event['id']!=identifier:fail('Judge stream generation identity changed')
            identifier=event['id'];response['id']=identifier
        for name in ('provider','created'):
            if event.get(name) is not None:response[name]=event[name]
        if isinstance(event.get('usage'),dict):response['usage']=event['usage']
        choices=event.get('choices',[])
        if not isinstance(choices,list):fail('Judge stream choices are malformed')
        for choice in choices:
            if not isinstance(choice,dict) or choice.get('index',0)!=0:fail('Judge stream has an unexpected choice')
            if choice.get('error'):fail('Judge stream choice reports an upstream error')
            delta=choice.get('delta') or {}
            if not isinstance(delta,dict):fail('Judge stream delta is malformed')
            if delta.get('tool_calls') or delta.get('function_call'):fail('Judge stream attempted a tool call')
            if delta.get('role') not in (None,'assistant'):fail('Judge stream changed message role')
            piece=delta.get('content')
            if piece is not None:
                if not isinstance(piece,str):fail('Judge stream content is not text')
                if finished and piece:fail('Judge stream content continued after its finish')
                content.append(piece)
            for key in ('reasoning','reasoning_content'):
                if isinstance(delta.get(key),str):reasoning.append(delta[key])
            if isinstance(delta.get('reasoning_details'),list):details.extend(delta['reasoning_details'])
            finish=choice.get('finish_reason')
            if finish is not None:
                if finished is not None and finish!=finished:fail('Judge stream finish reason changed')
                finished=finish;response['choices'][0]['finish_reason']=finish;proof['finish_reason']=finish
    if not done:fail('Judge stream ended without [DONE]')
    if finished!='stop':fail('Judge stream did not finish successfully')
    if not identifier:fail('Judge stream has no generation identity')
    if not ''.join(content).strip():fail('Judge stream contains no answer text')
    usage=response.get('usage',{})
    try:
        cost=Decimal(str(usage['cost']))
        if (not cost.is_finite() or cost<0 or type(usage['prompt_tokens']) is not int
                or type(usage['completion_tokens']) is not int
                or usage['prompt_tokens']<0 or usage['completion_tokens']<0):
            raise ValueError('invalid usage')
    except (ValueError,KeyError,InvalidOperation):fail('Judge stream lacks complete token and cost usage')
    response['choices'][0]['message']['content']=''.join(content)
    if reasoning:response['choices'][0]['message']['reasoning']=''.join(reasoning)
    if details:response['choices'][0]['message']['reasoning_details']=details
    proof['usage_complete']=True
    return response,proof


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

    from pacbench.gateway_client import client_settings, validate_route
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
