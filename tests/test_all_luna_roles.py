"""All-Luna route and structured judge checks run without any upstream traffic."""
import json,os,subprocess,sys
from pathlib import Path


def run(script):
    p=subprocess.run([sys.executable,'-c',script],env={**os.environ,
        'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global','PACBENCH_ROLE_ROUTES':'luna-all-roles-v1'},
        text=True,capture_output=True)
    assert p.returncode==0,p.stderr
    return json.loads(p.stdout)


def test_every_role_luna_exact_route_scope_and_historical_accounting(tmp_path):
    script='''
import json
from pathlib import Path
from pacbench.role_routes import profile_for_project,record
from pacbench.openrouter_authorize_route import register
from pacbench.direct_accounting import DirectBudget
from pacbench.openrouter_route import MODEL,ENDPOINT
p=Path(ROOT);p.mkdir()
params=dict(context_tokens=1050000,max_completion_tokens=128000,prompt_per_million='.45',completion_per_million='.75')
old={'id':'old','project':'old','state':'uncertain','accounted':'10'}
(p/'ledger.json').write_text(json.dumps({'version':1,'config':{'cap':'20','model':'deepseek/deepseek-v4.1-flash-1017','endpoint':'deepinfra/fp8/flex'},'calls':[old]}))
(p/'authorization.json').write_text(json.dumps({'task':'pacbench-pizza-day-20261112','approved_total_usd':'20'}))
register(params,'Offline unit authorization',state_dir=p)
b=DirectBudget(p/'ledger.json')
try:b.reserve('unit-driver',{'model':MODEL,'messages':[]})
except RuntimeError as e:assert 'import-only' in str(e)
else:raise AssertionError('Import-only grant allowed scoring')
register(params,'Offline explicit all-role authorization',state_dir=p,scope='scored-week-roles')
b=DirectBudget(p/'ledger.json')
for project in ['unit-memory','unit-driver','pacbench-judge']:
 assert profile_for_project(project)=='luna-bedrock-global'
 r=b.reserve(project,{'model':MODEL,'messages':[]})
 assert r['request']['provider']['only']==[ENDPOINT] and r['request']['provider']['allow_fallbacks'] is False
 assert not {'max_tokens','max_completion_tokens','reasoning_effort','reasoning'}&r['request'].keys()
 b.settle(r['id'],200,json.dumps({'model':MODEL,'usage':{'cost':'.001','prompt_tokens':2,'completion_tokens':3},'choices':[]}).encode())
s=json.loads((p/'ledger.json').read_text());assert s['calls'][0]==old and s['config']['cap']=='20'
assert {r['accounting_role'] for r in s['calls'][1:]}=={'memory_provider','hermes','judge'}
assert all(r['role_route_condition']=='luna-all-roles-v1' for r in s['calls'][1:])
assert all(r['reservation_policy']=='shared-context-v1' for r in s['calls'][1:])
assert len(s['route_authorizations'])==2
assert record()['judge']['model']==MODEL and record()['hermes']['model']==MODEL
print(json.dumps({'all_roles':True,'history_preserved':True}))
'''.replace('ROOT',repr(str(tmp_path/'state')))
    assert run(script)=={'all_roles':True,'history_preserved':True}


def test_luna_model_client_builds_structured_existing_judge_and_preserves_grade_shape(tmp_path):
    script='''
import json
from pathlib import Path
from pacbench.model_calls import ModelClient,ModelOutput
from pacbench.semantic_grade import Claim,grade_native_semantic,DATE_SCOPED_JUDGE_PROTOCOL,DIAGNOSTIC_VERSION
from pacbench.openrouter_route import MODEL
from pacbench.role_routes import record
root=Path(ROOT);root.mkdir()
client=ModelClient(key='offline',model=MODEL,record_dir=root/'model-calls',json_mode=True,reasoning_effort='low')
payload={'verdicts':[{'id':'day','verdict':'correct','reason':'Exact date.'}],
 'recovery':[{'id':'day','kind':'provided','reason':'Explicit.'}], 'interaction':'answer','benchmark_error':None,
 'evidence_diagnostic':{'claims':[{'id':'day','status':'supported','reason':'Shown.', 'citations':[{'id':'prefetch','quote':'Dinner is Saturday October 24.'}]}],'extra_assertions':[]}}
def request(body,*,protocol):
 assert body['model']==MODEL and body['response_format']=={'type':'json_object'}
 assert body['reasoning_effort']=='low' and 'max_tokens' not in body and 'max_completion_tokens' not in body
 assert record()['judge']['endpoint']=='amazon-bedrock/global'
 text=json.dumps(payload);folder=root/'model-calls';folder.mkdir(exist_ok=True)
 path=folder/'offline.json';path.write_text(json.dumps({'status':'ok','text':text,'request':body,'model':MODEL,'usage':{}}))
 return ModelOutput(text,MODEL,{},str(path))
client._request=request
claims=(Claim('day','Dinner is Saturday October 24.',('source-1',)),)
grade=grade_native_semantic('dinner','When is dinner?','Saturday October 24.',claims,client,
 record_path=root/'judgment.json',judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL,
 assess_recovery=True,assess_interaction=True,diagnostic_version=DIAGNOSTIC_VERSION,
 diagnostic_inputs={'answer_visible':[{'id':'prefetch','text':'Dinner is Saturday October 24.'}],
 'source_evidence':[{'id':'source-1','text':'Dinner is Saturday October 24.'}]})
saved=json.loads((root/'judgment.json').read_text())
assert grade.slots[0].verdict=='correct' and json.loads(saved['response'])['verdicts'][0]['verdict']=='correct'
assert saved['judge_protocol']==DATE_SCOPED_JUDGE_PROTOCOL
print(json.dumps({'structured_judge':True,'protocol':DATE_SCOPED_JUDGE_PROTOCOL}))
'''.replace('ROOT',repr(str(tmp_path/'judge')))
    assert run(script)['structured_judge'] is True
