"""Real direct-ledger integration; synthetic wire only, no external calls."""
import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from pacbench.direct_accounting import DirectBudget, MODEL
from pacbench.live_dispatch import CONDITION, OwnershipConflict
from pacbench.direct_http import Capture, before_send
from pacbench.receipt_costs import direct_evidence, request_record
from tests.test_direct_accounting import budget, reply


def reserve(budget):
    return budget.reserve('offline-driver', {'model':MODEL,'messages':[]})


@pytest.fixture(autouse=True)
def track(monkeypatch):
    monkeypatch.setenv('PACBENCH_DISPATCH_TRACKING',CONDITION)


def test_atomic_identity_predispatch_and_exact_receipt_reporting(budget):
    r=reserve(budget);row=budget.snapshot()[0];hold=row['accounted']
    assert row['dispatch_execution']=='prepared'
    assert row['dispatch_context']['reservation_id']==r['id']
    assert row['dispatch_owner']['pid']==os.getpid()
    before_send({**r,'budget':budget})
    capture=Capture({**r,'budget':budget},200)
    data=reply('0.00001000000000000000000000003')
    capture.add(data);capture.finish(complete=True)
    row=budget.snapshot()[0];assert row['dispatch_execution']=='response_ended'
    assert row['reserved']==hold and row['settled_charge_usd']=='0.00001000000000000000000000003'
    record=request_record(direct_evidence(budget.path,row))
    assert record['actual_usd']==row['settled_charge_usd']
    assert record['billing_provider']=='OpenRouter'
    assert record['dispatch_context']['attempt_id']==r['id']
    budget.settle(r['id'],200,data,body_complete=True)
    assert len(budget.snapshot())==1
    with pytest.raises(OwnershipConflict,match='replay'):reserve(budget)


def test_interrupted_stream_keeps_early_id_and_full_hold(budget):
    r=reserve(budget);hold=budget.snapshot()[0]['accounted'];before_send({**r,'budget':budget})
    capture=Capture({**r,'budget':budget},200)
    capture.add(b'data: {"id":"gen-early","choices":[{"delta":{"content":"PRIVATE"}}]}\n\n')
    early=budget.snapshot()[0]
    assert early['upstream_request_id']=='gen-early' and early['state']=='reserved'
    evidence=direct_evidence(budget.path,early)
    assert evidence['receipt_generation_ids']==['gen-early']
    assert request_record(evidence)['actual_usd'] is None
    capture.add(b'data: broken');capture.finish()
    row=budget.snapshot()[0]
    assert row['accounted']==hold and row['dispatch_execution']=='ambiguous'
    assert row['dispatch_events'][-1]['kind']=='local_disconnect'
    assert 'PRIVATE' not in ''.join(p.read_text() for p in budget.path.parent.rglob('*.json'))
    with pytest.raises(RuntimeError):reserve(budget)


def test_crash_after_reservation_cannot_be_taken_over_or_replayed(budget):
    script='''import sys
from pacbench.direct_accounting import DirectBudget,MODEL
from pacbench.live_dispatch import CONDITION
import os
os.environ['PACBENCH_DISPATCH_TRACKING']=CONDITION
b=DirectBudget(sys.argv[1]);b.reserve('offline-driver',{'model':MODEL,'messages':[]})
'''
    subprocess.run([os.sys.executable,'-c',script,str(budget.path)],check=True,cwd=Path(__file__).parents[1])
    row=budget.snapshot()[0];before=budget.path.read_bytes()
    assert row['dispatch_execution']=='prepared'
    with pytest.raises(OwnershipConflict):budget.mark_dispatch_possible(row['id'])
    with pytest.raises(OwnershipConflict):budget.settle(row['id'],200,reply(),body_complete=True)
    with pytest.raises(RuntimeError):reserve(budget)
    assert budget.path.read_bytes()==before


