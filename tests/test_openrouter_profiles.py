import json,os,subprocess,sys
from pathlib import Path


def run(script):
    p=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global'},text=True,capture_output=True)
    assert p.returncode==0,p.stderr
    return json.loads(p.stdout)


def test_explicit_luna_profile_has_no_fallback_or_injected_inference_settings():
    d=run("from pacbench.openrouter_route import *; import json; print(json.dumps(with_route({'messages':[]})))")
    assert d['model']=='openai/gpt-6-luna' and d['provider']['only']==['amazon-bedrock/global']
    assert d['provider']['allow_fallbacks'] is False
    assert not {'reasoning','reasoning_effort','max_tokens','max_completion_tokens'}&d.keys()


def test_luna_tier_bounds_and_shared_ledger_preserve_old_reservations(tmp_path):
    script='''
import json
from pathlib import Path
from pacbench.openrouter_authorize_route import register
from pacbench.openrouter_route import MODEL,ENDPOINT
from pacbench.openrouter_run import catalog_parameters
from pacbench.direct_accounting import DirectBudget
p=Path(ROOT);p.mkdir()
old={'id':'old','project':'old','state':'uncertain','accounted':'19.4'}
(p/'ledger.json').write_text(json.dumps({'version':1,'config':{'cap':'20','model':'deepseek/deepseek-v4.1-flash-1017','endpoint':'deepinfra/fp8/flex'},'calls':[old]}))
(p/'authorization.json').write_text(json.dumps({'task':'pacbench-pizza-day-20261112','approved_total_usd':'20'}))
params=catalog_parameters({'data':{'id':MODEL,'endpoints':[{'model_id':MODEL,'tag':ENDPOINT,'quantization':'unknown','service_tier':None,'context_length':1050000,'max_completion_tokens':128000,'supported_parameters':['tools','tool_choice','response_format'],'pricing':{'prompt':'0.0000001','completion':'0.0000005','input_cache_write':'0.000000125','web_search':'0.01','overrides':[{'min_prompt_tokens':272000,'prompt':'0.0000002','completion':'0.00000075','input_cache_write':'0.00000025'}]}}]}})
register(params,'Explicit unit-test authorization',state_dir=p)
b=DirectBudget(p/'ledger.json');r=b.reserve('unit-memory',{'model':MODEL,'messages':[]})
assert b.snapshot()[0]=={**old,'accounted_upper_bound_usd':19.4}
assert r['request']['provider']['only']==[ENDPOINT]
try:b.reserve('another-memory',{'model':MODEL,'messages':[]})
except RuntimeError as e:assert 'spending cap' in str(e)
else:raise AssertionError('Concurrent reservations exceeded shared cap')
try:b.reserve('pacbench-judge',{'model':MODEL,'messages':[]})
except RuntimeError as e:assert 'import-only' in str(e)
else:raise AssertionError('Benchmark scope was broadened')
print(json.dumps({'params':params,'bound':b.snapshot()[1]['reserved'],'old_preserved':json.loads((p/'ledger.json').read_text())['calls'][0]==old}))
'''.replace('ROOT',repr(str(tmp_path/'state')))
    d=run(script)
    assert d['params']['prompt_per_million']=='0.45000000' and d['params']['completion_per_million']=='0.75000000'
    assert d['bound']=='0.56850000' and d['old_preserved']


def test_budget_serial_openviking_condition_is_explicit_and_changes_only_concurrency():
    d=run("""
import os,json
from pathlib import Path
from provider_adapters.openviking_local import plan_config
before=plan_config(Path('/unit'),'openai/gpt-6-luna','https://openrouter.ai/api/v1','inert',port=1)
os.environ['PACBENCH_OPENVIKING_IMPORT_CONDITION']='luna-budget-serial-v1'
after=plan_config(Path('/unit'),'openai/gpt-6-luna','https://openrouter.ai/api/v1','inert',port=1)
assert 'max_concurrent' not in before['vlm']
assert after['vlm'].pop('max_concurrent')==1
print(json.dumps({'otherwise_identical':before==after}))
""")
    assert d['otherwise_identical']
