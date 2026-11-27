"""Loopback-only, budget-reserved OpenRouter transport for native clients.

No prompts, keys or upstream error bodies enter the ledger. A reservation is
made durable before each request; ambiguous outcomes retain the full bound.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import re
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .gateway import open_without_redirect
from .openrouter_route import CHAT_COMPLETIONS_URL as UPSTREAM
from .openrouter_route import MODEL
from .receipt_costs import read_frames
from .run_budget import AuthBlocked, Budget, BudgetRefused

MAX_BODY = 16 * 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
_FRAME_END = re.compile(rb'\r?\n\r?\n')
VERSION = 'run-owned-pinned-openrouter-v3'

def send_openrouter(request, api_key, *, budget=None, stream_callback=None):
    if not isinstance(budget, Budget):
        raise RuntimeError('A durable run-owned reservation is required before inference')
    budget.claim_dispatch(request)
    req = urllib.request.Request(UPSTREAM, data=json.dumps(request).encode(),
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    try:
        response = open_without_redirect(req, timeout=180)
    except urllib.error.HTTPError as exc:
        # Provider error bodies can contain prompts; expose only a fixed label.
        exc.close()
        return exc.code, b'{"error":{"type":"upstream_request_failed"}}'
    with response:
        chunks = []
        total = 0
        pending = bytearray()
        while chunk := response.read1(65536):
            total += len(chunk)
            if total > MAX_RESPONSE:
                raise ValueError('upstream response exceeds limit')
            chunks.append(chunk)
            if not request.get('stream') or stream_callback is None:
                continue
            start = max(0, len(pending) - 3)
            pending.extend(chunk)
            while delimiter := _FRAME_END.search(pending, start):
                start = 0
                end = delimiter.end()
                frame = bytes(pending[:end])
                del pending[:end]
                observed = read_frames(frame)
                if observed.errors:
                    raise ValueError('invalid upstream stream')
                if any(event.get('model') not in (None, MODEL) for event in observed.events):
                    raise ValueError('upstream stream model mismatch')
                stream_callback(frame)
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
                return self._reply(200, json.dumps(budget.health()).encode())
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
            except BudgetRefused as exc:
                if str(exc) == 'upstream_payment_required':
                    return self._reply(402, b'{"error":{"type":"upstream_payment_required"}}')
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
