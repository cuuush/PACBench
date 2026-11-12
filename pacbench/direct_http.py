"""Client-side HTTP instrumentation for direct native OpenRouter calls.

No listener or forwarding connection exists. This process's original HTTP client
connects to OpenRouter after a short durable reservation. Native streams are
passed through as read; an interrupted stream retains its cost bound.
"""
import json
import os
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from .direct_accounting import live_budget, project_from_authorization, VERSION

_installed=False
_MAX_RESPONSE=32*1024*1024


def admitted(url,headers,body):
    if os.environ.get('PACBENCH_DIRECT')!='1':return None
    parsed=urlsplit(str(url))
    if parsed.hostname!='openrouter.ai':
        auth=headers.get('authorization',headers.get('Authorization'))
        if auth and auth=='Bearer '+os.environ.get('PACBENCH_DIRECT_UPSTREAM_KEY',''):
            raise RuntimeError('Upstream credential destination refused')
        return None
    if parsed.scheme!='https' or parsed.netloc!='openrouter.ai' or parsed.query or parsed.fragment:
        raise RuntimeError('Unexpected direct OpenRouter URL')
    supplied=headers.get('authorization',headers.get('Authorization'))
    # Native Hermes metadata probes use this public, unmetered inventory without
    # credentials. Do not attach the upstream key or weaken paid admission.
    if not supplied and not body and parsed.path=='/api/v1/models':
        return {'headers':dict(headers),'free':True}
    project=project_from_authorization(supplied)
    if not project:raise RuntimeError('Unaccounted OpenRouter credential refused')
    auth='Bearer '+os.environ['PACBENCH_DIRECT_UPSTREAM_KEY']
    if not body and parsed.path in {'/api/v1/models','/api/v1/key'}:
        return {'headers':{**dict(headers),'Authorization':auth},'free':True}
    if parsed.path!='/api/v1/chat/completions' or not body:
        raise RuntimeError('Unsupported direct model endpoint')
    budget=live_budget();receipt=budget.reserve(project,json.loads(body))
    from .api_input_trace import record as record_api_input
    record_api_input(project,receipt)
    head={k:v for k,v in dict(headers).items() if k.lower() not in {'authorization','content-length'}}
    head['Authorization']=auth
    data=json.dumps(receipt['request'],ensure_ascii=False).encode()
    head['Content-Length']=str(len(data))
    return {'headers':head,'body':data,'id':receipt['id'],'budget':budget}


class Capture:
    def __init__(self,receipt,status,encoding=''):
        self.receipt=receipt;self.status=status;self.chunks=[];self.size=0;self.closed=False;self.encoding=encoding

    def add(self,data):
        self.size+=len(data)
        if self.size>_MAX_RESPONSE:
            self.finish();raise RuntimeError('Direct response exceeds accounting capture limit')
        self.chunks.append(data)

    def finish(self):
        if not self.closed:
            data=b''.join(self.chunks)
            try:
                if self.encoding=='gzip':data=__import__('gzip').decompress(data)
                elif self.encoding=='deflate':data=__import__('zlib').decompress(data)
                elif self.encoding=='br':data=__import__('brotli').decompress(data)
                elif self.encoding:data=b''
            except Exception:data=b''
            self.receipt['budget'].settle(self.receipt['id'],self.status,data)
            self.closed=True


class UrllibResponse:
    def __init__(self,raw,receipt):
        self.raw=raw;self.capture=Capture(receipt,raw.status)
    def __getattr__(self,name):return getattr(self.raw,name)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def read(self,*args):
        try:data=self.raw.read(*args)
        except BaseException:self.capture.finish();raise
        self.capture.add(data)
        if not args or args[0] in (-1,None) or not data:self.capture.finish()
        return data
    def read1(self,*args):
        data=self.raw.read1(*args);self.capture.add(data)
        if not data:self.capture.finish()
        return data
    def close(self):
        try:self.raw.close()
        finally:self.capture.finish()


def install():
    global _installed
    if _installed:return
    _installed=True
    original=urllib.request.OpenerDirector.open
    def opened(self,fullurl,data=None,timeout=__import__('socket')._GLOBAL_DEFAULT_TIMEOUT):
        request=fullurl if isinstance(fullurl,urllib.request.Request) else urllib.request.Request(fullurl,data=data)
        receipt=admitted(request.full_url,dict(request.header_items()),request.data if data is None else data)
        if receipt is None:return original(self,fullurl,data,timeout)
        target=urllib.request.Request(request.full_url,data=receipt.get('body'),headers=receipt['headers'],method=request.get_method())
        try:response=original(self,target,timeout=timeout)
        except BaseException as exc:
            if not receipt.get('free'):receipt['budget'].settle(receipt['id'],getattr(exc,'code',0))
            raise
        return response if receipt.get('free') else UrllibResponse(response,receipt)
    urllib.request.OpenerDirector.open=opened
    import importlib
    for name in ('httpx','httpx2'):
        try:client_module=importlib.import_module(name)
        except ImportError:continue
        _install_httpx(client_module)


def _install_httpx(httpx):
    class SyncStream(httpx.SyncByteStream):
        def __init__(self,raw,capture):self.raw=raw;self.capture=capture
        def __iter__(self):
            try:
                for chunk in self.raw:
                    self.capture.add(chunk);yield chunk
            finally:self.capture.finish()
        def close(self):
            try:self.raw.close()
            finally:self.capture.finish()
    class AsyncStream(httpx.AsyncByteStream):
        def __init__(self,raw,capture):self.raw=raw;self.capture=capture
        async def __aiter__(self):
            try:
                async for chunk in self.raw:
                    self.capture.add(chunk);yield chunk
            finally:self.capture.finish()
        async def aclose(self):
            try:await self.raw.aclose()
            finally:self.capture.finish()
    send=httpx.Client.send;asend=httpx.AsyncClient.send
    def prepared(request,body):
        receipt=admitted(request.url,request.headers,body)
        if receipt is None:return request,None
        fresh=httpx.Request(request.method,request.url,headers=receipt['headers'],
            content=receipt.get('body'),extensions=request.extensions)
        return fresh,receipt
    def observed(response,receipt,stream_class):
        if receipt is None or receipt.get('free'):return response
        capture=Capture(receipt,response.status_code)
        if response.is_stream_consumed:
            capture.add(response.content);capture.finish()
        else:
            capture.encoding=response.headers.get('content-encoding','')
            response.stream=stream_class(response.stream,capture)
        return response
    def sending(self,request,**kwargs):
        if request.url.host!='openrouter.ai':return send(self,request,**kwargs)
        fresh,receipt=prepared(request,request.read())
        kwargs['follow_redirects']=False
        try:response=send(self,fresh,**kwargs)
        except BaseException:
            if receipt and not receipt.get('free'):receipt['budget'].settle(receipt['id'],0)
            raise
        return observed(response,receipt,SyncStream)
    async def asending(self,request,**kwargs):
        if request.url.host!='openrouter.ai':return await asend(self,request,**kwargs)
        fresh,receipt=prepared(request,await request.aread())
        kwargs['follow_redirects']=False
        try:response=await asend(self,fresh,**kwargs)
        except BaseException:
            if receipt and not receipt.get('free'):receipt['budget'].settle(receipt['id'],0)
            raise
        return observed(response,receipt,AsyncStream)
    httpx.Client.send=sending;httpx.AsyncClient.send=asending

