"""Recommended manual self-hosting: local pgvector and unmodified Honcho API/deriver."""
from __future__ import annotations
import atexit
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import tomllib
import urllib.request
from pathlib import Path
from pacbench.followups import save
from pacbench.gateway_client import validate_route


def queue_errors(root):
    """Observe failed native work without changing the provider or its queue.

    Honcho's public completed counter includes terminally errored items. Read
    its local queue as telemetry; do not mistake that counter for success.
    Keep error text private: public evidence contains only class and hash.
    """
    identity=hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    sql="""SELECT COALESCE(json_agg(t),'[]'::json) FROM (
        SELECT q.id,q.task_type,s.name AS session_name,q.error
        FROM queue q LEFT JOIN sessions s ON s.id=q.session_id
        WHERE q.error IS NOT NULL AND q.task_type IN ('representation','summary','dream')
        ) t"""
    env={k:v for k,v in os.environ.items() if k not in {
        'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    p=subprocess.run(['docker','--host=unix:///var/run/docker.sock','exec',
        'pacbench-honcho-db-'+identity,'psql','-U','postgres','-d','postgres',
        '-X','-t','-A','-c',sql],env=env,capture_output=True,text=True,timeout=15)
    if p.returncode:raise RuntimeError('Native Honcho queue error telemetry unavailable')
    rows=json.loads(p.stdout)
    for row in rows:
        error=row.pop('error')
        label,separator,_=error.partition(':')
        row['error_class']=(label if separator and len(label)<=100 and
                            re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)',label)
                            else 'NativeError')
        row['error_sha256']=hashlib.sha256(error.encode()).hexdigest()
    return rows


def verify_persisted_messages(acknowledgments,actual):
    from datetime import datetime
    expected={m['id']:m for ack in acknowledgments for m in ack['messages']}
    if not expected or len(actual)!=len(expected) or {m['id'] for m in actual}!=set(expected):
        raise ValueError('Persisted Honcho message IDs differ from original acknowledgments')
    for m in actual:
        sent=expected[m['id']]
        if (any(m[k]!=sent[k] for k in ('session_id','peer_id','content','metadata')) or
                datetime.fromisoformat(m['created_at'])!=datetime.fromisoformat(sent['created_at'])):
            raise ValueError('Persisted Honcho source differs from original acknowledgment')
    return sorted(expected)


def start(root,env,model,base,key,*,resume=False):
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
    dbdir=root/'honcho-db'
    if resume:
        previous=json.loads((root/'native-server-setup.json').read_text())
        if (not dbdir.is_dir() or previous['models']!=model or
                previous['version']!=tomllib.loads((source/'pyproject.toml').read_text())['project']['version']):
            raise ValueError('Honcho diagnostic restart requires its existing store and unchanged model/server version')
    dbdir.mkdir(exist_ok=resume)
    docker=['docker','--host=unix:///var/run/docker.sock']
    docker_env={k:v for k,v in os.environ.items() if k not in {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    proc=subprocess.run([*docker,'run','--rm','-d','--name',name,
        '-p',f'127.0.0.1:{db_port}:5432','-e','POSTGRES_PASSWORD=postgres',
        '-v',f'{dbdir}:/var/lib/postgresql/data',
        *(['--entrypoint','/bin/sh'] if resume else []),'pgvector/pgvector:pg15',
        *(['-c','test -f "$PGDATA/PG_VERSION" && exec /usr/local/bin/docker-entrypoint.sh postgres'] if resume else [])],
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
    # Preserve native batching and global limits. Explicit documented per-model
    # environment settings are resolved by Honcho and recorded below.
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
deriver_limit=settings.DERIVER.MODEL_CONFIG.max_output_tokens
print(json.dumps({'native_defaults_verified':deriver_limit is None,
                 'native_batching_defaults_verified':True,
                 'deriver_max_output_tokens':deriver_limit,'deriver':processing,
                 'llm_default_max_tokens':settings.LLM.DEFAULT_MAX_TOKENS}))
'''
    child['PYTHONPATH']=str(source)+os.pathsep+str(Path(__file__).resolve().parents[1])
    child['PACBENCH_EXPECTED_MODEL']=model
    # Use the exact tokenizer already bundled by the installed LiteLLM runtime.
    # Native tiktoken cache loading/hash verification is unchanged; no network
    # mirror or tokenizer substitute when this environment blocks the download.
    from provider_adapters.tokenizer_cache import configure as configure_tokenizers
    configure_tokenizers(root,child)
    validated=subprocess.run([str(python),'-c',guard],env=child,cwd=root,capture_output=True,text=True,timeout=30)
    if validated.returncode:raise RuntimeError('Honcho model configuration validation failed: '+validated.stderr.replace(key,'<redacted>'))
    processing=json.loads(validated.stdout.strip().splitlines()[-1])
    save(root/'native-processing-settings.json',processing)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        ready=subprocess.run([*docker,'exec',name,'pg_isready','-U','postgres'],env=docker_env,capture_output=True,timeout=10)
        if ready.returncode==0:break
        time.sleep(.2)
    if resume:
        original=json.loads((root/'ingestion.json').read_text())
        sql="""SELECT COALESCE(json_agg(json_build_object('id',public_id,'session_id',session_name,
            'peer_id',peer_name,'content',content,'created_at',created_at,'metadata',metadata)),'[]'::json)
            FROM messages WHERE session_name LIKE 'source-%'"""
        p=subprocess.run([*docker,'exec',name,'psql','-U','postgres','-d','postgres','-X','-t','-A','-c',sql],
                         env=docker_env,capture_output=True,text=True,timeout=15)
        if p.returncode:raise RuntimeError('Cannot verify persisted Honcho source messages')
        actual=json.loads(p.stdout)
        ids=verify_persisted_messages(original['result']['result'],actual)
        save(root/'diagnostic-store-reopen.json',{'scored':False,'verified_source_messages':len(actual),
            'original_native_ids':ids,'source_resubmissions':0,'queue_edits':0,
            'scope':'Diagnostic store reopen only; no scored benchmark resume protocol'})
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
         'version':tomllib.loads((source/'pyproject.toml').read_text())['project']['version'],
         'source_revision':subprocess.run(['git','rev-parse','HEAD'],cwd=source,
             capture_output=True,text=True,check=True,timeout=10).stdout.strip(),
         'endpoint':endpoint,'health':health,'models':model,
         'embedding':'local Ollama nomic-embed-text','starter_facts':False,
         'hermes_session_strategy':'per-session',
         'processing':processing,'processing_option':('Unmodified native batching and output defaults'
             if processing['native_defaults_verified'] else
             'Native batching defaults; explicit documented deriver model output allowance')})
    if health is None:raise RuntimeError('Honcho native API startup failed; inspect local logs')
    save(root/'hermes-home/honcho.json',{'baseUrl':endpoint,'hosts':{'hermes':{
        'enabled':True,'aiPeer':'hermes','peerName':'user','workspace':'pacbench-'+identity,
        'sessionStrategy':'per-session',
        'observationMode':'directional'}}})  # Current native setup default, not the legacy-file migration mode.
    env['PACBENCH_HONCHO_TRACE_FILE']=str(root/'honcho-reasoning-traces.jsonl')
    env.update(HONCHO_BASE_URL=endpoint,HONCHO_API_KEY='',HONCHO_WORKSPACE='pacbench-'+identity)
    return stop
