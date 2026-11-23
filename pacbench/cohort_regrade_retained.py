"""Read retained Pizza Week inputs using the contracts that wrote them."""
from collections import defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
from io import StringIO
import json
from pathlib import Path

from .provider_versions import MEMCAL_WEEK_VERSION
from .workflow_capability import MEMCAL_SCOPE, MEMOS_OSS_ADMISSION, _memcal_schedule
from .workflow_document_progress import PROTOCOL as DOCUMENT_PROGRESS, PROVIDERS
from .workflow_daily_documents import LAYOUT as NORMALIZED_DAILY, compile_daily_week
from .workflow_memcal_tools import manifest as memcal_product, verify_captured_driver
from .workflow_runner import Condition, captured_turn, inspect_source_cutoff, judge_options
from .workflow_source_plan import SourcePlan
from .workflow_spec import Document, SPEC, Turn, compile_week, instant
from .workflow_stream import fingerprint

MEMCAL_PROGRESS = 'memcal-incremental-v1'
WEEK = 'pizza-week-conversation-v2'
THREAD_FILES = 'nightly-thread-files-v2'
CONNECTOR = 'native-connector-v2'
DAILY_FILES = 'seven-daily-files-v1'
DAILY_PROJECTIONS = frozenset((DAILY_FILES, NORMALIZED_DAILY))
HONCHO_WEEK = 'honcho-seven-daily-single-hermes-week-v1'
HONCHO_ADMISSION = 'honcho-seven-daily-documents-v1'
ADMISSIONS = {
    'memcal': MEMCAL_SCOPE,
    'mem0': 'mem0-native-oss-document-week-v1',
    'hindsight': 'hindsight-native-file-document-week-v1',
    'openviking': 'openviking-native-file-document-week-v1',
    'reme': 'reme-native-watched-resource-week-v1',
    'cognee': 'cognee-native-permanent-document-week-v1',
    'memos': MEMOS_OSS_ADMISSION,
    'supermemory': 'supermemory-stock-document-week-v1',
}


@dataclass
class RetainedArm:
    documents: list[Document]
    turns: list[Turn]
    condition: dict
    binding: dict
    source_provenance: dict
    answers: dict
    originals: dict
    execution_sealed: bool
    verified_pending_completion: list[str]


def read_bytes(path):
    path = Path(path).absolute()
    if path != path.resolve():
        raise ValueError(f'symlinked or noncanonical input path: {path}')
    raw = path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def read_json(path):
    raw, sha = read_bytes(path)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f'expected JSON object: {Path(path).name}')
    return value, sha


def journal(path):
    raw, sha = read_bytes(path)
    # The native journals write an LF for each complete event. Never consume a
    # torn final append, even if it happens to parse as JSON.
    lines = raw.split(b'\n')
    events = [json.loads(line) for line in lines[:-1]]
    if any(not isinstance(event, dict) or not isinstance(event.get('phase'), str) for event in events):
        raise ValueError('malformed original workflow journal event')
    return events, sha, bool(lines[-1])


@lru_cache(maxsize=4)
def _plan(spec_sha, state, canary, channels):
    raw, actual_sha = read_bytes(SPEC)
    if spec_sha != actual_sha:
        raise ValueError('compiled spec SHA-256 differs from the retained run; use its original reviewed tree')
    spec, documents, turns, basis = compile_week(state)
    if spec['version'] != WEEK:
        raise ValueError('unsupported compiled Week revision')
    if canary:
        if state != 'continuing-account':
            raise ValueError('canary is not a continuing-account schedule')
        turns = turns[:4]
        documents = [d for d in documents if instant(d.cutoff) <= instant(turns[-1].at)]
    documents = [d for d in documents if d.channel in channels]
    if set(channels) != {'email', 'imessage'}:
        turns = []
    dataset = SPEC.parents[1] / json.loads(raw)['dataset'] / 'manifest.json'
    dataset_manifest, dataset_sha = read_json(dataset)
    return documents, turns, basis, dataset_manifest['dataset_sha256'], dataset_sha


