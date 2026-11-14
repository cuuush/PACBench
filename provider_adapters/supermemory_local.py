"""Supermemory's published local server with supported v3 SDK and local embeddings."""
from __future__ import annotations
import atexit
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from pacbench.followups import save
from pacbench.gateway_client import validate_route



def plan_config(root, model, base, key, *, port):
    """Build native environment overrides without launching a server."""
    directory=root/'supermemory-data'
    return {'PORT':str(port),'SUPERMEMORY_PORT':str(port),
        'SUPERMEMORY_DATA_DIR':str(directory),'SUPERMEMORY_DISABLE_TELEMETRY':'1',
        'OPENAI_API_KEY':key,'OPENAI_BASE_URL':base,'OPENAI_MODEL':model,
        'OPENAI_FAST_MODEL':model,'OPENAI_TEXT_MODEL':model,
        'SUPERMEMORY_EMBEDDING_PROVIDER':'openai',
        'SUPERMEMORY_EMBEDDING_MODEL':'nomic-embed-text',
        'SUPERMEMORY_EMBEDDING_DIMENSIONS':'768',
        'SUPERMEMORY_EMBEDDING_BASE_URL':'http://127.0.0.1:11435/v1'}


def start(root, env, model, base, key, *, resume=False):
    validate_route(base, key)
    from pacbench.import_lifecycle import scan,cleanup
    if scan(root,'supermemory-start-inspection')[0]:raise RuntimeError('Previous run-owned Supermemory processes are still alive')
    runtime=Path(__file__).resolve().parents[1]/'vendor/supermemory-local'
    binary=runtime/'supermemory-server-linux-x64'
    if not binary.exists():raise RuntimeError('Install verified Supermemory local server first')
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    directory=root/'supermemory-data'
    if resume and not (directory/'data').is_file():raise ValueError('No persisted native Supermemory store to reopen')
    directory.mkdir(exist_ok=resume)
    from pacbench.openrouter_direct import active
    if active():
        # The shipped executable loads bunfig.toml preloads. Its older bundled
        # runtime ignores BUN_OPTIONS --preload; verify the actual entrypoint.
        hook=Path(__file__).resolve().parents[1]/'pacbench/direct_runtime/fetch.cjs'
        (root/'bunfig.toml').write_text('preload = ['+json.dumps(str(hook))+']\n')
    endpoint=f'http://127.0.0.1:{port}'
    # Only local embeddings and the shared spending gateway. Do not inherit
    # alternative LLM providers from the host or use a hosted Supermemory key.
    child={**os.environ, **env, **plan_config(root,model,base,key,port=port)}
    for name in ('ANTHROPIC_API_KEY','GEMINI_API_KEY','GROQ_API_KEY','WORKERS_AI_API_KEY',
                 'CLOUDFLARE_ACCOUNT_ID','GOOGLE_VERTEX_PROJECT_ID','GOOGLE_VERTEX_LOCATION',
                 'SUPERMEMORY_API_KEY','SUPERMEMORY_BASE_URL'):
        child.pop(name,None)
    log_path=root/'supermemory-server.private.log'
    log_offset=log_path.stat().st_size if log_path.exists() else 0
    log=log_path.open('ab');os.chmod(log_path,0o600)
    process=subprocess.Popen([str(binary)],cwd=root,env=child,stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log);log.close()
    def stop():
        owned,_=scan(root,'supermemory-native-stop')
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        stopped=cleanup(root,'supermemory-native-stop',known=owned)
        save(root/'native-service-stop.json',stopped)
    atexit.register(stop)
    deadline=time.monotonic()+300
    native_key=None
    while time.monotonic()<deadline and process.poll() is None:
        output=re.sub(r'\x1b\[[0-9;]*m','',log_path.read_text(errors='replace')[log_offset:])
        match=re.search(r'api key\s+([A-Za-z0-9_.-]+)',output,re.I)
        if match:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=1):pass
            except OSError:
                time.sleep(.2);continue
            candidate=match.group(1)
            request=urllib.request.Request(endpoint+'/v3/documents/list',
                data=json.dumps({'containerTags':[],'includeContent':False,'page':1,'limit':1}).encode(),
                headers={'Authorization':'Bearer '+candidate,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=2) as response:inventory=json.load(response)
                if not isinstance(inventory.get('memories'),list):raise ValueError('Native inventory unavailable')
            except (OSError,ValueError):time.sleep(.2);continue
            native_key=candidate;break
        time.sleep(.2)
    receipt={'route':'Published Supermemory local server; documented OpenAI-compatible inference and embeddings',
        'installation':json.loads((runtime/'installation.json').read_text()),
        'endpoint':endpoint,'pid':process.pid,'model':model,'native_api_key_received':bool(native_key),
        'readiness':'Authenticated native document inventory; TCP listening alone is insufficient',
        'embedding':'local Ollama nomic-embed-text','starter_facts':False,'resumed_store':resume,
        'network':'Run-owned endpoint in the private execution environment; no public port published'}
    save(root/'native-server-setup.json',receipt)
    save(root/'document-ingestion-config.json',{
        'dreaming':'instant',
        'route':'Native SDK documents.add documented per-document processing mode',
        'reason':'Each document already contains a complete nightly batch; do not defer it for dynamic cross-document grouping',
        'native_default':'dynamic','model_processing_overrides':False})
    if not native_key:
        stop();raise RuntimeError('Supermemory startup did not produce its native API key; inspect private log')
    # README manual endpoint first, then the key emitted by real first boot.
    save(root/'hermes-home/supermemory.json',{'base_url':endpoint,'container_tag':directory.name+'-'+root.parent.name+'-'+root.name})
    from pacbench.prefetch_provenance import VERSION
    declaration={'protocol':VERSION,'provider':'supermemory','state':'unverified',
        'reason':'Native v5 default hybrid retrieval can return document chunks; the plugin formatter merges chunk and memory text and omits their type. Independent passage provenance is pending.'}
    save(root/'prefetch-provenance.json',declaration)
    env['PACBENCH_PREFETCH_PROVENANCE']=json.dumps(declaration)
    env.update(SUPERMEMORY_BASE_URL=endpoint,SUPERMEMORY_API_KEY=native_key)
    return stop
