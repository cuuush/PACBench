"""Client-side HTTP instrumentation for direct native OpenRouter calls.

No listener or forwarding connection exists. This process's original HTTP client
connects to OpenRouter after a short durable reservation. Native streams are
passed through as read; an interrupted stream retains its cost bound.
"""
import asyncio
import json
import os
import urllib.request
import urllib.error
from weakref import WeakSet
from urllib.parse import urlsplit

from .direct_accounting import live_budget, project_from_authorization, VERSION

_installed=False
_httpx_wrappers=WeakSet()
_MAX_RESPONSE=32*1024*1024


def admitted(url,headers,body,*,wait=True):
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
    if not body and parsed.path in {'/api/v1/models','/api/v1/key','/api/v1/credits'}:
        return {'headers':{**dict(headers),'Authorization':auth},'free':True}
    if parsed.path!='/api/v1/chat/completions' or not body:
        raise RuntimeError('Unsupported direct model endpoint')
    budget=live_budget(project=project)
    receipt=budget.reserve(project,json.loads(body),wait=False) if not wait else budget.reserve(project,json.loads(body))
    from .api_input_trace import record as record_api_input
    record_api_input(project,receipt)
    from .api_input_trace import record_memory
    record_memory(project,receipt)
    head={k:v for k,v in dict(headers).items() if k.lower() not in {'authorization','content-length'}}
    head['Authorization']=auth
    data=json.dumps(receipt['request'],ensure_ascii=False).encode()
    head['Content-Length']=str(len(data))
    return {'headers':head,'body':data,'id':receipt['id'],'budget':budget}


class Capture:
    def __init__(self,receipt,status,encoding='',headers=None):
        self.receipt=receipt;self.status=status;self.chunks=[];self.size=0;self.closed=False;self.encoding=encoding;self.headers=headers
        from .response_receipts import EventReader, sanitized
        self._last_observation=None;self._decompressor=None;self._progress_disabled=False
        def observed(event):
            safe=sanitized(event)
            if safe and safe!=self._last_observation:
                self.receipt['budget'].observe_response(self.receipt['id'],self.status,event)
                self._last_observation=safe
        self._progress=EventReader(observed)
        if headers:self.receipt['budget'].observe_response(self.receipt['id'],status,headers=headers)

    def add(self,data):
        self.size+=len(data)
        if self.size>_MAX_RESPONSE:
            self.finish();raise RuntimeError('Direct response exceeds accounting capture limit')
        self.chunks.append(data)
        decoded=data
        if self.encoding:
            try:
                if self._decompressor is None:
                    if self.encoding in {'gzip','deflate'}:
                        import zlib
                        self._decompressor=zlib.decompressobj(16+zlib.MAX_WBITS if self.encoding=='gzip' else zlib.MAX_WBITS)
                    elif self.encoding=='br':self._decompressor=__import__('brotli').Decompressor()
                    else:self._progress_disabled=True
                if not self._progress_disabled:
                    decoded=self._decompressor.process(data) if self.encoding=='br' else self._decompressor.decompress(data)
            except Exception:self._progress_disabled=True
        if not self._progress_disabled:self._progress.feed(decoded)

    def finish(self,complete=False):
        if not self.closed:
            data=b''.join(self.chunks)
            try:
                if self.encoding=='gzip':data=__import__('gzip').decompress(data)
                elif self.encoding=='deflate':data=__import__('zlib').decompress(data)
                elif self.encoding=='br':data=__import__('brotli').decompress(data)
                elif self.encoding:data=b''
            except Exception:data=b''
            self.receipt['budget'].settle(self.receipt['id'],self.status,data,headers=self.headers,body_complete=complete)
            self.closed=True


class UrllibResponse:
    def __init__(self,raw,receipt):
        self.raw=raw;self.capture=Capture(receipt,raw.status,headers=dict(raw.headers.items()))
    def __getattr__(self,name):return getattr(self.raw,name)
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def read(self,*args):
        try:data=self.raw.read(*args)
        except BaseException:self.capture.finish();raise
        self.capture.add(data)
        if not args or args[0] in (-1,None) or not data and args[0]!=0:self.capture.finish(complete=True)
        return data
    def read1(self,*args):
        data=self.raw.read1(*args);self.capture.add(data)
        if not data and (not args or args[0]!=0):self.capture.finish(complete=True)
        return data
    def close(self):
        try:self.raw.close()
        finally:self.capture.finish()


