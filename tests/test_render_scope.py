import json
import subprocess
import sys
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench import role_routes


def test_accounting_can_validate_render_scope_without_loading_the_writer():
    probe = """
import os
import sys

os.environ['PACBENCH_EXECUTION_SCOPE'] = 'source-render-v1'
os.environ['PACBENCH_FUNDED_RUN_ID'] = 'offline'
from pacbench.role_routes import validate_render_scope

assert validate_render_scope('scored-week-roles', 'pacbench-authoring', 'luna-bedrock-global')
assert 'pacbench.render_sources' not in sys.modules
assert 'pacbench.corpus.authoring' not in sys.modules
"""
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def render_budget(tmp_path,monkeypatch):
    monkeypatch.setenv('PACBENCH_EXECUTION_SCOPE','source-render-v1')
    monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','existing')
    monkeypatch.setenv('PACBENCH_ROLE_ROUTES','luna-all-roles-v1')
    monkeypatch.setattr(role_routes,'PROFILE_NAME','luna-bedrock-global')
    snap={'remaining_usd':'1','request_started':'2026-11-15T00:00:00+00:00'}
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:snap)
    cfg=dict(cap='20',model='openai/gpt-6-luna',endpoint='amazon-bedrock/global',context_tokens=100,
        max_completion_tokens=50,prompt_per_million='1',completion_per_million='2',request_scope='scored-week-roles')
    p=tmp_path/'ledger.json';p.write_text(json.dumps(dict(version=1,config={'cap':'20'},
        route_configs={'luna-bedrock-global':cfg},calls=[],funded_runs={'existing':{
        'id':'existing','status':'active','limit_usd':'.00025','baseline_ledger_rows':0,
        'prepaid_safety':{'auto_recharge':'off','evidence':'offline-test'}}})))
    return DirectBudget(p,profile='luna-bedrock-global')

def test_render_writer_verifier_real_reserve_settle_and_cap(render_budget):
    b=render_budget;row=b.reserve('pacbench-authoring',{'model':b.model,'messages':[]})
    assert b.snapshot()[0]['state']=='reserved'
    assert b.snapshot()[0]['execution_scope']=='source-render-v1'
    assert b.snapshot()[0]['accounting_role']=='source_writer'
    with pytest.raises(RuntimeError,match='cap reached'):
        b.reserve('pacbench-scene-verifier',{'model':b.model,'messages':[]})
    b.settle(row['id'],200,json.dumps({'model':b.model,'provider':'Amazon Bedrock',
        'usage':{'cost':'.000001','prompt_tokens':1,'completion_tokens':1}}).encode())
    assert b.snapshot()[0]['state']=='settled'
    b.reserve('pacbench-scene-verifier',{'model':b.model,'messages':[]})

@pytest.mark.parametrize('project',['pacbench-judge','x-driver','x-memory','unknown','pacbench-route-probe-test'])
def test_render_denies_other_roles_without_ledger_change(render_budget,project):
    b=render_budget;before=b.path.read_bytes()
    with pytest.raises((ValueError,RuntimeError)):b.reserve(project,{'model':b.model,'messages':[]})
    assert b.path.read_bytes()==before

@pytest.mark.parametrize('mode',['unknown','import-and-connectivity-probes'])
def test_unknown_or_import_scope_cannot_be_repurposed(render_budget,mode):
    b=render_budget
    with b.locked() as s:s['route_configs']['luna-bedrock-global']['request_scope']=mode
    b=DirectBudget(b.path,profile='luna-bedrock-global');before=b.path.read_bytes()
    with pytest.raises(RuntimeError):b.reserve('pacbench-authoring',{'model':b.model,'messages':[]})
    assert b.path.read_bytes()==before

def test_scored_mode_still_rejects_authoring(render_budget,monkeypatch):
    monkeypatch.delenv('PACBENCH_EXECUTION_SCOPE')
    with pytest.raises((RuntimeError,ValueError)):render_budget.reserve('pacbench-authoring',{})

def test_render_requires_funded_prepaid_and_credit(render_budget,monkeypatch):
    b=render_budget
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:{'remaining_usd':'0','request_started':'2026-11-15T00:00:00+00:00'})
    with pytest.raises(RuntimeError,match='prepaid'):b.reserve('pacbench-authoring',{'model':b.model,'messages':[]})
    monkeypatch.delenv('PACBENCH_FUNDED_RUN_ID')
    with pytest.raises(RuntimeError,match='prepaid'):b.reserve('pacbench-authoring',{'model':b.model,'messages':[]})

def test_render_denies_roles_with_split_disabled(render_budget,monkeypatch):
    monkeypatch.delenv('PACBENCH_ROLE_ROUTES')
    for role in ['pacbench-judge','native-memory','actor-driver','unknown']:
        with pytest.raises(RuntimeError):render_budget.reserve(role,{})

def test_render_entry_only_realizes_scenes_with_stubs(tmp_path):
    from tests.test_scene_local_authoring import week,exact
    from tests.test_supplied_messages import checker
    from pacbench.render_sources import render
    spec=week([exact(),{'id':'two','thread':'friends','time':'7 pm','scene':'Sam and I discuss music.','facts':[]}])
    root=tmp_path/'render';calls=[]
    writer=lambda _:json.dumps({'dialogue':'Sam: Nice song.\nMe: I like it.','writer_note':'Brief chat.'})
    assert render(spec,root,writer,checker(root,calls),model='offline')==[]
    status=json.loads((root/'render-status.json').read_text())
    assert status['completed_scenes']==2 and len(calls)==1
    assert not (root/'week.json').exists() and not (root/'evaluation-plan.json').exists()
    assert not list(root.glob('**/chat.db'))
    assert 'prompt' not in (root/'transcript.txt').read_text()
