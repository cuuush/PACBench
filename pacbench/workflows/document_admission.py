"""Validate document requests against the active native workflow stage."""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
from typing import BinaryIO

from ..canary_capability import digest
from ..live_dispatch import process_identity
from .runner import Condition, inspect_source_cutoff
from .spec import Document, Turn, instant
from .tracks import MEMOS_OSS_LUNA_COMPAT, MEMOS_OSS_VERSION, validate_native_deployment

TAIL_BLOCK_BYTES = 4096


def _file_stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def _open_journal(path, *, allow_append=False):
    if path != path.resolve():
        raise ValueError('unexpected workflow journal location')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('unexpected workflow journal location')
        stamp = _file_stamp(info)
        yield stream, stamp
        current = _file_stamp(os.fstat(fd))
        try:
            named = _file_stamp(path.stat(follow_symlinks=False))
        except FileNotFoundError as exc:
            raise ValueError('Document workflow stage changed during verification') from exc
        def matches(observed):
            return observed == stamp or (allow_append and observed[:2] == stamp[:2] and observed[2] > stamp[2])

        if not matches(current) or not matches(named):
            raise ValueError('Document workflow stage changed during verification')


def _record(line: bytes) -> dict:
    if not line.endswith(b'\n'):
        raise ValueError('Document workflow stage changed: empty or incomplete journal record')
    event = json.loads(line.decode('utf-8'))
    if not isinstance(event, dict):
        raise ValueError('invalid document workflow journal record')
    return event


def _final_record(stream: BinaryIO, end: int) -> dict:
    """Seek backward only across the last record, including its closing newline."""
    stream.seek(max(0, end - TAIL_BLOCK_BYTES))
    block = stream.read(min(TAIL_BLOCK_BYTES, end))
    if not block.endswith(b'\n'):
        return _record(block)
    parts = [block]
    start = end - len(block)
    boundary = block.rfind(b'\n', 0, len(block) - 1)
    while boundary == -1 and start:
        size = min(start, TAIL_BLOCK_BYTES)
        start -= size
        stream.seek(start)
        block = stream.read(size)
        boundary = block.rfind(b'\n')
        parts.append(block)
    parts[-1] = parts[-1][boundary+1:]
    return _record(b''.join(reversed(parts)))


@dataclass
class JournalEvidence:
    tail: deque[dict] = field(default_factory=lambda: deque(maxlen=3))
    submit: dict | None = None
    ready_count: int = 0
    seen_count: int = 0
    seen_in_order: bool = True
    judge_question_seen: bool = False


def _journal_evidence(stream: BinaryIO, header: list[dict], documents: list[Document],
                      turns: list[Turn], judge_question: str | None) -> JournalEvidence:
    evidence = JournalEvidence()
    evidence.tail.extend(header)
    for line in stream:
        event = _record(line)
        evidence.tail.append(event)
        phase = event['phase']
        if phase == 'submit':
            evidence.submit = event
        elif phase == 'readback':
            proof = event['evidence']
            index = evidence.ready_count
            if (index >= len(documents) or proof.get('ready') is not True or not proof.get('readback')
                    or proof['submitted_sha256'] != documents[index].sha256):
                raise ValueError('previous native document readbacks incomplete')
            evidence.ready_count += 1
        elif phase == 'verified-turn':
            question = event['question']
            index = evidence.seen_count
            evidence.seen_in_order &= index < len(turns) and question == turns[index].id
            evidence.seen_count += 1
            if judge_question is not None and question == judge_question:
                evidence.judge_question_seen = True
    return evidence


def _deployment(header, scope, root, store):
    spec, native, initial = header
    if (spec['phase'] != 'spec' or spec['sha256'] != scope['spec_sha256']
            or spec['source_basis'] != scope['frozen_inputs']
            or native['phase'] != 'condition' or native['evidence_kind'] != 'native'):
        raise ValueError('unverified workflow or native account')
    condition = Condition(**native['condition'])
    record = native['deployment_witness']
    validate_native_deployment(condition, record)
    if (condition.provider != scope['provider'] or condition.track != 'nightly-document'
            or condition.state != 'continuing-account'
            or (condition.provider == 'memos' and condition.version != MEMOS_OSS_VERSION)
            or record['store_identity'] != str(root/store)):
        raise ValueError('deployment or native store differs')
    witness = initial.get('evidence', {})
    if (record['account'] != str(root) or initial.get('phase') != 'account-readback'
            or initial.get('question') is not None or witness.get('account') != str(root)
            or not witness.get('readback') or witness.get('chat_count') != 0
            or witness.get('conversation_writes') != 0 or witness.get('source_files') != []):
        raise ValueError('missing fresh native account readback')
    return condition, record


