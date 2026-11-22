"""One stock Hermes question on the closed, qualified Supermemory source prefix."""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from .canary_capability import binding, digest, read, sha
from .dispatch_ownership import OwnershipConflict
from .live_dispatch import process_identity
from .observational_receipts import RunReceiptJournal
from .openrouter_route import validate_request
from .supermemory_source_admission import (
    BINARY_SHA, PROFILE, PROVIDER, SCOPE as SOURCE, SourceJournal, check_lock, source_release,
)
from .supermemory_source_effect import BOUNDARY, NATIVE_ID, check_update_history
from .supermemory_week_observation import stock_temporal_metadata
from .workflow_spec import SPEC, instant
from .workflow_tracks import HERMES_TOOLS

SCOPE = 'supermemory-stock-first-question-v1'
PROTOCOL = 'pizza-week-conversation-v2'
SOURCE_RUN = 'supermemory-source-qualification-metadata-v2'
QUESTION_RUN = 'supermemory-first-question-v1'
# Exact ten offered schemas from the pinned Hermes and stock catalog runtime.
TOOLS_SHA = 'a9939718a03f3db23c4b548b340a16b00a30011c2801232024799d32db976924'
SOURCE_FILES = ('workflow-scope.json', 'ready-source-proof.json', 'result.json',
                'native-service-stop.json', 'native-server-setup.json', 'acceptances.jsonl', 'receipts.jsonl')


def original_source(campaign):
    from .workflow_capability import schedule

    first, documents, basis = source_release()
    _, turns, _ = schedule(sha(SPEC), False)
    turn = turns[0]
    old = campaign / SOURCE_RUN / PROVIDER
    namespace = f'supermemory-data-{SOURCE_RUN}-supermemory'
    if (old.resolve() != old or turn.id != 'rsvp-before'
            or not instant(first[0].cutoff) < instant(turn.at) < instant(documents[len(first)].cutoff)):
        raise ValueError('Supermemory first question or source chronology differs')
    inputs = {name: binding(old / name) for name in SOURCE_FILES}
    if any(Path(item['path']) != old / name for name, item in inputs.items()):
        raise ValueError('Supermemory original evidence is outside the qualified run')
    scope, proof, result, shutdown, setup = [read(inputs[name]) for name in SOURCE_FILES[:5]]
    if ((scope['root'], scope['run_id'], scope['admission'], scope['namespace'], scope['spec_sha256'],
         scope['frozen_inputs'], scope['cutoff']) != (str(old), SOURCE_RUN, SOURCE, namespace, sha(SPEC), basis, first[0].cutoff)
            or (result['state'], result['cleanup'], result['questions_sent'], result['billing']['attempts'])
                != ('stock-first-release-processed', 'verified-inventory-stopped', 0, 0)
            or (old / 'receipts.jsonl').stat().st_size or result['release'] != proof
            or shutdown['state'] != 'verified-inventory-stopped' or shutdown['remaining_processes']
            or setup['installation']['sha256'] != BINARY_SHA or setup.get('resumed_store') is not False
            or (proof['condition'], proof['namespace'], proof['cutoff'])
                != ('stock-document-processing-v1', namespace, first[0].cutoff)):
        raise ValueError('No closed, zero-dispatch stock Supermemory source qualification')
    expected = {doc.filename: doc for doc in first}
    if proof['documents'].keys() != expected.keys() or jsonl(old / 'acceptances.jsonl') != [
            dict(id=doc.filename, cutoff=doc.cutoff, sha256=doc.sha256) for doc in first]:
        raise ValueError('Supermemory source proof differs from the admitted first release')
    for name, doc in expected.items():
        row = proof['documents'][name]
        data = doc.text.encode('utf-8')
        size = len(data)
        if (row['status'] != 'done' or row['sha256'] != doc.sha256 or row['original_bytes'] != size
                or row['native_revision'] != 1 or row['metadata'] != stock_temporal_metadata(doc.cutoff)
                or row['source_projection'] != 'single-final-lf-omitted' or not data.endswith(b'\n')
                or row['canonical_bytes'] != size - 1 or row['removed_trailing_lf_bytes'] != 1
                or row['returned_sha256'] != sha256(data[:-1]).hexdigest()):
            raise ValueError('Supermemory original source identity, canonical bytes or revision differs')
    membership = {}
    for path in (old / 'source-membership').glob('*.json'):
        row = read(binding(path))
        native, public = row['native_id'], row['public_id']
        if (path.resolve() != path or path.stem != native or not NATIVE_ID.fullmatch(native)
                or native in membership.values() or public in membership or public not in expected
                or row != dict(native_id=native, public_id=public, submitted_sha256=expected[public].sha256,
                               cutoff=proof['cutoff'], namespace=namespace)):
            raise ValueError('Supermemory original native source membership differs')
        membership[public] = native
    if membership.keys() != expected.keys():
        raise ValueError('Supermemory original native source membership is incomplete')
    return dict(evidence=inputs, native_ids=membership), proof, turn


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def projects(root):
    prefix = 'pacbench-' + sha256(str(root).encode()).hexdigest()[:12] + '-supermemory'
    return {PROVIDER: {role: prefix + '-' + role for role in ('driver', 'memory')}}