def test_pid_reuse_and_legacy_unknown_fail_without_mutation(budget,monkeypatch):
    r=reserve(budget);before=budget.path.read_bytes()
    from pacbench import live_dispatch
    actual=live_dispatch.process_identity()
    monkeypatch.setattr(live_dispatch,'process_identity',lambda pid=None:{**actual,'start_ticks':actual['start_ticks']+1})
    with pytest.raises(OwnershipConflict):budget.mark_dispatch_possible(r['id'])
    assert budget.path.read_bytes()==before
    monkeypatch.delenv('PACBENCH_DISPATCH_TRACKING')
    old=budget.reserve('legacy-driver',{'model':MODEL,'messages':[]})
    monkeypatch.setenv('PACBENCH_DISPATCH_TRACKING',CONDITION);before=budget.path.read_bytes()
    with pytest.raises(OwnershipConflict,match='legacy'):budget.mark_dispatch_possible(old['id'])
    assert budget.path.read_bytes()==before


def test_concurrent_reservation_and_second_dispatch_fail_closed(budget):
    def attempt(_):
        try:return reserve(budget)
        except RuntimeError:return None
    with ThreadPoolExecutor(max_workers=6) as pool:rs=[r for r in pool.map(attempt,range(8)) if r]
    assert len(rs)==1 and len(budget.snapshot())==1
    budget.mark_dispatch_possible(rs[0]['id']);before=budget.path.read_bytes()
    with pytest.raises(OwnershipConflict):budget.mark_dispatch_possible(rs[0]['id'])
    assert budget.path.read_bytes()==before


def test_sidecar_error_does_not_erase_primary_usage_or_answer(budget,monkeypatch):
    r=reserve(budget);budget.mark_dispatch_possible(r['id'])
    def fail(*args):raise OSError('synthetic storage failure')
    monkeypatch.setattr('pacbench.receipt_costs.persist_metadata',fail)
    budget.settle(r['id'],200,reply(),body_complete=True)
    row=budget.snapshot()[0]
    assert row['state']=='settled' and row['receipt_metadata_error']=='OSError'
    assert request_record(direct_evidence(budget.path,row))['actual_usd']=='0.00001'


def test_tampered_billing_receipt_never_becomes_actual(budget):
    r=reserve(budget);budget.mark_dispatch_possible(r['id']);budget.settle(r['id'],200,reply(),body_complete=True)
    row=budget.snapshot()[0]
    # Isolate hash-bound receipt recovery, excluding independently valid sidecars.
    row={**row,'id':'different-attempt'}
    record=direct_evidence(budget.path,row)
    assert record['receipt_metadata_incomplete']
    assert request_record(record)['actual_usd'] is None


def test_full_launcher_refuses_legacy_owners_before_network(budget,monkeypatch):
    import scripts.run_three_arm_revised_week as launcher
    with budget.locked() as state:
        state['calls'].append({'id':'legacy','state':'reserved','accounted':'.17'})
    before=budget.path.read_bytes()
    for key,value in {'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global','PACBENCH_ROLE_ROUTES':'luna-all-roles-v1',
        'PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1','PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768',
        'PACBENCH_RESERVATION_POLICY':'openai-cache-total-shared-v1','PACBENCH_FUNDED_RUN_ID':'pizza-week-scored-prepaid-20261114'}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr('pacbench.direct_accounting.live_budget',lambda:budget)
    def no_network(*args,**kwargs):pytest.fail('Known ownership block must precede network/setup')
    monkeypatch.setattr('pacbench.openrouter_run.fetch_catalog',no_network)
    monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',no_network)
    monkeypatch.setattr('provider_adapters.embedding_health.check',no_network)
    with pytest.raises(RuntimeError,match='Original ownership guard'):launcher.preflight()
    assert budget.path.read_bytes()==before


def test_final_stream_parse_does_not_write_per_token(budget,monkeypatch):
    r=reserve(budget);budget.mark_dispatch_possible(r['id'])
    from pacbench import receipt_costs
    original=receipt_costs.persist_metadata;calls=[]
    def counted(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(receipt_costs,'persist_metadata',counted)
    event=b'data: {"id":"gen-many","choices":[{"delta":{"content":"PRIVATE"}}]}\n\n'
    tail=b'data: {"id":"gen-many","usage":{"cost":0.00001,"prompt_tokens":3,"completion_tokens":2}}\n\n'
    budget.settle(r['id'],200,event*2000+tail,headers={'x-request-id':'opaque-request'},body_complete=True)
    assert len(calls)<=2
    assert budget.snapshot()[0]['settled_charge_usd']=='0.00001'
