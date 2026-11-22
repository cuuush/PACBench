"""One chronological account stream. Native effects and judge work have separate durable owners."""
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .provider_versions import MEMCAL_WEEK_VERSION
from .workflow_memcal_tools import manifest
from .workflow_runner import (
    Journal, captured_turn, confirm_source_only, qualify_account, read_unit, submit_unit,
)
from .workflow_spec import instant
from .workflow_capability import memcal_operation
from .workflow_tracks import eligibility


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@contextmanager
def event_log(directory, elapsed_start, *, lane, resume):
    # Each continuation gets its own append-only segment. A torn final JSONL
    # record from an interrupted writer cannot corrupt the following segment.
    name = 'workflow.jsonl' if lane == 'account' and not resume else f'{lane}-{uuid.uuid4().hex}.jsonl'
    journal = Journal(directory)
    with (directory / name).open('x', encoding='utf-8') as stream:
        journal.file = stream
        journal.started = time.monotonic() - max(0, time.time() - elapsed_start)
        try:
            yield journal
        except BaseException as error:
            try:
                journal.write('stopped', error=type(error).__name__, automatic_replay=False)
            except Exception as recording_error:
                error.add_note(f'Could not append stop evidence: {type(recording_error).__name__}')
            raise


def release(account, condition, docs, progress, journal, through):
    at = docs[0].cutoff
    identity = [(d.filename, d.sha256) for d in docs]

    def advance():
        observation = account.advance(at)
        journal.write('source-clock', requested=at, observation=observation)
        if observation.get('cutoff') != at or instant(observation['as_of']) != instant(at):
            raise ValueError('source clock differs from scheduled cutoff')
        instant(observation['service_at'])
        return observation

    progress.effect('source-clock', at, identity, advance)
    accepted = progress.effect('sync', at, identity, lambda: submit_unit(account, condition, docs, journal))
    observed = None
    if account.evidence_kind == 'native':
        def process():
            with memcal_operation(progress, 'dream', at, accepted):
                return account.process_sync()

        dream = progress.effect('dream', at, accepted, process, role='memory')
        # Inventory/cursor observation is read-only. Rechecking it must never
        # repeat collection or another Dream on the source store.
        observed = account.observe_sync(accepted, dream)
        read_unit(account, condition, docs, accepted, journal, through, observed=observed)
    else:
        progress.effect('recording-readback', at, accepted,
                        lambda: read_unit(account, condition, docs, accepted, journal, through))
    progress.advance_cutoff(at, native_ready=observed)


def validate_answer(surface, turn, prior, condition, plan, progress, account, inventory, cutoff):
    plan.check_inventory(surface['source_files'], cutoff)
    activity = captured_turn(surface, turn, prior, condition, inventory)
    ids = [r['request_id'] for r in surface['model_requests']]
    if account.evidence_kind == 'native':
        from .openrouter_route import validate_request
        for request in surface['model_requests']:
            body = request.get('request') or {}
            validate_request(body, profile='luna-bedrock-global')
            if body.get('messages') != request['messages']:
                raise ValueError('driver input differs from approved route')
    linked = {a['id'] for a in (surface.get('attempts') or [])
              if isinstance(a, dict) and isinstance(a.get('id'), str)}
    missing = sorted(set(ids) - linked)
    reused = progress.reused_request_ids(ids)
    activity['driver_receipts'] = {
        'observed_requests': len(ids), 'missing': missing, 'reused': reused,
        'coverage': 'incomplete' if (missing or reused or len(ids) != len(set(ids))
                                     or activity['attempt_lineage'].get('state') == 'invalid') else 'complete',
    }
    plan.verify_no_leak(surface, turn.at, turn.prompt)
    if surface['account'] != account.identity or progress.session_owner(surface['session_id']) not in (None, turn.session):
        raise ValueError('unexpected physical account or reused chat')
    progress.bind('gateway', surface['gateway_identity'])
    return activity


