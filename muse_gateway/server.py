"""Pass through native chat/Responses protocols; persist billing metadata only."""
from __future__ import annotations

import hmac
import json
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ledger import BudgetRefused, CONTEXT_TOKENS, MODEL

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEADLINE_SECONDS = 180
ROUTE = re.compile(r'/p/([a-z0-9][a-z0-9_.-]{0,63})/v1/(chat/completions|responses|models)')


def usage_from(value):
    if not isinstance(value, dict):
        return None
    usage = value.get('usage') or (value.get('response') or {}).get('usage')
    if not isinstance(usage, dict):
        return None
    a = usage.get('prompt_tokens', usage.get('input_tokens'))
    b = usage.get('completion_tokens', usage.get('output_tokens'))
    if all(type(n) is int and 0 <= n <= CONTEXT_TOKENS for n in (a,b)):
        return a,b
    return None


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, ledger, client_key, upstream_key,
                 *, upstream='https://api.meta.ai/v1', opener=None):
        self.ledger, self.client_key, self.upstream_key = ledger, client_key, upstream_key
        self.upstream = upstream.rstrip('/')
        self.open = opener or urllib.request.urlopen
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass  # No prompts, credentials, upstream errors or request bodies in logs.

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Connection','close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, status, kind, label):
        self.reply(status, {'error':{'type':kind,'message':label}})

    def authenticated(self):
        supplied = self.headers.get('Authorization','').removeprefix('Bearer ')
        if not hmac.compare_digest(supplied, self.server.client_key):
            self.error(401,'authentication_error','Local gateway authentication required')
            return False
        return True

    def do_GET(self):
        if not self.authenticated():
            return
        if self.path == '/health':
            self.reply(200,self.server.ledger.health())
            return
        route = ROUTE.fullmatch(self.path)
        if not route or route[2] != 'models':
            self.error(404,'request_refused','Unsupported gateway path')
            return
        request = urllib.request.Request(self.server.upstream+'/models',
                    headers={'Authorization':'Bearer '+self.server.upstream_key})
        try:
            with self.server.open(request, timeout=30) as response:
                value = json.load(response)
            if not isinstance(value.get('data'),list):
                raise ValueError('Invalid model inventory')
            # Only this priced model is admitted for paid requests.
            value['data'] = [r for r in value['data'] if r.get('id') == MODEL]
            self.reply(200,value)
        except Exception:
            self.error(502,'upstream_error','Model inventory unavailable')

    def do_POST(self):
        if not self.authenticated():
            return
        route = ROUTE.fullmatch(self.path)
        if not route or route[2] not in ('chat/completions','responses'):
            self.error(404,'request_refused','Unsupported gateway path')
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            # Whole-body bytes plus overhead conservatively bound text tokens.
            if not 0 < length <= CONTEXT_TOKENS - 32768:
                raise ValueError('Request body exceeds the metered text-input bound')
            self.connection.settimeout(30)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete request body')
            value = json.loads(body)
            if not isinstance(value,dict) or value.get('model') != MODEL:
                raise ValueError('Only the configured priced Spark Contributor model is allowed')
            if value.get('stream') is not None and type(value['stream']) is not bool:
                raise ValueError('Invalid stream flag')
            if value.get('stream_options') is not None and not isinstance(value['stream_options'],dict):
                raise ValueError('Invalid stream options')
            for key in ('max_tokens','max_completion_tokens','max_output_tokens'):
                if key in value and (type(value[key]) is not int or not 0 < value[key] <= CONTEXT_TOKENS):
                    raise ValueError('Invalid output token bound')
        except (ValueError, TypeError, OSError):
            self.error(400,'request_refused','Invalid model request or token bound')
            return
        try:
            request_id = self.server.ledger.reserve(route[1],route[2])
        except BudgetRefused:
            self.error(429,'budget_refused','Daily spending boundary reached; wait for UTC reset')
            return
        except Exception:
            self.error(503,'request_refused','Spending ledger unavailable; inference blocked')
            return
        status, usage, error = None, None, None
        headers_sent = False
        completed = False
        def complete():
            nonlocal completed
            if not completed:
                self.server.ledger.finish(request_id,usage=usage,status=status,error=error)
                completed = True
        try:
            # The native API's final usage chunk is necessary for accounting.
            # Do not change native prompts, generation limits or reasoning.
            if value.get('stream') and route[2] == 'chat/completions':
                value['stream_options'] = {**(value.get('stream_options') or {}),'include_usage':True}
            request = urllib.request.Request(self.server.upstream+'/'+route[2],
                data=json.dumps(value,ensure_ascii=False).encode(), headers={
                    'Authorization':'Bearer '+self.server.upstream_key,
                    'Content-Type':'application/json'})
            deadline = time.monotonic() + DEADLINE_SECONDS
            with self.server.open(request, timeout=DEADLINE_SECONDS) as response:
                status = response.status
                stream = 'text/event-stream' in response.headers.get('Content-Type','')
                self.send_response(status)
                self.send_header('Content-Type','text/event-stream' if stream else 'application/json')
                self.send_header('X-Gateway-Request-ID',request_id)
                self.send_header('Connection','close')
                self.end_headers()
                self.close_connection = True
                headers_sent = True
                if stream:
                    total, event, disconnected = 0, [], False
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Bounded inference transport timeout')
                        line = response.readline(MAX_RESPONSE_BYTES+1)
                        if not line:
                            break
                        total += len(line)
                        if total > MAX_RESPONSE_BYTES:
                            raise ValueError('Response exceeds the transport bound')
                        if line.startswith(b'data:'):
                            event.append(line[5:].strip())
                        if not line.strip() and event:
                            try:
                                usage = usage_from(json.loads(b'\n'.join(event))) or usage
                            except (ValueError, TypeError, AttributeError):
                                pass
                            event = []
                        if not disconnected:
                            try:
                                self.wfile.write(line)
                                self.wfile.flush()
                            except (BrokenPipeError,ConnectionResetError):
                                # Continue consuming to settle usage, even after
                                # the downstream SDK has closed its connection.
                                disconnected = True
                else:
                    data = response.read(MAX_RESPONSE_BYTES+1)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ValueError('Response exceeds the transport bound')
                    try:
                        usage = usage_from(json.loads(data))
                    except (ValueError,TypeError,AttributeError):
                        pass
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError,ConnectionResetError):
                        pass
        except urllib.error.HTTPError as exc:
            status, error = exc.code, 'upstream_http_'+str(exc.code)
            complete()
            if not headers_sent:
                self.error(status,'upstream_error','Upstream model HTTP '+str(status))
        except Exception as exc:
            error = 'transport_'+type(exc).__name__
            complete()
            if not headers_sent:
                self.error(502,'upstream_error','Upstream transport failed; cost remains reserved')
        finally:
            complete()
