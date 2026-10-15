"""Recommended manual self-hosting: local pgvector and unmodified Honcho API/deriver."""
from __future__ import annotations
import atexit
import hashlib
import json
import os
import socket
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from pacbench.followups import save
from pacbench.gateway_client import validate_route


def start(root,env,model,base,key):
    validate_route(base,key)
    source=Path(__file__).resolve().parents[1]/'vendor/honcho-local'
    python=source/'.venv/bin/python'
    if not python.exists():raise RuntimeError('Install the Honcho server using its documented uv sync first')
    ports=[]
    for _ in range(2):
        with socket.socket() as s:s.bind(('127.0.0.1',0));ports.append(s.getsockname()[1])
    api_port,db_port=ports
    identity=hashlib.sha256(str(root).encode()).hexdigest()[:12]
    name='pacbench-honcho-db-'+identity
    dbdir=root/'honcho-db';dbdir.mkdir()
    docker=['docker','--host=unix:///var/run/docker.sock']
    docker_env={k:v for k,v in os.environ.items() if k not in {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    proc=subprocess.run([*docker,'run','--rm','-d','--name',name,
        '-p',f'127.0.0.1:{db_port}:5432','-e','POSTGRES_PASSWORD=postgres',
        '-v',f'{dbdir}:/var/lib/postgresql/data','pgvector/pgvector:pg15'],
        env=docker_env,capture_output=True,text=True,timeout=60)
    processes=[]
    def stop():
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=15)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
        subprocess.run([*docker,'stop',name],env=docker_env,capture_output=True,timeout=30)
    atexit.register(stop)
    if proc.returncode:raise RuntimeError('Honcho local database startup failed: '+proc.stderr)
    config={'transport':'openai','model':model,'overrides':{'base_url':base,'api_key_env':'PACBENCH_MEMORY_API_KEY'}}
    child={**os.environ,**env,'PYTHONPATH':str(source), 'HONCHO_CONFIG_TOML_DISABLED':'1',
        'DB_CONNECTION_URI':f'postgresql+psycopg://postgres:postgres@127.0.0.1:{db_port}/postgres',
        'AUTH_USE_AUTH':'false','CACHE_ENABLED':'false','SENTRY_ENABLED':'false','TELEMETRY_ENABLED':'false',
        'LLM_OPENAI_API_KEY':key,'PACBENCH_MEMORY_API_KEY':key,'LLM_OPENAI_BASE_URL':base,
        'COLLECT_METRICS_LOCAL':'true','LOCAL_METRICS_FILE':str(root/'honcho-model-metrics.jsonl'),
        'REASONING_TRACES_FILE':str(root/'honcho-reasoning-traces.jsonl'),
        'DERIVER_MODEL_CONFIG':json.dumps(config),'SUMMARY_MODEL_CONFIG':json.dumps(config),
        'DREAM_DEDUCTION_MODEL_CONFIG':json.dumps(config),'DREAM_INDUCTION_MODEL_CONFIG':json.dumps(config),
        'DIALECTIC_LEVELS':json.dumps({level:{'MODEL_CONFIG':config} for level in ('minimal','low','medium','high','max')}),
        'EMBEDDING_VECTOR_DIMENSIONS':'768',
        'EMBEDDING_MODEL_CONFIG':json.dumps({'transport':'openai','model':'nomic-embed-text',
            'dimensions_mode':'never','overrides':{'base_url':'http://127.0.0.1:11435/v1','api_key_env':'PACBENCH_MEMORY_API_KEY'}})}
    for k in ('LLM_ANTHROPIC_API_KEY','LLM_GEMINI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY'):
        child.pop(k,None)
    # Preserve native behavior, including the normal accumulation delay and
    # output limit. A model incompatibility is evidence, not a reason to tune it.
    for k in ('DERIVER_FLUSH_ENABLED','LLM_DEFAULT_MAX_TOKENS'):
        child.pop(k,None)
    # Check every resolved generation model/endpoint before migrations or workers.
    guard='''
from src.config import settings
from pacbench.gateway_client import validate_route
models=[settings.DERIVER.MODEL_CONFIG,settings.SUMMARY.MODEL_CONFIG,
        settings.DREAM.DEDUCTION_MODEL_CONFIG,settings.DREAM.INDUCTION_MODEL_CONFIG,
        *(v.MODEL_CONFIG for v in settings.DIALECTIC.LEVELS.values())]
import os,json
for m in models:
    assert m.transport=='openai' and m.model==os.environ['PACBENCH_EXPECTED_MODEL'] and m.fallback is None
    validate_route(m.overrides.base_url,os.environ[m.overrides.api_key_env])
fields=type(settings.DERIVER).model_fields
processing={name:getattr(settings.DERIVER,name) for name in (
    'FLUSH_ENABLED','REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS',
    'REPRESENTATION_BATCH_TARGET_INPUT_TOKENS','REPRESENTATION_BATCH_MAX_AGE_SECONDS')}
assert all(value==fields[name].default for name,value in processing.items())
assert settings.LLM.DEFAULT_MAX_TOKENS==type(settings.LLM).model_fields['DEFAULT_MAX_TOKENS'].default
print(json.dumps({'native_defaults_verified':True,'deriver':processing,
                 'llm_default_max_tokens':settings.LLM.DEFAULT_MAX_TOKENS}))
'''
    child['PYTHONPATH']=str(source)+os.pathsep+str(Path(__file__).resolve().parents[1])
    child['PACBENCH_EXPECTED_MODEL']=model
    # Use the exact tokenizer already bundled by the installed LiteLLM runtime.
    # Native tiktoken cache loading/hash verification is unchanged; no network
    # mirror or tokenizer substitute when this environment blocks the download.
    bundled=source.parent/'venv-hermes-isolated/lib/python3.12/site-packages/litellm/litellm_core_utils/tokenizers'
    required={'fb374d419588a4632f3f557e76b4b70aebbca790':'446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d',
              '9b5ad71b2ce5302211f9c61530b329a4922fc6a4':'223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7'}
    if all((bundled/name).exists() for name in required):
        cache=root/'tokenizer-cache';cache.mkdir()
        for name,expected in required.items():
            digest=hashlib.sha256((bundled/name).read_bytes()).hexdigest()
            if digest!=expected:raise RuntimeError('Bundled tokenizer hash differs from native tiktoken requirement')
            shutil.copyfile(bundled/name,cache/name)
        child['TIKTOKEN_CACHE_DIR']=str(cache)
        save(root/'native-tokenizer-setup.json',{'source':'installed LiteLLM bundled tokenizers',
            'sha256':required,'native_tiktoken_cache':True})
    validated=subprocess.run([str(python),'-c',guard],env=child,cwd=root,capture_output=True,text=True,timeout=30)
    if validated.returncode:raise RuntimeError('Honcho model configuration validation failed: '+validated.stderr.replace(key,'<redacted>'))
    processing=json.loads(validated.stdout.strip().splitlines()[-1])
    save(root/'native-processing-settings.json',processing)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        ready=subprocess.run([*docker,'exec',name,'pg_isready','-U','postgres'],env=docker_env,capture_output=True,timeout=10)
        if ready.returncode==0:break
        time.sleep(.2)
    migrated=subprocess.run([str(python),str(source/'scripts/provision_db.py')],cwd=source,env=child,
                             capture_output=True,text=True,timeout=90)
    save(root/'native-database-setup.json',{'route':'Honcho manual self-hosting migrations',
        'returncode':migrated.returncode,'stdout':migrated.stdout.replace(key,'<redacted>'),
        'stderr':migrated.stderr.replace(key,'<redacted>')})
    if migrated.returncode:raise RuntimeError('Honcho native database migrations failed')
    configured=subprocess.run([str(python),str(source/'scripts/configure_embeddings.py'),'--yes'],
        cwd=source,env=child,capture_output=True,text=True,timeout=90)
    save(root/'native-embedding-setup.json',{'route':'Documented configure_embeddings.py bootstrap',
        'returncode':configured.returncode,'stdout':configured.stdout.replace(key,'<redacted>'),
        'stderr':configured.stderr.replace(key,'<redacted>')})
    if configured.returncode:raise RuntimeError('Honcho native embedding schema configuration failed')
    endpoint=f'http://127.0.0.1:{api_port}'
    for label,args in [('api',['-m','uvicorn','src.main:app','--host','127.0.0.1','--port',str(api_port)]),
                       ('deriver',['-m','src.deriver'])]:
        log=(root/f'honcho-{label}.log').open('ab')
        processes.append(subprocess.Popen([str(python),*args],cwd=root,env=child,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log));log.close()
    deadline=time.monotonic()+90;health=None
    while time.monotonic()<deadline and processes[0].poll() is None:
        try:
            with urllib.request.urlopen(endpoint+'/health',timeout=2) as r:health=json.load(r)
            break
        except (OSError,ValueError):time.sleep(.2)
    save(root/'native-server-setup.json',{'route':'Documented manual self-hosting, native API + deriver + local pgvector',
         'version':'3.2.2','endpoint':endpoint,'health':health,'models':model,
         'embedding':'local Ollama nomic-embed-text','starter_facts':False,
         'processing':processing,'processing_option':'Unmodified native batching and output defaults'})
    if health is None:raise RuntimeError('Honcho native API startup failed; inspect local logs')
    save(root/'hermes-home/honcho.json',{'baseUrl':endpoint,'hosts':{'hermes':{
        'enabled':True,'aiPeer':'hermes','peerName':'user','workspace':'pacbench-'+identity,
        'observationMode':'directional'}}})  # Current native setup default, not the legacy-file migration mode.
    env['PACBENCH_HONCHO_TRACE_FILE']=str(root/'honcho-reasoning-traces.jsonl')
    env.update(HONCHO_BASE_URL=endpoint,HONCHO_API_KEY='',HONCHO_WORKSPACE='pacbench-'+identity)
    return stop
