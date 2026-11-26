"""Offline Mem0 native-document input and rolling store witnesses."""
import json

import pytest

from pacbench.workflows import capability as cap
from pacbench.canary_capability import sha
from pacbench.providers.mem0 import HISTORY_WINDOW, append_history


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
    assert scope['projects'] == cap._document_projects(root, 'mem0')


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


@pytest.fixture
def mem0_idle(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pacbench.providers.mem0 import Mem0Witness, PROTOCOL

    witness = Mem0Witness.__new__(Mem0Witness)
    witness.owner = SimpleNamespace(home=tmp_path, witness={'pid':10, 'start_time':2},
                                    pins={'reuse_observer_sha256':'fixture'})
    witness.ledger = Mock()
    sample = dict(pid=10, start_time=2, protocol=PROTOCOL, observer_sha256='fixture',
                  observed_monotonic_ns=0, native_release_threads=0, native_failures=[], sessions=[])
    return witness, sample, tmp_path/'reuse-observer.json'


def test_only_fresh_idle_samples_reconcile_all_nonjudge_receipts(mem0_idle, monkeypatch):
    witness, sample, observer = mem0_idle
    observer.write_text(json.dumps(sample))
    provider = {'name':'mem0', 'workers_alive':1}
    session = {'providers':[provider], 'pending_futures':1}
    updates = iter([
        {'observed_monotonic_ns':2**63, 'native_release_threads':1},
        {'native_release_threads':0, 'sessions':[session]},
        {'sessions':[{**session, 'pending_futures':0}]},
        {'sessions':[]},
        {},
    ])

    def advance(_):
        sample.update(next(updates))
        observer.write_text(json.dumps(sample))

    monkeypatch.setattr('pacbench.providers.mem0.time.sleep', advance)
    witness.ledger.snapshot.side_effect = [
        [{'finished':False, 'accounting_role':'memory_provider'}],
        [{'finished':False, 'accounting_role':'judge'}],
    ]
    assert witness.idle()['readback'] == sample
    assert witness.ledger.snapshot.call_count == 2
    assert list(updates) == []


@pytest.mark.parametrize('fault', [
    {'pid':9}, {'protocol':'wrong'}, {'observer_sha256':'wrong'}, {'native_failures':['error']},
    {'sessions':[{'providers':[{'name':'mem0', 'workers_alive':None}]}]},
    {'sessions':[{'providers':[{'name':'foreign'}]}]},
])
def test_stale_busy_sample_still_fails_immediately(mem0_idle, fault):
    witness, sample, observer = mem0_idle
    observer.write_text(json.dumps({**sample, 'native_release_threads':1, **fault}))
    with pytest.raises(RuntimeError):
        witness.idle()
    witness.ledger.snapshot.assert_not_called()


@pytest.mark.parametrize('broken_report', [False, True])
def test_failed_mem0_run_keeps_primary_failure_and_terminal_evidence(tmp_path, monkeypatch, broken_report):
    from unittest.mock import Mock
    from pacbench.providers.mem0 import terminal_result

    ledger = Mock()
    ledger.snapshot.return_value = []
    billing = Mock(return_value={'complete_cost_usd':None})
    if broken_report:
        billing.side_effect = ValueError('private report detail')
    monkeypatch.setattr('pacbench.workflows.recording.receipt_summary', billing)
    with pytest.raises(TimeoutError):
        with terminal_result(tmp_path, ledger, 'test-run', False, 0):
            raise TimeoutError('private execution detail')
    record = json.loads((tmp_path/'result.json').read_bytes())
    assert record['state'] == 'incomplete' and record['error'] == 'TimeoutError'
    assert record['billing'] == (None if broken_report else {'complete_cost_usd':None})
    assert record.get('report_error') == ('ValueError' if broken_report else None)
    assert 'private' not in json.dumps(record)
    ledger.snapshot.assert_called_once()


@pytest.mark.parametrize(('canary', 'cost', 'state'), [
    (True, None, 'incomplete'), (True, '0.001', 'live-canary-complete'),
    (False, None, 'live-week-complete'),
])
def test_mem0_terminal_state_uses_one_reconciled_snapshot(tmp_path, monkeypatch, canary, cost, state):
    from unittest.mock import Mock
    from pacbench.providers.mem0 import terminal_result

    rows = [{'accounting_role':role, 'upstream_request_id':str(i)}
            for i, role in enumerate(['judge','hermes','memory_provider'])]
    ledger = Mock()
    ledger.snapshot.return_value = rows
    report = {'coverage':{'verified_turns':1,'planned_turns':1,'graded_criteria':2,'planned_criteria':2},
              'turns':[{'driver_attempt_coverage':{'coverage':'complete'}}]}
    summarize = Mock(return_value=report)
    reconcile = Mock(return_value={'complete_cost_usd':cost})
    monkeypatch.setattr('pacbench.workflows.recording.summarize', summarize)
    monkeypatch.setattr('pacbench.workflows.recording.receipt_summary', reconcile)
    with terminal_result(tmp_path, ledger, 'run', canary, 0) as record:
        record['cleanup'] = 'complete'
    assert json.loads((tmp_path/'result.json').read_bytes()) == record
    assert record['state'] == state and record['workflow'] == report
    reconcile.assert_called_once_with(tmp_path, rows, 'run')
    ledger.snapshot.assert_called_once()
    summarize.assert_called_once()
