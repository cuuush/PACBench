"""Honcho evidence."""
from __future__ import annotations

import hashlib
import json
from hashlib import sha256
from pathlib import Path

from pacbench.corpus.r2 import cache_bound_schedule
from pacbench.evidence_store import file_binding as binding
from pacbench.evidence_store import read_bound_bytes as read_bytes
from pacbench.evidence_store import read_bound_json as read_json
from pacbench.receipt_projection import AdmissionSpec
from pacbench.workflows.progress import OwnershipConflict
from pacbench.workflows.spec import instant


def canonical_inputs(*, first_release=False):
    from pacbench.corpus.r2 import daily_week_for_scope, member_digest

    _, documents, _, _, manifest = daily_week_for_scope()
    if not first_release:
        return documents, manifest
    index = manifest['document_index'][:1]
    return documents[:1], dict(source_layout=manifest['source_layout'], documents=1,
                               records=len(documents[0].source_ids), document_index=index,
                               normalized_index_sha256=member_digest(index),
                               parent_index_sha256=manifest['normalized_index_sha256'],
                               layout_spec_sha256=manifest['layout_spec_sha256'])


def chat_enabled(scope):
    return bool(scope.get('first_question') or scope.get('week_schedule'))


def _successful(scope, row, release, queue_id):
    from pacbench.workflows.progress import operation_id

    slot = row.get('workflow_slot', {})
    return (row.get('workflow_root') == scope['root'] and row.get('project') == scope['projects']['honcho']['memory']
            and row.get('workflow_capability') == binding(Path(scope['root'])/'workflow-scope.json')
            and slot.get('honcho_daily_release') == release
            and row.get('honcho_daily_attempt', {}).get('queue_id') == queue_id
            and row.get('dispatch_context', {}).get('operation_id') == operation_id(row)
            and row.get('dispatch_execution') == 'response_ended' and row.get('http_status') == 200
            and bool(row.get('finished')) and (not chat_enabled(scope) or usable_response(row)))


def _source_completion(scope, rows, release, queue_id):

    attempts = release_attempts(rows, release)
    if (not attempts or not all(_successful(scope, row, release, queue_id) for row in attempts)
            or attempts[-1].get('honcho_validation_rejection')):
        return None
    final=attempts[-1]
    if len(attempts)==1:
        return final if not final.get('retry_of') else None
    if (scope.get('week_first_four') or not scope.get('week_schedule')
            or len({row['upstream_request_id'] for row in attempts})!=len(attempts)):
        return None

    previous=reserve_retry({row['id']:row for row in attempts[:-1]},final)
    return final if previous and final.get('retry_of')==previous['id'] else None


PROFILE = 'luna-bedrock-global'


def settled(row):
    from pacbench.openrouter_route import PROFILES

    validation = row.get('response_validation')
    slot = row.get('workflow_slot')
    if not isinstance(validation, dict):
        return False
    # Decoding the terminal SSE frame can close the native iterator before HTTP EOF.
    terminal = (row.get('dispatch_execution') == 'response_ended' and row.get('response_body_complete') is True
                or isinstance(slot, dict) and 'honcho_first_answer' in slot
                and row.get('dispatch_execution') == 'ambiguous' and validation.get('sse_done_observed') is True)
    return (row.get('state') == 'settled' and row.get('http_status') == 200
            and terminal and bool(validation.get('finish_reasons')) and not validation.get('envelope_errors')
            and not validation.get('wire_parse_errors')
            and row.get('served_model') == PROFILES[PROFILE]['model'] and row.get('served_provider') == 'Amazon Bedrock'
            and bool(row.get('billing_receipt')) and bool(row.get('upstream_request_id')))


