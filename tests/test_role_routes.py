"""Mixed routes exercise real reservations and client hooks against fake responses."""
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench.role_routes import CONDITION, profile_for_project, memory_model
from pacbench.openrouter_route import PROFILES


@pytest.fixture
def split(tmp_path,monkeypatch):
    monkeypatch.setenv('PACBENCH_ROLE_ROUTES',CONDITION)
    monkeypatch.setenv('PACBENCH_RESERVATION_POLICY','shared-context-v1')
    def config(name):
        return dict(cap='20',context_tokens=1050000,max_completion_tokens=128000,
                    prompt_per_million='.45',completion_per_million='.75',
                    model=PROFILES[name]['model'],endpoint=PROFILES[name]['endpoint'])
    lunar=config('luna-bedrock-global');lunar['request_scope']='import-and-connectivity-probes'
    path=tmp_path/'ledger.json';path.write_text(json.dumps(dict(version=1,
        config=config('deepseek-flex'),route_configs={'luna-bedrock-global':lunar},calls=[])))
    def budget(project):return DirectBudget(path,profile=profile_for_project(project))
    return path,budget


def reserve(factory,project):
    b=factory(project)
    return b,b.reserve(project,dict(model=b.model,messages=[{'role':'user','content':'synthetic'}]))


def settle(b,r,cost='.001'):
    b.settle(r['id'],200,json.dumps(dict(id='gen-'+r['id'],model=b.model,
        provider=b.profile['provider_name'],usage=dict(cost=cost,prompt_tokens=2,completion_tokens=3),
        choices=[dict(finish_reason='stop',message={'content':'ok'})])).encode(),body_complete=True)


def test_concurrent_roles_reverse_settlement_and_delayed_native_work(split):
    path,factory=split;projects=['unit-memory','unit-driver','pacbench-judge']
    with ThreadPoolExecutor(max_workers=3) as pool:pairs=list(pool.map(lambda p:reserve(factory,p),projects))
    rows=json.loads(path.read_text())['calls'];assert len(rows)==3
    assert {r['accounting_role'] for r in rows}=={'memory_provider','hermes','judge'}
    assert {r['route_profile'] for r in rows}=={'deepseek-flex','luna-bedrock-global'}
    assert rows[0]['reservation_policy'] in {'shared-context-v1','independent-input-output-v1'}
    for b,r in reversed(pairs):
        assert r['request']['provider']['only']==[b.endpoint]
        assert r['request']['provider']['allow_fallbacks'] is False
        assert not any(k in r['request'] for k in ['max_tokens','reasoning','reasoning_effort'])
        settle(b,r)
    # A retrieval tool's later memory work remains Luna after driver/judge finish.
    b,r=reserve(factory,'unit-memory');assert b.model=='openai/gpt-6-luna';settle(b,r)
    rows=json.loads(path.read_text())['calls'];assert sum(Decimal(r['accounted']) for r in rows)==Decimal('.004')
    assert all(r['state']=='settled' for r in rows)
    assert next(r for r in rows if r['project']=='unit-driver')['reservation_policy']=='independent-input-output-v1'


def test_aggregate_cap_contention_retains_old_hold(split):
    path,factory=split;state=json.loads(path.read_text());old={'id':'historical','project':'old','state':'uncertain','accounted':'19.1'};state['calls']=[old];path.write_text(json.dumps(state))
    b,r=reserve(factory,'unit-memory')
    with pytest.raises(RuntimeError,match='spending cap'):reserve(factory,'unit-driver')
    assert json.loads(path.read_text())['calls'][0]==old
    before=json.loads(path.read_text())['calls'][1]['accounted']
    b.settle(r['id'],0);assert json.loads(path.read_text())['calls'][1]['accounted']==before


