"""Process-local transport for the stock Supermemory Week executable rehearsal."""
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


INSTALLED = False
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
TOKEN = 'supermemory-offline-fixture'


def install():
    import httpx

    global INSTALLED
    if INSTALLED:
        return
    INSTALLED = True
    origin = os.environ['SM_OFFLINE_MODEL_ORIGIN']
    assert urlsplit(origin).hostname == '127.0.0.1'
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo

    def require_local(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in LOOPBACK:
            raise OSError('Offline Supermemory transport permits only loopback connections')

    def local_connect(sock, address):
        require_local(sock, address)
        return connect(sock, address)

    def local_connect_ex(sock, address):
        require_local(sock, address)
        return connect_ex(sock, address)

    def local_resolve(host, *args, **kwargs):
        if host not in LOOPBACK | {None}:
            raise OSError('Offline Supermemory transport permits only loopback DNS')
        return resolve(host, *args, **kwargs)

    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex
    socket.getaddrinfo = local_resolve
    sync = httpx.HTTPTransport.handle_request
    async_ = httpx.AsyncHTTPTransport.handle_async_request

    def redirect(request):
        if request.url.host != 'openrouter.ai':
            return request
        if (request.url.scheme, request.url.path) not in (
                ('https', '/api/v1/models'), ('https', '/api/v1/chat/completions')):
            raise ValueError('Unexpected offline model request path')
        headers = dict(request.headers)
        assert headers.pop('authorization') == 'Bearer ' + TOKEN
        headers['host'] = urlsplit(origin).netloc
        headers['x-offline-fixture-sender'] = 'hermes'
        return httpx.Request(request.method, origin + request.url.path, headers=headers, stream=request.stream,
                             extensions=request.extensions)

    def handle(self, request):
        return sync(self, redirect(request))

    async def handle_async(self, request):
        return await async_(self, redirect(request))

    httpx.HTTPTransport.handle_request = handle
    httpx.AsyncHTTPTransport.handle_async_request = handle_async