def wire_identity(frames):
    """Record only a consistent response identity from the actual wire frames."""
    for event in frames.events:
        choices = event.get('choices', [])
        if (not isinstance(choices, list) or len(choices) > 1
                or any(not isinstance(choice, dict) or choice.get('index', 0) != 0 for choice in choices)):
            return None
    identity = {}
    for key in ('id', 'model', 'provider'):
        values = [event[key] for event in frames.events if key in event and event[key] is not None]
        if (not values or any(not isinstance(value, str) or not value.strip() or len(value) > 256
                              for value in values)
                or len(set(values)) != 1):
            return None
        identity[key] = values[0]
    return dict(identity, stream=frames.sse)


def usable_response(row, *, allow_truncated=False):
    """Verify a finished Honcho response independently of its financial receipt."""
    from pacbench.openrouter_route import PROFILES

    if not row.get('finished'):
        return False
    if 'honcho_wire_identity' not in row:
        return settled(row)  # Historical rows only retained route identity in settled receipts.
    identity = row['honcho_wire_identity']
    evidence = row.get('response_validation')
    if not isinstance(evidence, dict):
        return False
    slot = row.get('workflow_slot')
    if not isinstance(slot, dict):
        slot = {}
    answer = bool(slot.get('honcho_first_answer'))
    if (not isinstance(identity, dict) or not identity.get('id') or identity['id'] != row.get('upstream_request_id')
            or type(identity.get('stream')) is not bool
            or identity.get('model') != PROFILES[PROFILE]['model'] or identity.get('provider') != 'Amazon Bedrock'
            or row.get('http_status') != 200 or row.get('provider_rejection')
            or row.get('state') not in ('settled', 'receipt_missing')
            or (row['state'] == 'receipt_missing' and row.get('error_label') != 'actual_cost_unknown')
            or evidence.get('envelope_errors') or evidence.get('wire_parse_errors')):
        return False
    if answer and not identity['stream']:
        return False
    if identity['stream'] and evidence.get('sse_done_observed') is not True:
        return False
    if not ((row.get('dispatch_execution') == 'response_ended' and row.get('response_body_complete') is True)
            or (answer and identity['stream'] and row.get('dispatch_execution') == 'ambiguous'
                and evidence.get('sse_done_observed') is True)):
        return False
    reasons = evidence.get('finish_reasons')
    if reasons == ['tool_calls']:
        return answer and bool(row.get('honcho_selected_tools'))
    if reasons == ['stop']:
        characters = evidence.get('assistant_nonwhitespace_characters')
        return type(characters) is int and characters > 0 and evidence.get('tool_call_fragments') == 0
    if reasons == ['length'] and (allow_truncated or row.get('honcho_validation_rejection')):

        return _representation(slot) and evidence.get('tool_call_fragments') == 0
    return False


def _generation(row):
    if row.get('finished'):
        # A finished row without an ID also participates in duplicate detection.
        return _key(row.get('upstream_request_id'))


def _invalid(row):

    return bool(row.get('finished') and not usable_response(row))


def _key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _scopes(row):
    slot = row.get('workflow_slot')
    if not isinstance(slot, dict):
        return
    if 'honcho_first_answer' in slot:
        yield 'driver', 'honcho'
        yield 'driver_question', slot.get('question_id')
    if slot.get('kind') == 'answer':
        yield 'answer_question', slot.get('question_id')
    if (release := slot.get('honcho_daily_release')) is not None:
        yield 'source_release', _key(release)
    if job := slot.get('honcho_chat_work'):
        if not isinstance(job, dict) or 'purpose' not in job:
            yield 'malformed_chat_scope', 'honcho'
        else:
            purpose = job['purpose']
            queues = job.get('queue_ids')
            if not isinstance(queues, (list, tuple)) or any(type(q) not in (int, str) for q in queues):
                yield 'malformed_chat_queue', purpose
            else:
                for queue_id in queues:
                    yield 'chat_queue', (purpose, queue_id)
    if isinstance(peer := slot.get('honcho_peer_read'), dict):
        operation = peer.get('operation')
        yield 'peer_operation', operation
        yield 'peer_claim', (operation, peer.get('run_id'), peer.get('claim'))


