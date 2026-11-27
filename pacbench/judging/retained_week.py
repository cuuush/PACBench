"""Read retained Pizza Week inputs using the contracts that wrote them."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from socket import create_connection
from urllib.parse import urlsplit

import pacbench.evidence_store as evidence_store
from pacbench.corpus.r2 import LAYOUT as NORMALIZED_DAILY
from pacbench.corpus.r2 import SPEC, compile_daily_week, compile_pizza_week
from pacbench.evidence_store import file_binding as binding
from pacbench.evidence_store import file_sha as sha
from pacbench.evidence_store import read_bound_bytes as read_bytes
from pacbench.evidence_store import read_bound_json as read_json
from pacbench.hermes_gateway import GatewayLifecycleError, _live
from pacbench.judging import retained_scope as full_cap
from pacbench.judging.retained_scope import (
    DOCUMENT_ARMS,
    MEMCAL_SCOPE,
    PROFILE,
    _memcal_schedule,
)
from pacbench.judging.retained_scope import read_canary_binding as document_canary_read
from pacbench.judging.retained_scope import retained_schedule as schedule
from pacbench.openrouter_route import PROFILES
from pacbench.provider_clock import history
from pacbench.provider_versions import MEMCAL_WEEK_VERSION
from pacbench.providers import validate_native_deployment
from pacbench.providers.honcho_evidence import (
    _honcho_answer_response,
    _honcho_releases,
    bound_json,
)
from pacbench.providers.memcal.source import manifest as memcal_product
from pacbench.providers.memcal.source import verify_captured_driver
from pacbench.receipt_costs import read_attempts
from pacbench.workflows.progress import DOCUMENT_PROGRESS_PROTOCOL as DOCUMENT_PROGRESS
from pacbench.workflows.progress import DOCUMENT_PROVIDERS as PROVIDERS
from pacbench.workflows.progress import workflow_fingerprint as fingerprint
from pacbench.workflows.recording import receipt_summary, summarize
from pacbench.workflows.runner import Condition, judge_options
from pacbench.workflows.source_plan import SourcePlan
from pacbench.workflows.spec import Document, Turn, instant
from pacbench.workflows.turn_evidence import (
    audit_transport,
    captured_turn,
    inspect_source_cutoff,
    verify_checkpoint,
)

MEMCAL_PROGRESS = 'memcal-incremental-v1'
WEEK = 'pizza-week-conversation-v2'
THREAD_FILES = 'nightly-thread-files-v2'
CONNECTOR = 'native-connector-v2'
DAILY_FILES = 'seven-daily-files-v1'
DAILY_PROJECTIONS = frozenset((DAILY_FILES, NORMALIZED_DAILY))
HONCHO_WEEK = 'honcho-seven-daily-single-hermes-week-v1'
HONCHO_ADMISSION = 'honcho-seven-daily-documents-v1'
SUPERMEMORY_ADMISSION = 'supermemory-stock-document-week-v1'
ADMISSIONS = {
    'memcal': MEMCAL_SCOPE,
    **{provider: arm.admission for provider, arm in DOCUMENT_ARMS.items()},
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


def journal(path):
    raw, sha = read_bytes(path)
    # The native journals write an LF for each complete event. Never consume a
    # torn final append, even if it happens to parse as JSON.
    lines = raw.split(b'\n')
    events = [json.loads(line) for line in lines[:-1]]
    if any(not isinstance(event, dict) or not isinstance(event.get('phase'), str) for event in events):
        raise ValueError('malformed original workflow journal event')
    return events, sha, bool(lines[-1])


def _plan(spec_sha, state, canary, channels):
    raw, actual_sha = read_bytes(SPEC)
    if spec_sha != actual_sha:
        raise ValueError('compiled spec SHA-256 differs from the retained run; use its original reviewed tree')
    if canary and state != 'continuing-account':
        raise ValueError('canary is not a continuing-account schedule')
    plan = compile_pizza_week(state, canary=canary)
    if plan.spec['version'] != WEEK:
        raise ValueError('unsupported compiled Week revision')
    documents = plan.documents_for(channels)
    turns = list(plan.turns) if set(channels) == set(plan.channels) else []
    dataset = SPEC.parents[1] / json.loads(raw)['dataset'] / 'manifest.json'
    dataset_manifest, dataset_sha = read_json(dataset)
    return documents, turns, plan.source_basis, dataset_manifest['dataset_sha256'], dataset_sha


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
    expected_scope = SUPERMEMORY_ADMISSION if projection == NORMALIZED_DAILY else ADMISSIONS.get(provider)
    if (scope.get('root') != str(directory.parent) or scope.get('provider') != provider
            or scope.get('run_id') != directory.parent.parent.name or scope.get('protocol') != WEEK
            or not isinstance(admission, str) or not admission
            or (projection != DAILY_FILES and admission != expected_scope)
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


def _verify_ready_at_turn(turn, releases, ready_times):
    question_at = instant(turn.at)
    for release, cutoff in releases.items():
        if cutoff > question_at:
            continue
        if release not in ready_times:
            raise ValueError(f'missing original native readback for {release} before question {turn.id}')
        if ready_times[release] > question_at:
            raise ValueError(f'original native readback for {release} is late at question {turn.id}')


def document_readbacks(events, documents, turns, *, answered=(), complete=False):
    """Bind native acceptance/readback to the original submitted document."""
    submitted, accepted, ready, intake_failures = set(), {}, {}, []
    planned = {doc.filename: doc for doc in documents}
    releases = {doc.filename: instant(doc.cutoff) for doc in documents}
    ready_times = {}
    ordered_questions = iter(turns)
    answered_questions = {turn.id: turn for turn in answered}
    pending, submitted_through = None, None
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
            cutoff = instant(doc.cutoff)
            submitted_through = cutoff if submitted_through is None else max(cutoff, submitted_through)
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
            observed_at = instant(proof['at'])
            if (doc.filename in ready or doc.filename in intake_failures
                    or observed_at < instant(acceptance['at'])):
                raise ValueError('original document readback is stale or duplicated')
            if proof.get('ready') is not True:
                intake_failures.append(doc.filename)
            elif proof.get('submitted_sha256') != doc.sha256 or not proof.get('readback'):
                raise ValueError('original document readback differs from its submitted content')
            else:
                ready[doc.filename] = proof
                ready_times[doc.filename] = observed_at
        elif phase == 'dispatch':
            turn = next(ordered_questions, None)
            if (turn is None or event['question'] != turn.id or event['prompt'] != turn.prompt or event['at'] != turn.at
                    or (submitted_through is not None and submitted_through > instant(turn.at))):
                raise ValueError('document intake or dispatched question differs at its original cutoff')
            _verify_ready_at_turn(turn, releases, ready_times)
        elif phase == 'answer-visible' and event['question'] in answered_questions:
            _verify_ready_at_turn(answered_questions[event['question']], releases, ready_times)
    # SQLite may have saved an answer beyond the end of the journal.
    for turn in answered:
        _verify_ready_at_turn(turn, releases, ready_times)
    if complete and set(ready) != set(planned):
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


def memcal_readbacks(progress, documents, *, answered=()):
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
    releases = {cutoff: instant(cutoff) for cutoff in batches}
    ready_times = {row['cutoff']: instant(row['ready_at']) for row in result}
    for turn in answered:
        _verify_ready_at_turn(turn, releases, ready_times)
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


def honcho_checkpoint(root, scope, scope_sha, expected_admission, projection):
    """Read the native Honcho Week seal without opening its service or provider state."""
    from pacbench.openrouter_route import validate_request
    from pacbench.providers import HERMES_TOOLS
    from pacbench.providers.honcho_evidence import AUTHORED_CHATS
    from pacbench.providers.honcho_evidence import honcho_plan_for as plan_for
    from pacbench.receipt_costs import replay_attempts

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
    authored = scope.get('week_session_layout') == AUTHORED_CHATS
    _, _, expected = plan_for(scope)
    schedule, protocol = expected['turns'], expected['protocol']
    result, result_sha = read_json(root/'result.json')
    if (manifest != canonical or planned != expected or result['protocol'] != protocol
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
    if any('honcho_wire_identity' in row for row in rows.values()):
        from pacbench.providers.honcho_evidence import usable_response

        generations = {row.get('upstream_request_id') for row in rows.values()}
        if len(generations) != len(rows) or not all(map(usable_response, rows.values())):
            raise ValueError('Retained Honcho run has an unusable or duplicate terminal response')
    ready = _honcho_releases(root, scope, scope_sha, documents, rows)
    startup, startup_sha = read_json(root/'startup.json')
    if startup['controller'] != scope['controller'] or startup['source_ready'] != result['source_ready']:
        raise ValueError('Honcho gateway startup does not match the owned run and source')
    pins = startup['pins']
    condition = Condition('honcho', 'nightly-document', 'continuing-account', 'Hermes ' + pins['hermes'],
                          'openai/gpt-6-luna', 'amazon-bedrock/global', protocol, '3.6.1 / SDK 2.9.0',
                          pins['hermes_isolation_sha256'], 'Physical Honcho server, bounded authored Hermes checkpoints',
                          HERMES_TOOLS['honcho'])
    answers, ids, turn_bindings = {}, set(), []
    last = {turn.session: turn.id for turn in turns}
    session_ids, chat_history, closures = {}, {}, []
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
        chat = turn.session if authored else 'single'
        prior, chat_binding = chat_history.get(chat, (None, None))
        if authored:
            if (verified.get('authored_session') != chat or verified.get('session_id') != surface['session_id']
                    or intent.get('authored_session') != chat or intent.get('chat_prior') != chat_binding
                    or session_ids.setdefault(surface['session_id'], chat) != chat):
                raise ValueError('Honcho saved native chat differs from the authored chat')
            if last[chat] == turn.id:
                closure, _ = read_json(folder/f'{index:02}-chat-close.json')
                if (closure.get('session_id') != surface['session_id']
                        or closure.get('gateway_identity') != surface['gateway_identity']
                        or closure.get('end_reason') != 'cli_close' or not closure.get('ended_at')
                        or closure.get('captures') != index):
                    raise ValueError('Honcho saved chat close differs from the frozen boundary')
                closures.append(closure)
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
        for ordinal, request in enumerate(requests, 1):
            validate_request(request['request'], profile='luna-bedrock-global')
            row = rows[request['request_id']]
            slot = row['workflow_slot']
            final_text = surface['answer'] if ordinal == len(requests) else None
            if (slot.get('kind') != 'answer' or slot.get('question_id') != turn.id
                    or not _honcho_answer_response(root, scope, scope_sha, row, request['request'], final_text)):
                raise ValueError('Honcho captured request differs from its verified driver operation')
        ids.update(turn_ids)
        inspect_source_cutoff({'model_requests': [r['request'] for r in requests], 'prefetch': surface['prefetch']},
                              documents, prefix, [t.prompt for t in turns[:index]])
        surface = {**surface, 'conversation_times': [*(prior['conversation_times'] if prior else ()), turn.at]}
        turn_bindings.append({'path': str(path), 'sha256': verified_sha})
        answers[turn.id] = (surface, {'retained_turn_sha256': verified_sha})
        chat_history[chat] = (surface, turn_bindings[-1])
    close, close_sha = read_json(root/'native-chat-close.json')
    if authored and (len(chat_history) != len(last) or len(session_ids) != len(last)
                     or len(closures) != len(last) or result.get('native_sessions') != len(last)
                     or not all(row['clean'] for row in close['sessions'])
                     or {row['session_id'] for row in close['sessions']} != set(session_ids)):
        raise ValueError('Honcho closure did not account for all authored chats')
    if close['native_failures'] or result['driver_requests'] != len(ids):
        raise ValueError('Honcho shutdown or driver request count differs from its original completion')
    condition = json.loads(json.dumps(asdict(condition)))
    _, _, binding = week_plan(spec_sha, basis, condition, False, projection=NORMALIZED_DAILY)
    binding.update(protocol=protocol, ingestion_protocol=HONCHO_ADMISSION, scope_sha256=scope_sha,
                   result_sha256=result_sha, ordered_native_schedule_sha256=scope['week_schedule']['sha256'],
                   wire_receipts_sha256=receipts_sha, startup_sha256=startup_sha, close_sha256=close_sha,
                   single_native_session=not authored, run_key=fingerprint([scope_sha, result_sha]))
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
    answered = turns[:len(saved)]
    if condition['track'] == 'nightly-document':
        provenance = document_readbacks(events, documents, turns, answered=answered, complete=complete)
    else:
        provenance = legacy_connector_readbacks(events, documents, condition, answered=answered, complete=complete)
    return RetainedArm(documents, turns, condition, binding, provenance, saved, originals, False, [])


def legacy_connector_readbacks(events, documents, condition, *, answered, complete):
    batches = defaultdict(list)
    for doc in documents:
        batches[doc.cutoff].append(doc)
    expected_order = list(batches)
    cutoffs = {cutoff: instant(cutoff) for cutoff in batches}
    ready_times = {}
    answered_questions = {turn.id: turn for turn in answered}
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
                        or not ready.get('readback') or sorted(ready.get('new_source_ids', [])) != expected
                        or not cutoffs[pending] <= instant(accepted['at']) <= instant(ready['at'])):
                    raise ValueError('unbound legacy connector release evidence')
                ids_seen.add(ident)
                releases.append({'cutoff': pending, 'native_collection_id': ident,
                                 'source_records': len(expected), 'ready_at': ready['at'],
                                 'witness_sha256': fingerprint([accepted, ready])})
            ready_times[pending] = instant(ready['at'])
            pending, accepted = None, None
        elif event['phase'] == 'answer-visible' and event['question'] in answered_questions:
            _verify_ready_at_turn(answered_questions[event['question']], cutoffs, ready_times)
    if complete and len(releases) != len(batches):
        raise ValueError('completed legacy journal lacks original connector readbacks')
    return {'kind': 'legacy-native-connector-collection', 'ready_documents': None,
            'ready_releases': len(releases), 'collections': releases,
            'last_completed_cutoff': releases[-1]['cutoff'] if releases else None}


def source_proof(prior,provider,group_id):
 """Resolve immutable source evidence through verified recovery ancestry."""
 path=Path(prior)/provider/('checkpoint-'+str(group_id))/'source-proof.json'
 if path.exists():return path
 # Prefix copies intentionally retain original receipt bindings.
 cap_file = Path(prior) / 'capability.json'
 if not cap_file.is_file():
  return path
 for ancestor in full_cap.recovery_origins(full_cap.load_full_scope(evidence_store.file_binding(cap_file))):
  candidate=Path(ancestor)/provider/('checkpoint-'+str(group_id))/'source-proof.json'
  if candidate.exists():return candidate
 return path


def verify_completed(prior,provider,checks,plan,schedule,*,prefix=False):
 """Re-read frozen identities and native receipts before skipping valid work."""
 from pacbench.harness import TurnResult
 from pacbench.source_policy import classify_retention as classify
 prior=Path(prior);evaluation=prior/provider/'evaluation';report=json.loads((evaluation/'evaluation.json').read_text())
 if (not prefix and report['state']!='diagnostic') or len(report['checks'])<len(checks):raise GatewayLifecycleError('Incomplete prior arm')
 for group in plan['groups']:
  if not any(q['id'] in {r['id'] for r,_,_ in checks} for q in group['questions']):continue
  verify_checkpoint(group,json.loads((source_proof(prior,provider,group['id'])).read_text()),schedule,provider)
 inherited={}
 for (row,contract,_),item in zip(checks,report['checks']):
  if item['state'] not in {'diagnostic','strict-ungraded'} or item.get('recovery',{}).get('state','complete')!='complete':raise GatewayLifecycleError('Prior prefix not complete')
  if item['id']!=row['id'] or item['contract_hash']!=contract.contract_hash:raise GatewayLifecycleError('Prior contract differs')
  folder=evaluation/'turns'/row['id'];turn=TurnResult(**json.loads((folder/'turn.json').read_text()));audit_transport(turn)
  if turn.prompt!=contract.question or turn.at!=row['at']:raise GatewayLifecycleError('Prior native prompt/time differs')
  key=row.get('session') or row['id'];decision=classify(provider,turn,inherited.get(key,'eligible'));inherited[key]=decision['state']
  if decision!=json.loads((folder/'strict-memory.json').read_text()):raise GatewayLifecycleError('Prior provenance changed')
  if decision['state']!='eligible':
   if item['state']!='strict-ungraded' or item['verdict'] is not None:raise GatewayLifecycleError('Prior raw/unknown answer scored')
  else:
   grade=json.loads((folder/'grade.json').read_text())
   if [x['id'] for x in grade['claims']]!=[x.id for x in contract.claims] or not (folder/'judgment.json').exists():raise GatewayLifecycleError('Prior grade coverage invalid')
 return {'state':'verified','questions':len(checks),'criteria':sum(len(c.claims) for _,c,_ in checks)}


PROTOCOL='native-cold-session-checkpoint-v1'


def verify(binding,owner,sid):
    p=Path(binding['path'])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=binding['sha256']:raise GatewayLifecycleError('Native transfer receipt changed')
    r=json.loads(p.read_text())
    if (r.get('protocol')!=PROTOCOL or r.get('destination_home')!=str(owner.home) or sid not in r['sessions'] or
        r.get('config_sha256')!=owner.config_sha256 or r.get('pins')!=owner.pins):
        raise GatewayLifecycleError('Native transfer does not bind this exact owner/session')
    seal_path=Path(r['source_seal']['path'])
    if hashlib.sha256(seal_path.read_bytes()).hexdigest()!=r['source_seal']['sha256']:raise GatewayLifecycleError('Native source seal changed')
    sealed=json.loads(seal_path.read_text())
    if (r['sessions']!=sealed['sessions'] or r['pins']!=sealed['pins'] or r['config_sha256']!=sealed['config_sha256']):
        raise GatewayLifecycleError('Native transfer differs from its sealed session history')
    if _live(sealed['source_owner']['pid']):raise GatewayLifecycleError('Previous native owner is live')
    row=r['sessions'][sid]
    if history(owner.home,sid)!=row['history']:raise GatewayLifecycleError('Restored native history changed')
    return row


REVIEWED_SHA = 'da11d4f51d9a6bfad6839088cf6f20e3645ba13bf92663f62301db6f5878875c'


CURRENT_SHA = '04f71e3f09a76ea4f499ac58cb6076a7b9aa74870202601c714c346e8ac28622'


RUNTIME_AST_SHA = 'a902b7ab0d91e76e5ee59e47d214ede346a056b5cafe459db59895896d08d587'


def execution_digest(source):
    tree=ast.parse(source)
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='audit_followups':node.body=[ast.Pass()]
    def canonical(value):
        if isinstance(value,ast.AST):
            return {'_type':type(value).__name__,**{k:canonical(v) for k,v in ast.iter_fields(value) if v is not None and v!=[]}}
        if isinstance(value,list):return [canonical(v) for v in value]
        return value
    # Python 3.14's ast.dump omits empty fields by default; canonicalize them
    # explicitly so the same parsed program has the same proof on 3.12/3.14.
    return hashlib.sha256(json.dumps(canonical(tree),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def audit_only_compatibility(repo,path,reviewed_sha):
    repo,path=Path(repo).resolve(),Path(path).resolve()
    source=path.read_bytes();current_sha=hashlib.sha256(source).hexdigest()
    if (path!=repo/'pacbench/followups.py' or reviewed_sha!=REVIEWED_SHA or current_sha!=CURRENT_SHA or
            execution_digest(source.decode())!=RUNTIME_AST_SHA):
        raise ValueError('Frozen reviewed source differs outside the exact audit-only compatibility pair')
    return {'protocol':'reviewed-followup-audit-only-compatibility-v1','path':'pacbench/followups.py',
        'reviewed_sha256':reviewed_sha,'current_sha256':current_sha,'execution_ast_sha256':RUNTIME_AST_SHA,
        'reviewed_source_revision':'486d7e1faefe01ef4f3cc344934ce85407b5a91c',
        'changed_body_only':'audit_followups','frozen_review_rewritten':False,
        'scope':'Post-run refusal audit only; generation, judging, eligibility, scoring and prompts unchanged'}


def read(path):return json.loads(path.read_text())


def honcho_completed_cleanup_import(root,receipt):
    """Recover an observer timeout from recorded native enrollment/cleanup.

    Retention is reconstructed from the same disabled-TOML configuration and
    inherited environment, then checked again on actual native reopen. Record
    that distinction rather than presenting a historical config observation.
    """
    import copy
    import subprocess

    from provider_adapters.honcho_clock import cleaned_queue_coverage
    if any('QUEUE_ERROR_RETENTION' in k for k in os.environ):
        raise ValueError('Historical native retention override cannot be reconstructed')
    repo=Path(__file__).resolve().parents[2];native=repo/'vendor/honcho-local'
    env={**os.environ,'PYTHONPATH':str(native),'HONCHO_CONFIG_TOML_DISABLED':'1'}
    p=subprocess.run([str(native/'.venv/bin/python'),'-c',
        'from src.config import settings; print(settings.DERIVER.QUEUE_ERROR_RETENTION_SECONDS)'],
        env=env,capture_output=True,text=True,check=True,timeout=30)
    retention=int(p.stdout.strip().splitlines()[-1]);r=receipt['result']
    if r.get('readiness')!='Real processing deadline exceeded; missing witnesses are not success':
        raise ValueError('Not a completed-queue observer timeout')
    from datetime import datetime
    lower_bound=min((m['created_at'] for a in r['result'] for m in a['messages']),key=datetime.fromisoformat)
    ids=cleaned_queue_coverage(r['message_witnesses'],r['processing_status'],retention_seconds=retention,not_before=lower_bound)
    expected={m['id'] for a in r['result'] for m in a['messages']}
    if ids!=expected:raise ValueError('Native successful cleanup does not cover all accepted messages')
    log=(root/'honcho-api.log').read_text()
    if '"DELETE ' in log or '"PATCH ' in log:raise ValueError('Native destructive API history requires separate review')
    recovered=copy.deepcopy(receipt);recovered.update(ready=True,state='ready',observer_recovery_protocol='honcho-completed-queue-observer-timeout-v1')
    recovered['result'].update(ready=True,readiness='Recorded enrolled native rows successfully cleaned within error retention',
        queue_cleanup_evidence={'protocol':'honcho-enrolled-queue-success-cleanup-v1','retention_seconds':retention,'message_ids':sorted(ids)})
    proof={'protocol':'honcho-completed-queue-observer-timeout-v1','original_sha256':hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest(),
        'native_source_resubmissions':0,'native_queue_edits':0,'retention_seconds':retention,
        'retention_evidence':'Same inherited environment has no retention override; disabled-TOML native config reconstructed; actual reopen revalidates the default',
        'native_source_sha256':{name:hashlib.sha256((native/name).read_bytes()).hexdigest() for name in
            ['src/reconciler/queue_cleanup.py','src/config.py']},
        'api_log_sha256':hashlib.sha256(log.encode()).hexdigest(),
        'deriver_log_sha256':hashlib.sha256((root/'honcho-deriver.log').read_bytes()).hexdigest()}
    return recovered,proof


def mem0_read_only_failed_import(output,root,receipt,batch):
    """Prove this specific SDK failure occurred before any persistent write.

    The reviewed pinned implementation only reads history/vector search before
    its first LLM extraction; writes, including message history, follow a usable
    extraction response. No paid dispatch and no response therefore means this
    failed attempt did not reveal its future source in native memory.
    """
    from pacbench import harness
    sdk=next((harness.ISOLATED_BIN.parent.parent/'lib').glob('python*/site-packages/mem0/memory/main.py'))
    sha=hashlib.sha256(sdk.read_bytes()).hexdigest()
    config=read(root/'hermes-home/mem0.json');usage=receipt.get('usage',{})
    observations=root/'ingestion-model-calls'/ (batch['id']+'.json')
    error=receipt.get('stderr','')
    if (sha!='f329ca4a17af1fbefb32ac10d4cc7ae7ddbdb7435a87eb74c30915cff98b8b7c' or
        config['oss'].get('graph_store') or receipt.get('ready') is not False or receipt.get('state')!='failed' or
        usage.get('state')!='captured' or usage.get('model_calls')!=[] or
        any(r.get('model_calls')!=0 for r in usage.get('roles',{}).values()) or
        not observations.exists() or read(observations)!=[] or
        'in _add_to_vector_store' not in error or 'LLM extraction failed: Connection error.' not in error):
        raise ValueError('Failed Mem0 import is not proven read-only')
    if receipt['document_sha256']!=hashlib.sha256((output/'sources'/batch['id']/'messages.txt').read_bytes()).hexdigest():raise ValueError('Failed source binding differs')
    return {'protocol':'mem0-pre-extraction-read-only-failure-v1','sdk_source_sha256':sha,
        'original_receipt_sha256':hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest(),
        'paid_dispatches':0,'extraction_responses':0,'persistent_native_writes':0,
        'classification':'Failed before extraction; not complete and not a source eligible for recall'}


def cognee_read_only_failed_import(output,root,receipt,batch):
    if (receipt.get('ready') is not False or receipt.get('state')!='failed' or
        receipt.get('usage',{}).get('state')!='captured' or receipt['usage'].get('model_calls')!=[] or
        not any(s in receipt.get('stderr','') for s in ['QualificationError: Dataset differs from verified prior source receipts',
            'QualificationError: Previous native operation has no terminal witness; do not add another document'])):
        raise ValueError('Cognee failure is not a pre-submission integrity refusal')
    expected=hashlib.sha256((output/'sources'/batch['id']/'messages.txt').read_bytes()).hexdigest()
    if expected!=receipt.get('document_sha256'):raise ValueError('Failed source identity differs')
    import sqlite3
    import urllib.parse
    with sqlite3.connect((root/'cognee-system/databases/cognee_db').resolve().as_uri()+'?mode=ro',uri=True) as db:
        hashes=[hashlib.sha256(Path(urllib.parse.urlparse(row[0]).path).read_bytes()).hexdigest()
            for row in db.execute('SELECT raw_data_location FROM data')]
    if expected in hashes:raise ValueError('Source may already be stored; do not resubmit')
    return {'protocol':'cognee-pre-submission-integrity-refusal-v1','paid_dispatches':0,
        'source_raw_hash_absent':expected,'classification':'No remember request was sent; original refused receipt retained'}


def verify_prefix(output,report,checks,*,allow_complete=False):
    from pacbench.harness import question_prompt
    from pacbench.workflows.source_plan import ready_sources
    provider=report['results'][0]['provider'];root=output/'repeat-1'/provider
    evaluation=read(root/'evaluation.json');prefix=[]
    strict=evaluation.get('strict_memory_protocol')
    if strict not in (None,'strict-memory-only-v1'):raise ValueError('Unknown retained strict memory protocol')
    sessions={}
    for item in evaluation['checks']:
        if item['state'] not in ({'diagnostic','strict-ungraded'} if strict else {'diagnostic'}) or item.get('recovery',{}).get('state','complete')!='complete':break
        prefix.append(item)
    if not 0<len(prefix)<=len(checks) or len(prefix)==len(checks) and not allow_complete:raise ValueError('No incomplete scored prefix to resume')
    for (row,contract,_),item in zip(checks,prefix):
        d=root/'turns'/row['id'];t=read(d/'turn.json')
        if (item['id']!=row['id'] or item['contract_hash']!=contract.contract_hash or
            read(d/'status.json')!=item or t['returncode'] or t['policy_blocks'] or
            t['context_isolation']['state']!='pass' or not t['session_id'] or
            t['prompt']!=question_prompt(contract.question,row['at'],report['question_prompt_protocol']) or t['at']!=row['at'] or
            not t.get('answer','').strip()):
            raise ValueError('Completed scored evidence differs')
        if strict:
            from pacbench.harness import TurnResult
            from pacbench.source_policy import classify_retention as classify
            from pacbench.workflows.turn_evidence import (
                advance,
                fresh_state,
                session_key,
            )
            state=sessions.setdefault(session_key(row,evaluation['context']),fresh_state())
            decision=classify(provider,t,state.get('strict','eligible'))
            if decision!=read(d/'strict-memory.json'):raise ValueError('Retained strict provenance differs')
            state['strict']=decision['state']
            if item['state']=='strict-ungraded':
                if decision['state']=='eligible' or item.get('verdict') is not None or (d/'grade.json').exists() or (d/'judgment.json').exists():
                    raise ValueError('Strict ungraded prefix has fabricated scoring or eligible provenance')
            elif decision['state']!='eligible':raise ValueError('Source-assisted prefix was graded')
            advance(state,TurnResult(**t),d,report.get('provenance_version','source-provenance-v1'))
        if item['state']=='diagnostic':
            g=read(d/'grade.json');j=read(d/'judgment.json')
            if j['answer']!=t['answer'] or json.loads(j['response'])['verdicts']!=g['claims']:
                raise ValueError('Completed scored evidence differs')
        expected={b['id'] for b in ready_sources(report['ingestion_schedule'],provider,row['at'],report['process_timezone'])}
        if set(read(d/'source-state.json')['ingested_batches'])!=expected:raise ValueError('Completed chronology differs')
    completed=[];gap=False
    for batch in report['ingestion_schedule']:
        p=root/batch['file']
        if not p.exists():gap=True;continue
        receipt=read(p);source=output/'sources'/batch['id']/'messages.txt'
        if provider=='honcho' and receipt.get('ready') is False:
            receipt,_=honcho_completed_cleanup_import(root,receipt)
        if provider in {'mem0','cognee'} and receipt.get('ready') is False:
            (mem0_read_only_failed_import if provider=='mem0' else cognee_read_only_failed_import)(output,root,receipt,batch);gap=True;continue
        if (gap or receipt.get('ready') is not True or receipt.get('state')!='ready' or
            receipt['source_ids']!=batch['source_ids'] or
            receipt['document_sha256']!=hashlib.sha256(source.read_bytes()).hexdigest()):
            raise ValueError('Partial or changed native ingest cannot be resumed')
        completed.append(batch['id'])
    next_row=checks[min(len(prefix),len(checks)-1)][0]
    if provider=='honcho':
        from datetime import datetime

        from provider_adapters.honcho_clock import load
        if load(root)[0].now()>=datetime.fromisoformat(next_row['at']):
            raise ValueError('Retained Honcho clock has reached the next unanswered checkpoint')
    allowed={b['id'] for b in ready_sources(report['ingestion_schedule'],provider,next_row['at'],report['process_timezone'])}
    if not set(completed)<=allowed:raise ValueError('Retained store contains future sources')
    for row,contract,_ in checks[len(prefix):]:
        p=root/'turns'/row['id']/'turn.json'
        if p.exists():
            t=read(p)
            if t.get('prompt')!=question_prompt(contract.question,row['at'],report['question_prompt_protocol']) or t.get('at')!=row['at']:
                raise ValueError('Archived failed turn differs from frozen question')
            if not t.get('returncode') and t.get('answer','').strip():raise ValueError('Ungraded successful turn needs judge-only recovery')
        if (root/'turns'/row['id']/'grade.json').exists():raise ValueError('Non-prefix grades require a different recovery protocol')
    return prefix,completed


@dataclass(frozen=True)
class DocumentCanaryAudit:
    root: Path
    bindings: dict[str, dict[str, str]]
    condition: dict
    deployment: dict
    outcome: tuple[str, str]
    spec_sha256: str
    turns: list[Turn]
    events: list[dict]
    receipts: list[dict]


def _verify_retained_identity(provider: str, scope: dict, result: dict) -> None:
    """Check the identity fields recorded by each retained layout."""
    if provider != 'memos' and scope['provider'] != provider:
        raise ValueError(f'{provider} canary has a different scope provider')
    if provider != 'openviking' and scope.get('admission') != DOCUMENT_ARMS[provider].admission:
        raise ValueError(f'{provider} canary has a different scope admission')
    if provider not in ('mem0', 'hindsight') and result['provider'] != provider:
        raise ValueError(f'{provider} canary has a different result provider')


def audit_document_canary(root: Path, provider: str) -> DocumentCanaryAudit:
    root = Path(root).resolve(strict=True)
    bindings = {'result': binding(root/'result.json'), 'scope': binding(root/'workflow-scope.json')}
    result, scope = document_canary_read(bindings['result']), document_canary_read(bindings['scope'])
    spec_sha256 = sha(SPEC)
    documents, turns, frozen = schedule(spec_sha256, True)
    if (root.name != provider or scope['root'] != str(root) or scope['canary'] is not True
            or scope['spec_sha256'] != spec_sha256 or scope['frozen_inputs'] != frozen):
        raise ValueError(f'{provider} canary scope differs from the frozen inputs')
    _verify_retained_identity(provider, scope, result)
    outcome = result['state'], result['cleanup']
    # Hindsight checks the outcome after reconciling shutdown in its provider audit.
    if provider != 'hindsight' and outcome != ('live-canary-complete', 'complete'):
        raise ValueError(f'{provider} canary did not complete successfully')

    events = []
    report = summarize(root/'workflow/workflow.jsonl', events=events)
    if not report or report['incomplete_tail'] is not None or report['status'] != 'completion':
        raise ValueError(f'{provider} canary journal did not reach a complete terminal event')
    condition = events[1]['condition']
    deployment = events[1]['deployment_witness']
    if provider != 'mem0':
        # The retained Mem0 canary carries a separate static plugin/runtime proof.
        validate_native_deployment(Condition(**condition), deployment)

    receipts = read_attempts(root / 'receipts.jsonl')
    billing = receipt_summary(root, receipts, scope['run_id'])
    roles = {row['accounting_role'] for row in receipts if row.get('upstream_request_id')}
    coverage = report['coverage']
    if (result['canary'] is not True or result['workflow'] != report or result['billing'] != billing
            or billing['complete_cost_usd'] is None or roles != {'hermes', 'judge', 'memory_provider'}
            or (coverage['verified_turns'], coverage['graded_criteria']) != (4, 12)
            or any(row['driver_attempt_coverage']['coverage'] != 'complete' for row in report['turns'])):
        raise ValueError(f'{provider} requires a complete native and receipted canary')
    if provider != 'memos' and (coverage['planned_turns'], coverage['planned_criteria']) != (4, 12):
        raise ValueError(f'{provider} canary plan has different question or criterion counts')

    if provider != 'memos':
        profile = PROFILES[PROFILE]
        checkout = Path(__file__).resolve().parents[2]
        revision = next(line.split('=', 1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                        if line.startswith('HERMES_PIN='))
        if (condition['provider'] != provider or condition['track'] != 'nightly-document'
                or condition['state'] != 'continuing-account' or condition['model'] != profile['model']
                or condition['model_route'] != profile['endpoint'] or condition['frontend'] != 'Hermes '+revision
                or condition['gateway_patch'] != sha(checkout/'patches/hermes-isolated.diff')
                or deployment['account'] != str(root)):
            raise ValueError(f'{provider} canary runtime and route differ from the Week condition')

    expected_questions = [turn.id for turn in turns]
    answer_phase = 'answer-visible' if provider == 'memos' else 'verified-turn'
    if [row['question'] for row in events if row['phase'] == answer_phase] != expected_questions:
        raise ValueError(f'{provider} canary questions differ from the frozen schedule')

    visible = [row for row in events if row['phase'] == 'answer-visible']
    if provider != 'mem0' and (len(visible) != 4
            or sum(row['phase'] == 'chat-closed' for row in events) != 3
            or visible[0]['surface']['session_id'] != visible[3]['surface']['session_id']):
        raise ValueError(f'{provider} canary sessions differ from the frozen schedule')

    sources = [row['evidence'] for row in events if row['phase'] == 'readback']
    ready = [row['ready'] for row in sources]
    if provider == 'mem0':
        ready = [bool(value) for value in ready]
    if ([(row['native_id'], row['submitted_sha256']) for row in sources]
            != [(doc.filename, doc.sha256) for doc in documents]
            or ready != [True]*len(documents)):
        raise ValueError(f'{provider} canary lacks the frozen native source readbacks')
    return DocumentCanaryAudit(root, bindings, condition, deployment, outcome, spec_sha256, turns, events, receipts)


def verify_listener_stopped(url: str, provider: str) -> None:
    endpoint = urlsplit(url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise ValueError(f'Unexpected {provider} service endpoint')
    try:
        connection = create_connection((endpoint.hostname, endpoint.port), timeout=2)
    except ConnectionRefusedError:
        return
    connection.close()
    raise ValueError(f'{provider} canary listener remains active')