def test_mismatched_role_model_and_settlement_fail_closed(split):
    path,factory=split;b,r=reserve(factory,'unit-memory');driver=factory('unit-driver')
    with pytest.raises(ValueError,match='row/profile'):driver.settle(r['id'],200,b'{}')
    with pytest.raises(ValueError,match='capability/profile'):driver.reserve('unit-memory',{'model':driver.model})
    with pytest.raises(ValueError,match='Exact model'):b.reserve('unit-memory',{'model':driver.model})
    with pytest.raises(ValueError,match='Conflicting provider'):b.reserve('unit-memory',{'model':b.model,'provider':{'only':['wrong']}})
    with pytest.raises(ValueError,match='alternative routing'):b.reserve('unit-memory',{'model':b.model,'plugins':[]})
    with pytest.raises(ValueError,match='Unknown accounting role'):profile_for_project('setup-check')
    assert len(json.loads(path.read_text())['calls'])==1


def test_actual_http_boundary_selects_memory_inside_driver_process(split,monkeypatch):
    path,factory=split
    import pacbench.direct_http as hook
    monkeypatch.setenv('PACBENCH_DIRECT','1');monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY','offline-secret')
    monkeypatch.setattr(hook,'live_budget',lambda *,project:factory(project))
    receipts=[]
    for project in ['unit-driver','unit-memory','pacbench-judge']:
        model=PROFILES[profile_for_project(project)]['model']
        r=hook.admitted('https://openrouter.ai/api/v1/chat/completions',{'Authorization':'Bearer pbdirect-'+project},json.dumps({'model':model,'messages':[]}).encode())
        receipts.append(r);assert json.loads(r['body'])['provider']['only']==[r['budget'].endpoint]
    for r in reversed(receipts):settle(r['budget'],r)
    assert all(x['state']=='settled' for x in json.loads(path.read_text())['calls'])


def test_native_configs_preserve_luna_compatibility_and_serial_condition(split):
    from pacbench.native_pilot import hindsight_model_settings,mem0_model_settings
    from pacbench.openrouter_direct import provider_plan
    from provider_adapters.openviking_local import plan_config
    model=memory_model();assert hindsight_model_settings('unit',model,'url','inert')[0]['llm_provider']=='openai'
    assert mem0_model_settings(model,'url',openrouter=True)['is_reasoning_model'] is True
    for n in ['memcal','mem0','hindsight','honcho','openviking','memos','reme','cognee','supermemory']:
        plan=provider_plan(n,Path('/offline')/n);assert plan['model']==model and plan['endpoint']=='amazon-bedrock/global'
    assert plan_config(Path('/offline'),model,'url','inert',port=1)['vlm']['max_concurrent']==1


def test_node_cli_settles_by_original_row_even_after_role_env_removed(split,monkeypatch,capsys):
    import io,sys
    import pacbench.direct_accounting as a
    path,factory=split;b,r=reserve(factory,'unit-memory')
    monkeypatch.delenv('PACBENCH_ROLE_ROUTES')
    monkeypatch.setattr(a,'live_budget',lambda **kw:DirectBudget(path,profile=kw.get('profile')))
    monkeypatch.setattr(sys,'argv',['accounting','settle'])
    data=dict(id='gen-offline',model=b.model,usage=dict(cost='.001',prompt_tokens=2,completion_tokens=3),choices=[])
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(dict(id=r['id'],status=200,data=json.dumps(data),body_complete=True))))
    a.main();assert json.loads(capsys.readouterr().out)=={}
    row=json.loads(path.read_text())['calls'][0]
    assert row['route_profile']=='luna-bedrock-global' and row['state']=='settled'