def snapshot_identity(directory):
    if directory.resolve(strict=True) != directory or any(path.is_symlink() for path in directory.rglob('*')):
        raise ValueError('Supermemory snapshot contains a redirected store path')
    return {name: sha(directory / name) for name in ('data', 'instance-id')}


def verify_restored_snapshot(scope):
    root = Path(scope['root'])
    original, _, _ = original_source(root.parent.parent)
    old = root.parent.parent / SOURCE_RUN / PROVIDER / 'supermemory-data'
    if (original != scope['source'] or snapshot_identity(old) != scope['copied_snapshot']
            or snapshot_identity(root / 'supermemory-data') != scope['copied_snapshot']):
        raise ValueError('Supermemory retained and working snapshots differ before native startup')


def validate(scope, bound):
    from .live_dispatch import CONDITION
    from .observational_receipts import COHORT_STOP

    root = Path(scope['root'])
    campaign = root.parent.parent
    first, _, basis = source_release()
    if (root != campaign / QUESTION_RUN / PROVIDER or root != root.resolve()
            or Path(bound['path']) != root / 'workflow-scope.json' or scope['run_id'] != QUESTION_RUN
            or scope['provider'] != PROVIDER or scope['protocol'] != PROTOCOL or scope['projects'] != projects(root)
            or scope['spec_sha256'] != sha(SPEC) or scope['frozen_inputs'] != basis or scope['cutoff'] != first[0].cutoff
            or scope['namespace'] != f'supermemory-data-{SOURCE_RUN}-supermemory' or scope['native_effect'] != BOUNDARY
            or os.environ.get('PACBENCH_STOP_FILE') != str(root / 'STOP')
            or os.environ.get(COHORT_STOP) != str(campaign / 'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != CONDITION
            or os.environ.get('PACBENCH_OFFLINE_CONFORMANCE') or os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY')):
        raise ValueError('Unbound Supermemory first-question scope')
    check_lock(scope, root)


def ready(scope):
    root = Path(scope['root'])
    original, proof, turn = original_source(root.parent.parent)
    if original != scope['source']:
        raise ValueError('Supermemory frozen source evidence changed')
    setup = read(binding(root / 'native-server-setup.json'))
    service = process_identity(setup['pid'])
    observed = read(binding(root / 'question-ready-source.json'))
    if (setup['installation']['sha256'] != BINARY_SHA or setup['resumed_store'] is not True
            or service != setup['process_identity'] or observed != dict(
                source=original, process=service, proof=observed['proof'])):
        raise ValueError('Unbound live Supermemory source readback')
    compare_source(proof, observed['proof'])
    return turn


def compare_source(original, observed):
    # Dreaming may progress independently of the already-complete document processing.
    def processing(proof):
        return {**proof, 'documents': {
            name: {key: value for key, value in row.items() if key != 'dreaming_status'}
            for name, row in proof['documents'].items()}}

    if processing(original) != processing(observed):
        raise ValueError('Supermemory reopened source content, version or processing differs')


def driver_owner(scope, turn, prior=None):
    from .checkpoint_clock import ENV, audit_question, descriptor
    from .hermes_gateway import _identity

    root = Path(scope['root'])
    home, clock = root / 'hermes-home', root / 'reuse-clock'
    owner = read(binding(root / 'question-gateway.json'))
    sender = process_identity()
    if (owner['process'] != sender or owner['controller'] != scope['controller']
            or Path(os.environ.get(ENV, '')) != clock / 'active.json' or os.environ.get('HERMES_HOME') != str(home)
            or os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE') != 'hermes-gateway'):
        raise OwnershipConflict('Supermemory request is not from the bound Hermes process')
    _identity(home, sender['pid'])
    d = descriptor()
    if (d['kind'] != ('continuation' if prior else 'fresh') or d['home'] != str(home)
            or d['checkpoint'] != scope['spec_sha256'] or d['at'] != turn.at
            or d['question_sha256'] != sha256(turn.prompt.encode()).hexdigest()):
        raise OwnershipConflict('Supermemory request is outside the scheduled question')
    native = read(binding(clock / 'bindings' / (d['nonce'] + '.json')))
    sid = native['session_id']
    if prior and (sid != prior['session_id'] or d.get('continued_session_id') != sid
                  or d.get('initial_at') != prior['conversation_times'][0]
                  or prior['gateway_identity'] != [sender['pid'], sender['start_ticks']]):
        raise OwnershipConflict('Supermemory continuation is outside its original native session')
    evidence = audit_question(clock, d, {'prompt': turn.prompt, 'session_id': sid}, owner['witness'])
    if (not evidence['native_admission'][-1].get('in_window') or native['nonce'] != d['nonce']
            or native['question_sha256'] != d['question_sha256']):
        raise OwnershipConflict('Supermemory native submission was not bound in time')
    return sid, d['nonce'], native['request_id']


def native_history(home, sid):
    db_path = home / 'state.db'
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            'SELECT role,content,api_content,tool_call_id,tool_calls,tool_name FROM messages '
            'WHERE session_id=? ORDER BY id', (sid,))]


def tool_rounds(wire, saved):
    from .workflow_runner import _tool_call_contract, _wire_tool_text

    names = HERMES_TOOLS[PROVIDER]
    if len(wire) != len(saved):
        raise ValueError('Supermemory tool loop differs from live Hermes history')
    remaining = {}
    groups = []
    for actual, native in zip(wire, saved, strict=True):
        role, ident = actual.get('role'), actual.get('tool_call_id')
        content = native.get('api_content') or native.get('content') or ''
        sent = actual.get('content') or ''
        if role != native['role'] or (content != sent and (role != 'tool' or not _wire_tool_text(content, sent))):
            raise ValueError('Supermemory tool loop changed native role or visible content')
        if role == 'assistant' and not remaining:
            calls = [_tool_call_contract(call) for call in actual.get('tool_calls', [])]
            stored = [_tool_call_contract(call, stored=True) for call in json.loads(native['tool_calls'] or '[]')]
            if not calls or calls != stored or any(call['function']['name'] not in names for call in calls):
                raise ValueError('Supermemory continuation lacks the native assistant call')
            ids = [call['id'] for call in calls]
            remaining = {call['id']: call['function']['name'] for call in calls}
            groups.append(ids)
        elif role == 'tool' and ident in remaining and remaining[ident] == native['tool_name'] and ident == native['tool_call_id']:
            del remaining[ident]
        else:
            raise ValueError('Unexpected Supermemory tool result or request grouping')
    ids = [ident for group in groups for ident in group]
    if remaining or not all(ids) or len(ids) != len(set(ids)):
        raise ValueError('Supermemory tool completion is missing or duplicated')
    return groups


def driver_slot(scope, body, turn, *, prior=None, cursor=Ellipsis, prefix=None, public_prompts=(), documents=None):
    from agent.memory_context import build_memory_context_block
    from agent.portal_tags import get_conversation_context
    from providers import get_provider_profile
    from .workflow_native import appended_rows
    from .workflow_runner import _tool_call_contract, _wire_tool_text, inspect_source_cutoff

    validate_request(body, profile=PROFILE)
    root = Path(scope['root'])
    sid, nonce, native_id = driver_owner(scope, turn) if prior is None else driver_owner(scope, turn, prior)
    affinity = get_provider_profile('openrouter').build_extra_body(session_id=sid)['session_id']
    if get_conversation_context() != sid or body.get('session_id') != affinity:
        raise OwnershipConflict('Supermemory request lacks stock routing affinity for the active Hermes chat')
    tools = sorted(body['tools'], key=lambda tool: tool['function']['name'])
    if ([tool['function']['name'] for tool in tools] != list(HERMES_TOOLS[PROVIDER]) or digest(tools) != TOOLS_SHA
            or body.get('stream') is not True or body.get('stream_options') != {'include_usage': True}
            or body.get('max_completion_tokens') != 32768 or body.get('reasoning') != {'enabled': True, 'effort': 'medium'}
            or body.keys() - {'model', 'provider', 'messages', 'tools', 'reasoning', 'session_id',
                              'stream', 'stream_options', 'max_completion_tokens'}):
        raise ValueError('Supermemory driver tool schemas or streaming condition differ')
    context = root / 'reuse-context.jsonl'
    observations = jsonl(context) if cursor is Ellipsis else appended_rows(context, cursor)
    hook = [row for row in observations if row['session_id'] == sid and row['query'] == turn.prompt]
    if len(hook) != 1 or not isinstance(hook[0]['prefetch'], str):
        raise ValueError('Supermemory opening request lacks the actual stock prefetch observation')
    native_block = build_memory_context_block(hook[0]['prefetch'])
    user = turn.prompt + ('\n\n' + native_block if native_block else '')
    messages = body['messages']
    previous = prior['history'] if prior else []
    boundary = len(previous) + 1
    if (len(messages) <= boundary or messages[0]['role'] != 'system' or not isinstance(messages[0]['content'], str)
            or '# Project Context' in messages[0]['content'] or messages[boundary] != {'role': 'user', 'content': user}):
        raise ValueError('Supermemory driver question or model-visible context differs')
    history = native_history(root / 'hermes-home', sid)
    if len(history) < len(previous):
        raise ValueError('Supermemory native continuation lost prior history')
    for saved, live, sent in zip(previous, history[:len(previous)], messages[1:boundary], strict=True):
        role, content = saved['role'], saved.get('api_content') or saved.get('content') or ''
        delivered = sent.get('content') or ''
        stored_calls = json.loads(saved.get('tool_calls') or '[]')
        if (any(saved.get(k) != v for k, v in live.items()) or sent.get('role') != role
                or (delivered != content and not (role == 'user' and delivered == saved['content'])
                    and not (role == 'tool' and _wire_tool_text(content, delivered)))
                or (role == 'tool' and sent.get('tool_call_id') != saved['tool_call_id'])
                or [_tool_call_contract(c, stored=True) for c in stored_calls]
                    != [_tool_call_contract(c) for c in sent.get('tool_calls') or []]):
            raise ValueError('Supermemory continued request differs from verified prior native history')
    current = history[len(previous):]
    if current and (current[0]['role'] != 'user' or current[0]['content'] != turn.prompt
                    or (current[0]['api_content'] or turn.prompt) != user):
        raise ValueError('Supermemory wire question differs from the native user row')
    groups = tool_rounds(messages[boundary+1:], current[1:])
    first, all_docs, _ = source_release()
    inspect_source_cutoff({'model_requests': [body], 'prefetch': hook[0]['prefetch']}, all_docs if documents is None else documents,
                          [doc.filename for doc in first] if prefix is None else prefix,
                          [*public_prompts, turn.prompt])
    return dict(provider=PROVIDER, kind='answer', question_id=turn.id, supermemory_first_question=dict(
        session_id=sid, turn_nonce=nonce, native_request_id=native_id, tool_rounds=groups))


def admit(scope, project, body):
    from .direct_accounting import stop_measured_admission
    from .supermemory_source_admission import admit as admit_source
    from .supermemory_source_qualify import check_stops
    from .workflow_capability import ENV

    try:
        check_stops(Path(scope['root']).parent.parent)
        turn = ready(scope)
        if project == scope['projects'][PROVIDER]['memory']:
            return admit_source(scope, project, body)
        slot = driver_slot(scope, body, turn)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        stop_measured_admission('supermemory_first_question_unverified_operation')
        raise
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=slot, projects=[project])