@dataclass(frozen=True)
class DocumentRequest:
    scope: dict
    body: dict
    condition: Condition
    deployment: dict
    sender: dict
    role: str
    documents: list[Document]
    turns: list[Turn]
    journal: JournalEvidence

    @property
    def event(self):
        return self.journal.tail[-1]


def _ingest(request: DocumentRequest):
    from .capability import schedule

    index = request.journal.ready_count
    if index >= len(request.documents) or request.journal.submit is None:
        raise ValueError('no pending native document')
    doc = request.documents[index]
    submit = request.journal.submit
    if (submit['filename'], submit['content'], submit['sha256'], submit['at']) != (
            doc.filename, doc.text, doc.sha256, doc.cutoff):
        raise ValueError('native submission differs from current nightly unit')
    all_documents, _, _ = schedule(request.scope['spec_sha256'], False)
    released = request.documents[:index+1]
    if request.condition.provider == 'mem0':
        if request.sender != request.scope['controller'] or not mem0_import_request(
                request.body, doc, all_documents, released):
            raise ValueError('Mem0 import request lacks its exact released document')
    else:
        inspect_source_cutoff({'model_requests':[request.body], 'prefetch':None}, all_documents,
                              [item.filename for item in released], [])
    return dict(kind='ingest', batch_id=doc.filename)


def _answer(request: DocumentRequest):
    evidence = request.journal
    if not evidence.seen_in_order or evidence.seen_count >= len(request.turns):
        raise ValueError('answer outside verified source prefix or schedule')
    turn, event = request.turns[evidence.seen_count], request.event
    inventory = evidence.tail[-2]
    account = request.deployment['account']
    prefix = [doc.filename for doc in request.documents if instant(doc.cutoff) <= instant(turn.at)]
    if (event['question'] != turn.id or event['prompt'] != turn.prompt or event['at'] != turn.at
            or inventory['phase'] != 'pre-dispatch-inventory' or inventory['account'] != account
            or event['account'] != account or inventory['question'] != turn.id
            or inventory['source_files'] != prefix or evidence.ready_count != len(prefix)):
        raise ValueError('answer outside verified source prefix or schedule')
    _require_mem0_gateway(request, 'answer')
    if request.role == 'driver':
        users = [message for message in request.body['messages'] if message['role'] == 'user']
        if (not users or not isinstance(users[-1].get('content'), str)
                or not (users[-1]['content'] == turn.prompt or users[-1]['content'].startswith(turn.prompt+'\n\n'))
                or sorted(tool['function']['name'] for tool in request.body.get('tools', []))
                    != sorted(request.condition.tool_names)):
            raise ValueError('driver question or tool surface differs')
    return dict(kind='answer', question_id=turn.id)


def _cleanup(request: DocumentRequest):
    visible, verified, event = request.journal.tail
    question = event['question']
    if (verified.get('phase') != 'verified-turn' or visible.get('phase') != 'answer-visible'
            or verified.get('question') != question or visible.get('question') != question
            or visible['surface']['session_id'] != event['session_id']):
        raise ValueError('native close differs from the verified session')
    _require_mem0_gateway(request, 'cleanup')
    return dict(kind='cleanup', question_id=question)


def _judge(request: DocumentRequest):
    event = request.event
    messages = request.body['messages']
    if (not request.journal.judge_question_seen or len(messages) != 1
            or messages[0]['role'] != 'user' or digest(messages[0]['content']) != event['prompt_sha256']):
        raise ValueError('judge request differs from verified turn and current rubric')
    return dict(kind='judge', question_id=event['question'])


def _require_mem0_gateway(request: DocumentRequest, stage: str):
    if request.condition.provider == 'mem0' and request.sender != request.deployment['native_processes']['driver']:
        raise ValueError(f'Mem0 {stage}-stage request came from outside the warm gateway')


STAGES = {
    'submit': (_ingest, {'memory'}),
    'acceptance': (_ingest, {'memory'}),
    'dispatch': (_answer, {'driver', 'memory'}),
    'chat-close-started': (_cleanup, {'memory'}),
    'judge-request': (_judge, {'judge'}),
}


def _sender(scope, provider, role, record, body):
    if provider == 'memos' and role == 'memory' and (
            os.environ.get('PACBENCH_MEMOS_OSS_LUNA_COMPAT') != MEMOS_OSS_LUNA_COMPAT or 'top_p' in body):
        raise ValueError('MemOS OSS native request lacks Luna sampling compatibility')
    senders = [scope['controller'] if role == 'judge' else record['native_processes'][role]]
    if provider == 'mem0' and role == 'memory':
        # The SDK runs in the controller during import and in Hermes during recall and writeback.
        senders.append(record['native_processes']['driver'])
    sender = process_identity()
    if sender not in senders:
        raise ValueError('sender differs from run-owned native process')
    return sender


