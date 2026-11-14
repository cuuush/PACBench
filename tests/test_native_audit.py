"""Source, tool and actual native context integrity; no accuracy simulation."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from pacbench.context_isolation import actual_context, workspace_for
from pacbench.document_ingestion import VERSION, prepare_sources, record_recall_isolation,processing_at
from pacbench.frozen import write_bundle
from pacbench.native_audit import audit
from pacbench.source_policy import SOURCE_ARGUMENT_RULES


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
    save(tmp_path / 'summary.json', {'ingestion_mode': 'documents', 'tool_policy': 'retained-memory-only-v2',
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
    save(arm / 'tool-policy.json', {'version': 'retained-memory-only-v2', 'provider': 'mem0',
        'allowed_tools': ['mem0_search'], 'blocked_source_tools': [], 'source_argument_rules': SOURCE_ARGUMENT_RULES, 'builtin_memory': False, 'os_sandbox': False})
    (arm / 'context.jsonl.tools.jsonl').write_text(json.dumps({'allowed_tools':['mem0_search'], 'offered_tools':['mem0_search'], 'source_argument_rules': SOURCE_ARGUMENT_RULES})+'\n')
    row = schedule[0]; meta = json.loads((tmp_path / 'sources' / row['id'] / 'source.json').read_text())
    save(arm / row['file'], {'version': VERSION, 'provider': 'mem0', 'at': row['at'],
        'source_ids': row['source_ids'], 'document_sha256': meta['document_sha256'],
        'hermes_ingestion_turns': 0, 'ready': True, 'state': 'ready', 'returncode': 0,
        'processing_at': processing_at(row['at'],'mem0','America/New_York')})
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


def test_scored_recovery_attributes_existing_sessions_without_faking_empty_receipt(trace):
    from pacbench.document_audit import audit_ingestion
    from pacbench.frozen import load_bundle,messages_for
    root,arm=trace;summary=json.loads((root/'summary.json').read_text())
    messages=messages_for(load_bundle(root/'bundle.json',summary['bundle_hash']),'test')
    save(arm/'pre-recall-isolation.json',{'hermes_sessions_before_recall':['recall'],'builtin_memory_before_recall':{}})
    assert 'Hermes source conversation or builtin facts existed before recall' in audit_ingestion(root,arm,messages,summary)
    save(arm/'scored-resumes/1/resume.json',{'protocol':'scored-prefix-retained-store-v1'})
    assert audit_ingestion(root,arm,messages,summary)==[]
    with sqlite3.connect(arm/'hermes-home/state.db') as db:
        db.execute("INSERT INTO sessions VALUES ('unattributed','/tmp',NULL,NULL)")
    assert 'Unexpected Hermes ingestion or prior sessions' in audit_ingestion(root,arm,messages,summary)


def test_declared_judge_reasoning_requires_the_actual_model_request(trace):
    root,arm=trace
    summary=json.loads((root/'summary.json').read_text())
    summary.update(inference_route='openrouter',judge_generation_conditions={'reasoning_effort':'low'})
    save(root/'summary.json',summary)
    assert any('generation condition' in e for e in audit(root)['errors'])
    judgment=json.loads((arm/'judgment.json').read_text());judgment['model_call']='judge.json'
    save(arm/'judgment.json',judgment)
    save(root/'model-calls/judge.json',{'request':{'reasoning_effort':'low'}})
    assert audit(root)['state']=='pass'
    save(root/'model-calls/judge.json',{'request':{'reasoning_effort':'high'}})
    assert any('generation condition' in e for e in audit(root)['errors'])


def test_v3_provenance_is_supported_without_rewriting_legacy_conditions(trace):
    root,_=trace
    summary=json.loads((root/'summary.json').read_text())
    summary['provenance_version']='source-provenance-v3'
    save(root/'summary.json',summary)
    assert audit(root)['state']=='pass'
    summary['provenance_version']='invented-protocol'
    save(root/'summary.json',summary)
    assert 'Unsupported provenance protocol' in str(audit(root)['errors'])


def test_selected_completed_arm_can_be_audited_without_certifying_an_interrupted_one(trace):
    root,arm=trace
    summary=json.loads((root/'summary.json').read_text())
    summary['results'].append({'provider':'interrupted','repetition':1,'state':'interrupted'})
    save(root/'summary.json',summary)
    assert audit(root)['state']=='fail'
    selected=audit(root,providers=['mem0'])
    assert selected['state']=='pass' and selected['selected_providers']==['mem0']
    assert audit(root,providers=['missing'])['state']=='fail'
    saved=json.loads((arm/'judgment.json').read_text());saved['question']='wrong question'
    save(arm/'judgment.json',saved)
    assert audit(root,providers=['mem0'])['state']=='fail'


def test_source_document_corruption_cannot_pass(trace):
    root, _ = trace
    next((root / 'sources').glob('*/messages.txt')).write_text('A preloaded answer')
    assert 'delivered source text differs' in str(audit(root)['errors'])


@pytest.mark.parametrize('field,value',[('sender','Maya'),('thread','other-thread')])
def test_sender_or_thread_only_exposure_change_cannot_pass(trace,field,value):
    root,_=trace
    path=next((root/'sources').glob('*/messages.json'))
    document=path.with_name('messages.txt');text=document.read_bytes()
    messages=json.loads(path.read_text());body=messages[0]['text']
    messages[0][field]=value;save(path,messages)
    assert messages[0]['text']==body and document.read_bytes()==text
    result=audit(root)
    assert result['state']=='fail'
    assert any('delivered source text differs from frozen observations' in e for e in result['errors'])


@pytest.mark.parametrize('compact',[False,True])
def test_checkpoint_audit_keeps_native_conversation_and_source_state_independent(trace,compact):
    from pacbench.document_audit import audit_ingestion
    from pacbench.document_ingestion import source_state
    from pacbench.frozen import load_bundle,messages_for
    root,arm=trace
    summary=json.loads((root/'summary.json').read_text())
    summary['evaluation_plan_hash']='checkpoint-test'
    turn=json.loads((arm/'recall.json').read_text())
    directory=arm/'turns/check';directory.mkdir(parents=True)
    (arm/'recall.json').rename(directory/'turn.json')
    save(root/'evaluation-plan.json',{'checks':[{'id':'check','at':turn['at']}]})
    days=summary['ingestion_schedule']
    state=source_state(turn['at'],[d['id'] for d in days]) if compact else {
        'at':turn['at'],'ingested_batches':[d['id'] for d in days],
        'source_ids':[sid for d in days for sid in d['source_ids']]}
    save(directory/'source-state.json',state)
    save(directory/'usage.json',{'state':'unavailable'})
    bundle=load_bundle(root/'bundle.json',summary['bundle_hash'])
    messages=messages_for(bundle,'test')
    assert audit_ingestion(root,arm,messages,summary)==[]
    save(directory/'source-state.json',{**state,'ingested_batches':['future-day']})
    assert 'Checkpoint source state has missing/future batches' in audit_ingestion(root,arm,messages,summary)
    save(directory/'source-state.json',state)
    with sqlite3.connect(arm/'hermes-home/state.db') as db:
        db.execute("INSERT INTO messages VALUES (2,'recall','user','Source transcript')")
    assert 'Native Hermes user messages differ from evaluation questions' in audit_ingestion(root,arm,messages,summary)


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


@pytest.mark.parametrize('corrupt_tools,unfinished', [(False, False), (True, False), (False, True), (True, True)])
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
    monkeypatch.setattr(native_audit, 'audit_checks', lambda *args, **kwargs: inspected.append(args) or [])
    if corrupt_tools:
        (arm / 'context.jsonl.tools.jsonl').write_text(json.dumps(
            {'allowed_tools': ['mem0_search'], 'offered_tools': ['terminal']}) + '\n')
    result = audit(root)
    if unfinished:
        assert 'incomplete arm' in str(result['errors'])
        assert inspected
        if corrupt_tools:assert 'non-provider schema exposed' in str(result['errors'])
    else:
        assert inspected, result
        assert result['state'] == ('fail' if corrupt_tools else 'pass')
        if corrupt_tools:
            assert 'non-provider schema exposed' in str(result['errors'])


def test_missing_arm_receipt_does_not_abort_later_provider_audits(trace):
    root,arm=trace
    summary=json.loads((root/'summary.json').read_text())
    summary['results'].insert(0,{'provider':'unconfigured','repetition':1,
        'state':'incomplete','capability':{}})
    save(root/'summary.json',summary)
    saved=json.loads((arm/'judgment.json').read_text())
    saved['question']='wrong question'
    save(arm/'judgment.json',saved)
    result=audit(root)
    assert result['state']=='fail'
    assert any('unconfigured' in e and 'FileNotFoundError' in e for e in result['errors'])
    assert any('judge lacks recall clock' in e for e in result['errors'])


@pytest.mark.parametrize('key', ['provenance_version', 'evidence_diagnostic_protocol', 'judge_protocol'])
def test_audit_rejects_unknown_recorded_protocol_before_reading_run(tmp_path, key):
    save(tmp_path / 'summary.json', {key: 'unknown-protocol'})
    result = audit(tmp_path)
    assert result['state'] == 'fail'
    assert 'Unsupported' in str(result['errors'])


def test_native_question_protocol_rejects_legacy_prefix(trace):
    root, arm = trace
    summary = json.loads((root / 'summary.json').read_text())
    summary['question_prompt_protocol'] = 'native-question-v2'
    save(root / 'summary.json', summary)
    assert any('recall/clock mismatch' in e for e in audit(root)['errors'])
    recall = json.loads((arm / 'recall.json').read_text())
    recall['prompt'] = 'When is dinner?'
    save(arm / 'recall.json', recall)
    judgment = json.loads((arm / 'judgment.json').read_text())
    judgment['question'] = recall['prompt']
    save(arm / 'judgment.json', judgment)
    # The native saved input must agree too; changing only public receipts
    # must not hide a prefixed question that Hermes actually received.
    assert 'Native Hermes user messages differ' in str(audit(root)['errors'])
    with sqlite3.connect(arm / 'hermes-home/state.db') as db:
        db.execute("UPDATE messages SET content=? WHERE role='user'", (recall['prompt'],))
    assert audit(root)['state'] == 'pass', audit(root)


def test_audit_reads_private_session_bound_prompt_after_native_gc(trace):
    root, arm = trace
    home = arm / 'hermes-home'
    workspace = Path(json.loads((arm / 'recall-workspace.json').read_text())['workspace'])
    context = actual_context(home, 'recall', workspace, capture=True)
    recall = json.loads((arm / 'recall.json').read_text())
    recall['context_isolation'] = context
    save(arm / 'recall.json', recall)
    replacement = 'Later native system prompt'
    digest = hashlib.sha256(replacement.encode()).hexdigest()
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('INSERT INTO system_prompts VALUES (?,?)', (digest, replacement))
        db.execute('UPDATE sessions SET system_prompt_hash=?', (digest,))
        db.execute('DELETE FROM system_prompts WHERE hash=?', (context['system_prompt_sha256'],))
    assert audit(root)['state'] == 'pass', audit(root)
    next((home / 'private-system-prompts').rglob('*.json')).unlink()
    assert any('snapshot unavailable or invalid' in e for e in audit(root)['errors'])


def test_import_only_audit_requires_no_queries_and_keeps_all_source_checks(trace):
    from pacbench.document_audit import audit_ingestion
    from pacbench.frozen import load_bundle,messages_for
    root,arm=trace
    summary=json.loads((root/'summary.json').read_text());summary['mode']='native-import-validation'
    messages=messages_for(load_bundle(root/'bundle.json',summary['bundle_hash']),'test')
    assert 'Import-only validation contains benchmark turns' in audit_ingestion(root,arm,messages,summary,import_only=True)
    (arm/'recall.json').unlink()
    with sqlite3.connect(arm/'hermes-home/state.db') as db:
        db.execute('DELETE FROM messages');db.execute('DELETE FROM sessions')
    assert audit_ingestion(root,arm,messages,summary,import_only=True)==[]
    record=arm/summary['ingestion_schedule'][0]['file'];data=json.loads(record.read_text())
    data['ready']=False;data['state']='failed';save(record,data)
    assert 'native processing did not complete' in audit_ingestion(root,arm,messages,summary,import_only=True)