HONCHO_RECEIPTS = AdmissionSpec('honcho-v1', _generation, _invalid, _scopes)


def _for_scope(rows, kind, *keys):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.for_scope(kind, *keys)
    wanted = [(kind, key) for key in keys]
    return [row for row in rows.values() if any(scope in wanted for scope in _scopes(row))]


def has_unusable_responses(rows):
    """A duplicate or unusable finished response blocks further Honcho work."""
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.duplicate_generations() or rows.invalid_terminal_responses()

    completed = [row for row in rows.values() if row.get('finished')]
    return (len({_generation(row) for row in completed}) != len(completed)
            or any(map(_invalid, completed)))


def last_driver(rows):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return rows.last_for_scope('driver', 'honcho')
    drivers = _for_scope(rows, 'driver', 'honcho')
    return drivers[-1] if drivers else None


def driver_history(rows, question_id):
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return (rows.count_for_scope('driver_question', question_id),
                rows.last_for_scope('driver_question', question_id))
    previous = _for_scope(rows, 'driver_question', question_id)
    return len(previous), previous[-1] if previous else None


def turn_answers(rows, question_id):
    return _for_scope(rows, 'answer_question', question_id)


def release_attempts(rows, release):
    # Match the whole receipt binding, including its path.
    return _for_scope(rows, 'source_release', _key(release))


def chat_attempts(rows, job):
    purpose = job['purpose']
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        malformed = (rows.count_for_scope('malformed_chat_scope', 'honcho')
                     or rows.count_for_scope('malformed_chat_queue', purpose))
    else:
        malformed = any(_for_scope(rows, kind, key) for kind, key in
                        (('malformed_chat_scope', 'honcho'), ('malformed_chat_queue', purpose)))
    if malformed:
        raise OwnershipConflict('A retained Honcho chat queue lineage is malformed')
    return _for_scope(rows, 'chat_queue', *((purpose, queue_id) for queue_id in job['queue_ids']))


def peer_history(rows, proof):
    operation = proof['operation']
    witness = (operation, proof['run_id'], proof['claim'])
    if hasattr(rows, 'enable_admission'):
        rows.enable_admission(HONCHO_RECEIPTS)
        return (rows.count_for_scope('peer_operation', operation),
                rows.count_for_scope('peer_claim', witness))
    previous = _for_scope(rows, 'peer_operation', operation)
    matched = sum(('peer_claim', witness) in _scopes(row) for row in previous)
    return len(previous), matched


PURPOSE = 'deriver.representation'


REJECTION = 'stock-prompt-representation-truncated-and-unparseable-v1'


SOURCE_REJECTION = 'stock-daily-prompt-representation-truncated-and-empty-v1'


EMPTY_SHA256 = sha256(b'').hexdigest()


def _representation(slot):
    return (bool(slot.get('honcho_daily_release'))
            or slot.get('honcho_chat_work', {}).get('purpose') == PURPOSE)


def _rejection(row):
    """Build the exact proof a matched native parser failure is allowed to record."""
    slot = row['workflow_slot']
    evidence = row.get('response_validation', {})
    if (not _representation(slot) or not usable_response(row, allow_truncated=True)
            or evidence.get('finish_reasons') != ['length']
            or evidence.get('tool_call_fragments') != 0):
        return None
    digest = evidence.get('assistant_text_sha256')
    characters = evidence.get('assistant_text_characters')
    if not digest or type(characters) is not int:
        return None
    if characters == 0 and digest == EMPTY_SHA256 and slot.get('honcho_daily_release'):
        reason = SOURCE_REJECTION
    elif characters > 0 and digest != EMPTY_SHA256:
        reason = REJECTION
    else:
        return None
    return dict(reason=reason,request_sha256=row['request_sha256'],response_sha256=digest,
                upstream_request_id=row['upstream_request_id'],native_attempt=row['honcho_daily_attempt']['native_attempt'])


