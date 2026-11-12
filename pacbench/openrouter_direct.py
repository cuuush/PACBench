"""Direct native configuration review; no services or model calls are started.

Some clients support request extensions. Others need a verified server-side
routing preset: changing their URL alone does not pin DeepInfra Flex.
"""
from copy import deepcopy
from contextlib import contextmanager
import json
import os
import sys
import time
from pathlib import Path

from .openrouter_route import MODEL, ENDPOINT, PROVIDER

BASE_URL = 'https://openrouter.ai/api/v1'


def active():
    return os.environ.get('PACBENCH_DIRECT')=='1'


def runtime_environment(env):
    """Install accounting in child clients without editing native provider files."""
    if not active():return env
    repo=Path(__file__).resolve().parents[1]
    bootstrap=repo/'pacbench/direct_runtime'
    existing=env.get('PYTHONPATH','')
    env['PYTHONPATH']=os.pathsep.join([str(bootstrap),str(repo),*filter(None,existing.split(os.pathsep))])
    env['NODE_OPTIONS']='--require '+str(bootstrap/'fetch.cjs')
    # Node 24 requires opt-in to honour the managed environment's HTTP(S)_PROXY.
    # This is the platform egress path, not a PACBench inference proxy.
    env['NODE_USE_ENV_PROXY']='1'
    return env


@contextmanager
def direct_environment(key):
    """Expose labelled capabilities to SDKs; only direct hooks use the real key."""
    from .direct_accounting import live_budget
    live_budget()
    names=['PACBENCH_TRANSPORT','PACBENCH_DIRECT','PACBENCH_DIRECT_UPSTREAM_KEY',
           'PACBENCH_DIRECT_PYTHON','PACBENCH_DIRECT_REPO','PACBENCH_DIRECT_CLOCK',
           'OPENROUTER_API_KEY','OPENAI_API_KEY','META_API_KEY','META_BASE_URL',
           'OPENAI_BASE_URL','OPENROUTER_BASE_URL','OPENROUTER_API_BASE','CUSTOM_BASE_URL',
           'NODE_OPTIONS','NODE_USE_ENV_PROXY','PYTHONPATH']
    before={n:os.environ.get(n) for n in names}
    repo=Path(__file__).resolve().parents[1]
    os.environ.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='1',
        PACBENCH_DIRECT_UPSTREAM_KEY=key,PACBENCH_DIRECT_PYTHON=sys.executable,
        PACBENCH_DIRECT_REPO=str(repo),PACBENCH_DIRECT_CLOCK=f'{time.time()},{time.monotonic()}',
        OPENROUTER_API_KEY='',OPENAI_API_KEY='',META_API_KEY='',META_BASE_URL='',
        OPENAI_BASE_URL='',OPENROUTER_BASE_URL='',OPENROUTER_API_BASE='',CUSTOM_BASE_URL='')
    runtime_environment(os.environ)
    from .direct_http import install
    install()
    try:yield
    finally:
        for n,v in before.items():
            if v is None:os.environ.pop(n,None)
            else:os.environ[n]=v


def provider_plan(provider: str, root: Path) -> dict:
    from .provider_lineup import require_not_retired
    require_not_retired(provider)
    from .native_pilot import memcal_model_settings, mem0_model_settings, hindsight_model_settings
    from provider_adapters import honcho_local, openviking_local, supermemory_local
    key = '<runtime-secret; omit from public evidence>'
    route = {'provider': deepcopy(PROVIDER)}
    extension = None
    if provider == 'memcal':
        settings = memcal_model_settings(MODEL, BASE_URL, key)
    elif provider == 'mem0':
        settings = {'model': MODEL, **mem0_model_settings(MODEL, BASE_URL, openrouter=True)}
    elif provider == 'hindsight':
        cfg, env = hindsight_model_settings('direct-plan', MODEL, BASE_URL, key)
        settings = {'config': cfg, 'environment': env}
        extension = {'HINDSIGHT_API_LLM_EXTRA_BODY': route}
    elif provider == 'openviking':
        settings = openviking_local.plan_config(root, MODEL, BASE_URL, key, port=1)
        extension = {'vlm.extra_request_body': route}
    elif provider == 'honcho':
        settings = honcho_local.plan_config(root, MODEL, BASE_URL, key, db_port=1)
        extension = {'each non-embedding model overrides.provider_params.extra_body': route}
    elif provider == 'supermemory':
        settings = supermemory_local.plan_config(root, MODEL, BASE_URL, key, port=1)
    elif provider == 'reme':
        from provider_adapters.reme_local import plan_config
        settings = plan_config(root, MODEL, BASE_URL, key)
    elif provider == 'cognee':
        from provider_adapters.cognee_runtime import plan_config
        settings = plan_config(root, MODEL, BASE_URL, key)
    elif provider == 'memos':
        from provider_adapters.memos_local import plan_config
        settings = plan_config(root, MODEL, BASE_URL, key)
    else:
        raise ValueError('Unsupported native provider')
    return {'provider': provider, 'model': MODEL, 'endpoint': ENDPOINT,
            'transport': 'direct OpenRouter HTTPS; no local inference proxy',
            'native_settings': settings,
            'documented_native_route_extensions': extension,
            'requires_server_side_route_preset': False,
            'native_extension_available': extension is not None,
            'route_enforcement': 'per-request direct client transport hooks',
            'paid_calls_enabled': False,
            'status': 'offline plan; native execution must still be validated'}
