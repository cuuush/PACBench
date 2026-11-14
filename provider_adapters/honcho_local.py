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

PGVECTOR_IMAGE = 'pgvector/pgvector@sha256:88164492558c9773a1e17a8305abaca7863e05b3d8bbcdb792774890a2047e06'

class OperatorConditionHold(RuntimeError):
    """An operator deferred this provider before native execution."""


def check_operator_condition(root):
    """Honor a run-owned Honcho hold without changing any other provider."""
    root = Path(root)
    if root.name != 'honcho' or not re.fullmatch(r'repeat-[1-9][0-9]*', root.parent.name):
        return
    run = root.parent.parent
    manifest = run/'provider-conditions'/'honcho.json'
    if not manifest.exists():
        return
    try:
        if manifest.is_symlink() or manifest.parent.is_symlink():
            raise ValueError('condition path is a symlink')
        raw = manifest.read_bytes()
        condition = json.loads(raw)
        summary = json.loads((run/'summary.json').read_text())
        if (condition.get('version') != 'provider-condition-v1' or
                condition.get('provider') != 'honcho' or condition.get('mode') != 'hold' or
                not isinstance(condition.get('reason'), str) or not condition['reason'].strip() or
                condition.get('bundle_hash') != summary.get('bundle_hash') or
                not isinstance(summary.get('bundle_hash'), str) or len(summary['bundle_hash']) != 64):
            raise ValueError('invalid condition or bundle binding')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError('Invalid Honcho operator condition; native setup withheld') from exc
    save(root/'operator-condition.json', {
        'version': 'provider-condition-v1', 'state': 'operator-held',
        'provider': 'honcho', 'bundle_hash': condition['bundle_hash'],
        'condition_sha256': hashlib.sha256(raw).hexdigest(),
        'reason': condition['reason'], 'native_setup_started': False})
    raise OperatorConditionHold('Honcho held by run-owned operator condition; native setup withheld')


def processing_snapshot(root,workspace,sessions):
    """Read causal message/queue telemetry from this run's native database."""
    settings=json.loads((root/'native-processing-settings.json').read_text())
    if settings.get('multi_tenant') is not False:
        raise ValueError('Native single-tenant processing condition is unverified')
    identity=hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    names=','.join(literal(s) for s in sessions)
    if not names:raise ValueError('No native sessions for processing observation')
    workspace=literal(workspace)
    sql=f"""SELECT json_build_object(
        'messages',(SELECT COALESCE(json_agg(m),'[]'::json) FROM (
            SELECT public_id,session_name,peer_name,seq_in_session
            FROM messages WHERE workspace_name={workspace} AND session_name IN ({names})
            AND deleted_at IS NULL ORDER BY session_name,seq_in_session) m),
        'queue',(SELECT COALESCE(json_agg(t),'[]'::json) FROM (
            SELECT q.id,q.task_type,q.processed,(q.error IS NOT NULL) AS native_error,
                   s.name AS session_name,m.public_id AS message_id
            FROM queue q LEFT JOIN sessions s ON s.id=q.session_id
                AND s.workspace_name=q.workspace_name
                AND (q.tenant_id IS NULL OR q.tenant_id=s.tenant_id)
            LEFT JOIN messages m ON m.id=q.message_id AND m.tenant_id=s.tenant_id
                AND m.workspace_name=s.workspace_name AND m.session_name=s.name
            WHERE q.workspace_name={workspace} AND
                ((s.name IN ({names}) AND q.task_type IN ('representation','summary'))
                 OR q.task_type='dream') ORDER BY q.id) t))"""
    env={k:v for k,v in os.environ.items() if k not in {
        'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    p=subprocess.run(['docker','--host=unix:///var/run/docker.sock','exec',
        'pacbench-honcho-db-'+identity,'psql','-U','postgres','-d','postgres',
        '-X','-t','-A','-c',sql],env=env,capture_output=True,text=True,timeout=15)
    if p.returncode:raise RuntimeError('Native Honcho processing telemetry unavailable')
    return json.loads(p.stdout)