def reserve_retry(rows,row):
    """Return one exact predecessor for a stock retry. Reject replay and changed batches."""
    slot=row['workflow_slot']
    native=row['honcho_daily_attempt']
    number=native['native_attempt']
    if release:=slot.get('honcho_daily_release'):
        prior=release_attempts(rows, release)
        claim_keys=('queue_id','claim','trace')
    else:
        job=slot['honcho_chat_work']
        prior=chat_attempts(rows, job)
        claim_keys=('queue_ids','claim','trace')
    if not prior and number==1:
        return None
    if not _representation(slot) or number not in (2,3) or len(prior)!=number-1:
        raise OwnershipConflict('Honcho representation retry is outside its observed stock attempt sequence')
    parent=None
    generations=set()
    for ordinal,old in enumerate(prior,1):
        earlier=old['honcho_daily_attempt']
        proof=_rejection(old)
        if (any(old.get(key)!=row.get(key) for key in ('workflow_slot','workflow_capability',
                'workflow_root','project','dispatch_owner','request_sha256'))
                or any(earlier[key]!=native[key] for key in claim_keys) or earlier['native_attempt']!=ordinal
                or old.get('retry_of')!=parent or not proof or old.get('honcho_validation_rejection')!=proof
                or old['upstream_request_id'] in generations):
            raise OwnershipConflict('Honcho representation retry lacks an exact native validation rejection')
        generations.add(old['upstream_request_id'])
        parent=old['id']
    return prior[-1]


PROTOCOL = 'honcho-seven-daily-single-hermes-week-v1'


AUTHORED_CHATS = 'authored-chats'


AUTHORED_PROTOCOL = 'honcho-seven-daily-authored-hermes-chats-v2'


CANARY_PROTOCOL = 'honcho-day-one-authored-chat-canary-v1'


CANARY_QUESTIONS = ('rsvp-before', 'cancellation-before', 'obligations-before', 'lunch-snapshot')


@cache_bound_schedule(maxsize=3)
def honcho_week_plan(layout=None, *, first_four=False):
    from pacbench.corpus.r2 import daily_week_for_scope

    if layout not in (None, AUTHORED_CHATS):
        raise ValueError('Unknown Honcho Week session layout')
    if type(first_four) is not bool or (first_four and layout != AUTHORED_CHATS):
        raise ValueError('The four-turn canary needs the authored chat layout')
    _, docs, turns, basis, manifest = daily_week_for_scope()
    if (len(docs),sum(len(d.source_ids) for d in docs),len(turns)) != (7,142,49):
        raise ValueError('Frozen Honcho Week coverage differs')
    if any(instant(a.at) >= instant(b.at) for a,b in zip(turns,turns[1:])):
        raise ValueError('Frozen Week turns are out of chronological order')
    schedule = [dict(index=i+1,id=t.id,at=t.at,authored_session=t.session,measure=t.measure,after=t.after,
                     prompt_sha256=sha256(t.prompt.encode()).hexdigest(),
                     ready_files=[d.filename for d in docs if instant(d.cutoff)<=instant(t.at)])
                for i,t in enumerate(turns)]
    protocol, session = ((AUTHORED_PROTOCOL, AUTHORED_CHATS) if layout == AUTHORED_CHATS
                         else (PROTOCOL, 'one continuing Hermes session'))
    if first_four:
        turns, schedule = turns[:4], schedule[:4]
        sessions = [turn.session for turn in turns]
        if (tuple(turn.id for turn in turns) != CANARY_QUESTIONS
                or sessions[0] != sessions[-1] or len(set(sessions)) != 3
                or any(row['ready_files'] != [docs[0].filename] for row in schedule)
                or len(docs[0].source_ids) != 39):
            raise ValueError('The frozen day-one source or four-turn chat sequence changed')
        docs, manifest = canonical_inputs(first_release=True)
        protocol = CANARY_PROTOCOL
    return docs,turns,dict(protocol=protocol,session=session,
                          corpus=basis,document_index_sha256=manifest['normalized_index_sha256'],
                          gradable_turns=sum(bool(t.claims) for t in turns),
                          criteria=sum(len(t.claims) for t in turns),turns=schedule)


