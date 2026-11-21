"""Regression checks for provider boundaries in the combined Week tree."""
import json

import pytest

from pacbench import live_dispatch, workflow_capability as cap
from pacbench.workflow_tracks import MEMOS_OSS_LUNA_COMPAT


@pytest.mark.parametrize('provider', ['reme', 'memos', 'cognee', 'openviking', 'hindsight', 'mem0'])
def test_combined_provider_dispatch_keeps_scope_and_stage_guards(tmp_path, monkeypatch, provider):
    root = tmp_path/'campaign'/provider
    root.mkdir(parents=True)
    args = dict(prepaid={}, auto_recharge_evidence={})
    with pytest.raises(ValueError, match='separately verified live canary'):
        cap.create(root, provider, canary=False, **args)
    bound = cap.create(root, provider, canary=True, **args)
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    assert cap.load()['provider'] == provider
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
