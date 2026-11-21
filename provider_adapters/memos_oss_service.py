"""Instance-owned MemTensor MemoryOS OSS server for a nightly file arm."""
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.request import urlopen
from uuid import uuid4


REVISION = '3a16e5fe3048a209f722806f4123fa2e533588fd'
IMAGE_NEO4J = 'neo4j@sha256:eef89955a0ff6ce578ec5fb264333818bb2f56e169bcb8dda5bcadad1fc48893'
IMAGE_QDRANT = 'qdrant/qdrant@sha256:31407c0e8e32eb771b71718f1a4772e2ad47a07557917b21ac96792f40eb8007'
TOKENIZER = '607a30d783dfa663caf39e06633721c8d4cfcd7e'
TOKENIZER_SHA = '8414cab924d8b9b33013f0d221c5862f365ee9be39c5c2bfae8a5a9e970478a6'


def _wait(probe, *, process=None):
    deadline = time.monotonic()+120
    while time.monotonic()<deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f'MemOS OSS ended during initialization ({process.returncode})')
        try:
            return probe()
        except OSError:
            time.sleep(.2)
    raise TimeoutError('MemOS OSS private service did not reach TCP/API readiness')


def _listen(port):
    with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
        return client.getpeername()


def _container(stack, root, name, image, port, configuration):
    name = 'pacbench-memos-'+sha256(str(root).encode()).hexdigest()[:14]+'-'+name
    extra = [part for key,val in configuration.items() for part in ('-e',f'{key}={val}')]
    subprocess.run(['docker','run','-d','--rm','--pull','never','--name',name,'-p',f'127.0.0.1::{port}',
                    *extra,image],stdout=subprocess.DEVNULL,check=True)
    stack.callback(subprocess.run,['docker','stop','--time','15',name],stdout=subprocess.DEVNULL,check=True)
    published=subprocess.check_output(['docker','port',name,f'{port}/tcp'],text=True).strip()
    if not published.startswith('127.0.0.1:'):
        raise RuntimeError('MemOS OSS database endpoint is outside loopback')
    number=int(published.removeprefix('127.0.0.1:'))
    _wait(lambda:_listen(number))
    return name,number


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def backend(root, source, qualification, model, model_base, model_marker):
    from pacbench.gateway_reuse import atomic
    from pacbench.live_dispatch import process_identity
    from pacbench.openrouter_direct import runtime_environment

    service=root/'memos-oss-service'
    service.mkdir(mode=0o700)
    for name in ('home','download'):
        (service/name).mkdir(mode=0o700)
    expected=qualification/'huggingface/hub/models--gpt2/snapshots'/TOKENIZER/'tokenizer.json'
    if sha256(expected.read_bytes()).hexdigest()!=TOKENIZER_SHA:
        raise ValueError('MemOS stock GPT-2 tokenizer differs')
    if (subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=REVISION
            or subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip()):
        raise ValueError('MemOS OSS source checkout differs from the stock release')
    public_docs = {}
    pulls = []
    lock = threading.Lock()

    class Files(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            with lock:
                expected_data=public_docs.get(self.path)
            if expected_data is None:
                self.send_error(404)
                return
            name,data=expected_data
            self.send_response(200)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            with lock:
                pulls.append((name,sha256(data).hexdigest()))

    with ExitStack() as stack:
        files=stack.enter_context(ThreadingHTTPServer(('127.0.0.1',0),Files))
        threading.Thread(target=files.serve_forever,daemon=True).start()
        stack.callback(files.shutdown)
        local_secret=uuid4().hex
        graph,graph_port=_container(stack,root,'graph',IMAGE_NEO4J,7687,
            {'NEO4J_AUTH':'neo4j/'+local_secret,'NEO4J_server_memory_heap_max__size':'640m',
             'NEO4J_server_memory_pagecache_size':'192m'})
        vector,vector_port=_container(stack,root,'vector',IMAGE_QDRANT,6333,{})
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0))
            port=reserved.getsockname()[1]
        auth=uuid4().hex
        environment={k:v for k,v in os.environ.items() if k in {
            'PATH','LANG','LC_ALL','HTTPS_PROXY','HTTP_PROXY','NO_PROXY',
            'no_proxy','https_proxy','http_proxy','SSL_CERT_FILE','SSL_CERT_DIR',
        } or k.startswith('PACBENCH_')}
        environment.update(HOME=str(service/'home'),PYTHONPATH=str(source/'src'),PYTHONDONTWRITEBYTECODE='1',
            HF_HOME=str(qualification/'huggingface'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
            FILE_LOCAL_PATH=str(service/'download'), QDRANT_HOST='127.0.0.1',QDRANT_PORT=str(vector_port),
            NEO4J_BACKEND='neo4j-community',NEO4J_URI=f'bolt://127.0.0.1:{graph_port}',NEO4J_USER='neo4j',
            NEO4J_PASSWORD=local_secret,NEO4J_DB_NAME='neo4j',MOS_NEO4J_SHARED_DB='true',
            API_KEY=auth,OPENAI_API_KEY=model_marker,OPENAI_API_BASE=model_base,
            MOS_CHAT_MODEL=model,MEMRADER_MODEL=model,MEMRADER_API_KEY=model_marker,
            MEMRADER_API_BASE=model_base,MEMREADER_GENERAL_MODEL=model,
            MOS_EMBEDDER_MODEL='nomic-embed-text',MOS_EMBEDDER_BACKEND='universal_api',
            MOS_EMBEDDER_API_BASE='http://127.0.0.1:11435/v1',MOS_EMBEDDER_API_KEY='local-embedding',
            EMBEDDING_DIMENSION='768',MOS_RERANKER_BACKEND='cosine_local',
            ENABLE_INTERNET='false',ENABLE_CHAT_API='false',MEMSCHEDULER_USE_REDIS_QUEUE='false')
        # Exclude Hermes checkpoint state from the physical provider clock.
        runtime_environment(environment)
        stream=stack.enter_context((root/'memos-service.private.log').open('w'))
        proc=subprocess.Popen([str(qualification/'venv/bin/python'),'-m','uvicorn',
            'memos.api.server_api:app','--host','127.0.0.1','--port',str(port)],
            cwd=service,env=environment,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream)
        stack.callback(_stop,proc)
        endpoint=f'http://127.0.0.1:{port}'
        def health():
            with urlopen(endpoint+'/health',timeout=2) as response:
                return json.load(response)
        if _wait(health,process=proc)['status']!='healthy':
            raise RuntimeError('Stock MemOS OSS health response differs')
        native={'endpoint':endpoint,'process':process_identity(proc.pid),'revision':REVISION,
                'neo4j':IMAGE_NEO4J,'qdrant':IMAGE_QDRANT,'containers':[graph,vector],
                'tokenizer_sha256':TOKENIZER_SHA,'scheduler':'local in-memory','internet_search':False}
        atomic(root/'native-server-setup.json',native)

        def release(document):
            path='/released/'+document.filename
            raw=document.text.encode()
            if '/' in document.filename or sha256(raw).hexdigest()!=document.sha256:
                raise ValueError('MemOS OSS staged file does not match its frozen hash')
            with lock:
                if path in public_docs:
                    raise ValueError('Refuse to publish the same MemOS nightly file twice')
                public_docs[path]=(document.filename,raw)
            return f'http://127.0.0.1:{files.server_port}'+path

        yield {**native,'user':str(uuid4()),'local_api_token':auth},release,pulls

    if proc.poll() is None:
        raise RuntimeError('Stock MemOS OSS owned processes remained after teardown')
    # Docker's auto-removal can finish just after a successful docker stop.
    deadline=time.monotonic()+20
    while True:
        containers=set(subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines())
        if not containers.intersection({graph,vector}):
            break
        if time.monotonic()>=deadline:
            raise RuntimeError('Stock MemOS OSS run-owned Docker services remained after teardown')
        time.sleep(.2)
    try:
        _listen(port)
    except ConnectionRefusedError:
        atomic(root/'native-service-stop.json',{'state':'verified-after-exit','process':native['process']})
    else:
        raise RuntimeError('MemOS OSS native endpoint remained open')