def _daily_documents(documents, dataset_manifest, manifest_sha, events):
    """Verify the seven original daily blobs against the captured submit rows."""
    manifest, actual_sha = read_json(dataset_manifest)
    if actual_sha != manifest_sha:
        raise ValueError('frozen daily dataset manifest changed')
    batches = manifest['source_batches']
    submissions = [e for e in events if e['phase'] == 'submit']
    if len(submissions) != len(batches) or len(batches) != 7:
        raise ValueError('seven-daily-files requires all seven original submit witnesses')
    nightly = {doc.cutoff[:10]: doc.cutoff for doc in documents}
    observed = [instant(row['at']) for row in submissions]
    if observed == [instant(nightly[row['id']]) for row in batches]:
        clock = 'v2-nightly-cutoff'
    elif observed == [instant(row['at']) for row in batches]:
        clock = 'r2-original-batch-cutoff'
    else:
        raise ValueError('seven-daily-files release clock differs from the frozen schedule')

    frozen = []
    seen = set()
    for row, batch in zip(submissions, batches, strict=True):
        raw, sha = read_bytes(dataset_manifest.parent / batch['source_file'])
        name = row['filename']
        if (not isinstance(name, str) or not name or name in seen or name.startswith('/')
                or '..' in Path(name).parts or sha != batch['document_sha256']
                or row['sha256'] != sha or row['source_ids'] != batch['source_ids']
                or row['content'].encode('utf-8') != raw):
            raise ValueError('seven-daily-files payload differs from a frozen daily source')
        seen.add(name)
        frozen.append(Document(name, row['at'], 'mixed', raw.decode('utf-8'), tuple(batch['source_ids'])))
    return frozen, clock


def week_plan(spec_sha, source_basis, condition, canary, *, projection=None, events=()):
    if type(canary) is not bool:
        raise ValueError('missing retained canary/full condition')
    channels = tuple(condition['channels'])
    if not channels or len(set(channels)) != len(channels) or not set(channels) <= {'email', 'imessage'}:
        raise ValueError('invalid original ingestion channels')
    track = condition['track']
    if track not in ('nightly-document', 'first-party-connector'):
        raise ValueError('unsupported original ingestion track')
    documents, turns, basis, dataset, manifest_sha = _plan(spec_sha, condition['state'], canary, channels)
    if source_basis != basis:
        raise ValueError('source manifest binding differs from the compiled Week')
    default_projection = CONNECTOR if track == 'first-party-connector' else THREAD_FILES
    projection = projection or default_projection
    clock = 'v2-nightly-cutoff'
    if projection in DAILY_PROJECTIONS:
        if track != 'nightly-document' or canary or not turns or condition['state'] != 'continuing-account':
            raise ValueError('seven-daily-files requires a complete continuing document condition')
        spec, _ = read_json(SPEC)
        dataset_manifest = SPEC.parents[1] / spec['dataset'] / 'manifest.json'
        if projection == NORMALIZED_DAILY:
            _, documents, daily_turns, daily_basis, _ = compile_daily_week()
            if (daily_turns, daily_basis) != (turns, basis):
                raise ValueError('normalized daily projection changed the frozen questions')
        else:
            documents, clock = _daily_documents(documents, dataset_manifest, manifest_sha, events)
    elif projection != default_projection:
        raise ValueError('input projection differs from its original ingestion track')
    return documents, turns, {
        'spec_sha256': spec_sha,
        'source_basis': basis,
        'dataset_sha256': dataset,
        'dataset_manifest_sha256': manifest_sha,
        'ordered_questions_sha256': fingerprint([asdict(turn) for turn in turns]),
        'input_projection': projection,
        'release_clock': clock,
        'document_schedule_sha256': fingerprint(
            [{'filename': d.filename, 'cutoff': d.cutoff, 'sha256': d.sha256, 'source_ids': d.source_ids}
             for d in documents]),
        'canary': canary,
    }


def select_projection(expected, *recorded):
    values = {value for value in (expected, *recorded) if value is not None}
    if len(values) > 1:
        raise ValueError('conflicting retained input projection')
    return values.pop() if values else None


def verify_scope(progress, identity, directory, projection, supplied=None, expected_admission=None):
    """Verify the original scope bytes without opening any prepaid or credential references."""
    provider = identity['condition']['provider']
    expected = directory.parent/'workflow-scope.json'
    bound = progress.binding('workflow_scope')
    if bound is None:
        if supplied is not None or expected_admission is not None or (directory.name == 'workflow' and expected.exists()):
            raise ValueError('workflow scope exists without its original run binding')
        return None, None
    if directory.name != 'workflow' or bound.get('path') != str(expected):
        raise ValueError('foreign workflow scope binding')
    scope, sha = read_json(expected)
    if sha != bound.get('sha256') or (supplied is not None and sha != supplied):
        raise ValueError('workflow-scope.json SHA-256 differs')
    admission = scope.get('admission')
    if (scope.get('root') != str(directory.parent) or scope.get('provider') != provider
            or scope.get('run_id') != directory.parent.parent.name or scope.get('protocol') != WEEK
            or not isinstance(admission, str) or not admission
            or (projection != DAILY_FILES and admission != ADMISSIONS.get(provider))
            or (expected_admission is not None and admission != expected_admission)
            or scope.get('canary') is not identity['canary']
            or scope.get('spec_sha256') != identity['spec_sha256']
            or scope.get('frozen_inputs') != identity['source_basis']):
        raise ValueError('original workflow scope, ingestion protocol or source manifest differs')
    select_projection(projection, scope.get('input_projection'))
    if projection == NORMALIZED_DAILY and (provider != 'supermemory'
            or scope.get('contract') != compile_daily_week()[4]):
        raise ValueError('original Supermemory scope lacks the canonical daily document contract')
    if provider == 'memcal':
        contract, plan_sha, *_ = _memcal_schedule(identity['spec_sha256'], identity['canary'])
        if (scope.get('account') != identity.get('account') or scope.get('store') != identity.get('store')
                or scope.get('plan_sha256') != plan_sha or scope.get('contract') != contract):
            raise ValueError('original MemCal scope or native store differs')
        if 'product_manifest' in identity and any(
                scope.get('access', {}).get(k) != v for k, v in identity['product_manifest'].items()):
            raise ValueError('original MemCal scope product manifest differs')
    elif identity.get('account') != scope['root']:
        raise ValueError('document workflow account differs from its scope')
    return scope, sha


