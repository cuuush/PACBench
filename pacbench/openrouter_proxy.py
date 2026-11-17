"""Loopback-only, budget-reserved OpenRouter transport for native clients.

No prompts, keys or upstream error bodies enter the ledger. A reservation is
made durable before each request; ambiguous outcomes retain the full bound.
"""
from __future__ import annotations

import fcntl
import contextlib
import secrets
import threading
import hmac
import json
import math
import re
import uuid
from datetime import datetime, timezone
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .openrouter_route import MODEL, ENDPOINT, PROVIDER, CHAT_COMPLETIONS_URL as UPSTREAM, with_route
MAX_BODY = 16 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
VERSION = 'task-shared-pinned-openrouter-v2-streaming'

class BudgetRefused(ValueError):
    pass

class AuthBlocked(ValueError):
    pass


def _money(value):
    if isinstance(value, bool):
        raise ValueError('invalid monetary value')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid monetary value') from None
    if not result.is_finite() or result < 0:
        raise ValueError('invalid monetary value')
    return result


def _price_float(value):
    exact = _money(value)
    result = float(exact)
    if not math.isfinite(result):
        raise ValueError('invalid price')
    if Decimal(str(result)) > exact:
        result = math.nextafter(result, 0.0)
    return result


class Budget:
    """Exclusive file lock covers reservation, inference and settlement."""
    def __init__(self, path, *, cap, context_tokens, prompt_per_million,
                 completion_per_million, max_completion_tokens):
        self.path = Path(path)
        if not isinstance(context_tokens, int) or isinstance(context_tokens, bool) or context_tokens <= 0:
            raise ValueError('invalid context length')
        if not isinstance(max_completion_tokens, int) or isinstance(max_completion_tokens, bool) or max_completion_tokens <= 0:
            raise ValueError('invalid completion limit')
        self.config = {'cap': str(_money(cap)), 'context_tokens': context_tokens,
                       'prompt_per_million': str(_money(prompt_per_million)),
                       'completion_per_million': str(_money(completion_per_million)),
                       'max_completion_tokens': max_completion_tokens,
                       'model': MODEL, 'endpoint': ENDPOINT}
        if _money(cap) <= 0:
            raise ValueError('positive explicit spending cap required')
        if _money(prompt_per_million) <= 0 or _money(completion_per_million) <= 0:
            raise ValueError('positive explicit input and output price ceilings required')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def request(self, project, body, send):
        if body.get('n', 1) not in (None, 1) or body.get('modalities') not in (None, ['text']):
            raise ValueError('unsupported paid feature')
        if any(k in body for k in ('audio', 'prediction', 'service_tier', 'web_search_options', 'max_output_tokens')):
            raise ValueError('unsupported paid feature')
        if any(not isinstance(tool, dict) or tool.get('type') != 'function' for tool in body.get('tools', [])):
            raise ValueError('only function tools are supported')
        for msg in body.get('messages', []):
            if not isinstance(msg, dict):
                raise ValueError('invalid messages')
            content = msg.get('content')
            if content is not None and not isinstance(content, str):
                if not isinstance(content, list) or any(not isinstance(x, dict) or x.get('type') != 'text' or not isinstance(x.get('text'), str) for x in content):
                    raise ValueError('only text content is supported')
        maximum = body.get('max_completion_tokens', body.get('max_tokens', self.config['max_completion_tokens']))
        if ('max_completion_tokens' in body and 'max_tokens' in body or
                not isinstance(maximum, int) or isinstance(maximum, bool) or
                not 0 < maximum <= self.config['max_completion_tokens']):
            raise ValueError('explicit bounded completion limit required')
        bound = (Decimal(self.config['context_tokens']) * _money(self.config['prompt_per_million']) +
                 Decimal(maximum) * _money(self.config['completion_per_million'])) / Decimal(1000000)
        request = dict(body)
        supplied = request.get('provider')
        if supplied is not None:
            if not isinstance(supplied, dict) or any(k not in PROVIDER or PROVIDER[k] != v for k, v in supplied.items()):
                raise ValueError('conflicting provider route')
            request['provider'] = dict(PROVIDER)
        request = with_route(request)
        request['provider']['max_price'] = {'prompt': _price_float(self.config['prompt_per_million']),
                                            'completion': _price_float(self.config['completion_per_million']), 'request': 0}
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(self.path, os.O_RDWR)
            created = False
        with os.fdopen(fd, 'r+', encoding='utf-8') as ledger:
            fcntl.flock(ledger, fcntl.LOCK_EX)
            raw = ledger.read()
            if not raw and not created:
                raise ValueError('empty existing ledger; refusing reset')
            state = json.loads(raw) if raw else {'version': 1, 'config': self.config, 'calls': []}
            if created:
                self._persist_state(ledger, state)
            if state.get('version') != 1 or state.get('config') != self.config or not isinstance(state.get('calls'), list):
                raise ValueError('ledger configuration mismatch')
            if any(row.get('http_status') in (401, 403) for row in state['calls']):
                raise AuthBlocked('upstream authentication blocked')
            total = sum((_money(row['accounted']) for row in state['calls']), Decimal(0))
            if total + bound > _money(self.config['cap']):
                raise BudgetRefused('spending cap reached')
            row = {'id': str(uuid.uuid4()), 'project': project, 'model': MODEL,
                   'started': datetime.now(timezone.utc).isoformat(), 'finished': None,
                   'reserved': str(bound), 'accounted': str(bound), 'state': 'uncertain',
                   'provider_request': request['provider'], 'completion_bound_tokens': maximum,
                   'input_tokens': None, 'output_tokens': None, 'http_status': None, 'error_label': None}
            state['calls'].append(row)
            self._persist_state(ledger, state)
            try:
                self._sending = True
                self._created_this_request = created
                status, data = send(request)
            except Exception:
                row.update(finished=datetime.now(timezone.utc).isoformat(), error_label='upstream_unavailable')
                self._persist_state(ledger, state)
                raise
            finally:
                self._sending = False
            row.update(finished=datetime.now(timezone.utc).isoformat(), http_status=status,
                       error_label=None if status == 200 else 'upstream_request_failed')
            if status == 200:
                try:
                    if request.get('stream'):
                        events = [json.loads(line[6:]) for line in data.decode().splitlines() if line.startswith('data: ') and line[6:] != '[DONE]']
                        if any(event.get('model') is not None and event['model'] != MODEL for event in events):
                            row['error_label'] = 'model_mismatch'
                            self._persist_state(ledger, state)
                            return 502, b'{"error":{"type":"model_mismatch"}}'
                        payload = next(event for event in reversed(events) if isinstance(event.get('usage'), dict))
                    else:
                        payload = json.loads(data)
                    metadata = events if request.get('stream') else [payload]
                    for item in metadata:
                        for source, target in (('id','upstream_request_id'), ('model','served_model'),
                                               ('provider','served_provider')):
                            if isinstance(item.get(source), str):
                                row[target] = item[source][:256]
                        for choice in item.get('choices', []):
                            if isinstance(choice.get('finish_reason'), str):
                                row['finish_reason'] = choice['finish_reason'][:64]
                    if payload.get('model') is not None and payload['model'] != MODEL:
                        row['error_label'] = 'model_mismatch'
                        self._persist_state(ledger, state)
                        return 502, b'{"error":{"type":"model_mismatch"}}'
                    usage = payload['usage']
                    cost = _money(usage['cost'])
                    inp, out = usage['prompt_tokens'], usage['completion_tokens']
                    if type(inp) is int and type(out) is int and 0 <= inp <= self.config['context_tokens'] and 0 <= out <= maximum and cost <= bound:
                        row.update(accounted=str(cost), state='settled', input_tokens=inp, output_tokens=out)
                except (ValueError, KeyError, TypeError, StopIteration, UnicodeDecodeError):
                    pass
            self._persist_state(ledger, state)
            return status, data

    def _persist_state(self, ledger, state):
        _persist(ledger, state)
        self._last_snapshot = json.loads(json.dumps(state['calls']))

    def snapshot(self):
        if not self.path.exists():
            return []
        with self.path.open('r') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                if not hasattr(self, '_last_snapshot'):
                    raise RuntimeError('Task ledger is busy and no durable local snapshot is available') from None
                return [{**row, 'accounted_upper_bound_usd': float(_money(row['accounted']))}
                        for row in self._last_snapshot]
            state = json.load(file)
            if state.get('config') != self.config:
                raise ValueError('ledger configuration mismatch')
            return [{**row, 'accounted_upper_bound_usd': float(_money(row['accounted']))} for row in state['calls']]