def process_turn(ordinal, turn, last_in_chat, account, condition, plan, progress, journal):
    verified = progress.get_verified(turn.id)
    if verified is None:
        previous = progress.latest_surface(turn.session, before=ordinal)
        session_id = previous['session_id'] if previous else None
        cutoff = progress.last_cutoff()
        inventory = account.source_inventory()
        plan.check_inventory(inventory, cutoff)
        inputs = dict(ordinal=ordinal, prompt=turn.prompt, at=turn.at, session=session_id, cutoff=cutoff)
        journal.write('pre-dispatch-inventory', question=turn.id, account=account.identity, source_files=inventory)

        def capture():
            journal.write('dispatch', question=turn.id, account=account.identity,
                          at=turn.at, prompt=turn.prompt, session=session_id)
            if account.evidence_kind == 'recording-fake':
                return account.ask(turn.prompt, at=turn.at, session_id=session_id)
            def save_return(value):
                progress.record_observation('answer', turn.id, 'cli-return', value)
            with memcal_operation(progress, 'answer', turn.id, inputs):
                return account.capture_answer(turn.prompt, at=turn.at, session_id=session_id, on_return=save_return)

        raw = progress.effect('answer', turn.id, inputs, capture, role='driver')
        # Native writes happen on the original turn. This observer only checks
        # their durable result. If it is interrupted, sampling again is safe.
        surface = progress.observation('answer', turn.id, 'native-write-readback')
        if surface is None:
            surface = raw if account.evidence_kind == 'recording-fake' else account.observe_answer(raw)
            progress.record_observation('answer', turn.id, 'native-write-readback', surface)
        surface['conversation_times'] = [*(previous['conversation_times'] if previous else ()), turn.at]
        activity = validate_answer(surface, turn, previous, condition, plan, progress, account, inventory, cutoff)
        progress.verified_turn(turn, surface, activity)
        journal.write('answer-visible', question=turn.id, measure=turn.measure, surface=surface)
        journal.write('verified-turn', question=turn.id, measure=turn.measure, activity=activity)
    else:
        _, surface, _ = verified
    if last_in_chat:
        session_id = surface['session_id']

        def close():
            started = time.monotonic()
            journal.write('chat-close-started', question=turn.id, session_id=session_id)
            with memcal_operation(progress, 'chat-close', turn.id, {'session_id':session_id}):
                proof = account.end_chat(session_id)
            if proof.get('session_id') != session_id or not proof.get('readback') or proof.get('pending_writes') != 0:
                raise ValueError('native chat close is unverified')
            journal.write('chat-closed', question=turn.id, evidence=proof, cleanup_s=time.monotonic()-started)
            return proof

        progress.effect('chat-close', turn.id, {'session_id':session_id}, close, role='memory')
    progress.mark_turn_done(turn.id)


