"""Regression checks for provider boundaries in the combined Week tree."""
import json

import pytest

from pacbench import live_dispatch
from pacbench.workflows import capability as cap
from pacbench.workflows.tracks import MEMOS_OSS_LUNA_COMPAT


@pytest.mark.parametrize('provider', ['reme', 'memos', 'cognee', 'openviking', 'hindsight', 'mem0'])
def test_combined_provider_dispatch_keeps_scope_and_stage_guards(tmp_path, monkeypatch, provider):
    root = tmp_path/'campaign'/provider
    root.mkdir(parents=True)
    args = dict(prepaid={}, auto_recharge_evidence={})
    with pytest.raises(ValueError, match='separately verified live canary'):
        cap.create(root, provider, canary=False, **args)
    bound = cap.create(root, provider, canary=True, **args)
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    scope = cap.load()
    assert scope['provider'] == provider
    assert cap.scope_campaign_stop(scope) == tmp_path/'ACCOUNT_STOP'
    stage = {'phase': 'dispatch', 'question': 'synthetic-question'}
    journal = root/'workflow/workflow.jsonl'
    journal.parent.mkdir()
    journal.write_text(json.dumps(stage)+'\n')
    row = dict(workflow_slot={'provider':provider, 'journal_stage_sha256':cap.digest(stage)},
               workflow_root=str(root), workflow_capability=bound,
               accounting_role='agent', dispatch_execution='prepared', dispatch_events=[])
    monkeypatch.setattr(live_dispatch, 'check_owner', lambda *args: True)
    live_dispatch.mark_possible(row, 'synthetic-time')
    assert row['dispatch_execution'] == 'ambiguous'
    row['dispatch_execution'] = 'prepared'
    journal.write_text(json.dumps({**stage, 'question':'changed'})+'\n')
    with pytest.raises(ValueError, match='stage changed'):
        live_dispatch.mark_possible(row, 'synthetic-time')
    journal.write_text(json.dumps(stage)+'\n')
    row['workflow_slot']['provider'] = 'different-provider'
    with pytest.raises(ValueError, match='active scope'):
        cap.confirm_document_sender(row)


def test_memos_compatibility_is_rechecked_at_physical_dispatch(tmp_path, monkeypatch):
    root = tmp_path/'campaign'/'memos'
    root.mkdir(parents=True)
    bound = cap.create(root, 'memos', canary=True, prepaid={}, auto_recharge_evidence={})
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    monkeypatch.setenv('PACBENCH_MEMOS_OSS_LUNA_COMPAT', MEMOS_OSS_LUNA_COMPAT)
    stage = {'phase':'dispatch'}
    journal = root/'workflow/workflow.jsonl'
    journal.parent.mkdir()
    journal.write_text(json.dumps(stage)+'\n')
    row = dict(workflow_slot={'provider':'memos', 'journal_stage_sha256':cap.digest(stage)},
               workflow_root=str(root), workflow_capability=bound, accounting_role='memory_provider',
               native_request_compatibility=MEMOS_OSS_LUNA_COMPAT)
    cap.confirm_document_sender(row)
    monkeypatch.delenv('PACBENCH_MEMOS_OSS_LUNA_COMPAT')
    with pytest.raises(ValueError, match='sampling compatibility changed'):
        cap.confirm_document_sender(row)


@pytest.mark.parametrize('admission', [
    cap.MEMCAL_SCOPE, 'pizza-day-native-document-v1', 'pizza-day-synthetic-memcal-v1',
])
def test_legacy_direct_arm_scope_uses_its_parent_campaign_stop(tmp_path, admission):
    root = tmp_path/'campaign'/'memcal'
    scope = dict(root=str(root), admission=admission)
    assert cap.scope_campaign_stop(scope) == root.parent/'ACCOUNT_STOP'
    scope['campaign_stop'] = str(root.parent.parent/'ACCOUNT_STOP')
    with pytest.raises(ValueError, match='differs from the original run layout'):
        cap.scope_campaign_stop(scope)


@pytest.mark.parametrize(('provider', 'admission', 'module'), [
    ('mem0', 'mem0-native-oss-document-week-v1', '.providers.mem0'),
    ('hindsight', 'hindsight-native-file-document-week-v1', '.providers.hindsight.workflow'),
    ('openviking', 'openviking-native-file-document-week-v1', '.providers.openviking'),
    ('reme', 'reme-native-watched-resource-week-v1', '.providers.reme'),
    ('cognee', 'cognee-native-permanent-document-week-v1', '.providers.cognee'),
    ('memos', 'memos-oss-native-file-document-week-v1', '.providers.memos'),
])
def test_document_scope_preserves_qualification_and_project_names(tmp_path, monkeypatch, provider, admission, module):
    from hashlib import sha256
    from types import SimpleNamespace

    canary = tmp_path/'campaign'/'canary'/provider
    week = tmp_path/'campaign'/'week'/provider
    canary.mkdir(parents=True)
    week.mkdir(parents=True)
    args = dict(prepaid={}, auto_recharge_evidence={})
    imported = []

    def import_verifier(name, package):
        imported.append((name, package))
        return SimpleNamespace(check_canary=lambda root: qualification if root == canary else None)

    monkeypatch.setattr(cap, 'import_module', import_verifier)
    canary_binding = cap.create(canary, provider, canary=True, **args)
    assert imported == []
    result = canary/'result.json'
    state, cleanup = ('incomplete', 'unverified') if provider == 'hindsight' else ('live-canary-complete', 'complete')
    result.write_text(json.dumps(dict(state=state, cleanup=cleanup, canary=True)))
    qualification = dict(scope=canary_binding, result=cap.binding(result))
    if provider == 'hindsight':
        reconciliation = canary/'service-shutdown-reconciliation.json'
        reconciliation.write_text(json.dumps(dict(state='verified-after-exit', result=qualification['result'])))
        qualification['cleanup'] = cap.binding(reconciliation)

    bound = cap.create(week, provider, canary=False, qualification=qualification, **args)
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    scope = cap.load()
    assert imported == [(module, 'pacbench')]
    assert scope['admission'] == admission
    assert scope['qualification'] == qualification
    prefix = f'pacbench-{sha256(str(week).encode()).hexdigest()[:12]}-{provider}'
    assert scope['projects'] == {provider: {role: f'{prefix}-{role}' for role in ('memory', 'driver', 'judge')}}

    path = week/'workflow-scope.json'
    path.write_text(json.dumps({**scope, 'admission': 'other-provider-admission'}))
    monkeypatch.setenv(cap.ENV, json.dumps(cap.binding(path)))
    with pytest.raises(ValueError, match='continuing-account scope'):
        cap.load()