def confirm_sender(row, scope, *, slot_factory=None):
    from .api_input_trace import rows
    from .supermemory_source_qualify import check_stops

    root = Path(scope['root'])
    check_stops(root.parent.parent)
    matches = [r for r in rows(root / 'api-inputs.jsonl') if r['request_id'] == row['id']]
    if len(matches) != 1:
        raise OwnershipConflict('No unique Supermemory prepared request capture')
    sent = matches[0]
    owner = row['dispatch_owner']
    slot = row['workflow_slot']
    if (sent['protocol'] != 'workflow-driver-input-v1' or sent['project'] != row['project']
            or (sent['pid'], sent['process_start_ticks']) != (owner['pid'], owner['start_ticks'])
            or sent['turn_nonce'] != slot['supermemory_first_question']['turn_nonce']
            or sent['canonical_request_sha256'] != row['request_sha256']
            or digest(sent['request']) != row['request_sha256']):
        raise OwnershipConflict('Supermemory prepared request changed before dispatch')
    current = (driver_slot(scope, sent['request'], ready(scope)) if slot_factory is None
               else slot_factory(scope, sent['request']))
    if current != slot:
        raise OwnershipConflict('Supermemory native operation changed before dispatch')


def terminal_response(row):
    validation = row['response_validation']
    return (row['http_status'] == 200 and not validation['envelope_errors'] and not validation['wire_parse_errors']
            and (row['dispatch_execution'] == 'response_ended'
                 or (row['dispatch_execution'] == 'ambiguous' and validation.get('sse_done_observed') is True
                     and row['state'] == 'settled'
                     and bool(row.get('upstream_request_id')) and bool(validation['finish_reasons']))))