def verify_progress(progress, identity, saved_turns, directory, scope_sha=None, *,
                    projection=None, events=(), expected_admission=None):
    """Check persisted order and source contract against the reviewed compiler."""
    condition = identity['condition']
    provider = condition['provider']
    protocol = identity.get('protocol')
    projection = select_projection(projection, identity.get('input_projection'),
                                   events[0].get('input_projection') if events else None)
    if protocol == DOCUMENT_PROGRESS:
        allowed = PROVIDERS | {'honcho', 'supermemory'} if projection in DAILY_PROJECTIONS else PROVIDERS
        if provider not in allowed or condition['track'] != 'nightly-document':
            raise ValueError('document progress has the wrong provider or ingestion track')
    elif protocol == MEMCAL_PROGRESS:
        if provider != 'memcal' or condition['track'] != 'first-party-connector':
            raise ValueError('MemCal progress is not its original connector condition')
    else:
        raise ValueError('unsupported retained Pizza Week progress protocol')
    if condition['state'] != 'continuing-account':
        raise ValueError('incremental progress requires a continuing account')
    documents, turns, binding = week_plan(identity['spec_sha256'], identity['source_basis'],
                                          condition, identity['canary'], projection=projection, events=events)
    if saved_turns != turns:
        raise ValueError('saved turn order, question, time or criteria differ from their frozen schedule')
    if progress.binding('evidence_kind') != 'native' or progress.binding('canary') is not identity['canary']:
        raise ValueError('progress evidence kind or canary binding differs')
    scope, bound_sha = verify_scope(progress, identity, directory, binding['input_projection'],
                                    scope_sha, expected_admission)
    binding.update(protocol=protocol, scope_sha256=bound_sha,
                   ingestion_protocol=scope['admission'] if scope else None)
    if protocol == DOCUMENT_PROGRESS:
        if identity.get('schedule_sha256') != binding['ordered_questions_sha256']:
            raise ValueError('document progress schedule fingerprint differs')
        return documents, binding
    _, expected_plan, *_ = _memcal_schedule(identity['spec_sha256'], identity['canary'])
    if identity.get('plan_sha256') != expected_plan:
        raise ValueError('MemCal source-plan SHA-256 differs')
    stored = SourcePlan.from_connection(progress.db, expected_plan)
    if ([turn for _, turn, _ in stored.iter_turns()] != turns
            or [doc for _, batch in stored.iter_nights() for doc in batch] != documents):
        raise ValueError('MemCal source index differs from its frozen documents and question order')
    binding['source_plan_sha256'] = expected_plan
    if 'product_manifest' in identity:
        if condition['version'] != MEMCAL_WEEK_VERSION or identity['product_manifest'] != memcal_product() or scope is None:
            raise ValueError('qualified MemCal product manifest differs or lacks its scope')
        binding['memcal_product'] = identity['product_manifest']
    return documents, binding


def verify_journal_header(events, turns, condition, spec_sha, basis, canary):
    if len(events) < 2 or [r['phase'] for r in events[:2]] != ['spec', 'condition']:
        raise ValueError('workflow.jsonl lacks its original spec and condition events')
    spec, deployment = events[:2]
    if (spec.get('sha256') != spec_sha or spec.get('source_basis') != basis
            or spec.get('canary') is not canary or deployment.get('condition') != condition
            or deployment.get('evidence_kind') != 'native'):
        raise ValueError('journal source manifest or native condition differs')
    if 'schedule' in spec:
        expected = [dict(id=t.id, at=t.at, chat=t.session, measure=t.measure) for t in turns]
        if spec['schedule'] != expected:
            raise ValueError('journal ordered question schedule differs')
    elif (spec.get('planned_turns'), spec.get('planned_criteria')) != (len(turns), sum(len(t.claims) for t in turns)):
        raise ValueError('journal planned turn or criterion denominator differs')