def queue_errors(root, *, include_all=False):
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
    if include_all:
        sql = sql.replace(" AND q.task_type IN ('representation','summary','dream')", '')
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



def plan_config(root, model, base, key, *, db_port):
    """Build Honcho native environment overrides without launching services."""
    source=Path(__file__).resolve().parents[1]/'vendor/honcho-local'
    config={'transport':'openai','model':model,'overrides':{'base_url':base,'api_key_env':'PACBENCH_MEMORY_API_KEY'}}
    return {'PYTHONPATH':str(source), 'HONCHO_CONFIG_TOML_DISABLED':'1',
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



def child_environment(inherited, env, overrides, model):
    """Merge native settings, removing the Spark-only allowance for DeepSeek."""
    child = {**inherited, **env, **overrides}
    from pacbench.role_routes import memory_model
    OPENROUTER_MODEL=memory_model()
    if model == OPENROUTER_MODEL:
        child.pop('DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS', None)
    return child

def start(root,env,model,base,key,*,resume=False,simulated_at=None):
    check_operator_condition(root)
    validate_route(base,key)
    source=Path(__file__).resolve().parents[1]/'vendor/honcho-local'
    python=source/'.venv/bin/python'
    if not python.exists():raise RuntimeError('Install the Honcho server using its documented uv sync first')
    # A healthy API/database alone does not establish embedding readiness.
    # Catch a missing local service before startup dialectic/model smoke calls.
    from .embedding_health import check as check_embeddings
    save(root/'native-embedding-preflight.json', check_embeddings())
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
    clock = None
    clock_args = []
    if simulated_at is not None:
        if resume:
            raise ValueError('A simulated-clock store needs its own verified resume protocol')
        from .honcho_clock import create, docker_clock_args
        clock = create(root, simulated_at)
        clock_args = docker_clock_args(clock)
    docker=['docker','--host=unix:///var/run/docker.sock']
    docker_env={k:v for k,v in os.environ.items() if k not in {'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    if clock:
        # The image normally starts as root and drops privileges. Preloading
        # before that drop creates root-owned libfaketime semaphores. Initialize
        # only filesystem ownership as root, then start the native image as its
        # own PostgreSQL user. No database rows are changed here.
        owned=subprocess.run([*docker,'run','--rm','--user','0:0',
            '-v',f'{dbdir}:/var/lib/postgresql/data','--entrypoint','chown',
            PGVECTOR_IMAGE,'999:999','/var/lib/postgresql/data'],
            env=docker_env,capture_output=True,text=True,timeout=60)
        if owned.returncode:raise RuntimeError('Cannot initialize isolated PostgreSQL directory ownership')
    proc=subprocess.run([*docker,'run','--rm','-d','--name',name,
        '--label','pacbench.import-root='+os.environ.get('PACBENCH_IMPORT_ROOT',str(root.resolve())),
        '-p',f'127.0.0.1:{db_port}:5432','-e','POSTGRES_PASSWORD=postgres',
        '-v',f'{dbdir}:/var/lib/postgresql/data',
        *clock_args,
        *(['--entrypoint','/bin/sh'] if resume else []),PGVECTOR_IMAGE,
        *(['-c','test -f "$PGDATA/PG_VERSION" && exec /usr/local/bin/docker-entrypoint.sh postgres'] if resume else [])],
        env=docker_env,capture_output=True,text=True,timeout=60)
    if proc.returncode == 0:
        save(root/'native-container-ownership.json',{'container_id':proc.stdout.strip(),
            'run_root':str(root.resolve()),'data_bind_mount':str(dbdir.resolve()),
            'label_root':os.environ.get('PACBENCH_IMPORT_ROOT',str(root.resolve())),
            'preserve_data_on_stop':True})
    owned_container_id=proc.stdout.strip() if proc.returncode == 0 else None
    processes=[]
    def stop():
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=15)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
        if owned_container_id:
            checked=subprocess.run([*docker,'inspect',owned_container_id],env=docker_env,
                capture_output=True,text=True,timeout=10)
            if checked.returncode == 0:
                rows=json.loads(checked.stdout)
                if (len(rows)==1 and rows[0].get('Id')==owned_container_id and
                    rows[0].get('Config',{}).get('Labels',{}).get('pacbench.import-root')==os.environ.get('PACBENCH_IMPORT_ROOT',str(root.resolve())) and
                    any(m.get('Type')=='bind' and m.get('Source')==str(dbdir.resolve())
                        for m in rows[0].get('Mounts',[]))):
                    subprocess.run([*docker,'stop',owned_container_id],env=docker_env,capture_output=True,timeout=30)
    atexit.register(stop)
    if proc.returncode:raise RuntimeError('Honcho local database startup failed: '+proc.stderr)
    child=child_environment(os.environ,env,plan_config(root,model,base,key,db_port=db_port),model)
    for k in ('LLM_ANTHROPIC_API_KEY','LLM_GEMINI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY'):
        child.pop(k,None)
    # Preserve native batching and global limits. Explicit documented per-model
    # environment settings are resolved by Honcho and recorded below.
    for k in ('DERIVER_FLUSH_ENABLED','LLM_DEFAULT_MAX_TOKENS'):
        child.pop(k,None)
    if clock:
        child = clock.python_environment(child)
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
deriver_thinking=settings.DERIVER.MODEL_CONFIG.thinking_effort
print(json.dumps({'native_defaults_verified':deriver_limit is None and deriver_thinking is None,
                 'multi_tenant':settings.MULTI_TENANT,
                 'native_batching_defaults_verified':True,
                 'deriver_max_output_tokens':deriver_limit,'deriver_thinking_effort':deriver_thinking,
                 'deriver':processing,
                 'llm_default_max_tokens':settings.LLM.DEFAULT_MAX_TOKENS}))
'''
    child['PYTHONPATH']=str(source)+os.pathsep+str(Path(__file__).resolve().parents[1])
    from pacbench.openrouter_direct import runtime_environment
    runtime_environment(child)
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
    native_processes = {}
    for label,args in [('api',['-m','uvicorn','src.main:app','--host','127.0.0.1','--port',str(api_port)]),
                       ('deriver',['-m','src.deriver'])]:
        log=(root/f'honcho-{label}.log').open('ab')
        native_env={**child,**({'PACBENCH_PYTHON_CLOCK_ROLE':label} if clock else {})}
        processes.append(subprocess.Popen([str(python),*args],cwd=root,env=native_env,
            start_new_session=bool(clock), stdin=subprocess.DEVNULL,stdout=log,stderr=log))
        native_processes[label] = processes[-1]
        log.close()
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
             'Native batching defaults; explicit documented deriver model configuration')})
    if health is None:raise RuntimeError('Honcho native API startup failed; inspect local logs')
    if clock:
        from .honcho_clock import register
        try:
            register(root, name, endpoint, native_processes)
        except Exception:
            stop()
            raise
    save(root/'hermes-home/honcho.json',{'baseUrl':endpoint,'hosts':{'hermes':{
        'enabled':True,'aiPeer':'hermes','peerName':'user','workspace':'pacbench-'+identity,
        'sessionStrategy':'per-session',
        'observationMode':'directional'}}})  # Current native setup default, not the legacy-file migration mode.
    env['PACBENCH_HONCHO_TRACE_FILE']=str(root/'honcho-reasoning-traces.jsonl')
    env.update(HONCHO_BASE_URL=endpoint,HONCHO_API_KEY='',HONCHO_WORKSPACE='pacbench-'+identity)
    return stop