def run_account_stream(condition, account, documents, turns, directory, *, spec_digest, source_basis,
                       all_source_documents=None, judge=None, canary=False, resume=False):
    from .workflow_progress import WorkflowProgress
    from .workflow_source_plan import SourcePlan

    if (condition.provider != 'memcal' or condition.track != 'first-party-connector'
            or condition.state != 'continuing-account' or set(condition.channels) != {'imessage', 'email'}
            or account.evidence_kind not in {'native','recording-fake'}):
        raise ValueError('incremental execution requires the continuing mixed MemCal connector')
    if judge is not None and account.evidence_kind != 'native':
        raise ValueError('recording fakes do not receive benchmark grades')
    route = eligibility(condition.provider, condition.track, condition.channels, version=condition.version)
    if not route['documented_for_scope']:
        raise ValueError('unsupported source route')
    deployment, store = qualify_account(account, condition, account.evidence_kind)
    directory = Path(directory).resolve()
    if not resume:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    elif not (directory/'run.json').is_file() or not (directory/'workflow.sqlite3').is_file():
        raise ValueError('no bound incremental run to resume')
    with SourcePlan(directory, documents, turns, all_source_documents=all_source_documents) as plan:
        if set(plan.channels) != set(condition.channels):
            raise ValueError('frozen dataset differs from source scope')
        meta = dict(protocol='memcal-incremental-v1', spec_sha256=spec_digest, source_basis=source_basis,
                    plan_sha256=plan.digest, account=account.identity, store=store, condition=asdict(condition),
                    canary=canary, evidence_kind=account.evidence_kind)
        if condition.provider == 'memcal' and condition.version == MEMCAL_WEEK_VERSION and account.evidence_kind == 'native':
            meta['product_manifest'] = {k:deployment[k] for k in manifest()}
        key = fingerprint(meta)
        if resume:
            saved = json.loads((directory/'run.json').read_text())
            if saved['run_key'] != key or fingerprint(saved['identity']) != key:
                raise ValueError('incremental account, store or frozen plan differs')
        else:
            saved = {'run_key':key, 'identity':meta, 'started_unix':time.time(), 'evidence_kind':account.evidence_kind}
            with (directory/'run.json').open('x') as stream:
                json.dump(saved, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        # Reopening an initialized arm must use its existing progress table. A
        # first process may have stopped between the identity file and schema
        # initialization; no external effect is admitted before this marker.
        marker = directory/'initialized.json'
        with WorkflowProgress(directory, key, create=not marker.exists()) as progress, progress.account_owner():
            if marker.exists():
                if json.loads(marker.read_text()) != {'run_key':key}:
                    raise ValueError('incremental initialization identity differs')
            else:
                from .gateway_reuse import atomic
                atomic(marker, {'run_key':key})
            for name, value in [('state', condition.state), ('evidence_kind', account.evidence_kind), ('identity', meta)]:
                progress.bind(name, value)
            if os.environ.get('PACBENCH_WORKFLOW_SCOPE'):
                progress.bind('workflow_scope', json.loads(os.environ['PACBENCH_WORKFLOW_SCOPE']))
            progress.bind('canary', canary)
            if not progress.binding('plan_registered'):
                for ordinal, turn, _ in plan.iter_turns():
                    progress.plan_turn(ordinal, turn)
                progress.bind('plan_registered', True)
            with ExitStack() as logs:
                journal = logs.enter_context(event_log(directory, saved['started_unix'], lane='account', resume=resume))
                if judge is not None:
                    from .workflow_grading import grade_verified
                    grade_log = logs.enter_context(event_log(directory, saved['started_unix'], lane='judging', resume=resume))
                journal.write('spec', sha256=spec_digest, source_basis=source_basis, canary=canary,
                              planned_turns=plan.turn_count, planned_criteria=plan.criteria_count)
                journal.write('condition', condition=asdict(condition), capabilities=route,
                              deployment_witness=deployment, evidence_kind=account.evidence_kind)
                if progress.binding('initial_account_verified'):
                    if account.evidence_kind == 'native':
                        # Restoration needs independent evidence of the same
                        # physical process, store, chats, and current upstream.
                        account.verify_resume(progress, plan)
                    elif progress.binding('gateway') is not None and account.gateway != progress.binding('gateway'):
                        raise ValueError('recording gateway identity changed')
                else:
                    if account.evidence_kind == 'native':
                        progress.bind('gateway', account.frontend.gateway_identity())
                    confirm_source_only(account, [], journal)
                    progress.bind('initial_account_verified', True)
                if account.evidence_kind == 'native' and os.environ.get('PACBENCH_WORKFLOW_SCOPE'):
                    from .workflow_capability import activate_memcal
                    activate_memcal(progress, account, condition, deployment, resume_plan=plan if resume else None)
                    if resume and judge is not None:
                        from .workflow_capability import load
                        from .live_dispatch import process_identity
                        from .workflow_grading import judge_only_owner
                        scope = load()
                        if scope['controller'] != process_identity():
                            logs.enter_context(judge_only_owner(progress, scope))
                start = progress.next_ordinal()
                if start is not None:
                    for ordinal, turn, last in plan.iter_turns(start):
                        for _, batch in plan.iter_nights(after_cutoff=progress.last_cutoff(), through=turn.at):
                            release(account, condition, batch, progress, journal, turn.at)
                        process_turn(ordinal, turn, last, account, condition, plan, progress, journal)
                        if judge is not None:
                            # Yield a single slot between native operations. A separate
                            # event log and evaluator context keep results out of Hermes.
                            grade_verified(progress, judge, directory, grade_log, limit=1)
                for _, batch in plan.iter_nights(after_cutoff=progress.last_cutoff()):
                    release(account, condition, batch, progress, journal, '9999-12-31T00:00:00+00:00')
                plan.check_inventory(account.source_inventory(), progress.last_cutoff())
                progress.seal_execution(plan.turn_count, plan.criteria_count)
                journal.write('account-complete', verified_turns=plan.turn_count)
                if judge is not None:
                    while grade_verified(progress, judge, directory, grade_log, limit=16):
                        pass
            return progress.summary()
