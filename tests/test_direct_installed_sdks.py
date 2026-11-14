"""Exercise the installed OpenAI SDK in each Python runtime with no upstream traffic."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from pacbench.direct_accounting import MODEL

REPO=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('runtime',[
    'vendor/venv-hermes-isolated/bin/python',
    'vendor/venv-hindsight/bin/python',
    'vendor/venv-openviking/bin/python',
    'vendor/honcho-local/.venv/bin/python',
])
def test_installed_sdk_uses_direct_reservations_and_authentication(tmp_path,runtime):
    python=REPO/runtime
    if not python.exists():pytest.skip('Native interpreter not installed')
    ledger=tmp_path/'ledger.json'
    ledger.write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':100,
       'prompt_per_million':'1','completion_per_million':'2','max_completion_tokens':50,
       'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    script='''
import asyncio,json,os,sys
from pathlib import Path
from pacbench.direct_accounting import DirectBudget,MODEL
import pacbench.direct_http as hook
hook.live_budget=lambda **kw:DirectBudget(Path(sys.argv[1]))
os.environ.update(PACBENCH_DIRECT='1',PACBENCH_DIRECT_UPSTREAM_KEY='offline-upstream')
hook.install()
import openai._base_client as base
from openai import AsyncOpenAI
http=getattr(base,'httpx2',None) or base.httpx
def fake(request):
    assert request.headers['Authorization']=='Bearer offline-upstream'
    body=json.loads(request.content)
    assert body['provider']['only']==['deepinfra/fp8/flex']
    assert body['provider']['allow_fallbacks'] is False
    assert body['max_tokens']==32 and body['messages']==[{'role':'user','content':'offline probe'}]
    return http.Response(200,json={'id':'gen-offline','model':MODEL,'object':'chat.completion',
      'created':1,'provider':'DeepInfra','choices':[{'index':0,'finish_reason':'stop',
        'message':{'role':'assistant','content':'OK'}}],
      'usage':{'cost':0.00001,'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})
async def check():
    async with AsyncOpenAI(api_key='pbdirect-native',base_url='https://openrouter.ai/api/v1',
      http_client=http.AsyncClient(transport=http.MockTransport(fake))) as client:
        response=await client.chat.completions.create(model=MODEL,
          messages=[{'role':'user','content':'offline probe'}],max_tokens=32)
        assert response.choices[0].message.content=='OK'
asyncio.run(check())
assert DirectBudget(Path(sys.argv[1])).snapshot()[0]['state']=='settled'
print(http.__name__)
'''
    env={k:v for k,v in os.environ.items() if 'KEY' not in k and 'TOKEN' not in k}
    env.update(PYTHONPATH=str(REPO),HOME=str(tmp_path))
    result=subprocess.run([str(python),'-c',script,str(ledger)],env=env,cwd=tmp_path,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'httpx' in result.stdout
