"""Historical ByteRover configuration evidence; new execution is retired."""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from pacbench.followups import save
from pacbench.gateway_client import validate_route



def plan_config(root, model, base, key):
    """Return native setup commands without running the CLI."""
    return [['vc', 'init'], ['providers', 'connect', 'openai-compatible',
            '--base-url', base, '--api-key', key, '--model', model, '--format', 'json']]

def configure(root,env,model,base,key):
    from pacbench.provider_lineup import require_not_retired
    require_not_retired('byterover')
    validate_route(base,key)
    binary=Path(__file__).resolve().parents[1]/'vendor/byterover-local/node_modules/.bin/brv'
    if not binary.exists():raise RuntimeError('Install byterover-cli in vendor/byterover-local')
    project=root/'hermes-home/byterover';project.mkdir()
    env.update(PATH=str(binary.parent)+os.pathsep+os.environ.get('PATH',''),
        XDG_CONFIG_HOME=str((root/'fallback-home/.config').resolve()),
        XDG_DATA_HOME=str((root/'fallback-home/.local/share').resolve()),
        BRV_DATA_DIR=str((root/'fallback-home/.local/share/brv').resolve()),
        PACBENCH_PROVIDER_TRACE_ROOT=str((root/'fallback-home/.local/share/brv').resolve()),
        OPENAI_COMPATIBLE_API_KEY=key,BRV_API_KEY='')
    attempts=[]
    for args in plan_config(root,model,base,key):
        p=subprocess.run([str(binary),*args],cwd=project,env={**os.environ,**env},
                         stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120)
        attempts.append({'arguments':[a.replace(key,'<gateway-token>') for a in args],
            'returncode':p.returncode,'stdout':p.stdout.replace(key,'<gateway-token>'),
            'stderr':p.stderr.replace(key,'<gateway-token>')})
        save(root/'native-byterover-setup.json',attempts)
        if p.returncode:raise RuntimeError('ByteRover documented setup failed; inspect native-byterover-setup.json')
    cfg=json.loads((Path(env['XDG_CONFIG_HOME'])/'brv/providers.json').read_text())
    active=cfg['providers'].get('openai-compatible',{})
    if cfg['activeProvider']!='openai-compatible' or active.get('activeModel')!=model:
        raise RuntimeError('ByteRover did not select the configured route model')
    validate_route(active['baseUrl'],key)
