"""Native MemOS raw-document import and unchanged Hermes bridge configuration."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from pacbench.followups import save
from pacbench.gateway_client import validate_route
from provider_adapters.reme_memos_documents import request

REPO=Path(__file__).resolve().parents[1]


def plan_config(root,model,base,key):
    return {'version':1,'llm':{'provider':'openai_compatible','endpoint':base,
        'model':model,'apiKey':key,'fallbackToHost':False},
        # L3/evolver inherit that same explicitly selected native LLM config.
        'embedding':{'provider':'local','model':'Xenova/all-MiniLM-L6-v2',
            'local':{'cacheDir':'/workspace/shared/pacbench-memos-models','offline':True}},
        'hub':{'enabled':False},'telemetry':{'enabled':False}}


def start(root,env,model,base,key):
    validate_route(base,key)
    root=Path(root).resolve()
    from pacbench.provider_versions import verify_memos_package
    package,release=verify_memos_package(REPO)
    revision=release['sha256']
    if not (package/'dist/bridge.cjs').exists():raise RuntimeError('Build the native MemOS package before execution')
    plugin=package/'adapters/hermes/memos_provider'
    (root/'hermes-home/plugins/memtensor').symlink_to(plugin,target_is_directory=True)
    memos_home=root/'memos-home';memos_home.mkdir(mode=0o700)
    save(memos_home/'config.yaml',plan_config(root,model,base,key))
    (memos_home/'config.yaml').chmod(0o600)
    env.update(MEMOS_HOME=str(memos_home),MEMOS_HERMES_BRIDGE_MODE='shared',
        LLM_PROVIDER='openai_compatible',LLM_ENDPOINT=base,LLM_MODEL=model,LLM_API_KEY=key,
        LLM_FALLBACK_TO_HOST='false',L3_LLM_PROVIDER='openai_compatible',L3_LLM_ENDPOINT=base,
        L3_LLM_MODEL=model,L3_LLM_API_KEY=key,SKILL_EVOLVER_PROVIDER='openai_compatible',
        SKILL_EVOLVER_ENDPOINT=base,SKILL_EVOLVER_MODEL=model,SKILL_EVOLVER_API_KEY=key,
        TELEMETRY_ENABLED='false',HUB_ENABLED='false',EMBEDDING_PROVIDER='local')
    child={**os.environ,**env}
    from pacbench.openrouter_direct import runtime_environment
    runtime_environment(child)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    endpoint=f'http://127.0.0.1:{port}'
    log_path=root/'memos-native-http.private.log'
    with log_path.open('ab') as log:
        log_path.chmod(0o600)
        process=subprocess.Popen(['node',str(REPO/'provider_adapters/memos_service.mjs'),str(port)],
            cwd=root,env=child,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
    def stop():
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    atexit.register(stop)
    deadline=time.monotonic()+90;health=None
    while time.monotonic()<deadline and process.poll() is None:
        try:
            candidate=request(endpoint,'/api/v1/health',None,2)
            if candidate.get('service')=='memos-local-plugin' or candidate.get('status')=='ok':
                health=candidate;break
        except Exception:pass
        time.sleep(.2)
    if health is None:
        stop();raise RuntimeError('Native MemOS HTTP service failed health check; inspect private log')
    # Native overall ok also reflects last successful use: a fresh unused
    # client has lastOkAt=None. Fail concrete errors/unavailability, not absence
    # of historical activity. Stock smoke/actual processing still prove readiness.
    if health.get('ok') is False and any(
        value.get('lastError') or value.get('available') is False
        for value in (health.get('llm',{}),health.get('embedder',{}),health.get('skillEvolver',{}))):
        save(root/'native-startup-failure.json',{'state':'native-health-failed','health':health})
        stop();raise RuntimeError('Native MemOS reported unhealthy; no questions may run')
    from pacbench.prefetch_provenance import VERSION
    declaration={'protocol':VERSION,'provider':'memos','state':'source',
        'reason':'This arm natively imports original text as raw document traces; generated extraction is not claimed'}
    save(root/'prefetch-provenance.json',declaration)
    env['PACBENCH_PREFETCH_PROVENANCE']=json.dumps(declaration)
    save(root/'memos-document-config.json',{'endpoint':endpoint})
    save(root/'native-server-setup.json',{'route':'Pinned native public core bootstrap and HTTP API; unchanged Hermes memtensor stdio bridge',
        'provider_revision':revision,'source_type':'official-npm-release','native_package_release':release,'endpoint':endpoint,'pid':process.pid,'health':health,
        'model':model,'embedding':'Native local MiniLM384; verified cached assets; offline loading',
        'inference_overrides':'Exact selected OpenAI-compatible model; host fallback disabled; native output/reasoning/batching defaults unchanged',
        'http_role':'Hermes viewer role: autoRecovery false; native stdio bridge owns recovery writes',
        'ingestion_condition':'native-raw-document-import-v1','extraction_performed':False,
        'retrieval_credit':'source-assisted','native_plugin_name':'memtensor',
        'provider_clock':'Real native processing clock; imported trace/source timestamps preserve original scenario dates',
        'plugin_source_sha256':hashlib.sha256((plugin/'__init__.py').read_bytes()).hexdigest()})
    return stop
