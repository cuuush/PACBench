"""Admit stock Honcho representation for the shared seven-document condition."""
from contextlib import ExitStack
from contextvars import ContextVar
import json
import os
from functools import lru_cache, wraps
from hashlib import sha256
from pathlib import Path
import re
import tempfile
import time

from .canary_capability import CanaryBusy, binding, digest, read, sha
from .live_dispatch import enabled, process_identity
from .workflow_spec import SPEC, instant

CONDITION = 'honcho-seven-daily-documents-v1'
LOCK = 'native-diagnostic-owner.lock'
RUNTIME = 'honcho-daily-runtime.json'
EXTENSION = 'pacbench.honcho.daily-attempt'
REVISION = '70c67624169683b9e265517aec95a51b8077cabc'
CANONICAL_LAYOUT = ('document_index', 'cutoff', 'name', 'sha256', 'source_ids')
_work = ContextVar('honcho_daily_work', default=None)
_batch = ContextVar('honcho_daily_batch', default=None)
_executor = ContextVar('honcho_daily_executor', default=None)


class GateError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def schedule():
    from .workflow_memcal_account import _source_messages
    from .workflow_spec import compile_week

    _, legacy, turns, _ = compile_week('continuing-account')
    cutoffs = {key: doc.cutoff for doc in legacy for key in doc.source_ids}
    groups = {}
    for row in sorted(_source_messages(), key=lambda r: (instant(r['written_at']), r['source_id'])):
        key = row['source_id']
        groups.setdefault(cutoffs[key], []).append(key)
    ordered = sorted(groups.items(), key=lambda release: instant(release[0]))
    if [len(ids) for _, ids in ordered] != [39, 23, 27, 24, 9, 10, 10]:
        raise GateError('Frozen daily source inventory differs')
    return ordered, turns


def _plan(manifest, layout):
    """Read the producer's exact JSON. Layout selects existing fields only."""
    collection, cutoff_key, file_key, hash_key, ids_key = layout
    source = read(manifest)
    if tuple(layout) == CANONICAL_LAYOUT:
        from .workflow_daily_documents import compile_daily_week

        if source != compile_daily_week()[4]:
            raise GateError('Honcho manifest differs from the frozen daily renderer')
    documents = source[collection]
    folder = Path(manifest['path']).parent
    plan = []
    for row, (cutoff, ids) in zip(documents, schedule()[0], strict=True):
        path = folder / row[file_key]
        if (instant(row[cutoff_key]) != instant(cutoff) or row[ids_key] != ids
                or path != path.resolve() or not path.is_relative_to(folder) or path.suffix != '.txt'
                or not re.fullmatch('[0-9a-f]{64}', row[hash_key])):
            raise GateError('Canonical manifest differs from the frozen seven daily releases')
        plan.append(dict(at=cutoff, path=str(path), sha256=row[hash_key], source_ids=ids))
    if len({r['path'] for r in plan}) != 7 or len({r['sha256'] for r in plan}) != 7:
        raise GateError('Canonical daily files must be distinct')
    return plan


def materialize_canonical_files(folder):
    """Render the shared seven-file input and return its manifest binding."""
    from .workflow_daily_documents import compile_daily_week

    folder = Path(folder).absolute()
    if folder != folder.resolve():
        raise GateError('Expected an owned canonical input directory')
    _, documents, _, _, manifest = compile_daily_week()
    folder.mkdir(mode=0o700)
    for doc in documents:
        with (folder / doc.filename).open('xb') as stream:
            stream.write(doc.text.encode('utf-8'))
    return _write(folder / 'manifest.json', manifest)


def _payload(entry, layout=None):
    raw = Path(entry['path']).read_bytes()
    content = raw.decode('utf-8')
    if len(content) > 25000 or sha256(raw).hexdigest() != entry['sha256']:
        raise GateError('Canonical file changed or exceeds a single stock upload chunk')
    if layout and tuple(layout) == CANONICAL_LAYOUT:
        from .workflow_daily_documents import compile_daily_week

        _, documents, _, _, _ = compile_daily_week()
        if not any(doc.cutoff == entry['at'] and doc.filename == Path(entry['path']).name
                   and list(doc.source_ids) == entry['source_ids'] and doc.text == content for doc in documents):
            raise GateError('Honcho release bytes or source IDs differ from the frozen daily renderer')
        return raw
    ids = entry['source_ids']
    found = re.findall(r'\bmessage-[0-9a-f]{24}\b', content)
    if found != ids:
        raise GateError('Daily file omits, repeats, reorders or exposes a foreign source ID')
    return raw


