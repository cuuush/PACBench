"""Supermemory's published local server with supported v3 SDK and local embeddings."""
from __future__ import annotations
import atexit
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from pacbench.followups import save
from pacbench.gateway_client import validate_route


def start(root, env, model, base, key):
    validate_route(base, key)
    runtime=Path(__file__).resolve().parents[1]/'vendor/supermemory-local'
    binary=runtime/'supermemory-server-linux-x64'
    if not binary.exists():raise RuntimeError('Install verified Supermemory local server first')
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    directory=root/'supermemory-data';directory.mkdir()
    endpoint=f'http://127.0.0.1:{port}'
    # Only local embeddings and the shared spending gateway. Do not inherit
    # alternative LLM providers from the host or use a hosted Supermemory key.
    child={**os.environ, **env, 'PORT':str(port),'SUPERMEMORY_PORT':str(port),
        'SUPERMEMORY_DATA_DIR':str(directory),'SUPERMEMORY_DISABLE_TELEMETRY':'1',
        'OPENAI_API_KEY':key,'OPENAI_BASE_URL':base,'OPENAI_MODEL':model,
        'OPENAI_FAST_MODEL':model,'OPENAI_TEXT_MODEL':model,
        'SUPERMEMORY_EMBEDDING_PROVIDER':'openai',
        'SUPERMEMORY_EMBEDDING_MODEL':'nomic-embed-text',
        'SUPERMEMORY_EMBEDDING_DIMENSIONS':'768',
        'SUPERMEMORY_EMBEDDING_BASE_URL':'http://127.0.0.1:11435/v1'}
    for name in ('ANTHROPIC_API_KEY','GEMINI_API_KEY','GROQ_API_KEY','WORKERS_AI_API_KEY',
                 'CLOUDFLARE_ACCOUNT_ID','GOOGLE_VERTEX_PROJECT_ID','GOOGLE_VERTEX_LOCATION',
                 'SUPERMEMORY_API_KEY','SUPERMEMORY_BASE_URL'):
        child.pop(name,None)
    log_path=root/'supermemory-server.private.log'
    log=log_path.open('ab');os.chmod(log_path,0o600)
    process=subprocess.Popen([str(binary)],cwd=root,env=child,stdin=subprocess.DEVNULL,
                             stdout=log,stderr=log);log.close()
    def stop():
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    atexit.register(stop)
    deadline=time.monotonic()+120
    native_key=None
    while time.monotonic()<deadline and process.poll() is None:
        output=re.sub(r'\x1b\[[0-9;]*m','',log_path.read_text(errors='replace'))
        match=re.search(r'api key\s+([A-Za-z0-9_.-]+)',output,re.I)
        if match:
            native_key=match.group(1);break
        time.sleep(.2)
    receipt={'route':'Published Supermemory local server; documented OpenAI-compatible inference and embeddings',
        'installation':json.loads((runtime/'installation.json').read_text()),
        'endpoint':endpoint,'pid':process.pid,'model':model,'native_api_key_received':bool(native_key),
        'embedding':'local Ollama nomic-embed-text','starter_facts':False,
        'network':'Run-owned endpoint in the private execution environment; no public port published'}
    save(root/'native-server-setup.json',receipt)
    if not native_key:
        stop();raise RuntimeError('Supermemory startup did not produce its native API key; inspect private log')
    # README manual endpoint first, then the key emitted by real first boot.
    save(root/'hermes-home/supermemory.json',{'base_url':endpoint,'container_tag':directory.name+'-'+root.parent.name+'-'+root.name})
    env.update(SUPERMEMORY_BASE_URL=endpoint,SUPERMEMORY_API_KEY=native_key)
    return stop
