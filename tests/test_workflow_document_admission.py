"""Exercise offline document admission and its physical sender recheck."""
from io import BytesIO
import json
import os

import pytest

from pacbench.workflows import capability as cap
from pacbench.workflows import document_admission as document
from pacbench.openrouter_route import PROFILES, with_route
from pacbench.workflows.tracks import HERMES_TOOLS, reviewed_provider


def records(events):
    return b''.join(json.dumps(event).encode() + b'\n' for event in events)


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    root = tmp_path/'campaign'/'reme'
    root.mkdir(parents=True)
    bound = cap.create(root, 'reme', canary=True, prepaid={}, auto_recharge_evidence={})
    monkeypatch.setenv(cap.ENV, json.dumps(bound))
    scope = cap.load()
    journal = root/'workflow/workflow.jsonl'
    journal.parent.mkdir()
    docs, turns, frozen = cap.schedule(scope['spec_sha256'], True)
    tools = HERMES_TOOLS['reme']
    route = PROFILES[cap.PROFILE]
    version = reviewed_provider('reme', None).reviewed_version
    condition = dict(provider='reme', track='nightly-document', state='continuing-account',
                     frontend='fixture', model=route['model'], model_route=route['endpoint'],
                     deployment='fixture', version=version, gateway_patch='fixture',
                     service_clock='fixture', tool_names=tools)
    runtime = dict(account=str(root), synthetic_source_provenance='fixture', observed_version=version,
                   store_identity=str(root/cap.DOCUMENT_ARMS['reme'].store), frontend_identity='fixture',
                   observed_patch='fixture', granted_tools=tools, effective_model_route=route['endpoint'],
                   native_processes={'memory': scope['controller'], 'driver': scope['controller']})
    header = [
        dict(phase='spec', sha256=scope['spec_sha256'], source_basis=frozen),
        dict(phase='condition', evidence_kind='native', condition=condition, deployment_witness=runtime),
        dict(phase='account-readback', evidence=dict(account=str(root), readback=True,
             chat_count=0, conversation_writes=0, source_files=[])),
    ]
    first = turns[0]
    released = [doc for doc in docs if document.instant(doc.cutoff) <= document.instant(first.at)]
    readbacks = [dict(phase='readback', evidence=dict(submitted_sha256=doc.sha256, ready=True, readback=True))
                 for doc in released]
    answer = [
        dict(phase='pre-dispatch-inventory', account=str(root), question=first.id,
             source_files=[doc.filename for doc in released]),
        dict(phase='dispatch', question=first.id, account=str(root), prompt=first.prompt, at=first.at),
    ]
    body = with_route({'messages': [{'role': 'user', 'content': first.prompt}],
                       'tools': [{'function': {'name': tool}} for tool in tools]}, profile=cap.PROFILE)
    return journal, header, readbacks, answer, scope, bound, body


def test_dispatch_preserves_source_order_and_armed_file_identity(workflow):
    journal, header, readbacks, answer, scope, bound, body = workflow
    role = scope['projects']['reme']['driver']
    valid = records([*header, *readbacks, *answer])
    journal.write_bytes(valid)
    slot = cap.admission(role, cap.PROFILE, body)['slot']
    assert slot['kind'] == 'answer' and slot['question_id'] == answer[-1]['question']
    row = dict(workflow_slot=slot, workflow_root=scope['root'], workflow_capability=bound,
               accounting_role='agent')
    cap.confirm_document_sender(row)

    replacement = journal.with_suffix('.replacement')
    replacement.write_bytes(valid)
    os.replace(replacement, journal)
    with pytest.raises(ValueError, match='stage changed'):
        cap.confirm_document_sender(row)

    fresh_slot = cap.admission(role, cap.PROFILE, body)['slot']
    row['workflow_slot'] = {**fresh_slot, 'journal_stage_sha256': 'a'*64}
    with pytest.raises(ValueError, match='stage changed'):
        cap.confirm_document_sender(row)

    journal.write_bytes(records([*header, *reversed(readbacks), *answer]))
    assert len(readbacks) > 1
    with pytest.raises(ValueError, match='readbacks incomplete'):
        cap.admission(role, cap.PROFILE, body)
    journal.write_bytes(records([*header, *readbacks, {'phase': 'verified-turn', 'question': 'foreign'}, *answer]))
    with pytest.raises(ValueError, match='outside verified source prefix'):
        cap.admission(role, cap.PROFILE, body)


