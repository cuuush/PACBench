"""Offline Mem0 native-document input and rolling store witnesses."""
import json

import pytest

from pacbench import workflow_capability as cap
from pacbench.canary_capability import sha
from pacbench.workflow_mem0 import HISTORY_WINDOW, append_history


@pytest.fixture(autouse=True)
def native_test_home(tmp_path, monkeypatch):
    monkeypatch.setenv('MEM0_DIR', str(tmp_path/'mem0-test'))
    monkeypatch.setenv('MEM0_TELEMETRY', 'false')


def test_canary_import_only_admits_exact_current_source():
    from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT, generate_additive_extraction_prompt
    from mem0.memory.utils import parse_messages

    docs, _, _ = cap.schedule(sha(cap.SPEC), True)
    assert len(docs) == 12
    current, future = docs[0], docs[1:]

    def request(text, **kwargs):
        new_messages = parse_messages([{'role':'user','content':text}])
        return {'messages': [
            {'role':'system','content':ADDITIVE_EXTRACTION_PROMPT},
            {'role':'user','content':generate_additive_extraction_prompt(new_messages=new_messages, **kwargs)},
        ]}

    assert cap.mem0_import_request(request(current.text), current, docs, [current])
    assert not cap.mem0_import_request(request(current.text+future[0].text), current, docs, [current])
    assert not cap.mem0_import_request(request(current.text, summary=future[0].text), current, docs, [current])


def test_mem0_scope_refuses_week_until_live_canary_is_verified(tmp_path, monkeypatch):
    root = tmp_path/'campaign'/'mem0'
    root.mkdir(parents=True)
    args = dict(prepaid={}, auto_recharge_evidence={})
    with pytest.raises(ValueError, match='separately verified live canary'):
        cap.create(root, 'mem0', canary=False, **args)
    bound = cap.create(root, 'mem0', canary=True, **args)
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    scope = cap.load()
    assert scope['canary'] is True
    assert scope['projects'] == cap._mem0_projects(root)


def test_mem0_history_proves_new_writes_after_native_eviction(tmp_path):
    from mem0.memory.storage import SQLiteManager

    manager = SQLiteManager(str(tmp_path/'history.db'))
    try:
        prior = []
        all_ids = set()
        for i in range(12):
            message = {'role':'user','content':f'Nightly source {i}'}
            manager.save_messages([message], 'source')
            current = list(manager.connection.execute(
                'SELECT id,role,content FROM messages WHERE session_scope=? ORDER BY rowid', ('source',)))
            new, = append_history(prior, current, [('user', message['content'])])
            assert new[0] not in all_ids
            all_ids.add(new[0])
            prior = current
        assert len(prior) == HISTORY_WINDOW
        with pytest.raises(RuntimeError, match='expected next write'):
            append_history(prior, prior, [('user', prior[-1][2])])
    finally:
        manager.connection.close()