def _persist(file, state):
    file.seek(0)
    file.write(json.dumps(state, separators=(',', ':')))
    file.truncate()
    file.flush()
    os.fsync(file.fileno())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_openrouter(request, api_key, *, budget=None, stream_callback=None):
    from .openrouter_task import require_authorization
    if budget is None or not getattr(budget, '_sending', False):
        raise RuntimeError('A durable task reservation is required before inference')
    require_authorization(budget)
    req = urllib.request.Request(UPSTREAM, data=json.dumps(request).encode(),
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(req, timeout=180)
    except urllib.error.HTTPError as exc:
        # Provider error bodies can contain prompts; expose only a fixed label.
        exc.close()
        return exc.code, b'{"error":{"type":"upstream_request_failed"}}'
    with response:
        chunks, total, pending = [], 0, b''
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE:
                raise ValueError('upstream response exceeds limit')
            chunks.append(chunk)
            if request.get('stream') and stream_callback is not None:
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if line.startswith(b'data: ') and line[6:].strip() != b'[DONE]':
                        event = json.loads(line[6:])
                        if event.get('model') is not None and event['model'] != MODEL:
                            raise ValueError('upstream stream model mismatch')
                stream_callback(chunk)
        return response.status, b''.join(chunks)


def make_server(port, token, api_key, budget, *, send=None):
    if not token or not api_key:
        raise ValueError('run token and upstream key required')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(210)

        def log_message(self, *args):
            pass

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_GET(self):
            if not self.authorized():
                return self._reply(401, b'{"error":"unauthorized"}')
            if self.path == '/health':
                calls = budget.snapshot()
                used = sum(_money(row['accounted']) for row in calls)
                return self._reply(200, json.dumps({'service':'pacbench-openrouter', 'model': MODEL, 'endpoint': ENDPOINT, 'limit_usd':budget.config['cap'], 'accounted_usd':float(used), 'budget_blocked':used >= _money(budget.config['cap']), 'auth_blocked':any(row.get('http_status') in (401, 403) for row in calls)}).encode())
            if self.path == '/usage':
                return self._reply(200, json.dumps({'state':'captured', 'calls':budget.snapshot()}).encode())
            if re.fullmatch(r'/p/[a-z0-9][a-z0-9_.-]{0,63}/v1/models', self.path):
                return self._reply(200, json.dumps({'object':'list', 'data':[{'id':MODEL, 'object':'model'}]}).encode())
            return self._reply(404, b'{"error":"not_found"}')

        def do_POST(self):
            match = re.fullmatch(r'/p/([a-z0-9][a-z0-9_.-]{0,63})/v1/chat/completions', self.path)
            if not match:
                return self._reply(404, b'{"error":"not_found"}')
            if not self.authorized():
                return self._reply(401, b'{"error":"unauthorized"}')
            streaming_started = False
            client_closed = False
            def forward(chunk):
                nonlocal streaming_started, client_closed
                if client_closed:
                    return
                try:
                    if not streaming_started:
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.end_headers()
                        streaming_started = True
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Keep collecting the upstream result for accounting even
                    # after a native client gives up. Never resend the request.
                    client_closed = True
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
                sender = send or (lambda request: send_openrouter(request, api_key, budget=budget,
                                                                 stream_callback=forward))
                status, data = budget.request(match.group(1), body, sender)
                if streaming_started or client_closed:
                    return
                return self._reply(status, data, stream=bool(body.get('stream')))
            except BudgetRefused:
                return self._reply(429, b'{"error":{"type":"budget_refused"}}')
            except AuthBlocked:
                return self._reply(403, b'{"error":{"type":"upstream_auth_blocked"}}')
            except (ValueError, TypeError, json.JSONDecodeError):
                return self._reply(400, b'{"error":"request_or_budget_rejected"}')
            except Exception:
                if streaming_started or client_closed:
                    return
                return self._reply(502, b'{"error":"upstream_unavailable"}')

        def _reply(self, status, data, stream=False):
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if stream and status == 200 else 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class TaskHTTPServer(ThreadingHTTPServer):
        # Native background work can outlive its client. Drain requests and
        # settle their usage before a normal task-proxy shutdown completes.
        daemon_threads = False
        block_on_close = True

    return TaskHTTPServer(('127.0.0.1', port), Handler)


@contextlib.contextmanager
def running_proxy(budget, *, api_key, port=0):
    """Start loopback listener; token and upstream key stay in this process."""
    token = secrets.token_urlsafe(32)
    server = make_server(port, token, api_key, budget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {'url': f'http://127.0.0.1:{server.server_port}', 'token': token}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