def document_readbacks(events, documents, turns, *, through=None, complete=False):
    """Bind native acceptance/readback to the original submitted document."""
    submitted, accepted, ready, intake_failures = set(), {}, {}, []
    planned = {doc.filename: doc for doc in documents}
    questions = {turn.id: turn for turn in turns}
    pending = None
    for event in events:
        phase = event['phase']
        if phase == 'submit':
            name = event['filename']
            doc = planned.get(name)
            if (pending is not None or doc is None or name in submitted or event['sha256'] != doc.sha256
                    or event['source_ids'] != list(doc.source_ids) or event['content'] != doc.text
                    or instant(event['at']) != instant(doc.cutoff)):
                raise ValueError('submitted document differs from the original source manifest')
            submitted.add(name)
            pending = doc
        elif phase == 'acceptance':
            proof = event['evidence']
            native_id = proof.get('native_id')
            if pending is None:
                raise ValueError('unbound document acceptance')
            if proof.get('accepted') is not True:
                intake_failures.append(pending.filename)
            elif (not native_id or native_id in accepted or not proof.get('request')
                  or instant(proof['at']) < instant(pending.cutoff)):
                raise ValueError('original document acceptance is unbound, stale or duplicated')
            else:
                accepted[native_id] = pending, proof
            pending = None
        elif phase == 'readback':
            proof = event['evidence']
            submission = accepted.get(proof['native_id'])
            if submission is None:
                raise ValueError('unbound document readback')
            doc, acceptance = submission
            if (doc.filename in ready or doc.filename in intake_failures
                    or instant(proof['at']) < instant(acceptance['at'])):
                raise ValueError('original document readback is stale or duplicated')
            if proof.get('ready') is not True:
                intake_failures.append(doc.filename)
            elif proof.get('submitted_sha256') != doc.sha256 or not proof.get('readback'):
                raise ValueError('original document readback differs from its submitted content')
            else:
                ready[doc.filename] = proof
        elif phase == 'dispatch':
            turn = questions.get(event['question'])
            if (turn is None or event['prompt'] != turn.prompt or event['at'] != turn.at
                    or any(d.filename not in ready or instant(ready[d.filename]['at']) > instant(turn.at)
                           for d in documents if instant(d.cutoff) <= instant(turn.at))):
                raise ValueError('document intake or dispatched question differs at its original cutoff')
    if (complete and set(ready) != set(planned)) or (
            through and any(d.filename not in ready for d in documents if instant(d.cutoff) <= instant(through))):
        raise ValueError('missing original document readbacks through retained execution')
    return {'kind': 'native-document-readback', 'ready_documents': len(ready),
            'ready_releases': sum(all(d.filename in ready for d in documents if d.cutoff == at)
                                  for at in {d.cutoff for d in documents}),
            'evidence_sha256': fingerprint(ready), 'intake_failures': intake_failures}


def _collection(cutoff, docs, accepted, ready, ids_seen, *, require_archive=False):
    by_channel = {channel: sorted(sid for d in docs if d.channel == channel for sid in d.source_ids)
                  for channel in ('email', 'imessage')}
    expected = sorted(sid for ids in by_channel.values() for sid in ids)
    native_id = accepted.get('native_id')
    if (not native_id or native_id in ids_seen or accepted.get('accepted') is not True
            or ready.get('native_id') != native_id or ready.get('ready') is not True
            or not ready.get('readback') or sorted(ready.get('new_source_ids', [])) != expected
            or not ready.get('source_cursor')
            or not instant(cutoff) <= instant(accepted['at']) <= instant(ready['at'])):
        raise ValueError('original MemCal collection/readback is unbound, duplicated or incomplete')
    channels = ready.get('channel_readback', {})
    if set(channels) != set(by_channel) or any(
            not row.get('cursor') or instant(row['as_of']) != instant(cutoff)
            or sorted(row['new_source_ids']) != by_channel[channel] for channel, row in channels.items()):
        raise ValueError('original MemCal channel cursors or source IDs differ at collection cutoff')
    if require_archive:
        request = accepted.get('request', {})
        if (request.get('sync_through') != cutoff or set(request.get('channels', [])) != set(by_channel)
                or any(row['cursor'] != ready['source_cursor'].get(channel) for channel, row in channels.items())):
            raise ValueError('original MemCal request cutoff or native cursor differs')
        rows = accepted.get('rows', {})
        source_hashes = {sid: hashlib.sha256(body.encode()).hexdigest()
                         for doc in docs for sid, body in zip(doc.source_ids, doc.bodies, strict=True)}
        if set(rows) != set(source_hashes) or any(
                rows[sid].get('source_sha256') != sha or not rows[sid].get('archive_sha256')
                for sid, sha in source_hashes.items()):
            raise ValueError('original MemCal archive row receipts differ from the source manifest')
    ids_seen.add(native_id)
    return {'cutoff': cutoff, 'native_collection_id': native_id,
            'source_records': len(expected), 'ready_at': ready['at'],
            'witness_sha256': fingerprint([accepted, ready])}