def _write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
        stream.write((json.dumps(value, sort_keys=True) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
        os.link(stream.name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return binding(path)


def _project(root):
    return 'pacbench-' + sha256(str(root).encode()).hexdigest()[:12] + '-honcho-memory'


def projects(root, first_question=False):
    memory = _project(root)
    roles = {'memory': memory}
    if first_question:
        prefix = memory.removesuffix('-memory')
        roles.update(driver=prefix+'-driver', dialectic=prefix+'-dialectic-memory')
    return {'honcho': roles}


def chat_enabled(scope):
    return bool(scope.get('first_question') or scope.get('week_schedule'))


def _lock(scope):
    from .workflow_capability import _has_flock

    owner, proof = scope['controller'], scope['lock']
    path, fd = Path(proof['path']), proof['fd']
    st = path.stat()
    expected = Path(scope['root']).parent.parent/'execution.lock' if chat_enabled(scope) else path.parent/LOCK
    if (path != expected or path != path.resolve() or path.is_relative_to(scope['root'])
            or proof['inode'] != [st.st_dev, st.st_ino] or process_identity(owner['pid']) != owner
            or Path(f"/proc/{owner['pid']}/fd/{fd}").resolve() != path or not _has_flock(owner['pid'], fd)):
        raise GateError('Shared execution lock is no longer held by its controller')


def create(root, *, execution_lock, session_id, canonical_manifest, manifest_sha256,
           prepaid, auto_recharge_evidence,
           layout=('documents', 'cutoff', 'file', 'document_sha256', 'source_ids'), first_question=False, week=False):
    """Bind the canonical producer's files and the already-held execution lock."""
    root = Path(root).resolve(strict=True)
    path = Path(canonical_manifest).absolute()
    if (root.name != 'honcho' or path != path.resolve()
            or not re.fullmatch('[A-Za-z0-9_-]+', session_id)
            or any((root / p).exists() for p in ('workflow-scope.json', 'honcho-db', 'receipts.jsonl'))):
        raise GateError('Expected a fresh Honcho arm and an exact canonical manifest')
    manifest = binding(path)
    if manifest['sha256'] != manifest_sha256:
        raise GateError('Canonical manifest SHA-256 differs from the pinned producer output')
    plan = _plan(manifest, layout)
    if (type(first_question) is not bool or type(week) is not bool or first_question and week
            or (first_question or week) and tuple(layout) != CANONICAL_LAYOUT):
        raise GateError('Honcho chat needs one scope and the exact shared daily layout')
    for entry in plan:
        _payload(entry, layout)
    fd = execution_lock.fileno()
    st = os.fstat(fd)
    scope = dict(admission=CONDITION, protocol='pizza-week-conversation-v2', provider='honcho',
                 root=str(root), run_id=root.parent.name, controller=process_identity(), session=session_id,
                 projects=projects(root,first_question or week), spec_sha256=sha(SPEC),
                 manifest=manifest, layout=list(layout), plan=plan, prepaid=prepaid,
                 auto_recharge_evidence=auto_recharge_evidence,
                 lock=dict(fd=fd, path=str(Path(f'/proc/self/fd/{fd}').resolve()), inode=[st.st_dev, st.st_ino]))
    if first_question:
        scope['first_question'] = 'rsvp-before'
    if week:
        from .honcho_week_state import plan as week_plan

        (root/'week-turns').mkdir(mode=0o700)
        (root/'chat-writes').mkdir(mode=0o700)
        scope['week_schedule'] = _write(root/'ordered-questions.json',week_plan()[2])
    _lock(scope)
    return _write(root / 'workflow-scope.json', scope)


def validate(scope, bound):
    root = Path(scope['root'])
    first_question = 'first_question' in scope
    if (root != root.resolve() or root.name != 'honcho' or scope['provider'] != 'honcho'
            or scope['admission'] != CONDITION or scope['protocol'] != 'pizza-week-conversation-v2'
            or scope['run_id'] != root.parent.name or bound != binding(root / 'workflow-scope.json')
            or scope['projects'] != projects(root,chat_enabled(scope))
            or first_question and (scope['first_question'] != 'rsvp-before' or tuple(scope['layout']) != CANONICAL_LAYOUT)
            or scope['spec_sha256'] != sha(SPEC) or scope['plan'] != _plan(scope['manifest'], scope['layout'])
            or not re.fullmatch('[A-Za-z0-9_-]+', scope['session'])):
        raise GateError('Unbound Honcho daily document condition')
    if 'week_schedule' in scope:
        from .honcho_week_state import plan as week_plan

        schedule = scope['week_schedule']
        if (first_question or tuple(scope['layout'])!=CANONICAL_LAYOUT
                or schedule!=binding(root/'ordered-questions.json') or read(schedule)!=week_plan()[2]):
            raise GateError('Unbound Honcho Week question schedule')
    _lock(scope)


def controller(root):
    from .workflow_capability import load

    scope = load()
    if scope['admission'] != CONDITION or scope['root'] != str(root) or scope['controller'] != process_identity():
        raise GateError('Only the bound controller may advance the release')
    return scope


def register(root, worker_pid, *, api_pid=None, api_url=None):
    scope = controller(root)

    def owned(pid):
        child = process_identity(pid)
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if int(stat[1]) != scope['controller']['pid'] or child['uid'] != scope['controller']['uid']:
            raise GateError('Honcho runtime is not the controller child')
        return child

    worker = owned(worker_pid)
    host = json.loads((root / 'hermes-home/honcho.json').read_bytes())['hosts']['hermes']
    api = {}
    if chat_enabled(scope):
        if not api_pid or api_pid == worker_pid or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+',api_url or ''):
            raise GateError('First question requires a distinct owned native loopback API')
        (root/'peer-reads').mkdir(mode=0o700)
        api = dict(api=owned(api_pid),api_url=api_url)
    return _write(root / RUNTIME, dict(worker=worker, host=host, scope=binding(root/'workflow-scope.json'),
        store=binding(root/'native-container-ownership.json'), settings=binding(root/'native-processing-settings.json'),**api))


def runtime(root):
    record = read(binding(root / RUNTIME))
    worker = record['worker']
    if process_identity(worker['pid']) != worker or record['scope'] != binding(root/'workflow-scope.json'):
        raise GateError('Owned Honcho deriver runtime changed')
    return record


def owned_container(root,native):
    from provider_adapters.honcho_checkpoint import docker
    from provider_adapters.honcho_local import PGVECTOR_IMAGE

    host = native['host']
    space = host['workspace']
    settings, owner = read(native['settings']), read(native['store'])
    cid = owner['container_id']
    mount = str(root / 'honcho-db')
    if (not re.fullmatch('[A-Za-z0-9_-]+', space) or not re.fullmatch('[0-9a-f]{64}', cid)
            or settings['multi_tenant'] is not False or settings['native_batching_defaults_verified'] is not True
            or (owner['run_root'], owner['label_root'], owner['data_bind_mount']) != (str(root), str(root), mount)):
        raise GateError('Foreign Honcho workspace, store or native settings')
    inspected, = json.loads(docker('inspect', cid, capture_output=True).stdout)
    mounts = [(m['Type'], m['Source']) for m in inspected['Mounts'] if m['Destination']=='/var/lib/postgresql/data']
    if (inspected['Id'] != cid or not inspected['State']['Running'] or inspected['Config']['Image'] != PGVECTOR_IMAGE
            or inspected['Config']['Labels'].get('pacbench.import-root') != str(root) or mounts != [('bind', mount)]):
        raise GateError('Honcho database mount or container changed')
    return cid


def capture(root, scope, native):
    """One physical, read-only observation of the source uploads and their queue."""
    from provider_adapters.honcho_checkpoint import sql

    cid = owned_container(root,native)
    space,session = native['host']['workspace'],scope['session']
    return json.loads(sql(cid, 'postgres', f"""
        SELECT coalesce(json_agg(json_build_object(
          'public_id',m.public_id,'local_id',m.id,'session',s.name,'session_id',s.id,
          'peer',m.peer_name,'at',m.created_at,'sha256',encode(sha256(convert_to(m.content,'UTF8')),'hex'),
          'seq',m.seq_in_session,'meta',m.metadata,'file',m.internal_metadata,
          'embeddings',(SELECT json_build_array(count(*),
            count(*) FILTER (WHERE e.sync_state='synced' AND e.embedding IS NOT NULL),
            count(*) FILTER (WHERE e.sync_state='failed')) FROM message_embeddings e
            WHERE e.tenant_id=m.tenant_id AND e.message_id=m.public_id AND e.workspace_name=m.workspace_name),
          'queue',CASE WHEN q.id IS NULL THEN NULL ELSE json_build_object(
            'id',q.id,'session_id',q.session_id,'unit',q.work_unit_key,'tenant',q.tenant_id,
            'peer',q.payload->>'observed','observers',q.payload->'observers','at',q.payload->>'created_at',
            'sha256',encode(sha256(convert_to(q.payload->>'content','UTF8')),'hex'),
            'session',q.payload->>'session_name','processed',q.processed,'failed',q.error IS NOT NULL,
            'claim',a.id) END) ORDER BY m.seq_in_session,m.id,q.id),'[]'::json)
        FROM messages m JOIN sessions s ON s.workspace_name=m.workspace_name AND s.name=m.session_name
          AND s.tenant_id=m.tenant_id
        LEFT JOIN queue q ON q.workspace_name=m.workspace_name AND q.message_id=m.id AND q.task_type='representation'
        LEFT JOIN active_queue_sessions a ON a.work_unit_key=q.work_unit_key AND a.tenant_id IS NOT DISTINCT FROM q.tenant_id
        WHERE m.workspace_name='{space}' AND m.deleted_at IS NULL AND s.deleted_at IS NULL AND s.is_active
          AND (s.name='{session}' OR m.internal_metadata ? 'file_id')
        """))


def _read_stage(root, index, phase):
    path = root / f'honcho-daily-{index}-{phase}.json'
    if path != path.resolve():
        raise GateError('Foreign release evidence path')
    return path, read(binding(path)) if path.exists() else None


def _progress(root, scope):
    """Return the completed prefix and its one possible pending upload."""
    completed = []
    pending = None
    for index, entry in enumerate(scope['plan'], 1):
        intent, seal, ready = (_read_stage(root, index, phase) for phase in ('intent', 'sealed', 'ready'))
        if intent[1] is None:
            if seal[1] is not None or ready[1] is not None:
                raise GateError('Native release evidence lacks its original upload intent')
            continue
        if pending is not None or index != len(completed)+1 or intent[1] != entry:
            raise GateError('Honcho uploads are out of order or the previous release is unresolved')
        if ready[1] is not None:
            if seal[1] is None or ready[1]['release'] != binding(seal[0]):
                raise GateError('Retained readiness does not bind its sealed release')
            completed.append({**seal[1], 'ready': ready[1]})
        else:
            pending = (index, entry, seal[1])
    return completed, pending


def _file(scope, native, actual, index):
    entry = scope['plan'][index-1]
    path = Path(entry['path'])
    meta = actual['file']
    if (actual['seq'] != index or actual['session'] != scope['session']
            or actual['peer'] != native['host']['peerName'] or instant(actual['at']) != instant(entry['at'])
            or not actual['public_id'] or type(actual['local_id']) is not int or actual['local_id'] <= 0
            or not actual['session_id']
            or actual['sha256'] != entry['sha256'] or actual['meta'] != {'filename': path.name}
            or not meta.get('file_id') or meta.get('filename') != path.name or meta.get('chunk_index') != 0
            or meta.get('total_chunks') != 1 or meta.get('original_file_size') != path.stat().st_size):
        raise GateError('Native chunk does not bind one exact daily file and cutoff')
    return {key: actual[key] for key in actual if key not in ('queue', 'embeddings')}


def _inventory(root, scope, native, completed, pending=None):
    actual = capture(root, scope, native)
    expected = [saved['file'] for saved in completed]
    if len(actual) != len(expected)+int(pending is not None):
        raise GateError('Honcho has an extra, duplicate or missing upload chunk')
    files = [_file(scope, native, row, index) for index, row in enumerate(actual, 1)]
    if (files[:len(expected)] != expected or len({f['session_id'] for f in files}) > 1
            or any(len({f[key] for f in files}) != len(files) for key in ('public_id','local_id'))
            or len({f['file']['file_id'] for f in files}) != len(files)):
        raise GateError('Honcho upload identities changed or were reused')
    for row, released in zip(actual, completed):
        _embedding_ready(row)
        if row['queue'] is not None:
            q = _queue(scope, native, row)
            if q['id'] != released['queue_id'] or q['processed'] is not True:
                raise GateError('Previously verified native representation was replaced or reopened')
    return files, actual


def _embedding_ready(row):
    total, synced, failed = row['embeddings']
    if failed:
        raise GateError('Native indexing failed for the daily upload')
    if not total or total != synced:
        raise CanaryBusy('Native indexing for the exact upload is not complete')


def _queue(scope, native, row):
    q = row['queue']
    if q is None:
        raise CanaryBusy('Native asynchronous representation enrollment has not been observed')
    host = native['host']
    peer = host['peerName']
    unit = ':'.join(('representation', host['workspace'], scope['session'], peer))
    if (type(q['id']) is not int or q['id'] <= 0 or q['unit'] != unit or q['failed']
            or q['tenant'] is not None or q['session_id'] != row['session_id'] or q['session'] != row['session']
            or q['peer'] != peer or sorted(q['observers']) != sorted((peer, host['aiPeer']))
            or instant(q['at']) != instant(row['at']) or q['sha256'] != row['sha256']):
        raise GateError('Representation enrollment differs from its source chunk')
    return q


def begin(root, at):
    """Durably authorize one stock upload and return only that day's bytes."""
    scope = controller(root)
    completed, pending = _progress(root, scope)
    index = len(completed)+1
    limit = 1 if scope.get('first_question') else len(scope['plan'])
    if pending or index > limit or at != scope['plan'][index-1]['at']:
        raise GateError('Next exact daily release required, with no upload replay')
    if scope.get('week_schedule'):
        from .honcho_week_state import release_scope

        release_scope(scope,at)
    _require_receipts(root, scope, completed)
    _inventory(root, scope, runtime(root), completed)
    entry = scope['plan'][index-1]
    raw = _payload(entry, scope['layout'])
    _write(_read_stage(root, index, 'intent')[0], entry)
    return Path(entry['path']).name, raw


def seal(root, at, session, acknowledged, *, timeout=30):
    """Verify one SDK acknowledgement, independent scoped GET, and native enrollment."""
    scope = controller(root)
    completed, pending = _progress(root, scope)
    if not pending or at != pending[1]['at'] or pending[2] is not None or len(acknowledged) != 1:
        raise GateError('Expected one previously authorized file upload acknowledgement')
    index, entry, _ = pending
    native = runtime(root)
    host = native['host']
    if session.workspace_id != host['workspace'] or session.id != scope['session']:
        raise GateError('Upload readback is outside the bound stock session')
    ack, = acknowledged
    stored = session.get_message(ack.id)
    for message in (ack, stored):
        if ((message.workspace_id, message.session_id, message.peer_id, message.id)
                != (host['workspace'], scope['session'], host['peerName'], ack.id)
                or message.created_at != instant(at) or message.metadata != {'filename': Path(entry['path']).name}
                or sha256(message.content.encode()).hexdigest() != entry['sha256']):
            raise GateError('Stock upload acknowledgement or readback differs from the canonical file')
    deadline = time.monotonic()+timeout
    while True:
        files, rows = _inventory(root, scope, native, completed, pending)
        file, row = files[-1], rows[-1]
        try:
            q = _queue(scope, native, row)
            break
        except CanaryBusy:
            if time.monotonic() >= deadline:
                raise TimeoutError('Honcho did not enroll the exact stock upload') from None
            time.sleep(.2)
    if file['public_id'] != ack.id or q['processed'] or any(s['queue_id']==q['id'] for s in completed):
        raise GateError('Ambiguous native chunk or enrollment was already consumed')
    return _write(_read_stage(root, index, 'sealed')[0],
                  dict(file=file, queue_id=q['id'], at=at, runtime=binding(root/RUNTIME)))


def _check_release(root, scope, native, *, complete=False):
    completed, pending = _progress(root, scope)
    if pending is None or pending[2] is None:
        raise CanaryBusy('Honcho daily release has not been sealed')
    index, entry, sealed = pending
    files, rows = _inventory(root, scope, native, completed, pending)
    q = _queue(scope, native, rows[-1])
    if sealed != dict(file=files[-1], queue_id=q['id'], at=entry['at'], runtime=binding(root/RUNTIME)):
        raise GateError('Honcho native representation no longer matches the sealed release')
    if q['processed'] is not complete:
        if complete:
            raise CanaryBusy('The exact native representation row is still pending')
        raise GateError('Honcho native representation is already processed')
    if complete:
        _embedding_ready(rows[-1])
    return index, files, q, binding(_read_stage(root, index, 'sealed')[0])


def _successful(scope, row, release, queue_id):
    from .live_dispatch import operation_id
    from .honcho_first_answer_admission import settled

    slot = row.get('workflow_slot', {})
    return (row.get('workflow_root') == scope['root'] and row.get('project') == scope['projects']['honcho']['memory']
            and row.get('workflow_capability') == binding(Path(scope['root'])/'workflow-scope.json')
            and slot.get('honcho_daily_release') == release
            and row.get('honcho_daily_attempt', {}).get('queue_id') == queue_id
            and row.get('dispatch_context', {}).get('operation_id') == operation_id(row)
            and row.get('dispatch_execution') == 'response_ended' and row.get('http_status') == 200
            and bool(row.get('finished')) and (not chat_enabled(scope) or settled(row)))


def _source_completion(scope, rows, release, queue_id):
    attempts=[row for row in rows.values() if row.get('workflow_slot',{}).get('honcho_daily_release')==release]
    if (not attempts or not all(_successful(scope, row, release, queue_id) for row in attempts)
            or attempts[-1].get('honcho_validation_rejection')):
        return None
    final=attempts[-1]
    if len(attempts)==1:
        return final if not final.get('retry_of') else None
    if not scope.get('week_schedule') or len({row['upstream_request_id'] for row in attempts})!=len(attempts):
        return None
    from .honcho_week_retry import reserve_retry

    previous=reserve_retry({row['id']:row for row in attempts[:-1]},final)
    return final if previous and final.get('retry_of')==previous['id'] else None


def _require_receipts(root, scope, completed):
    if not completed:
        return
    from .observational_receipts import RunReceiptJournal

    with RunReceiptJournal(root / 'receipts.jsonl').journal() as (rows, _):
        for saved in completed:
            ready = saved['ready']
            final = _source_completion(scope, rows, ready['release'], saved['queue_id'])
            if final is None or final['id'] != ready['attempt']:
                raise GateError('Retained readiness lost its exact receipt attempt')


def finish(root, at):
    """Retain successful processing of the exact source before exposing it to follow-ups."""
    from .observational_receipts import RunReceiptJournal

    scope = controller(root)
    journal = RunReceiptJournal(root / 'receipts.jsonl')
    with journal.journal() as (rows, _):
        index, _, q, release = _check_release(root, scope, runtime(root), complete=True)
        if at != scope['plan'][index-1]['at']:
            raise GateError('Readiness cutoff differs from the sealed release')
        final = _source_completion(scope, rows, release, q['id'])
        if final is None:
            raise GateError('Native completion lacks a matching finished generation attempt')
        return _write(_read_stage(root, index, 'ready')[0], dict(release=release, attempt=final['id']))


def followup_ready(root, turn_id):
    """Check the exact source prefix for one frozen question without granting a model permit."""
    scope = controller(root)
    turn = next((t for t in schedule()[1] if t.id == turn_id), None)
    completed, pending = _progress(root, scope)
    if (turn is None or pending is not None
            or scope.get('first_question') not in (None,turn_id)
            or [s['at'] for s in completed] != [e['at'] for e in scope['plan'] if instant(e['at']) <= instant(turn.at)]):
        raise GateError('Frozen follow-up does not match the verified release prefix')
    _require_receipts(root, scope, completed)
    _inventory(root, scope, runtime(root), completed)
    return turn.prompt


def observe(original, variable, extract):
    @wraps(original)
    async def wrapped(*args, **kwargs):
        scope = [extract(*args, **kwargs)]
        token = variable.set(scope)
        try:
            return await original(*args, **kwargs)
        finally:
            scope.clear()  # Expire the evidence inherited by any later task.
            variable.reset(token)
    return wrapped


def work(manager, work_unit_key, worker_id):
    return manager, work_unit_key, worker_id, manager.worker_ownership.get(worker_id)


def batch(messages, configuration, **kwargs):
    return messages, kwargs


def executor(*args, **kwargs):
    return kwargs.get('telemetry'), kwargs.get('plan')


def _request_binding(request):
    return request.method, str(request.url), sha256(request.content).digest()


def observe_build(original):
    @wraps(original)
    def wrapped(client, options, *, retries_taken=0):
        request = original(client, options, retries_taken=retries_taken)
        if EXTENSION in request.extensions:
            raise GateError('Unexpected caller-supplied native SDK evidence')
        scope = _executor.get()
        if scope:
            request.extensions[EXTENSION] = scope, options.idempotency_key, retries_taken, _request_binding(request)
        return request
    return wrapped


def _live(scope, slot=None):
    root = Path(scope['root'])
    native = runtime(root)
    if native['worker'] != process_identity() or not enabled():
        raise GateError('Only the metered owned Honcho deriver may send')
    work, batch, call = _work.get(), _batch.get(), _executor.get()
    if not work or not call:
        raise GateError('Paid call lacks stock work, batch or executor evidence')
    manager, unit, worker_id, owner = work[0]
    telemetry, plan = call[0]
    if (not owner or manager.worker_ownership.get(worker_id) is not owner
            or owner.work_unit_key != unit or not owner.aqs_id or not telemetry or not plan
            or not telemetry.trace_id
            or telemetry.step_seq != 0 or plan.is_fallback):
        raise GateError('Missing first-send ownership or unsupported Honcho call purpose')
    if scope.get('week_schedule') and unit.split(':')[2:3] != [scope['session']]:
        from .honcho_week_capture import native_batch

        if telemetry.call_purpose not in ('deriver.representation','summary.short','summary.long'):
            raise GateError('Unsupported stock Honcho Week worker inference purpose')
        if plan.attempt!=1 and (telemetry.call_purpose!='deriver.representation' or plan.attempt not in (2,3)):
            raise GateError('Only an exact stock chat representation can request a validated retry')
        expected,witness=native_batch(scope,native,telemetry,batch,unit,owner.aqs_id)
        if slot is not None and slot!=expected:
            raise GateError('Honcho chat worker job changed before dispatch')
        return expected,dict(**witness,native_attempt=plan.attempt)
    allowed_attempts = (1,2,3) if scope.get('week_schedule') else (1,)
    if not batch or telemetry.call_purpose!='deriver.representation' or plan.attempt not in allowed_attempts:
        raise GateError('Only the exact native source representation may send')
    index, files, q, release = _check_release(root, scope, native)
    expected = dict(provider='honcho', kind='ingest', at=scope['plan'][index-1]['at'], honcho_daily_release=release)
    latest = files[-1]
    messages, ctx = batch[0]
    qids, ids = [q['id']], [latest['local_id']]
    host = native['host']
    by_id = {f['local_id']:f for f in files}
    observed = {m.id:(m.public_id,m.session_name,m.peer_name,instant(m.created_at.isoformat()),
                      sha256(m.content.encode()).hexdigest()) for m in messages
                if m.id in by_id and m.workspace_name == host['workspace']}
    target = {key:(f['public_id'], f['session'], f['peer'], instant(f['at']), f['sha256'])
              for key,f in by_id.items() if key in observed}
    if (slot is not None and slot != expected or q['claim'] != owner.aqs_id or q['unit'] != unit
            or telemetry.workspace_name != host['workspace'] or telemetry.session_id != latest['session_id']
            or telemetry.queue_item_ids != qids or telemetry.source_message_ids != [latest['public_id']]
            or telemetry.observed != host['peerName'] or sorted(telemetry.observers) != sorted(q['observers'])
            or ctx.get('queue_item_ids') != qids or ctx.get('queue_item_message_ids') != ids
            or ctx.get('session_id') != latest['session_id'] or ctx.get('observed') != host['peerName']
            or sorted(ctx.get('observers') or ()) != sorted(q['observers'])
            or len(messages) != len(observed) or latest['local_id'] not in observed or observed != target):
        raise GateError('Honcho native claim, trigger or model batch differs from the sealed upload')
    witness = dict(queue_id=q['id'], claim=owner.aqs_id, trace=telemetry.trace_id)
    if scope.get('week_schedule'):
        witness['native_attempt'] = plan.attempt
    return expected, witness


def admission(scope, project, profile, body):
    from .openrouter_route import validate_request
    from .workflow_capability import ENV, PROFILE

    roles = scope['projects']['honcho']
    if chat_enabled(scope) and project == roles.get('driver') and profile == PROFILE:
        from .honcho_first_answer_admission import permit
        return permit(scope, project, body)
    if chat_enabled(scope) and project == roles.get('dialectic') and profile == PROFILE:
        from .honcho_first_answer_peer import permit
        return permit(scope, project, body)
    if project != roles['memory'] or profile != PROFILE:
        raise GateError('Only the exact Honcho daily representation role is enabled')
    validate_request(body, profile=PROFILE)
    slot, _ = _live(scope)
    if scope.get('week_schedule'):
        from .honcho_week_state import plan, position
        from .workflow_runner import inspect_source_cutoff

        docs,turns,_=plan()
        completed,pending=_progress(Path(scope['root']),scope)
        visible=[doc.filename for doc in docs[:len(completed)+int(pending is not None)]]
        inspect_source_cutoff({'model_requests':[body],'prefetch':None},docs,visible,
                              [turn.prompt for turn in turns[:position(scope)+1]])
    return dict(root=scope['root'], run_id=scope['run_id'], capability=json.loads(os.environ[ENV]),
                projects=[project], slot=slot)


def bind_attempt(row):
    from .direct_http import _native_http_request
    from .openrouter_route import CHAT_COMPLETIONS_URL
    from .workflow_capability import load

    request = _native_http_request.get()
    evidence = request.extensions.get(EXTENSION) if request is not None else None
    if not evidence:
        raise GateError('No native SDK request at the tracked physical transport')
    active, sdk_key, retry, bound = evidence
    if (not active or active is not _executor.get() or not isinstance(sdk_key, str) or not sdk_key or retry != 0
            or bound != _request_binding(request) or bound[:2] != ('POST', CHAT_COMPLETIONS_URL)):
        raise GateError('Native SDK evidence expired or the request is a retry')
    _, attempt = _live(load(), row['workflow_slot'])
    row['honcho_daily_attempt'] = dict(**attempt, sdk_key_sha256=sha256(sdk_key.encode()).hexdigest())


def confirm_sender(row):
    from .workflow_capability import load

    scope = load()
    _, now = _live(scope, row['workflow_slot'])
    if any(row['honcho_daily_attempt'][key] != value for key,value in now.items()):
        raise GateError('Honcho native claim changed after reservation')
    if scope.get('week_schedule'):
        from .honcho_week_retry import mark_physical_send

        mark_physical_send(row)


def worker(source):
    """Observe requests inside the pinned, unmodified stock deriver."""
    import runpy
    import subprocess
    from unittest.mock import patch
    import httpx
    import openai
    from .direct_http import _httpx_wrappers
    from .openrouter_route import PROFILES
    from .workflow_capability import PROFILE, budget, load

    source = Path(source).resolve(strict=True)
    git = ['git', '-C', str(source)]
    if (subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip() != REVISION
            or subprocess.run([*git,'diff','--quiet','HEAD','--','src','uv.lock']).returncode
            or openai.__version__ != '2.36.0' or httpx.AsyncClient.send not in _httpx_wrappers):
        raise GateError('Honcho source or accounted SDK transport differs from its pin')
    scope = load()
    if scope['admission'] != CONDITION:
        raise GateError('Honcho deriver is outside its daily document capability')
    budget()
    from src.config import settings
    from src.deriver import consumer, deriver, queue_manager
    from src.llm import api, executor as native_executor, tool_loop

    modules = (consumer,deriver,queue_manager,api,native_executor,tool_loop)
    if (any(Path(m.__file__).resolve() != source.joinpath(*m.__name__.split('.')).with_suffix('.py') for m in modules)
            or consumer.process_representation_tasks_batch is not deriver.process_representation_tasks_batch
            or not api.honcho_llm_call_inner is native_executor.honcho_llm_call_inner is tool_loop.honcho_llm_call_inner):
        raise GateError('Loaded native worker aliases differ from pinned Honcho')
    generation = (settings.DERIVER.MODEL_CONFIG, settings.SUMMARY.MODEL_CONFIG,
                  settings.DREAM.DEDUCTION_MODEL_CONFIG, settings.DREAM.INDUCTION_MODEL_CONFIG,
                  *(item.MODEL_CONFIG for item in settings.DIALECTIC.LEVELS.values()))
    for item in generation:
        if (item.transport != 'openai' or item.model != PROFILES[PROFILE]['model'] or item.fallback is not None
                or str(item.overrides.base_url).rstrip('/') != 'https://openrouter.ai/api/v1'
                or item.overrides.api_key_env != 'PACBENCH_MEMORY_API_KEY' or item.overrides.api_key is not None
                or item.overrides.provider_params):
            raise GateError('Unqualified native Honcho generation route')
    embed = settings.EMBEDDING.MODEL_CONFIG
    if (settings.DERIVER.SCHEDULER != 'deriver' or settings.EMBED_MESSAGES is not True
            or embed.transport != 'openai' or embed.model != 'nomic-embed-text'
            or str(embed.overrides.base_url).rstrip('/') != 'http://127.0.0.1:11435/v1'
            or embed.overrides.api_key_env != 'PACBENCH_MEMORY_API_KEY' or embed.overrides.api_key is not None
            or embed.overrides.provider_params):
        raise GateError('Unqualified native scheduler or local embedder')
    manager = queue_manager.QueueManager
    native_call=api.honcho_llm_call_inner
    if scope.get('week_schedule'):
        from .honcho_week_retry import watch

        native_call=watch(native_call)
    inner = observe(native_call, _executor, executor)
    with ExitStack() as stack:
        for module in (api, tool_loop):
            stack.enter_context(patch.object(module,'honcho_llm_call_inner',inner))
        stack.enter_context(patch.object(manager,'process_work_unit',observe(manager.process_work_unit,_work,work)))
        stack.enter_context(patch.object(consumer,'process_representation_tasks_batch',
                                       observe(consumer.process_representation_tasks_batch,_batch,batch)))
        stack.enter_context(patch.object(openai.AsyncOpenAI,'_build_request',observe_build(openai.AsyncOpenAI._build_request)))
        runpy.run_module('src.deriver',run_name='__main__')


if __name__ == '__main__':
    import sys
    from pacbench.honcho_daily_gate import worker as run_worker

    if len(sys.argv) != 3 or sys.argv[1] != 'src.deriver':
        raise SystemExit('usage: python -m pacbench.honcho_daily_gate src.deriver HONCHO_SOURCE')
    run_worker(sys.argv[2])