def admit_document(scope, project, body):
    from ..openrouter_route import validate_request
    from .capability import DOCUMENT_ARMS, ENV, PROFILE, schedule
    from .grading import claimed_request

    provider = scope['provider']
    arm = DOCUMENT_ARMS.get(provider)
    if arm is None:
        raise ValueError('unsupported document workflow provider')
    validate_request(body, profile=PROFILE)
    root = Path(scope['root'])
    path = root/'workflow/workflow.jsonl'
    checkpoint = claimed_request(scope, project, body)
    result = dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                  projects=[project])
    with _open_journal(path, allow_append=checkpoint is not None) as (stream, stamp):
        header = [_record(next(stream, b'')) for _ in range(3)]
        condition, record = _deployment(header, scope, root, arm.store)
        if checkpoint is not None:
            # The SQL claim binds the verified judge turn while the native journal advances.
            return {**result, 'slot': checkpoint}
        role = next(role for role, name in scope['projects'][provider].items() if name == project)
        sender = _sender(scope, provider, role, record, body)
        documents, turns, _ = schedule(scope['spec_sha256'], scope['canary'])
        judge_question = None
        if role == 'judge':
            # Legacy judges need membership for one question only. Keep that question,
            # rather than all prior IDs, while scanning the retained evidence.
            beginning = stream.tell()
            judge_event = _final_record(stream, stamp[2])
            if judge_event['phase'] == 'judge-request':
                judge_question = judge_event['question']
            stream.seek(beginning)
        evidence = _journal_evidence(stream, header, documents, turns, judge_question)
    request = DocumentRequest(scope, body, condition, record, sender, role, documents, turns, evidence)
    event = request.event
    policy = STAGES.get(event['phase'])
    if policy is None or role not in policy[1]:
        raise ValueError('workflow stage does not permit this request')
    handler, _ = policy
    slot = handler(request)
    slot.update(provider=provider, journal_stage_sha256=digest(event), journal_identity=stamp)
    if provider == 'hindsight' and role == 'memory':
        work = hindsight_consolidation_call(root)
        if work is not None:
            slot['native_work'] = work
    return {**result, 'slot': slot}


def confirm_sender(scope, row):
    from .capability import DOCUMENT_ARMS, ENV

    root = Path(scope['root'])
    provider, slot = scope['provider'], row['workflow_slot']
    journal = root/'workflow/workflow.jsonl'
    if (provider not in DOCUMENT_ARMS or provider != slot['provider'] or row['workflow_root'] != str(root)
            or row['workflow_capability'] != json.loads(os.environ[ENV]) or journal != journal.resolve()):
        raise ValueError('Document dispatch no longer belongs to its active scope')
    if provider == 'memos' and row['accounting_role'] == 'memory_provider' and (
            os.environ.get('PACBENCH_MEMOS_OSS_LUNA_COMPAT') != MEMOS_OSS_LUNA_COMPAT
            or row.get('native_request_compatibility') != MEMOS_OSS_LUNA_COMPAT):
        raise ValueError('MemOS OSS sampling compatibility changed before dispatch')
    with _open_journal(journal) as (stream, stamp):
        # Already-prepared historical rows carried only the stage digest.
        if ('journal_identity' in slot and slot['journal_identity'] != stamp
                or digest(_final_record(stream, stamp[2])) != slot['journal_stage_sha256']):
            raise ValueError('Document workflow stage changed before dispatch')
    if provider == 'hindsight' and row['accounting_role'] == 'memory_provider':
        if hindsight_consolidation_call(root) != slot.get('native_work'):
            raise ValueError('Hindsight native consolidation boundary changed before dispatch')


def mem0_import_request(body, document, all_documents, released):
    from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT
    from mem0.memory.utils import parse_messages

    messages = body.get('messages', [])
    if (len(messages) != 2 or messages[0].get('role') != 'system'
            or messages[0].get('content') != ADDITIVE_EXTRACTION_PROMPT or messages[1].get('role') != 'user'
            or not isinstance(messages[1].get('content'), str) or body.get('tools')):
        return False
    current = parse_messages([{'role':'user','content':document.text}])
    prompt = messages[1]['content']
    _, marker, tail = prompt.partition('\n\n## New Messages\n')
    observed, boundary, _ = tail.partition('\n\n## Observation Date\n')
    if (not marker or not boundary or observed != current
            or not prompt.startswith('## Summary\n\n\n## Last k Messages\n')
            or '\n\n## Custom Instructions\n' in prompt):
        return False
    try:
        inspect_source_cutoff({'model_requests':[body], 'prefetch':None}, all_documents,
                              [doc.filename for doc in released], [])
    except ValueError:
        return False
    return True


def hindsight_consolidation_call(root):
    from pacbench.providers.hindsight.evidence import native_consolidation_call

    bank = json.loads((root/'hermes-home/hindsight/config.json').read_text())['bank_id']
    return native_consolidation_call(bank)