def honcho_plan_for(scope):
    return honcho_week_plan(scope.get('week_session_layout'), first_four=scope.get('week_first_four', False))


def bound_json(path, proof):
    value, sha = read_json(path)
    if proof != {'path': str(path), 'sha256': sha}:
        raise ValueError('retained Honcho file differs from its original binding')
    return value


def _honcho_response_binding(root, scope, scope_sha, row, role):
    from pacbench.workflows.progress import operation_id

    context = row.get('dispatch_context', {})
    return (usable_response(row) and row.get('workflow_root') == str(root)
            and row.get('workflow_capability') == {'path': str(root/'workflow-scope.json'), 'sha256': scope_sha}
            and row.get('project') == scope['projects']['honcho'][role]
            and context.get('attempt_id') == row['id'] and context.get('run_id') == scope['run_id']
            and context.get('operation_id') == operation_id(row))


def _honcho_ingest_response(root, scope, scope_sha, rows, attempt, ready, sealed):
    if 'honcho_wire_identity' in attempt:
        from pacbench.workflows.progress import OwnershipConflict

        if not _honcho_response_binding(root, scope, scope_sha, attempt, 'memory'):
            return False
        try:
            completed = _source_completion(scope, rows, ready['release'], sealed['queue_id'])
        except OwnershipConflict:
            return False
        if completed != attempt:
            return False
        if attempt['state'] == 'receipt_missing':
            return True

    receipt = attempt.get('billing_receipt') or {}
    receipt_path = root/'billing-receipts'/Path(receipt.get('relative_path', '')).name
    proof, receipt_sha = read_json(receipt_path)
    return (attempt['state'] == 'settled' and attempt['http_status'] == 200
            and receipt.get('relative_path') == str(receipt_path.relative_to(root))
            and receipt_sha == receipt.get('sha256') and proof.get('attempt_id') == attempt['id']
            and proof.get('generation_id') == attempt.get('upstream_request_id')
            and 'cost' in proof.get('usage', {}))


def _honcho_answer_response(root, scope, scope_sha, row, request, final_text=None):
    if 'honcho_wire_identity' not in row:
        return row['state'] == 'settled' and row['http_status'] == 200
    from pacbench.evidence_store import binding_digest as digest

    if not (_honcho_response_binding(root, scope, scope_sha, row, 'driver')
            and row.get('request_sha256') == digest(request)):
        return False
    if final_text is None:
        return True
    evidence = row['response_validation']
    return (evidence.get('finish_reasons') == ['stop']
            and evidence.get('assistant_trimmed_text_sha256') == hashlib.sha256(final_text.strip().encode()).hexdigest())


def _honcho_releases(root, scope, scope_sha, documents, rows):
    """Check the exact daily file, stock upload record, and native completion."""
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
        if (sealed['at'] != doc.cutoff or source['sha256'] != doc.sha256
                or source['file']['filename'] != doc.filename or source['meta'] != {'filename': doc.filename}
                or source['session'] != scope['session']
                or source['public_id'] in native_ids or sealed['queue_id'] in queue_ids
                or slot.get('honcho_daily_release') != ready['release'] or slot.get('at') != doc.cutoff
                or slot.get('kind') != 'ingest'
                or not _honcho_ingest_response(root, scope, scope_sha, rows, attempt, ready, sealed)):
            raise ValueError('Honcho native readiness is outside the original file or verified ingest attempt')
        native_ids.add(source['public_id'])
        queue_ids.add(sealed['queue_id'])
        ready_bindings.append({'path': str(ready_file), 'sha256': ready_sha})
    return ready_bindings
