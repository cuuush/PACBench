"""Run-owned OpenViking server using documented local/model configuration."""
from __future__ import annotations
import atexit
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from pacbench.followups import save
from pacbench.gateway_client import validate_route


def start(root,env,model,base,key):
    validate_route(base,key)
    binary=Path(__file__).resolve().parents[1]/'vendor/venv-openviking/bin/openviking-server'
    if not binary.exists():raise RuntimeError('Install OpenViking in vendor/venv-openviking first')
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    cfg={'storage':{'workspace':str((root/'openviking-data').resolve())},
         'embedding':{'dense':{'provider':'ollama','model':'nomic-embed-text',
             'api_base':'http://127.0.0.1:11435/v1','dimension':768,'input':'text'}},
         'vlm':{'provider':'openai','model':model,'api_base':base,'api_key':key,'max_tokens':16384},
         'server':{'host':'127.0.0.1','port':port,'observability':{
             'traces':{'enabled':True,'protocol':'local','local_path':str((root/'provider-api-calls.jsonl').resolve())},
             'dump_body':{'enabled':True,'max_bytes':8_000_000}}}}
    path=root/'ov.conf';save(path,cfg);os.chmod(path,0o600)
    endpoint=f'http://127.0.0.1:{port}'
    env.update(OPENVIKING_ENDPOINT=endpoint,OPENVIKING_CONFIG_FILE=str(path.resolve()),
               OPENVIKING_API_KEY='',OPENVIKING_ACCOUNT='default',OPENVIKING_USER='default',OPENVIKING_AGENT='')
    # Current upstream CLI accepts --config; its validated configuration is the
    # manual setup route. No prompts, missions or extraction settings are changed.
    log=(root/'openviking-server.log').open('ab')
    process=subprocess.Popen([str(binary),'--config',str(path.resolve())],
        env={**os.environ,**env},cwd=root,stdout=log,stderr=log)
    log.close()
    def stop():
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    atexit.register(stop)
    deadline=time.monotonic()+120
    health=None
    while time.monotonic()<deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(endpoint+'/health',timeout=2) as response:health=json.load(response)
            break
        except (OSError,ValueError):time.sleep(.2)
    save(root/'native-server-setup.json',{'route':'OpenViking documented ov.conf and --config',
        'version':'0.4.23','endpoint':endpoint,'health':health,'pid':process.pid,
        'model':model,'embedding':'local Ollama nomic-embed-text','starter_facts':False})
    if health is None:
        stop();raise RuntimeError('OpenViking local startup failed; inspect openviking-server.log')
    return stop
