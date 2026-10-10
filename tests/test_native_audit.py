"""Source, tool and actual native context integrity; no accuracy simulation."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from pacbench.context_isolation import actual_context, workspace_for
from pacbench.document_ingestion import VERSION, prepare_sources, record_recall_isolation
from pacbench.frozen import write_bundle
from pacbench.native_audit import audit


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def trace(tmp_path):
    message = {'source_id': 'chat/0', 'channel': 'imessage', 'thread': 'devon',
        'sender': 'Devon', 'from_owner': False, 'structured': None,
        'text': 'Dinner at seven.', 'written_at': '2026-09-07T18:00:00-04:00',
        'collected_at': '2026-09-08T18:00:00-04:00'}
    digest = hashlib.sha256(json.dumps([message], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    bundle_hash = write_bundle({'lives': {'test': {'messages': [message]}}}, tmp_path / 'bundle.json')
    body = {'claims': [{'id': 'fact-1', 'statement': 'Dinner is at seven.', 'evidence': ['chat/0']}],
        'question': 'When is dinner?', 'evidence_hash': digest, 'author_fact_count': 1}
    contract_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    save(tmp_path / 'contract.json', {**body, 'contract_hash': contract_hash})
    at = '2026-09-09T09:00:00-04:00'
    prompt = f'[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{body["question"]}'
    save(tmp_path / 'resolved-spec.json', {'question_at': at, 'timezone': 'America/New_York'})
    schedule = prepare_sources(tmp_path, [message], 'America/New_York')
    save(tmp_path / 'summary.json', {'ingestion_mode': 'documents', 'tool_policy': 'retained-memory-only-v1',
        'builtin_memory': False, 'process_timezone': 'America/New_York', 'ingestion_schedule': schedule,
        'bundle_hash': bundle_hash, 'input_hash': digest, 'contract_hash': contract_hash,
        'message_count': 1, 'results': [{'provider': 'mem0', 'repetition': 1, 'state': 'diagnostic',
        'input_hash': digest, 'capability': {'wiring_verified': True, 'tools': ['mem0_search']}}]})
    arm = tmp_path / 'repeat-1/mem0'; home = arm / 'hermes-home'; home.mkdir(parents=True)
    record_recall_isolation(arm)
    workspace = workspace_for(home)
    native_prompt = 'Native Hermes provider instructions'
    prompt_hash = hashlib.sha256(native_prompt.encode()).hexdigest()
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE system_prompts(hash TEXT, prompt TEXT)')
        db.execute('CREATE TABLE sessions(id TEXT,cwd TEXT,system_prompt TEXT,system_prompt_hash TEXT)')
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT)')
        db.execute('INSERT INTO system_prompts VALUES (?,?)', (prompt_hash, native_prompt))
        db.execute('INSERT INTO sessions VALUES (?,?,NULL,?)', ('recall', str(workspace), prompt_hash))
        db.execute('INSERT INTO messages VALUES (1,?,?,?)', ('recall', 'user', prompt))
    context = actual_context(home, 'recall', workspace)
    save(arm / 'recall.json', {'prompt': prompt, 'at': at, 'session_id': 'recall',
        'returncode': 0, 'policy_blocks': [], 'answer': 'At seven.', 'tool_calls': [], 'context_isolation': context})
    save(arm / 'judgment.json', {'question': prompt, 'answer': 'At seven.'})
    save(arm / 'grade.json', {'claims': [{'id': 'fact-1', 'verdict': 'correct'}]})
    save(arm / 'simulation-clock.json', {'process_timezone': 'America/New_York'})
    save(arm / 'tool-policy.json', {'version': 'retained-memory-only-v1', 'provider': 'mem0',
        'allowed_tools': ['mem0_search'], 'blocked_source_tools': [], 'builtin_memory': False, 'os_sandbox': False})
    (arm / 'context.jsonl.tools.jsonl').write_text(json.dumps({'allowed_tools':['mem0_search'], 'offered_tools':['mem0_search']})+'\n')
    row = schedule[0]; meta = json.loads((tmp_path / 'sources' / row['id'] / 'source.json').read_text())
    save(arm / row['file'], {'version': VERSION, 'provider': 'mem0', 'at': row['at'],
        'source_ids': row['source_ids'], 'document_sha256': meta['document_sha256'],
        'hermes_ingestion_turns': 0, 'ready': True, 'state': 'ready', 'returncode': 0,
        'processing_at': row['at']})
    assert audit(tmp_path)['state'] == 'pass', audit(tmp_path)
    return tmp_path, arm


@pytest.mark.parametrize('file,key,value,error', [
    ('judgment.json', 'question', 'When is dinner?', 'judge lacks recall clock'),
    ('recall.json', 'policy_blocks', ['blocked'], 'recall failure'),
    ('grade.json', 'claims', [], 'incomplete judgments'),
    ('tool-policy.json', 'allowed_tools', ['terminal'], 'provider tool policy mismatch'),
    ('simulation-clock.json', 'process_timezone', 'UTC', 'inconsistent provider process timezone'),
])
def test_corrupted_trace_cannot_pass(trace, file, key, value, error):
    root, arm = trace
    data = json.loads((arm / file).read_text()); data[key] = value; save(arm / file, data)
    result = audit(root)
    assert result['state'] == 'fail' and any(error in e for e in result['errors'])


def test_missing_native_ingestion_cannot_pass(trace):
    root, arm = trace
    next((arm / 'ingestion').glob('*.json')).unlink()
    assert audit(root)['state'] == 'fail'


def test_source_document_corruption_cannot_pass(trace):
    root, _ = trace
    next((root / 'sources').glob('*/messages.txt')).write_text('A preloaded answer')
    assert 'delivered source text differs' in str(audit(root)['errors'])


def test_native_source_handoff_or_project_context_cannot_hide_behind_receipt(trace):
    root, arm = trace
    with sqlite3.connect(arm / 'hermes-home/state.db') as db:
        db.execute("INSERT INTO messages VALUES (2,'recall','user','Source transcript')")
        db.execute("UPDATE system_prompts SET prompt=prompt || '\n# Project Context\nAnswer key'")
    result = audit(root)
    assert result['state'] == 'fail'
    assert 'Native Hermes user messages differ' in str(result['errors'])
    assert 'project context' in str(result['errors']) or 'revision hash mismatch' in str(result['errors'])


def test_unfinished_arm_does_not_hide_later_trace_corruption(trace):
    root, arm = trace
    summary = json.loads((root / 'summary.json').read_text())
    summary['results'].insert(0, {'provider': 'unconfigured', 'repetition': 1,
        'state': 'incomplete', 'capability': {}})
    # Keep same process clock so the audit can inspect all arms.
    save(root / 'repeat-1/unconfigured/simulation-clock.json', {'process_timezone': 'America/New_York'})
    save(root / 'summary.json', summary)
    saved = json.loads((arm / 'judgment.json').read_text()); saved['question'] = 'wrong question'
    save(arm / 'judgment.json', saved)
    result = audit(root)
    assert result['state'] == 'fail'
    assert 'unconfigured: incomplete arm' in str(result['errors'])
    assert 'judge lacks recall clock' in str(result['errors'])


@pytest.mark.parametrize('corrupt_tools,unfinished', [(False, False), (True, False), (False, True)])
def test_partial_story_audits_skips_without_bypassing_tool_boundary(trace, monkeypatch, corrupt_tools, unfinished):
    import pacbench.native_audit as native_audit
    root, arm = trace
    checks = [{'id': 'answer', 'state': 'diagnostic'},
              {'id': 'reply', 'state': 'ungraded' if unfinished else 'skipped'}]
    summary = json.loads((root / 'summary.json').read_text())
    summary['results'][0].update(state='partial', checks=checks)
    summary['evaluation_plan_hash'] = 'test-plan'
    save(root / 'summary.json', summary)
    save(root / 'evaluation-plan.json', {'checks': checks})
    save(arm / 'evaluation.json', {'checks': checks})
    monkeypatch.setattr(native_audit, 'load_evaluations',
        lambda *_: ({'plan_hash': 'test-plan', 'context': 'shared'}, [('answer', None, None)]))
    inspected = []
    monkeypatch.setattr(native_audit, 'audit_checks', lambda *args: inspected.append(args) or [])
    if corrupt_tools:
        (arm / 'context.jsonl.tools.jsonl').write_text(json.dumps(
            {'allowed_tools': ['mem0_search'], 'offered_tools': ['terminal']}) + '\n')
    result = audit(root)
    if unfinished:
        assert 'incomplete arm' in str(result['errors'])
        assert not inspected
    else:
        assert inspected, result
        assert result['state'] == ('fail' if corrupt_tools else 'pass')
        if corrupt_tools:
            assert 'non-provider schema exposed' in str(result['errors'])