def memcal_readbacks(progress, documents):
    """Use recorded connector effects and Dream observations instead of a file denominator."""
    batches = defaultdict(list)
    for doc in documents:
        batches[doc.cutoff].append(doc)
    latest = progress.last_cutoff()
    if latest and latest not in batches:
        raise ValueError('MemCal collection cursor lies outside the source manifest')
    if progress.execution_sealed and latest != max(batches, key=instant, default=None):
        raise ValueError('sealed MemCal run lacks its full original collection boundary')
    ids_seen, result = set(), []
    source_map = (progress.binding('memcal_activation') or {}).get('source_map')
    for cutoff, docs in batches.items():
        if latest is None or instant(cutoff) > instant(latest):
            break
        accepted = progress.get_effect('sync', cutoff) or {}
        clock = progress.get_effect('source-clock', cutoff) or {}
        dream = progress.get_effect('dream', cutoff) or {}
        ready = progress.observation('dream', cutoff, 'native-ready') or {}
        if (clock.get('cutoff') != cutoff or instant(clock['as_of']) != instant(cutoff)
                or ready.get('readback') != dream or not dream.get('finished_at')
                or dream.get('pending_spool') != 0 or ready.get('source_cursor') != accepted.get('native_cursors')
                or not clock.get('source_map_sha256')
                or clock['source_map_sha256'] != accepted.get('source_map_sha256')
                or source_map and clock['source_map_sha256'] != source_map
                or not clock.get('observation_sha256')
                or clock['observation_sha256'] != accepted.get('source_observation_sha256')):
            raise ValueError('original MemCal clock, archive mapping or Dream readback differs')
        result.append(_collection(cutoff, docs, accepted, ready, ids_seen, require_archive=True))
    return {'kind': 'native-connector-collection', 'ready_documents': None, 'ready_releases': len(result),
            'collections': result, 'last_completed_cutoff': latest}


def verify_surface(turn, surface, prior, documents, *, qualified_memcal=False):
    history = surface['history']
    previous = prior['history'] if prior else []
    if (not surface.get('session_id') or instant(surface['question_at']) != instant(turn.at)
            or history[:len(previous)] != previous
            or (prior and (surface['session_id'], surface['gateway_identity'])
                           != (prior['session_id'], prior['gateway_identity']))):
        raise ValueError('saved question clock or native conversation prefix differs')
    delta = history[len(previous):]
    if (len(delta) < 2 or delta[0]['role'] != 'user' or delta[0]['content'] != turn.prompt
            or delta[-1]['role'] != 'assistant' or delta[-1]['content'] != surface['answer']
            or not isinstance(surface['answer'], str) or not surface['answer'].strip()
            or any(r['role'] not in ('assistant', 'tool') for r in delta[1:])):
        raise ValueError('saved final question, answer or history differs')
    times = [*(prior['conversation_times'] if prior else ()), turn.at]
    if surface['conversation_times'] != times:
        raise ValueError('saved conversation times differ from the original question order')
    prefix = [d for d in documents if instant(d.cutoff) <= instant(turn.at)]
    if surface.get('source_files') != [d.filename for d in prefix]:
        raise ValueError('saved source inventory differs at the question cutoff')
    if qualified_memcal:
        requests = surface.get('model_requests')
        if not requests:
            raise ValueError('qualified MemCal lacks captured driver requests')
        for request in requests:
            verify_captured_driver(request)
    return judge_options(turn, surface), prefix[-1].cutoff if prefix else None


def bound_json(path, proof):
    value, sha = read_json(path)
    if proof != {'path': str(path), 'sha256': sha}:
        raise ValueError('retained Honcho file differs from its original binding')
    return value


