"""A diagnostic restart must prove the original source survived unchanged."""
from copy import deepcopy
import pytest
from provider_adapters.honcho_local import verify_persisted_messages


def message():
    return {'id':'native-1','session_id':'source-thread','peer_id':'Riley',
        'content':'See you Saturday.','created_at':'2026-10-19T09:00:00-04:00',
        'metadata':{'source_id':'message-1','thread':'dm'}}


def test_native_ids_and_source_survive_timezone_equivalent_database_export():
    sent=message();actual=deepcopy(sent);actual['created_at']='2026-10-19T13:00:00+00:00'
    assert verify_persisted_messages([{'messages':[sent]}],[actual])==['native-1']


@pytest.mark.parametrize('change',['id','session_id','peer_id','content','created_at','metadata','missing','duplicate'])
def test_reopen_rejects_missing_replaced_or_changed_native_sources(change):
    sent=message();actual=deepcopy(sent)
    if change not in {'missing','duplicate'}:
        actual[change]={'source_id':'other'} if change=='metadata' else 'changed'
    rows=[] if change=='missing' else [actual,actual] if change=='duplicate' else [actual]
    with pytest.raises(ValueError):verify_persisted_messages([{'messages':[sent]}],rows)


@pytest.mark.parametrize('limit',[None,8192])
def test_documented_model_setting_changes_only_resolved_deriver_allowance(limit):
    import json,os,subprocess
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'vendor/honcho-local'
    python=source/'.venv/bin/python'
    if not python.exists():pytest.skip('Native Honcho runtime not installed')
    env={k:v for k,v in os.environ.items() if not k.startswith(('DERIVER_','LLM_'))}
    env.update(PYTHONPATH=str(source),HONCHO_CONFIG_TOML_DISABLED='1')
    if limit is not None:env['DERIVER_MODEL_CONFIG__MAX_OUTPUT_TOKENS']=str(limit)
    script='''
import json
from src.config import settings
d=settings.DERIVER
print(json.dumps({'limit':d.MODEL_CONFIG.max_output_tokens,'global_limit':settings.LLM.DEFAULT_MAX_TOKENS,
 'flush':d.FLUSH_ENABLED,'age':d.REPRESENTATION_BATCH_MAX_AGE_SECONDS,
 'target':d.REPRESENTATION_BATCH_TARGET_INPUT_TOKENS,'effort':d.MODEL_CONFIG.thinking_effort}))
'''
    p=subprocess.run([str(python),'-c',script],cwd=source,env=env,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)=={'limit':limit,'global_limit':2500,'flush':False,
        'age':1800,'target':1024,'effort':None}
