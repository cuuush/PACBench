import json

import pytest

from pacbench import remaining_r2_capability as cap
from pacbench.gateway_reuse import atomic
from pacbench.hermes_gateway import GatewayLifecycleError


def test_only_frozen_remaining_native_import_allowed(tmp_path, monkeypatch):
    # Exercise authorization independently of the retained full benchmark run.
    for key in ['PACBENCH_CHECKPOINT_FULL_RUN', 'PACBENCH_CHECKPOINT_CANARY']:
        monkeypatch.delenv(key, raising=False)
    source = tmp_path / 'source.txt'
    source.write_text('Synthetic source from a sealed daily batch.\n')
    batch = {'id': 'day-1', 'source_file': source.name, 'document_sha256': cap.frozen.sha(source)}
    plan = {'frozen_inputs': {'sha256': cap.frozen.FROZEN, 'messages': 142, 'questions': 42, 'criteria': 188}}
    monkeypatch.setattr(cap.frozen, 'frozen', lambda: (tmp_path, [batch], [], plan))

    binding = cap.create(tmp_path, 'memos')
    monkeypatch.setenv(cap.ENV, json.dumps(binding))
    capability = cap.load()
    slot = {'kind': 'ingest', 'provider': 'memos', 'batch_id': batch['id'],
            'source_file': str(source), 'document_sha256': batch['document_sha256']}
    atomic(tmp_path / 'active-slot.json', slot)
    projects = capability['projects']['memos']
    key = projects['memory']
    assert cap.admission(key, 'luna-bedrock-global')['slot'] == slot
    for wrong in [projects['driver'], projects['judge'], 'unknown']:
        with pytest.raises(GatewayLifecycleError):
            cap.admission(wrong, 'luna-bedrock-global')
    with pytest.raises(GatewayLifecycleError):
        cap.admission(key, 'deepseek-flex')
    source.write_text('Incorrect source.\n')
    with pytest.raises(GatewayLifecycleError):
        cap.admission(key, 'luna-bedrock-global')
    for provider in ['memcal', 'mem0', 'reme', 'byterover', 'memori']:
        with pytest.raises(GatewayLifecycleError):
            cap.create(tmp_path, provider)