def _honcho_releases(root, scope, documents, rows):
    """Check the exact daily file, stock upload record, and settled completion."""
    ready_bindings, native_ids, queue_ids = [], set(), set()
    for index, (doc, entry) in enumerate(zip(documents, scope['plan'], strict=True), 1):
        path = root/'frozen-documents'/doc.filename
        cutoff = dict(at=doc.cutoff, path=str(path), sha256=doc.sha256, source_ids=list(doc.source_ids))
        raw, sha = read_bytes(path)
        intent, _ = read_json(root/f'honcho-daily-{index}-intent.json')
        if entry != cutoff or intent != cutoff or sha != doc.sha256 or raw != doc.text.encode():
            raise ValueError('Honcho release file or chronology differs from the frozen daily source')
        ready_file = root/f'honcho-daily-{index}-ready.json'
        ready, ready_sha = read_json(ready_file)
        sealed = bound_json(root/f'honcho-daily-{index}-sealed.json', ready['release'])
        source = sealed['file']
        attempt = rows[ready['attempt']]
        slot = attempt['workflow_slot']
        receipt = attempt.get('billing_receipt') or {}
        receipt_path = root/'billing-receipts'/Path(receipt.get('relative_path', '')).name
        proof, receipt_sha = read_json(receipt_path)
        if (sealed['at'] != doc.cutoff or source['sha256'] != doc.sha256
                or source['file']['filename'] != doc.filename or source['meta'] != {'filename': doc.filename}
                or source['session'] != scope['session']
                or source['public_id'] in native_ids or sealed['queue_id'] in queue_ids
                or slot.get('honcho_daily_release') != ready['release'] or slot.get('at') != doc.cutoff
                or slot.get('kind') != 'ingest' or attempt['state'] != 'settled' or attempt['http_status'] != 200
                or receipt.get('relative_path') != str(receipt_path.relative_to(root))
                or receipt_sha != receipt.get('sha256') or proof.get('attempt_id') != attempt['id']
                or proof.get('generation_id') != attempt.get('upstream_request_id')
                or 'cost' not in proof.get('usage', {})):
            raise ValueError('Honcho native readiness is outside the original file or settled ingest attempt')
        native_ids.add(source['public_id'])
        queue_ids.add(sealed['queue_id'])
        ready_bindings.append({'path': str(ready_file), 'sha256': ready_sha})
    return ready_bindings


