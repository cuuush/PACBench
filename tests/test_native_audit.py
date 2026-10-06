"""Synthetic trace-integrity regressions, never provider performance evidence."""
import hashlib
import json

import pytest

from pacbench.frozen import write_bundle
from pacbench.native_audit import audit


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def clock(at, text):
    return f"[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{text}"


@pytest.fixture
def trace(tmp_path):
    message = {'source_id': 'chat/0', 'channel': 'imessage', 'thread': 'devon',
               'sender': 'Me', 'text': 'Dinner at seven.',
               'written_at': '2026-09-07T18:00:00-04:00',
               'collected_at': '2026-09-08T18:00:00-04:00'}
    digest = hashlib.sha256(json.dumps([message], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    bundle_hash = write_bundle({'lives': {'test': {'messages': [message]}}}, tmp_path / 'bundle.json')
    body = {'claims': [{'id': 'fact-1', 'statement': 'Dinner is at seven.', 'evidence': ['chat/0']}],
            'question': 'When is dinner?', 'evidence_hash': digest, 'author_fact_count': 1}
    contract_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    save(tmp_path / 'contract.json', {**body, 'contract_hash': contract_hash})
    at = '2026-09-09T09:00:00-04:00'
    save(tmp_path / 'resolved-spec.json', {'question_at': at})
    save(tmp_path / 'summary.json', {'bundle_hash': bundle_hash, 'input_hash': digest,
         'contract_hash': contract_hash, 'message_count': 1, 'results': [{
             'provider': 'mem0', 'repetition': 1, 'state': 'diagnostic',
             'input_hash': digest, 'capability': {'wiring_verified': True}}]})
    arm = tmp_path / 'repeat-1/mem0'
    public = 'Here are my recent messages. Catch up on them for me.\n\n' + (
        '[2026-09-07T18:00:00-04:00] imessage / devon / Me: Dinner at seven.')
    save(arm / 'exposure-2026-09-08.json', {'prompt': clock(message['collected_at'], public),
         'at': message['collected_at'], 'session_id': 'exposure', 'returncode': 0, 'policy_blocks': []})
    save(arm / 'recall.json', {'prompt': clock(at, body['question']), 'at': at,
         'session_id': 'recall', 'returncode': 0, 'policy_blocks': [], 'answer': 'At seven.'})
    save(arm / 'judgment.json', {'question': clock(at, body['question']), 'answer': 'At seven.'})
    save(arm / 'grade.json', {'claims': [{'id': 'fact-1', 'verdict': 'correct'}]})
    assert audit(tmp_path)['state'] == 'pass'
    return tmp_path, arm


@pytest.mark.parametrize('file,key,value,error', [
    ('judgment.json', 'question', 'When is dinner?', 'judge lacks recall clock'),
    ('recall.json', 'session_id', 'exposure', 'reused exposure session'),
    ('exposure-2026-09-08.json', 'prompt', 'PRIVATE ANSWER', 'exposure/clock mismatch'),
    ('recall.json', 'policy_blocks', ['blocked'], 'recall failure'),
    ('grade.json', 'claims', [], 'incomplete judgments'),
])
def test_corrupted_trace_cannot_pass(trace, file, key, value, error):
    root, arm = trace
    data = json.loads((arm / file).read_text())
    data[key] = value
    save(arm / file, data)
    result = audit(root)
    assert result['state'] == 'fail'
    assert any(error in e for e in result['errors'])


def test_missing_native_exposure_cannot_pass(trace):
    root, arm = trace
    (arm / 'exposure-2026-09-08.json').unlink()
    assert audit(root)['state'] == 'fail'
