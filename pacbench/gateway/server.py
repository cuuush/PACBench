"""Pass through native chat/Responses protocols; persist billing metadata only."""
from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pacbench.gateway import open_without_redirect
from pacbench.gateway.ledger import Ledger

from .ledger import CONTEXT_TOKENS, MODEL, BudgetRefused

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEADLINE_SECONDS = 180
ROUTE = re.compile(r'/p/([a-z0-9][a-z0-9_.-]{0,63})/v1/(chat/completions|responses|models)')


def upstream_error_diagnostic(response):
    """Read a bounded error body in memory; return only fixed protocol labels."""
    try:
        value = json.loads(response.read(16385))
        error = value.get('error', {})
        if not isinstance(error, dict):return {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
    result = {}
    codes = {'invalid_api_key', 'authentication_error', 'invalid_request_error',
             'context_length_exceeded', 'unsupported_parameter', 'invalid_value',
             'rate_limit_exceeded', 'model_not_found', 'content_filter'}
    params = {'max_tokens', 'max_completion_tokens', 'max_output_tokens', 'temperature',
              'response_format', 'reasoning_effort', 'tools', 'tool_choice',
              'messages', 'input', 'stream', 'stream_options', 'model', 'top_p'}
    if isinstance(error.get('code'),str) and error['code'] in codes:result['code'] = error['code']
    if isinstance(error.get('param'),str) and error['param'] in params:result['param'] = error['param']
    # Some compatible transports return only prose. Never return that text or
    # arbitrary parsed fields; these hints are classifications, not raw errors.
    message = error.get('message')
    if isinstance(message, str):
        lowered = message.lower()
        for phrase, label in [('unsupported parameter', 'unsupported_parameter'),
                              ('context length', 'context_length_exceeded'),
                              ('invalid json schema', 'invalid_json_schema'),
                              ('invalid api key', 'invalid_api_key')]:
            if phrase in lowered:
                result['message_class'] = label
                break
    return result


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


def response_diagnostic(value):
    """Only fixed finish labels and reported counts; never response text."""
    if not isinstance(value,dict):return {}
    value=value.get('response') if isinstance(value.get('response'),dict) else value
    result={}
    choices=value.get('choices')
    if isinstance(choices,list) and choices and isinstance(choices[0],dict):
        reason=choices[0].get('finish_reason')
        if reason in ('stop','length','tool_calls','function_call','content_filter'):
            result['finish_reason']=reason
    usage=value.get('usage')
    if isinstance(usage,dict):
        details=usage.get('completion_tokens_details',usage.get('output_tokens_details'))
        if isinstance(details,dict):
            tokens=details.get('reasoning_tokens')
            if type(tokens) is int and 0<=tokens<=CONTEXT_TOKENS:result['reasoning_tokens']=tokens
    return result


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, ledger, client_key, upstream_key,
                 *, upstream='https://api.meta.ai/v1', opener=None):
        self.ledger, self.client_key, self.upstream_key = ledger, client_key, upstream_key
        self.upstream = upstream.rstrip('/')
        self.open = opener or open_without_redirect
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
            if not value['data']:
                raise ValueError('Priced model is unavailable')
            self.server.ledger.confirm_auth()
            self.reply(200,value)
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403):self.server.ledger.block_auth()
            self.error(exc.code,'upstream_error','Model inventory HTTP '+str(exc.code))
        except Exception:
            self.error(502,'upstream_error','Model inventory unavailable')

    def do_POST(self):
        if not self.authenticated():
            return
        route = ROUTE.fullmatch(self.path)
        if not route or route[2] not in ('chat/completions','responses'):
            self.error(404,'request_refused','Unsupported gateway path')
            return
        if self.server.ledger.health()['upstream_auth_blocked']:
            self.error(503,'upstream_auth_blocked','Upstream authentication failed; paid work blocked until a successful model inventory')
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
        diagnostic={'protocol':protocol_request_shape(value)}
        metadata = {}
        headers_sent = False
        completed = False
        def complete():
            nonlocal completed
            if not completed:
                self.server.ledger.finish(request_id,usage=usage,status=status,error=error,diagnostic=diagnostic,metadata=metadata)
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
                metering_observe_metadata(metadata, headers=response.headers)
                self.server.ledger.observe_metadata(request_id, metadata)
                media_type=response.headers.get('Content-Type','').split(';',1)[0].strip().lower()
                stream = media_type == 'text/event-stream'
                diagnostic['protocol']['wire']='sse' if stream else 'json' if media_type=='application/json' else 'other'
                self.send_response(status)
                self.send_header('Content-Type','text/event-stream' if stream else 'application/json')
                self.send_header('X-Gateway-Request-ID',request_id)
                self.send_header('Connection','close')
                self.end_headers()
                self.close_connection = True
                headers_sent = True
                if stream:
                    total, event, disconnected = 0, [], False
                    def event_usage():
                        try:
                            parsed=json.loads(b'\n'.join(event))
                            protocol_observe_usage(diagnostic['protocol'],parsed)
                            metering_observe_metadata(metadata, value=parsed)
                            self.server.ledger.observe_metadata(request_id, metadata)
                            diagnostic.update(response_diagnostic(parsed))
                            return usage_from(parsed)
                        except (ValueError, TypeError, AttributeError):
                            return None
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Bounded inference transport timeout')
                        line = response.readline(MAX_RESPONSE_BYTES+1)
                        if not line:
                            # A complete final usage object can arrive without
                            # an SSE blank-line terminator. Preserve the wire
                            # bytes and still account for valid reported usage.
                            if event:usage = event_usage() or usage
                            break
                        total += len(line)
                        if total > MAX_RESPONSE_BYTES:
                            raise ValueError('Response exceeds the transport bound')
                        if line.startswith(b'data:'):
                            event.append(line[5:].strip())
                        if not line.strip() and event:
                            usage = event_usage() or usage
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
                        parsed=json.loads(data)
                        protocol_observe_usage(diagnostic['protocol'],parsed)
                        metering_observe_metadata(metadata, value=parsed)
                        usage = usage_from(parsed)
                        diagnostic.update(response_diagnostic(parsed))
                    except (ValueError,TypeError,AttributeError):
                        pass
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError,ConnectionResetError):
                        pass
                if usage is None:
                    error = 'missing_usage_sse' if stream else 'missing_usage_json'
        except urllib.error.HTTPError as exc:
            status, error = exc.code, 'upstream_http_'+str(exc.code)
            metering_observe_metadata(metadata, headers=exc.headers)
            upstream_diagnostic = upstream_error_diagnostic(exc)
            diagnostic.update(upstream_diagnostic)
            if upstream_diagnostic:error += ':'+':'.join(upstream_diagnostic[k] for k in sorted(upstream_diagnostic))
            if status in (401,403):self.server.ledger.block_auth()
            complete()
            if not headers_sent:
                self.reply(status, {'error':{'type':'upstream_error',
                    'message':'Upstream model HTTP '+str(status), **upstream_diagnostic},
                    'gateway_request_id':request_id})
        except Exception as exc:
            error = 'transport_'+type(exc).__name__
            complete()
            if not headers_sent:
                self.reply(502, {'error':{'type':'upstream_error','message':'Upstream transport failed; cost remains reserved'}, 'gateway_request_id':request_id})
        finally:
            complete()


