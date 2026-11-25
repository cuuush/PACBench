from dataclasses import asdict
from hashlib import sha256
import json
import os

import pytest

from pacbench import supermemory_source_admission as source, workflow_capability as cap
from pacbench.canary_capability import binding, sha
from pacbench.direct_accounting import live_budget
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.live_dispatch import CONDITION, process_identity
from pacbench.openrouter_route import with_route
from pacbench.supermemory_source_effect import BOUNDARY, SourceEffect
from pacbench.supermemory_source_qualify import account_lock
from pacbench.workflow_spec import SPEC

DOCUMENT = 'd' * 22
UPDATE = 'u' * 22


@pytest.fixture
def scoped_receipts(tmp_path, monkeypatch):
    campaign = tmp_path/'campaign'
    root = campaign/'first-source'/'supermemory'
    root.mkdir(parents=True)
    (campaign/'execution.lock').touch()
    (root/'source-membership').mkdir()
    folder = root/'prepaid-verification'
    folder.mkdir()
    raw = b'{"data":{"total_credits":2,"total_usage":1}}'
    digest = sha256(raw).hexdigest()
    (folder/(digest+'.private.json')).write_bytes(raw)
    confirmation = tmp_path/'confirmation.json'
    confirmation.write_text('{"auto_recharge":"off","confirmed_by":"offline-fixture","confirmed_at":"2026-11-22T00:00:00Z"}')
    first, _, basis = source.source_release()
    parent = process_identity()
    worker = [SourceEffect(UPDATE, DOCUMENT)]
    monkeypatch.setattr(source, 'effect_for', lambda _: worker[0])
    monkeypatch.setattr(source.os, 'getppid', lambda: parent['pid'])
    scope_path = root/'workflow-scope.json'
    project = source.project_name(root)
    for key,value in {
        'PACBENCH_DISPATCH_TRACKING':CONDITION, 'PACBENCH_STOP_FILE':str(root/'STOP'),
        'PACBENCH_OBSERVATIONAL_COHORT_STOP':str(campaign/'ACCOUNT_STOP'),
        'PACBENCH_RECEIPT_JOURNAL':str(root/'receipts.jsonl'),
        'PACBENCH_OPENROUTER_PROFILE':source.PROFILE,
        'PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768',
        'PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1',
    }.items():
        monkeypatch.setenv(key,value)
    with account_lock(campaign) as fd:
        scope = dict(admission=source.SCOPE, root=str(root), run_id=root.parent.name,
            provider='supermemory', projects={'supermemory':{'memory':project}}, controller=parent,
            lock_fd=fd, native_effect=BOUNDARY, cutoff=first[0].cutoff, frozen_inputs=basis,
            spec_sha256=sha(SPEC), namespace='supermemory-data-first-source-supermemory',
            prepaid={'receipt_sha256':digest,'remaining_usd':'1','source':'OpenRouter GET /api/v1/credits'},
            auto_recharge_evidence=binding(confirmation))
        scope_path.write_text(json.dumps(scope))
        monkeypatch.setenv(cap.ENV,json.dumps(binding(scope_path)))
        yield root,project,worker,parent,scope


def model_call(budget, project, effect, sender, prompt, *, status=200, complete=True, cost=True):
    body = with_route({'messages':[{'role':'user','content':prompt}]}, profile=source.PROFILE)
    reservation = budget.reserve(project, body, sender=sender)
    budget.mark_dispatch_possible(reservation['id'], sender=sender)
    response = {'id':'gen-'+effect.update_id, 'model':body['model'], 'provider':'Amazon Bedrock',
                'usage':{'prompt_tokens':10,'completion_tokens':3},'choices':[]}
    if cost:
        response['usage']['cost'] = '0.01'
    budget.settle(reservation['id'], status, json.dumps(response), body_complete=complete, sender=sender)


def test_distinct_native_updates_can_send_identical_prompts(scoped_receipts):
    root, project, worker, sender, _ = scoped_receipts
    budget = live_budget(project=project, profile=source.PROFILE)
    assert budget.__class__ is source.SourceJournal
    model_call(budget, project, worker[0], sender, 'Same worker prompt.')
    worker[0] = SourceEffect('v'*22, DOCUMENT)
    model_call(budget, project, worker[0], sender, 'Same worker prompt.')
    rows = budget.snapshot()
    assert [r['workflow_slot']['stock_source_effect'] for r in rows] == [
        asdict(SourceEffect(UPDATE, DOCUMENT)), asdict(worker[0])]
    assert all(r['state']=='settled' and not r.get('retry_of') for r in rows)
    assert budget.retry_delay(rows[-1]['id']) is None
    assert not (root/'STOP').exists()


@pytest.mark.parametrize('repeat', ['Same worker prompt.', 'Changed worker prompt.'])
def test_native_retry_stops_without_preparing_a_second_send(scoped_receipts, repeat):
    root, project, worker, sender, _ = scoped_receipts
    budget = live_budget(project=project)
    model_call(budget, project, worker[0], sender, 'Same worker prompt.')
    with pytest.raises(OwnershipConflict, match='already prepared'):
        model_call(budget, project, worker[0], sender, repeat)
    assert len(budget.snapshot()) == 1
    assert (root/'STOP').is_file()


def test_account_stop_or_unended_transport_refuses_before_transport(scoped_receipts):
    root, project, worker, sender, _ = scoped_receipts
    budget = live_budget(project=project)
    model_call(budget, project, worker[0], sender, 'Potentially delivered.', complete=False, cost=False)
    worker[0] = SourceEffect('v'*22,DOCUMENT)
    with pytest.raises(OwnershipConflict, match='unambiguous end'):
        model_call(budget, project, worker[0], sender, 'New update cannot proceed yet.')
    (root.parent.parent/'ACCOUNT_STOP').touch()
    with pytest.raises(RuntimeError, match='account stopped'):
        model_call(budget, project, worker[0], sender, 'New update cannot proceed yet.')
    assert len(budget.snapshot()) == 1


def test_no_driver_or_judge_grant(scoped_receipts):
    _, project, _, _, _ = scoped_receipts
    for role in ('judge','driver'):
        with pytest.raises(ValueError):
            live_budget(project=project.removesuffix('memory')+role)


def test_a_normal_response_with_unknown_cost_does_not_block_a_distinct_update(scoped_receipts):
    _, project, worker, sender, _ = scoped_receipts
    budget = live_budget(project=project)
    model_call(budget, project, worker[0], sender, 'Complete, no price in response.', cost=False)
    worker[0] = SourceEffect('v'*22, DOCUMENT)
    model_call(budget, project, worker[0], sender, 'A different durable update.')
    assert [row['state'] for row in budget.snapshot()] == ['receipt_missing', 'settled']