def honcho_checkpoint(root, scope, scope_sha, expected_admission, projection):
    """Read the native Honcho Week seal without opening its service or provider state."""
    from .observational_receipts import replay_attempts
    from .openrouter_route import validate_request
    from .workflow_tracks import HERMES_TOOLS

    if (scope['root'] != str(root) or scope['run_id'] != root.parent.name
            or scope['provider'] != 'honcho' or scope['protocol'] != WEEK
            or scope['admission'] != HONCHO_ADMISSION or expected_admission not in (None, HONCHO_ADMISSION)
            or projection not in (None, NORMALIZED_DAILY)):
        raise ValueError('foreign or unsupported Honcho Week scope')
    _, documents, turns, basis, canonical = compile_daily_week()
    spec_sha = read_bytes(SPEC)[1]
    if scope['spec_sha256'] != spec_sha:
        raise ValueError('Honcho scope uses a different frozen workflow')
    manifest = bound_json(root/'frozen-documents/manifest.json', scope['manifest'])
    planned = bound_json(root/'ordered-questions.json', scope['week_schedule'])
    schedule = [dict(index=i, id=turn.id, at=turn.at, authored_session=turn.session, measure=turn.measure,
                     after=turn.after, prompt_sha256=hashlib.sha256(turn.prompt.encode()).hexdigest(),
                     ready_files=[doc.filename for doc in documents if instant(doc.cutoff) <= instant(turn.at)])
                for i, turn in enumerate(turns, 1)]
    expected = dict(protocol=HONCHO_WEEK, session='one continuing Hermes session', corpus=basis,
                    document_index_sha256=canonical['normalized_index_sha256'],
                    gradable_turns=sum(bool(turn.claims) for turn in turns),
                    criteria=sum(len(turn.claims) for turn in turns), turns=schedule)
    result, result_sha = read_json(root/'result.json')
    if (manifest != canonical or planned != expected or result['protocol'] != HONCHO_WEEK
            or result['state'] != 'stock-week-answers-complete' or result['cleanup'] != 'owned-services-stopped'
            or result['ordered_schedule_sha256'] != scope['week_schedule']['sha256']
            or result['canonical_manifest_sha256'] != scope['manifest']['sha256']
            or result['document_index_sha256'] != canonical['normalized_index_sha256']
            or (result['documents_verified'], result['source_records'], result['answers_received'],
                result['scheduled_turns'], result['scheduled_criteria'], result['last_question'])
            != (7, 142, len(turns), len(turns), expected['criteria'], turns[-1].id)):
        raise ValueError('Honcho completion differs from its frozen source and ordered questions')
    raw, receipts_sha = read_bytes(root/'receipts.jsonl')
    if not raw.endswith(b'\n'):
        raise ValueError('Honcho receipt journal is incomplete')
    rows = replay_attempts(StringIO(raw.decode()))
    ready = _honcho_releases(root, scope, documents, rows)
    startup, startup_sha = read_json(root/'startup.json')
    if startup['controller'] != scope['controller'] or startup['source_ready'] != result['source_ready']:
        raise ValueError('Honcho gateway startup does not match the owned run and source')
    pins = startup['pins']
    condition = Condition('honcho', 'nightly-document', 'continuing-account', 'Hermes ' + pins['hermes'],
                          'openai/gpt-6-luna', 'amazon-bedrock/global', HONCHO_WEEK, '3.6.1 / SDK 2.9.0',
                          pins['hermes_isolation_sha256'], 'Physical Honcho server, bounded authored Hermes checkpoints',
                          HERMES_TOOLS['honcho'])
    answers, ids, turn_bindings = {}, set(), []
    prior = None
    folder = root/'week-turns'
    sealed = sorted(folder.glob('*-verified.json'))
    if sealed != [folder/f'{i:02}-verified.json' for i in range(1, len(turns)+1)]:
        raise ValueError('Honcho sealed turn inventory differs from the full ordered Week')
    for path, turn, listed in zip(sealed, turns, schedule, strict=True):
        index = listed['index']
        verified, verified_sha = read_json(path)
        intent = bound_json(folder/f'{index:02}-intent.json', verified['intent'])
        surface = bound_json(folder/f'{index:02}-surface.private.json', verified['surface'])
        prefix = listed['ready_files']
        if (verified['index'] != index or verified['question'] != turn.id
                or intent['index'] != index-1 or intent['question'] != turn.id or intent['ready'] != ready[:len(prefix)]
                or intent['prior'] != (turn_bindings[-1] if turn_bindings else None)
                or intent['controller'] != scope['controller'] or intent['gateway']['pid'] != startup['gateway']
                or intent['session_id'] != (prior['session_id'] if prior else None)):
            raise ValueError('Honcho turn seal lost its chronological, release or session binding')
        captured_turn(surface, turn, prior, condition, prefix)
        requests = surface['model_requests']
        turn_ids = {request['request_id'] for request in requests}
        if len(turn_ids) != len(requests) or ids.intersection(turn_ids):
            raise ValueError('Honcho captured driver request was reused')
        for request in requests:
            validate_request(request['request'], profile='luna-bedrock-global')
            row = rows[request['request_id']]
            slot = row['workflow_slot']
            if (row['state'] != 'settled' or row['http_status'] != 200 or
                    slot.get('kind') != 'answer' or slot.get('question_id') != turn.id):
                raise ValueError('Honcho captured request differs from its settled driver operation')
        ids.update(turn_ids)
        inspect_source_cutoff({'model_requests': [r['request'] for r in requests], 'prefetch': surface['prefetch']},
                              documents, prefix, [t.prompt for t in turns[:index]])
        surface = {**surface, 'conversation_times': [t.at for t in turns[:index]]}
        turn_bindings.append({'path': str(path), 'sha256': verified_sha})
        answers[turn.id] = (surface, {'retained_turn_sha256': verified_sha})
        prior = surface
    close, close_sha = read_json(root/'native-chat-close.json')
    if close['native_failures'] or result['driver_requests'] != len(ids):
        raise ValueError('Honcho shutdown or driver request count differs from its original completion')
    condition = json.loads(json.dumps(asdict(condition)))
    _, _, binding = week_plan(spec_sha, basis, condition, False, projection=NORMALIZED_DAILY)
    binding.update(protocol=HONCHO_WEEK, ingestion_protocol=HONCHO_ADMISSION, scope_sha256=scope_sha,
                   result_sha256=result_sha, ordered_native_schedule_sha256=scope['week_schedule']['sha256'],
                   wire_receipts_sha256=receipts_sha, startup_sha256=startup_sha, close_sha256=close_sha,
                   single_native_session=True, run_key=fingerprint([scope_sha, result_sha]))
    provenance = dict(kind='native-honcho-daily-file-readback', ready_documents=len(ready),
                      ready_releases=len(ready), readiness_sha256=fingerprint(ready))
    return RetainedArm(documents, turns, condition, binding, provenance, answers, {}, True, [])