def check_driver_rounds(drivers, slot):
    current = slot['supermemory_first_question']
    sequence = current['tool_rounds']
    if len(drivers) != len(sequence):
        raise OwnershipConflict('Supermemory opening or tool round already prepared, or round skipped')
    if drivers:
        prior = drivers[-1]
        previous = prior['workflow_slot']['supermemory_first_question']
        if (not prior.get('finished') or previous != {**current, 'tool_rounds': sequence[:-1]}
                or 'tool_calls' not in prior['response_validation']['finish_reasons']):
            raise OwnershipConflict('Supermemory continuation lacks an unambiguous preceding tool response')


class QuestionJournal(SourceJournal):
    def check_prepared(self, rows, row):
        from .direct_accounting import stop_measured_admission
        from .supermemory_source_effect import SourceEffect

        try:
            drivers = [r for r in rows.values() if r['accounting_role'] == 'hermes']
            self.check_history(rows.values())
            slot = row['workflow_slot']
            if 'stock_source_effect' in slot:
                check_update_history((r for r in rows.values() if r['accounting_role'] == 'memory_provider'),
                                     SourceEffect(**slot['stock_source_effect']))
            else:
                check_driver_rounds(drivers, slot)
        except OwnershipConflict:
            stop_measured_admission('supermemory_first_question_receipt_or_replay_refused')
            raise

    @staticmethod
    def check_history(rows):
        successful = [row for row in rows if row.get('finished')]
        generations = [row.get('upstream_request_id') for row in successful if row.get('upstream_request_id')]
        if len(generations) != len(set(generations)) or any(not terminal_response(row) for row in successful):
            raise OwnershipConflict('Supermemory prior request did not settle an unambiguous response')

    def mark_dispatch_possible(self, identifier, *, sender=None):
        from .workflow_capability import load

        if sender is not None:
            # The source worker helper rechecks its exact physical update before dispatch.
            ready(load())
            return super().mark_dispatch_possible(identifier, sender=sender)
        RunReceiptJournal.mark_dispatch_possible(self, identifier)
