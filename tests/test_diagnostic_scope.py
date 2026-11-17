import json
import pytest
from tests.test_single_admission_owner import owner,body,response
from pacbench.diagnostic_scope import ENV,PROTOCOL,REVISION,PROJECT,INPUT_MANIFEST_SHA256


def scope():return {'protocol':PROTOCOL,'project':PROJECT,'source_revision':REVISION,'input_manifest_sha256':INPUT_MANIFEST_SHA256,'max_attempts':12}

def test_native_diagnostic_counts_all_attempts_and_never_exempts_new_namespace_from_original_guard(owner,monkeypatch):
    d=scope();monkeypatch.setenv(ENV,json.dumps(d));b=owner.budget
    for i in range(12):
        r=b.reserve(d['project'],body(str(i)));status,headers,data=response();b.settle(r['id'],status,data,headers=headers,body_complete=True)
    assert len(b.snapshot())==12
    with pytest.raises(RuntimeError,match='attempt ceiling'):b.reserve(d['project'],body('thirteenth'))
    assert len(b.snapshot())==12 and all(r['native_diagnostic_scope']==d for r in b.snapshot())
    with pytest.raises(RuntimeError,match='capability'):b.reserve('pacbench-judge',body())


def test_active_funded_run_still_applies_to_fresh_diagnostic_project(owner,monkeypatch):
    d=scope();monkeypatch.setenv(ENV,json.dumps(d));monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','old')
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:{'remaining_usd':'20','request_started':'2026-11-16T00:00:00+00:00','observed_at':'2026-11-16T00:00:01+00:00'})
    with owner.budget.locked() as state:
        state['funded_runs']={'old':{'id':'old','status':'active','limit_usd':'20','baseline_ledger_rows':0,
            'prepaid_safety':{'auto_recharge':'off','evidence':'Synthetic owner confirmation'}}}
        state['calls'].append({'id':'old-hold','project':'other-project','funded_run_id':'old','state':'uncertain','accounted':'19.99999'})
    before=owner.budget.snapshot()
    with pytest.raises(RuntimeError,match='spending cap'):owner.budget.reserve(d['project'],body())
    assert owner.budget.snapshot()==before


def test_uncertain_or_live_attempt_blocks_different_later_payload(owner,monkeypatch):
    d=scope();monkeypatch.setenv(ENV,json.dumps(d));b=owner.budget
    first=b.reserve(d['project'],body('first'))
    with pytest.raises(RuntimeError,match='unsettled'):b.reserve(d['project'],body('different'))
    b.settle(first['id'],200,b'{}',body_complete=True)
    with pytest.raises(RuntimeError,match='unsettled'):b.reserve(d['project'],body('different'))
    assert len(b.snapshot())==1


@pytest.mark.parametrize('field,value',[('protocol','unknown'),('project','pacbench-0123456789ab-memcal-memory'),
    ('source_revision','a'*40),('input_manifest_sha256','a'*64),('max_attempts',13)])
def test_unknown_or_changed_scope_cannot_reserve(owner,monkeypatch,field,value):
    d=scope();d[field]=value;monkeypatch.setenv(ENV,json.dumps(d))
    with pytest.raises(RuntimeError,match='capability'):owner.budget.reserve(d['project'],body())
    assert owner.budget.snapshot()==[]


def test_diagnostic_maximum_is_enforced_in_original_atomic_admission(owner,monkeypatch):
    d=scope();monkeypatch.setenv(ENV,json.dumps(d))
    from pacbench.direct_accounting import DirectBudget
    with owner.budget.locked() as state:
        state['route_configs']['luna-bedrock-global'].update(context_tokens=1050000,prompt_per_million='.45',completion_per_million='.75',max_completion_tokens=128000)
    budget=DirectBudget(owner.budget.path,profile='luna-bedrock-global')
    with pytest.raises(RuntimeError,match='maximum exceeded'):
        budget.reserve(d['project'],{**body(), 'max_tokens':128000})
    assert budget.snapshot()==[]


def test_operations_revision_is_distinct_and_keeps_settlement_and_route_guards(owner,monkeypatch):
    from pacbench.diagnostic_scope import scope_for_revision,selected
    from pacbench.provider_versions import MEMCAL_OPERATIONS_REVISION
    d=scope_for_revision(MEMCAL_OPERATIONS_REVISION,INPUT_MANIFEST_SHA256)
    assert d['project']!=PROJECT and d['protocol']!=PROTOCOL and scope_for_revision(REVISION,INPUT_MANIFEST_SHA256)==scope()
    monkeypatch.setenv(ENV,json.dumps(d))
    assert selected(d['project'],'luna-bedrock-global')==d
    with pytest.raises(RuntimeError,match='capability'):selected(d['project'],'deepseek-flex')
    first=owner.budget.reserve(d['project'],body())
    with pytest.raises(RuntimeError,match='unsettled'):owner.budget.reserve(d['project'],body('next'))
    owner.budget.settle(first['id'],200,b'{}',body_complete=True)
    with pytest.raises(RuntimeError,match='unsettled'):owner.budget.reserve(d['project'],body('next'))
    with pytest.raises(RuntimeError,match='capability'):scope_for_revision('f'*40,INPUT_MANIFEST_SHA256)


def test_four_call_stage_cannot_advance_without_explicit_scope_preserving_gate(owner,monkeypatch):
    from pacbench.diagnostic_scope import scope_for_revision
    from pacbench.provider_versions import MEMCAL_OPERATIONS_REVISION
    d=scope_for_revision(MEMCAL_OPERATIONS_REVISION,INPUT_MANIFEST_SHA256)
    monkeypatch.setenv(ENV,json.dumps(d));monkeypatch.setenv('PACBENCH_NATIVE_DIAGNOSTIC_STAGE_LIMIT','4')
    for i in range(4):
        r=owner.budget.reserve(d['project'],body(str(i)));status,headers,data=response()
        owner.budget.settle(r['id'],status,data,headers=headers,body_complete=True)
    with pytest.raises(RuntimeError,match='stage ceiling'):owner.budget.reserve(d['project'],body('fifth'))
    monkeypatch.setenv('PACBENCH_NATIVE_DIAGNOSTIC_STAGE_LIMIT','5')
    with pytest.raises(RuntimeError,match='Unknown'):owner.budget.reserve(d['project'],body('fifth'))
    monkeypatch.setenv('PACBENCH_NATIVE_DIAGNOSTIC_STAGE_LIMIT','12')
    owner.budget.reserve(d['project'],body('fifth'))
    assert owner.budget.snapshot()[-1]['native_diagnostic_scope']==d