def test_provision_keeps_hermes_deepseek_and_native_service_luna(split,monkeypatch,tmp_path):
    import pacbench.native_pilot as pilot
    import pacbench.live_trials as trials
    import pacbench.catalog_plugins as catalog
    import provider_adapters.cognee_runtime as native
    monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter');monkeypatch.setenv('PACBENCH_DIRECT','1')
    monkeypatch.setattr(pilot,'require_live_cost_boundary',lambda:None)
    monkeypatch.setattr(trials,'require_live_trials_ready',lambda:None)
    monkeypatch.setattr(pilot,'require_trial_provider',lambda *a,**k:None)
    monkeypatch.setattr(catalog,'CATALOG',{})
    observed={}
    def home(path,**kw):
        observed['hermes']=kw;path.mkdir(parents=True)
    def service(root,env,model,base,key):
        observed['native']={'model':model,'key':key};return lambda:None
    monkeypatch.setattr(pilot.harness,'provision_home',home)
    monkeypatch.setattr(native,'start',service)
    root=tmp_path/'fresh';root.mkdir()
    env=pilot.provision(root,'cognee',PROFILES['deepseek-flex']['model'],inference_route='openrouter')
    assert observed['hermes']['model']==PROFILES['deepseek-flex']['model']
    assert observed['native']['model']==PROFILES['luna-bedrock-global']['model']
    assert observed['native']['key'].endswith('-memory')
    assert env['OPENROUTER_API_KEY'].endswith('-driver')
    assert json.loads((root/'role-routes.json').read_text())['condition']==CONDITION


def test_cohort_limit_coordinates_roles_and_cannot_change_midrun(split,monkeypatch):
    path,factory=split;monkeypatch.setenv('PACBENCH_COHORT_ID','a'*32)
    monkeypatch.setenv('PACBENCH_COHORT_LIMIT_USD','.6')
    b,r=reserve(factory,'unit-memory')
    # Plenty of aggregate task headroom, but separate cohort bound is binding.
    with pytest.raises(RuntimeError,match='Cohort liability envelope reached'):reserve(factory,'unit-driver')
    settle(b,r);b,r=reserve(factory,'unit-driver');settle(b,r)
    monkeypatch.setenv('PACBENCH_COHORT_LIMIT_USD','8')
    with pytest.raises(RuntimeError,match='envelope changed'):reserve(factory,'unit-memory')
    assert len(json.loads(path.read_text())['calls'])==2


def test_frozen_week_all333_targets_have_chronological_source_gates(tmp_path):
    from pacbench.native_pilot import read_prepared
    from pacbench.evaluation import load_evaluations
    from pacbench.document_ingestion import prepare_sources,ready_sources,validate_evaluation_clocks
    from pacbench.provider_lineup import ACTIVE_PROVIDERS
    from datetime import datetime
    source=Path(__file__).resolve().parents[1]/'artifacts/pizza-week-hybrid-stream-v7-20261113'
    messages,contract,resolved=read_prepared(source,150)
    plan,checks=load_evaluations(source,messages,resolved)
    schedule=prepare_sources(tmp_path,messages,resolved['timezone'])
    assert len(checks)==37 and len(ACTIVE_PROVIDERS)==9 and len(messages)==125
    validate_evaluation_clocks(schedule,ACTIVE_PROVIDERS,[x['at'] for x,c,p in checks],resolved['timezone'],chronological=True)
    for provider in ACTIVE_PROVIDERS:
        prior=set()
        for row,c,p in checks:
            visible={i for b in ready_sources(schedule,provider,row['at'],resolved['timezone']) for i in b['source_ids']}
            assert prior<=visible
            assert all(datetime.fromisoformat(m['collected_at'])<=datetime.fromisoformat(row['at']) for m in messages if m['source_id'] in visible)
            prior=visible
        assert len(prior)==125


def test_resumed_child_envelope_does_not_double_count_spent_or_unknown():
    from pacbench.role_routes import liability_envelope
    calls=[{'accounted':'12','state':'uncertain'}, {'cohort_id':'unit','accounted':'.1','state':'settled'},
           {'cohort_id':'unit','accounted':'.5','state':'uncertain'}]
    r=liability_envelope(calls,'8','unit')
    assert r['funded'] and Decimal(r['remaining_envelope_usd'])==Decimal('7.4')
    assert Decimal(r['available_usd'])==Decimal('7.4')
    assert not liability_envelope(calls,'8','new-child-id')['funded']