def legacy_journal(events, document_sha, torn, *, projection=None, expected_admission=None):
    """Read the non-SQLite writer format, preserving its condition and verdicts."""
    if len(events) < 2 or [r['phase'] for r in events[:2]] != ['spec', 'condition']:
        raise ValueError('workflow.jsonl lacks its original spec and condition events')
    spec, deployment = events[:2]
    condition = deployment['condition']
    if spec.get('version') != WEEK or not isinstance(spec.get('schedule'), list):
        raise ValueError('unsupported original workflow journal format')
    projection = select_projection(projection, spec.get('input_projection'))
    documents, turns, binding = week_plan(spec['sha256'], spec['source_basis'], condition, spec['canary'],
                                          projection=projection, events=events)
    if expected_admission is not None and deployment.get('admission') != expected_admission:
        raise ValueError('legacy journal ingestion protocol differs')
    verify_journal_header(events, turns, condition, spec['sha256'], spec['source_basis'], spec['canary'])
    saved = {}
    originals = {}
    pending = None
    times = {}
    for event in events[2:]:
        phase = event['phase']
        if phase == 'answer-visible':
            if pending is not None:
                raise ValueError('legacy journal has duplicate or unmatched answers')
            pending = event
        elif phase == 'verified-turn':
            if (pending is None or len(saved) >= len(turns)
                    or turns[len(saved)].id != event['question'] or pending['question'] != event['question']):
                raise ValueError('legacy verified answer differs from the frozen order')
            surface = dict(pending['surface'])
            sid = surface['session_id']
            times[sid] = [*times.get(sid, []), turns[len(saved)].at]
            if 'conversation_times' in surface and surface['conversation_times'] != times[sid]:
                raise ValueError('legacy conversation clock differs')
            surface['conversation_times'] = times[sid]
            saved[event['question']] = (surface, event['activity'])
            pending = None
        elif phase in ('grade', 'ungraded'):
            question = event['question']
            if question not in saved or question in originals:
                raise ValueError('legacy journal has a duplicate or unbound original grade')
            originals[question] = {
                'grade_state': 'graded' if phase == 'grade' else 'failed',
                'criteria': event.get('criteria'),
                'failure': event.get('reason'),
                'judge_protocol': event.get('judge'),
            }
    complete = events[-1]['phase'] == 'completion' and not torn
    binding.update(protocol='legacy-workflow-journal-v2', journal_sha256=document_sha,
                   journal_completed=complete, journal_torn_tail=torn,
                   ingestion_protocol=deployment.get('admission'), scope_sha256=None)
    if complete and (pending or len(saved) != len(turns)):
        raise ValueError('legacy completion differs from retained answers')
    if condition['track'] == 'nightly-document':
        latest = turns[len(saved)-1].at if saved else None
        provenance = document_readbacks(events, documents, turns, through=latest, complete=complete)
    else:
        provenance = legacy_connector_readbacks(events, documents, condition, complete=complete)
    return RetainedArm(documents, turns, condition, binding, provenance, saved, originals, False, [])


def legacy_connector_readbacks(events, documents, condition, *, complete):
    batches = defaultdict(list)
    for doc in documents:
        batches[doc.cutoff].append(doc)
    expected_order = list(batches)
    releases, ids_seen = [], set()
    pending, accepted = None, None
    for event in events:
        if event['phase'] == 'sync':
            if (pending is not None or len(releases) >= len(expected_order)
                    or event['cutoff'] != expected_order[len(releases)]
                    or event['channels'] != condition['channels']):
                raise ValueError('legacy sync differs from its original ordered release')
            pending = event['cutoff']
        elif event['phase'] == 'acceptance':
            if pending is None or accepted is not None:
                raise ValueError('unbound legacy connector acceptance')
            accepted = event['evidence']
        elif event['phase'] == 'readback':
            if pending is None or accepted is None:
                raise ValueError('unbound legacy connector readback')
            ready = event['evidence']
            if condition['provider'] == 'memcal' and set(condition['channels']) == {'email', 'imessage'}:
                releases.append(_collection(pending, batches[pending], accepted, ready, ids_seen))
            else:
                expected = sorted(sid for doc in batches[pending] for sid in doc.source_ids)
                ident = accepted['native_id']
                if (not ident or ident in ids_seen or accepted.get('accepted') is not True
                        or ready.get('native_id') != ident or ready.get('ready') is not True
                        or not ready.get('readback') or sorted(ready.get('new_source_ids', [])) != expected):
                    raise ValueError('unbound legacy connector release evidence')
                ids_seen.add(ident)
                releases.append({'cutoff': pending, 'native_collection_id': ident,
                                 'source_records': len(expected), 'ready_at': ready['at'],
                                 'witness_sha256': fingerprint([accepted, ready])})
            pending, accepted = None, None
    if complete and len(releases) != len(batches):
        raise ValueError('completed legacy journal lacks original connector readbacks')
    return {'kind': 'legacy-native-connector-collection', 'ready_documents': None,
            'ready_releases': len(releases), 'collections': releases,
            'last_completed_cutoff': releases[-1]['cutoff'] if releases else None}