# Accept only known opaque identifier shapes. In particular, do not persist
# arbitrary response fields, headers, prompts, or upstream error bodies.
_ID = re.compile(r'^(?:gen-[A-Za-z0-9-]{1,123}|req-[A-Za-z0-9-]{1,123}|chatcmpl-[A-Za-z0-9_-]{1,119}|resp_[A-Za-z0-9_-]{1,123}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$')


_TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens',
                 'total_tokens', 'reasoning_tokens', 'cached_tokens',
                 'cache_read_tokens', 'cache_write_tokens', 'audio_tokens', 'image_tokens',
                 'accepted_prediction_tokens', 'rejected_prediction_tokens')


_DETAILS = ('prompt_tokens_details', 'completion_tokens_details',
            'input_tokens_details', 'output_tokens_details')


_MAX = 1_048_576


def safe_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('upstream_request_id', 'generation_id'):
        v = value.get(key)
        if isinstance(v, str) and _ID.fullmatch(v):
            result[key] = v
    usage = value.get('usage')
    if isinstance(usage, dict):
        safe = {}
        for key, v in usage.items():
            # Keys are canonical paths assembled below, not arbitrary JSON.
            parts = key.split('.') if isinstance(key, str) else []
            if (len(parts) == 1 and parts[0] in _TOKEN_FIELDS or
                len(parts) == 2 and parts[0] in _DETAILS and parts[1] in _TOKEN_FIELDS):
                if type(v) is int and 0 <= v <= _MAX:
                    safe[key] = v
        if safe:
            result['usage'] = safe
    return result


def metering_observe_metadata(target, *, value=None, headers=None):
    candidate = {}
    if headers is not None:
        candidate['upstream_request_id'] = headers.get('X-Request-ID')
    if isinstance(value, dict):
        obj = value.get('response') if isinstance(value.get('response'), dict) else value
        candidate['generation_id'] = obj.get('id')
        usage = obj.get('usage')
        if isinstance(usage, dict):
            fields = {k: usage[k] for k in _TOKEN_FIELDS if k in usage}
            for group in _DETAILS:
                detail = usage.get(group)
                if isinstance(detail, dict):
                    fields.update({group+'.'+k: detail[k] for k in _TOKEN_FIELDS if k in detail})
            candidate['usage'] = fields
    candidate = safe_metadata(candidate)
    if 'usage' in candidate:
        target.setdefault('usage', {}).update(candidate.pop('usage'))
    target.update(candidate)