def before_send(receipt):
    from .live_dispatch import enabled
    if receipt and not receipt.get('free') and enabled():
        receipt['budget'].mark_dispatch_possible(receipt['id'])


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
        before_send(receipt)
        try:response=original(self,target,timeout=timeout)
        except urllib.error.HTTPError as exc:
            if receipt.get('free'):raise
            if exc.code in (402,429) and hasattr(receipt['budget'],'retry_delay'):
                raw=exc.read();receipt['budget'].settle(receipt['id'],exc.code,raw,headers=dict(exc.headers.items()),body_complete=True)
                delay=receipt['budget'].retry_delay(receipt['id'])
                if delay is not None:
                    __import__('time').sleep(delay);return opened(self,request,timeout=timeout)
                raise urllib.error.HTTPError(exc.url,exc.code,exc.msg,exc.headers,__import__('io').BytesIO(raw)) from exc
            # Keep HTTPError behavior and its original body available to callers.
            # Settlement happens only as authenticated bytes are actually read.
            wrapped=UrllibResponse(exc,receipt)
            raise urllib.error.HTTPError(exc.url,exc.code,exc.msg,exc.headers,wrapped) from exc
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
    # SDK aliases and repeated setup can expose the same client methods.
    # Wrapping twice would admit the translated upstream request a second time.
    if httpx.Client.send in _httpx_wrappers and httpx.AsyncClient.send in _httpx_wrappers:
        return
    class SyncStream(httpx.SyncByteStream):
        def __init__(self,raw,capture):self.raw=raw;self.capture=capture
        def __iter__(self):
            try:
                for chunk in self.raw:
                    self.capture.add(chunk);yield chunk
                self.capture.finish(complete=True)
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
                self.capture.finish(complete=True)
            finally:self.capture.finish()
        async def aclose(self):
            try:await self.raw.aclose()
            finally:self.capture.finish()
    send=httpx.Client.send;asend=httpx.AsyncClient.send
    def prepared(request,body,*,wait=True):
        receipt=admitted(request.url,request.headers,body,wait=wait)
        if receipt is None:return request,None
        fresh=httpx.Request(request.method,request.url,headers=receipt['headers'],
            content=receipt.get('body'),extensions=request.extensions)
        return fresh,receipt
    def observed(response,receipt,stream_class):
        if receipt is None or receipt.get('free'):return response
        capture=Capture(receipt,response.status_code,headers=dict(response.headers.items()))
        if response.is_stream_consumed:
            capture.add(response.content);capture.finish(complete=True)
        else:
            capture.encoding=response.headers.get('content-encoding','')
            response.stream=stream_class(response.stream,capture)
        return response
    def sending(self,request,**kwargs):
        if request.url.host!='openrouter.ai':return send(self,request,**kwargs)
        fresh,receipt=prepared(request,request.read())
        kwargs['follow_redirects']=False
        eager=not kwargs.get('stream',False)
        # Return headers to the accounting observer before HTTPX's eager body
        # read. Preserve the caller's eager/streaming behavior at this boundary.
        kwargs['stream']=True
        before_send(receipt)
        try:response=send(self,fresh,**kwargs)
        except BaseException:
            if receipt and not receipt.get('free'):receipt['budget'].settle(receipt['id'],0)
            raise
        response=observed(response,receipt,SyncStream)
        if response.status_code in (402,429) and receipt and hasattr(receipt['budget'],'retry_delay'):
            response.read();delay=receipt['budget'].retry_delay(receipt['id'])
            if delay is not None:
                response.close();__import__('time').sleep(delay);return sending(self,request,**{**kwargs,'stream':not eager})
        if eager:
            try:response.read()
            except BaseException:response.close();raise
        return response
    async def asending(self,request,**kwargs):
        if request.url.host!='openrouter.ai':return await asend(self,request,**kwargs)
        body=await request.aread()
        from .observational_receipts import enabled as observing
        if observing():
            from .canary_capability import CanaryBusy
            loop=asyncio.get_running_loop()
            started=loop.time()
            while True:
                try:
                    fresh,receipt=prepared(request,body,wait=False)
                    break
                except CanaryBusy as exc:
                    if loop.time()-started>=exc.queue_timeout_s:
                        raise RuntimeError('Current execution queue deadline; no request dispatched') from None
                    # An active async request on this loop must be able to settle.
                    await asyncio.sleep(.05)
        else:
            fresh,receipt=prepared(request,body)
        kwargs['follow_redirects']=False
        eager=not kwargs.get('stream',False);kwargs['stream']=True
        before_send(receipt)
        try:response=await asend(self,fresh,**kwargs)
        except BaseException:
            if receipt and not receipt.get('free'):receipt['budget'].settle(receipt['id'],0)
            raise
        response=observed(response,receipt,AsyncStream)
        if response.status_code in (402,429) and receipt and hasattr(receipt['budget'],'retry_delay'):
            await response.aread();delay=receipt['budget'].retry_delay(receipt['id'])
            if delay is not None:
                await response.aclose();await __import__('asyncio').sleep(delay);return await asending(self,request,**{**kwargs,'stream':not eager})
        if eager:
            try:await response.aread()
            except BaseException:await response.aclose();raise
        return response
    if send not in _httpx_wrappers:
        _httpx_wrappers.add(sending)
        httpx.Client.send=sending
    if asend not in _httpx_wrappers:
        _httpx_wrappers.add(asending)
        httpx.AsyncClient.send=asending
