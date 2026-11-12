"""Exercise installed runtimes with a fake upstream; never sends paid traffic."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from pacbench.direct_accounting import MODEL

REPO=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('runtime',['node','supermemory-bun'])
def test_native_fetch_preloads_reserve_route_and_settle(tmp_path,runtime):
    binary=(shutil.which('node') if runtime=='node' else
            REPO/'vendor/supermemory-local/supermemory-server-linux-x64')
    if not binary or not Path(binary).exists():pytest.skip('Native runtime not installed')
    ledger=tmp_path/'ledger.json'
    ledger.write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':100,
       'prompt_per_million':'1','completion_per_million':'2','max_completion_tokens':50,
       'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    helper=tmp_path/'accounting-python'
    helper.write_text('#!/usr/bin/env python3\nimport sys\nsys.path.insert(0,'+repr(str(REPO))+')\n'
       'import pacbench.direct_accounting as a\na.live_budget=lambda:a.DirectBudget('+repr(str(ledger))+')\n'
       'sys.argv=["accounting",sys.argv[-1]]\na.main()\n')
    helper.chmod(0o700)
    script=tmp_path/'probe.cjs'
    script.write_text('''
const assert=require('node:assert/strict');
globalThis.fetch=async function(req) {
  assert.equal(req.url,'https://openrouter.ai/api/v1/chat/completions');
  assert.equal(req.headers.get('authorization'),'Bearer offline-upstream');
  const body=JSON.parse(await req.text());
  assert.deepEqual(body.provider.only,['deepinfra/fp8/flex']);
  assert.equal(body.provider.allow_fallbacks,false);
  assert.equal(body.stream_options.include_usage,true);
  return new Response('data: '+JSON.stringify({model:body.model,id:'gen-offline',provider:'DeepInfra',
    usage:{cost:0.00001,prompt_tokens:3,completion_tokens:2},choices:[]})+'\\n\\ndata: [DONE]\\n\\n');
};
require('''+json.dumps(str(REPO/'pacbench/direct_runtime/fetch.cjs'))+''');
(async()=>{
  const response=await fetch('https://openrouter.ai/api/v1/chat/completions',{
    method:'POST',headers:{Authorization:'Bearer pbdirect-native'},
    body:JSON.stringify({model:'''+json.dumps(MODEL)+''',stream:true})});
  assert((await response.text()).includes('gen-offline'));
  console.log('DIRECT_RUNTIME_CONFIRMED');process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
''')
    env={k:v for k,v in os.environ.items() if 'KEY' not in k and 'TOKEN' not in k}
    env.update(PACBENCH_DIRECT='1',PACBENCH_DIRECT_UPSTREAM_KEY='offline-upstream',
       PACBENCH_DIRECT_PYTHON=str(helper),PACBENCH_DIRECT_REPO=str(REPO),
       HOME=str(tmp_path),NODE_OPTIONS='')
    if runtime=='node':cmd=[str(binary),str(script)]
    else:
        (tmp_path/'bunfig.toml').write_text('preload = ['+json.dumps(str(script))+']\n')
        cmd=[str(binary)]
    result=subprocess.run(cmd,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'DIRECT_RUNTIME_CONFIRMED' in result.stdout
    row=json.loads(ledger.read_text())['calls'][0]
    assert row['state']=='settled' and row['accounted']=='0.00001'
    assert row['project']=='native' and row['upstream_request_id']=='gen-offline'