LABELS = {
    'tool_choice': ('absent','auto','none','required','function_object','other_string','other_object','invalid'),
    'tools': ('absent','array','invalid'),
    'response_format': ('absent','text','json_object','json_schema','other'),
    'wire': ('sse','json','other'),
}


TOKEN_FIELDS = ('prompt_tokens','completion_tokens','input_tokens','output_tokens','total_tokens')


TYPES = ('integer','number','boolean','null','string','array','object','other')


COUNTS = ('tool_count','json_values','usage_objects')


def safe_protocol(value):
    if not isinstance(value,dict):return {}
    result={key:value[key] for key,labels in LABELS.items() if value.get(key) in labels}
    for key in COUNTS:
        n=value.get(key)
        if type(n) is int and 0<=n<=1_048_576:result[key]=n
    for key in ('stream','top_usage','nested_usage'):
        if type(value.get(key)) is bool:result[key]=value[key]
    fields=value.get('usage_fields')
    if isinstance(fields,dict):
        result['usage_fields']={k:fields[k] for k in TOKEN_FIELDS if fields.get(k) in TYPES}
    return result


def protocol_request_shape(value):
    choice=value.get('tool_choice')
    if 'tool_choice' not in value:kind='absent'
    elif isinstance(choice,str):kind=choice if choice in ('auto','none','required') else 'other_string'
    elif isinstance(choice,dict):kind='function_object' if choice.get('type')=='function' else 'other_object'
    else:kind='invalid'
    tools=value.get('tools')
    result={'tool_choice':kind,'tools':'absent' if 'tools' not in value else 'array' if isinstance(tools,list) else 'invalid',
            'stream':value.get('stream',False),'response_format':'absent'}
    if isinstance(tools,list):result['tool_count']=len(tools)
    if 'response_format' in value:
        fmt=value['response_format']
        fmt=fmt.get('type') if isinstance(fmt,dict) else None
        result['response_format']=fmt if fmt in ('text','json_object','json_schema') else 'other'
    return safe_protocol(result)


def protocol_observe_usage(shape,value):
    shape['json_values']=shape.get('json_values',0)+1
    if not isinstance(value,dict):return
    nested=value.get('response')
    for key,parent in (('top_usage',value),('nested_usage',nested)):
        if not isinstance(parent,dict) or not isinstance(parent.get('usage'),dict):continue
        usage=parent['usage']
        shape[key]=True
        shape['usage_objects']=shape.get('usage_objects',0)+1
        fields=shape.setdefault('usage_fields',{})
        for field in TOKEN_FIELDS:
            if field not in usage:continue
            raw=usage[field]
            kind={int:'integer',float:'number',bool:'boolean',str:'string',list:'array',dict:'object',type(None):'null'}.get(type(raw),'other')
            fields[field]=kind


def serve_main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8099)
    parser.add_argument('--initialize-new-state',action='store_true',help='Create a genuinely new state directory and ledger')
    args = parser.parse_args(argv)
    upstream_key = os.environ.get('META')
    if not upstream_key:
        parser.error('Managed META binding is required; no credential fallback')
    if args.initialize_new_state:
        try:
            args.state.mkdir(mode=0o700)
        except FileExistsError:
            parser.error('New state directory already exists; refusing to initialize')
    elif not args.state.is_dir() or not (args.state/'client-token').is_file() or not (args.state/'ledger.sqlite3').is_file():
        parser.error('Existing state, client token and ledger are required')
    lock = (args.state/'server.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    token = args.state/'client-token'
    if args.initialize_new_state:
        fd = os.open(token,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as output:
            output.write(secrets.token_urlsafe(40))
    try:
        ledger = Ledger(args.state/'ledger.sqlite3', existing_only=not args.initialize_new_state)
    except (OSError, ValueError, __import__('sqlite3').Error):
        parser.error('Existing gateway ledger is unavailable or invalid')
    client_key = token.read_text().strip()
    if not client_key:
        parser.error('Existing gateway client token is empty')
    ledger.recover_abandoned()
    server = Server(('127.0.0.1',args.port),ledger,client_key,upstream_key)
    address = args.state/'address.json'
    address.write_text(json.dumps({'base':f'http://127.0.0.1:{server.server_port}'})+'\n')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lock.close()