def test_legacy_judge_checks_question_digest_and_terminal_newline(workflow):
    journal, header, readbacks, answer, scope, bound, _ = workflow
    role = scope['projects']['reme']['judge']
    question = answer[-1]['question']
    body = with_route({'messages': [{'role': 'user', 'content': 'offline rubric'}]}, profile=cap.PROFILE)
    seen = [{'phase': 'verified-turn', 'question': question}]
    stage = {'phase': 'judge-request', 'question': question, 'prompt_sha256': cap.digest('offline rubric')}
    prefix = records([*header, *readbacks, *seen])
    journal.write_bytes(prefix + records([stage]))
    slot = cap.admission(role, cap.PROFILE, body)['slot']
    assert slot['kind'] == 'judge' and slot['question_id'] == question
    cap.confirm_document_sender(dict(workflow_slot=slot, workflow_root=scope['root'], workflow_capability=bound,
                                     accounting_role='judge'))
    for request in ({**stage, 'question': 'unknown'}, {**stage, 'prompt_sha256': 'b'*64}):
        journal.write_bytes(prefix + records([request]))
        with pytest.raises(ValueError, match='current rubric'):
            cap.admission(role, cap.PROFILE, body)

    legacy = {key: value for key, value in slot.items() if key != 'journal_identity'}
    row = dict(workflow_slot=legacy, workflow_root=scope['root'], workflow_capability=bound,
               accounting_role='judge')
    for invalid in (b'', prefix + records([stage]).rstrip(b'\n'), prefix + b'{'):
        journal.write_bytes(invalid)
        with pytest.raises(ValueError, match='empty or incomplete'):
            cap.confirm_document_sender(row)
    journal.write_bytes(prefix + b'{\n' + records([stage]))
    with pytest.raises(json.JSONDecodeError):
        cap.admission(role, cap.PROFILE, body)


def test_ingest_and_cleanup_bind_the_native_unit_and_session(workflow):
    journal, header, readbacks, answer, scope, _, _ = workflow
    roles = scope['projects']['reme']
    memory_body = with_route({'messages': [{'role': 'user', 'content': 'offline native call'}]}, profile=cap.PROFILE)
    docs, _, _ = cap.schedule(scope['spec_sha256'], True)
    first_doc = docs[0]
    submit = dict(phase='submit', filename=first_doc.filename, content=first_doc.text,
                  sha256=first_doc.sha256, at=first_doc.cutoff)
    journal.write_bytes(records([*header, submit, {'phase': 'acceptance'}]))
    assert cap.admission(roles['memory'], cap.PROFILE, memory_body)['slot']['batch_id'] == first_doc.filename
    with pytest.raises(ValueError, match='stage does not permit'):
        cap.admission(roles['driver'], cap.PROFILE, memory_body)
    journal.write_bytes(records([*header, {**submit, 'content': 'different source bytes'}]))
    with pytest.raises(ValueError, match='submission differs'):
        cap.admission(roles['memory'], cap.PROFILE, memory_body)

    question = answer[-1]['question']
    events = [*header, *readbacks, *answer,
              dict(phase='answer-visible', question=question, surface={'session_id': 'observed'}),
              dict(phase='verified-turn', question=question)]
    close = dict(phase='chat-close-started', question=question, session_id='observed')
    journal.write_bytes(records([*events, close]))
    assert cap.admission(roles['memory'], cap.PROFILE, memory_body)['slot']['kind'] == 'cleanup'
    journal.write_bytes(records([*events, {**close, 'session_id': 'foreign'}]))
    with pytest.raises(ValueError, match='native close differs'):
        cap.admission(roles['memory'], cap.PROFILE, memory_body)


class MeasuredStream(BytesIO):
    bytes_read = 0

    def read(self, size=-1):
        block = super().read(size)
        self.bytes_read += len(block)
        return block

    def __next__(self):
        line = super().__next__()
        self.bytes_read += len(line)
        return line


@pytest.mark.parametrize('count', [100, 1000])
def test_document_journal_work_at_tenfold_scale(count):
    row = records([{'phase': 'answer-visible', 'surface': {'history': 'x'*1024}}])
    stage = {'phase': 'dispatch', 'question': 'offline answer'}
    raw = row * count + records([stage])
    tail = MeasuredStream(raw)
    assert document._final_record(tail, len(raw)) == stage
    assert tail.bytes_read == document.TAIL_BLOCK_BYTES
    scan = MeasuredStream(raw)
    summary = document._journal_evidence(scan, [], [], [], None)
    assert scan.bytes_read == len(raw)
    assert len(summary.tail) == 3 and summary.tail[-1] == stage


def test_final_stage_can_cross_blocks_in_a_crlf_journal():
    stage = {'phase': 'dispatch', 'question': 'x' * (2*document.TAIL_BLOCK_BYTES)}
    raw = records([{'phase': 'spec'}, stage]).replace(b'\n', b'\r\n')
    assert document._final_record(BytesIO(raw), len(raw)) == stage
