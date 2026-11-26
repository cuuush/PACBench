"""Supermemory's published local server with supported v3 SDK and local embeddings."""
from __future__ import annotations
import atexit
import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4
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
    from pacbench.import_lifecycle import OWNER, cleanup, scan
    if scan(root,'supermemory-start-inspection')[0]:raise RuntimeError('Previous run-owned Supermemory processes are still alive')
    runtime=Path(__file__).resolve().parents[1]/'vendor/supermemory-local'
    binary=runtime/'supermemory-server-linux-x64'
    if not binary.exists():raise RuntimeError('Install verified Supermemory local server first')
    stock_source = os.environ.get('PACBENCH_SUPERMEMORY_STOCK_SOURCE') == '1'
    namespace = 'supermemory-data-'+root.parent.name+'-'+root.name
    if stock_source:
        from pacbench.canary_capability import sha
        from pacbench.providers.supermemory.source_admission import BINARY_SHA, SCOPE
        from pacbench.providers.supermemory.question_admission import SCOPE as QUESTION_SCOPE, verify_restored_snapshot
        from pacbench.workflows.capability import SM_WEEK_ADMISSION, load
        scope = load()
        if Path(scope['root']) != root or sha(binary) != BINARY_SHA:
            raise ValueError('Expected the pinned stock binary and the owned workflow root')
        if resume and scope['admission'] == QUESTION_SCOPE:
            verify_restored_snapshot(scope)
            namespace = scope['namespace']
        elif resume or scope['admission'] not in (SCOPE, SM_WEEK_ADMISSION):
            raise ValueError('Expected a fresh stock scope or the exact first-question snapshot')
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    settings = plan_config(root, model, base, key, port=port)
    if not resume:
        from .embedding_health import check

        check(settings['SUPERMEMORY_EMBEDDING_BASE_URL'],
              model=settings['SUPERMEMORY_EMBEDDING_MODEL'],
              dimensions=int(settings['SUPERMEMORY_EMBEDDING_DIMENSIONS']))
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
    owner = uuid4().hex
    child={**os.environ, **env, **settings, OWNER:owner}
    for name in ('ANTHROPIC_API_KEY','GEMINI_API_KEY','GROQ_API_KEY','WORKERS_AI_API_KEY',
                 'CLOUDFLARE_ACCOUNT_ID','GOOGLE_VERTEX_PROJECT_ID','GOOGLE_VERTEX_LOCATION',
                 'SUPERMEMORY_API_KEY','SUPERMEMORY_BASE_URL'):
        child.pop(name,None)
    log_path=root/'supermemory-server.private.log'
    log_offset=log_path.stat().st_size if log_path.exists() else 0
    process = None
    owned = ()
    verified = None
    recorded = False
    errors = []

    def stop():
        nonlocal owned, verified, recorded
        if recorded:
            return
        primary = None

        def failed(stage, error):
            nonlocal primary
            errors.append({'stage': stage, 'failure_type': type(error).__name__})
            if primary is None:
                primary = error
            else:
                primary.add_note(f'Additional Supermemory shutdown failure in {stage}: {type(error).__name__}')

        if verified is None:
            try:
                found, _ = scan(root, owner, known=owned)
                owned = found
            except BaseException as error:
                failed('process_inventory', error)
            try:
                _stop_group(process)
            except BaseException as error:
                failed('process_group', error)
            stopped = {'state': 'cleanup-unverified'}
            try:
                stopped = cleanup(root, owner, known=owned)
            except BaseException as error:
                failed('process_cleanup', error)
            if stopped['state'] == 'verified-inventory-stopped' and process.poll() is None:
                stopped['state'] = 'cleanup-unverified'
                failed('process_group', RuntimeError('Owned Supermemory process is still running'))
            if stopped['state'] == 'verified-inventory-stopped':
                verified = stopped
        else:
            stopped = verified

        if errors:
            stopped['shutdown_errors'] = list(errors)
        try:
            save(root/'native-service-stop.json', stopped)
        except BaseException as error:
            failed('receipt_write', error)
        else:
            if verified is not None:
                recorded = True
                atexit.unregister(stop)
        if primary is not None:
            raise primary

    try:
        with log_path.open('ab') as log:
            os.chmod(log_path, 0o600)
            process = subprocess.Popen([str(binary)], cwd=root, env=child, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
            atexit.register(stop)
        _configure_started(root, env, model, resume, runtime, log_path, log_offset, endpoint, port,
                           namespace, stock_source, process)
    except BaseException as error:
        if process is not None:
            try:
                stop()
            except BaseException as shutdown_error:
                error.add_note(f'Supermemory shutdown during startup failed: {type(shutdown_error).__name__}')
        raise
    return stop


def _stop_group(process):
    # A live direct child keeps this session-leader PID from being reused until
    # we reap it. After reaping, process inventory uses captured start ticks.
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _configure_started(root, env, model, resume, runtime, log_path, log_offset, endpoint, port,
                       namespace, stock_source, process):
    deadline=time.monotonic()+300
    native_key=None
    while time.monotonic()<deadline and process.poll() is None:
        output=re.sub(r'\x1b\[[0-9;]*m','',log_path.read_bytes()[log_offset:].decode(errors='replace'))
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
    if stock_source:
        from pacbench.live_dispatch import process_identity
        from pacbench.providers.supermemory.source_effect import BOUNDARY
        receipt.update(dispatch_operation_boundary=BOUNDARY, process_identity=process_identity(process.pid))
    save(root/'native-server-setup.json',receipt)
    save(root/'document-ingestion-config.json',({
        'dreaming':'native default', 'route':'Native SDK add without processing overrides',
        'native_default':'dynamic', 'model_processing_overrides':False,
    } if stock_source else {
        'dreaming':'instant',
        'route':'Native SDK documents.add documented per-document processing mode',
        'reason':'Each document already contains a complete nightly batch; do not defer it for dynamic cross-document grouping',
        'native_default':'dynamic','model_processing_overrides':False}))
    if not native_key:
        raise RuntimeError('Supermemory startup did not produce its native API key; inspect private log')
    # README manual endpoint first, then the key emitted by real first boot.
    save(root/'hermes-home/supermemory.json',{'base_url':endpoint,'container_tag':namespace})
    from pacbench.prefetch_provenance import VERSION
    declaration={'protocol':VERSION,'provider':'supermemory','state':'unverified',
        'reason':'Native v5 default hybrid retrieval can return document chunks; the plugin formatter merges chunk and memory text and omits their type. Independent passage provenance is pending.'}
    save(root/'prefetch-provenance.json',declaration)
    env['PACBENCH_PREFETCH_PROVENANCE']=json.dumps(declaration)
    env.update(SUPERMEMORY_BASE_URL=endpoint,SUPERMEMORY_API_KEY=native_key)
