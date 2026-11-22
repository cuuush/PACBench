"""Authorize the frozen Week only while its stock account and Hermes owner are live."""
from contextlib import contextmanager
from dataclasses import asdict
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path

from .canary_capability import sha
from .dispatch_ownership import OwnershipConflict
from .live_dispatch import CONDITION, process_identity
from .observational_receipts import COHORT_STOP, RunReceiptJournal
from .openrouter_route import validate_request
from .supermemory_question_admission import PROTOCOL, check_driver_rounds, driver_slot, projects, terminal_response
from .supermemory_source_admission import PROFILE, PROVIDER, check_lock, physical_effect
from .supermemory_source_effect import BOUNDARY
from .workflow_progress import WorkflowProgress
from .workflow_spec import SPEC, instant

SCOPE = 'supermemory-stock-document-week-v1'
WEEK_RUN = 'supermemory-stock-week-v2'


@lru_cache(maxsize=1)
def plan():
    from .workflow_daily_documents import compile_daily_week

    _, docs, turns, basis, contract = compile_daily_week()
    return docs, turns, basis, contract


def validate(scope, bound):
    root = Path(scope['root'])
    campaign = root.parent.parent
    if (root != campaign / WEEK_RUN / PROVIDER or root != root.resolve()
            or Path(bound['path']) != root / 'workflow-scope.json' or scope['run_id'] != WEEK_RUN
            or scope['provider'] != PROVIDER or scope['protocol'] != PROTOCOL or scope['projects'] != projects(root)
            or scope['spec_sha256'] != sha(SPEC) or scope['frozen_inputs'] != plan()[2]
            or scope['contract'] != plan()[3] or scope['canary'] is not False
            or scope['namespace'] != f'supermemory-data-{WEEK_RUN}-{PROVIDER}' or scope['native_effect'] != BOUNDARY
            or os.environ.get('PACBENCH_STOP_FILE') != str(root / 'STOP')
            or os.environ.get(COHORT_STOP) != str(campaign / 'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != CONDITION
            or os.environ.get('PACBENCH_OFFLINE_CONFORMANCE') or os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY')):
        raise ValueError('Unbound Supermemory Week scope')
    check_lock(scope, root)


@contextmanager
def progress_reader(scope):
    from .workflow_capability import ENV

    directory = Path(scope['root']) / 'workflow'
    metadata = json.loads((directory / 'run.json').read_text())
    identity = metadata['identity']
    if (identity['account'] != scope['root'] or identity['source_basis'] != scope['frozen_inputs']
            or identity['spec_sha256'] != scope['spec_sha256'] or identity['evidence_kind'] != 'native'
            or identity['canary'] is not False):
        raise OwnershipConflict('Supermemory durable Week belongs to a different account or plan')
    with WorkflowProgress(directory, metadata['run_key'], read_only=True) as progress:
        if (progress.binding('workflow_scope') != json.loads(os.environ[ENV])
                or progress.binding('identity') != identity or not progress.binding('plan_registered')):
            raise OwnershipConflict('Supermemory Week is outside the registered durable plan')
        yield progress


def active_answer(scope):
    docs, turns, _, _ = plan()
    with progress_reader(scope) as progress:
        ordinal = progress.next_ordinal()
        if ordinal is None:
            raise OwnershipConflict('Supermemory Week has no pending authored question')
        turn = turns[ordinal]
        prior = progress.latest_surface(turn.session, before=ordinal)
        sid = prior['session_id'] if prior else None
        claim = progress.active_effect('answer', turn.id, dict(at=turn.at, prompt=turn.prompt, session_id=sid))
        if claim is None or claim['role'] != 'driver':
            raise OwnershipConflict('Supermemory question lacks the active durable answer operation')
    expected = [doc for doc in docs if instant(doc.cutoff) <= instant(turn.at)]
    root = Path(scope['root'])
    inventory = json.loads((root / 'ready-inventory.json').read_text())
    if (inventory != dict(namespace=scope['namespace'], sources=[dict(name=d.filename, sha256=d.sha256,
                          cutoff=d.cutoff) for d in expected]) or not expected):
        raise OwnershipConflict('Supermemory stock inventory differs from this question’s eligible prefix')
    return turn, prior, claim, expected, [t.prompt for t in turns[:ordinal]]


def prepare_driver(scope, prompt, at, session_id):
    from .workflow_native import trace_position

    if process_identity() != scope['controller']:
        raise OwnershipConflict('Only the owned controller may prepare a Supermemory question')
    turn, prior, claim, _, _ = active_answer(scope)
    if (prompt, at, session_id) != (turn.prompt, turn.at, prior['session_id'] if prior else None):
        raise OwnershipConflict('Supermemory driver submission differs from the frozen question or native session')
    root = Path(scope['root'])
    path = root / 'answer-admissions' / (claim['attempt_id'] + '.json')
    with path.open('x') as stream:
        json.dump(dict(claim=claim, cursor=trace_position(root / 'reuse-context.jsonl')), stream)
        stream.flush()
        os.fsync(stream.fileno())


def answer_slot(scope, body):
    turn, prior, claim, expected, public_prompts = active_answer(scope)
    path = Path(scope['root']) / 'answer-admissions' / (claim['attempt_id'] + '.json')
    start = json.loads(path.read_text())
    if path.resolve() != path or start['claim'] != claim:
        raise OwnershipConflict('Supermemory question observation belongs to a different durable operation')
    return {**driver_slot(scope, body, turn, prior=prior, cursor=start['cursor'],
                          prefix=[d.filename for d in expected], public_prompts=public_prompts, documents=plan()[0]),
            'supermemory_week_claim': claim}


def memory_slot(scope, body=None):
    from .workflow_runner import inspect_source_cutoff

    effect = physical_effect(scope)
    root = Path(scope['root'])
    docs, turns, _, _ = plan()
    ident = effect.document_id
    path = root / 'source-membership' / (ident + '.json')
    if path.is_file() and path.resolve() == path:
        entry = json.loads(path.read_text())
        doc = next((d for d in docs if d.filename == entry.get('public_id')), None)
        if doc is None or entry != dict(native_id=ident, public_id=doc.filename, submitted_sha256=doc.sha256,
                                        cutoff=doc.cutoff, namespace=scope['namespace']):
            raise OwnershipConflict('Supermemory update lacks its exact accepted source document')
        slot = dict(kind='ingest', batch_id=doc.cutoff)
    else:
        path = root / 'capture-membership' / (ident + '.json')
        if not path.is_file() or path.resolve() != path:
            raise OwnershipConflict('No accepted source or verified capture for the native Supermemory update')
        entry = json.loads(path.read_text())
        if entry['native_id'] != ident or entry['namespace'] != scope['namespace'] or entry['revision'] != 1:
            raise OwnershipConflict('Supermemory cannot bind this native update to a unique captured revision')
        with progress_reader(scope) as progress:
            verified = progress.get_verified(entry['question_id'])
            turn, surface, _ = verified or (None, {}, None)
            ordinal = progress.next_ordinal()
            pending_turn = turns[ordinal] if ordinal is not None else None
            if (turn is None or surface['session_id'] != entry['session_id']
                    or surface['write_barrier']['readback'] != entry['readback']
                    or pending_turn and pending_turn.session == turn.session
                    and progress.active_effect('answer', pending_turn.id) is not None):
                raise OwnershipConflict('Supermemory mutable capture lacks an unambiguous native append')
        slot = dict(kind='background', question_id=entry['question_id'])
    if body is not None:
        validate_request(body, profile=PROFILE)
        names = {json.loads(f.read_text())['public_id'] for f in (root / 'source-membership').glob('*.json')}
        inspect_source_cutoff({'model_requests': [body], 'prefetch': None}, docs, names, [])
    return dict(provider=PROVIDER, stock_source_effect=asdict(effect), **slot)


def admit(scope, project, body):
    from .direct_accounting import stop_measured_admission
    from .supermemory_source_qualify import check_stops
    from .workflow_capability import ENV

    try:
        check_stops(Path(scope['root']).parent.parent)
        slot = (answer_slot(scope, body) if project == scope['projects'][PROVIDER]['driver']
                else memory_slot(scope, body))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        stop_measured_admission('supermemory_week_unverified_' + ('driver' if project.endswith('-driver') else 'native_effect'))
        raise
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                projects=[project], slot=slot)


def verified_final_response(row, answer):
    # The actual decoded answer can independently verify a final round. This
    # does not represent missing HTTP/SSE framing as observed.
    validation = row.get('response_validation') or {}
    return (row['http_status'] == 200 and row.get('finished') and 'stop' in validation.get('finish_reasons', ())
            and not validation['envelope_errors'] and not validation['wire_parse_errors']
            and validation['assistant_text_sha256'] == sha256(answer.encode()).hexdigest())


class WeekJournal(RunReceiptJournal):
    def check_prepared(self, rows, row):
        from .direct_accounting import stop_measured_admission

        try:
            slot = row['workflow_slot']
            prior = list(rows.values())
            generations = [r['upstream_request_id'] for r in prior if r.get('upstream_request_id')]
            if len(generations) != len(set(generations)):
                raise OwnershipConflict('A Supermemory Week receipt duplicates a generation')
            if 'supermemory_week_claim' in slot:
                drivers = [r for r in prior if r.get('workflow_slot', {}).get('question_id') == slot['question_id']
                           and r['accounting_role'] == 'hermes']
                if any(not r.get('finished') or not terminal_response(r) for r in drivers):
                    raise OwnershipConflict('Supermemory tool round lacks a witnessed terminal model response')
                if any(r['workflow_slot']['supermemory_week_claim'] != slot['supermemory_week_claim'] for r in drivers):
                    raise OwnershipConflict('Earlier Supermemory operation already prepared this question')
                check_driver_rounds(drivers, slot)
            else:
                update_id = slot['stock_source_effect']['update_id']
                if any((r['workflow_slot'].get('stock_source_effect') or {}).get('update_id') == update_id for r in prior):
                    raise OwnershipConflict('Supermemory durable update already prepared; no automatic replay')
            if any(r.get('finished') and r['accounting_role'] == 'memory_provider' and not terminal_response(r)
                   for r in prior):
                raise OwnershipConflict('A native Supermemory worker response remains uncertain')
        except OwnershipConflict:
            stop_measured_admission('supermemory_week_receipt_or_replay_refused')
            raise

    def mark_dispatch_possible(self, identifier, *, sender=None):
        if sender is not None:
            from .workflow_capability import load

            row = next(r for r in self.snapshot() if r['id'] == identifier)
            if row['workflow_slot'] != memory_slot(load()) or sender != process_identity(os.getppid()):
                raise OwnershipConflict('Supermemory native worker changed before dispatch')
        super().mark_dispatch_possible(identifier, sender=sender)

    def retry_delay(self, identifier):
        return None
