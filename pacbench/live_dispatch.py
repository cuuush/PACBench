"""Opt-in direct dispatch evidence in the original atomic reservation ledger.

This records new sender identity, not a supervisor fencing/takeover protocol.
It never imports legacy owners, permits retries, releases holds or admits work.
The staged SQLite journal remains offline and disconnected from dispatch.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .dispatch_ownership import AttemptContext, OwnershipConflict

ENV = 'PACBENCH_DISPATCH_TRACKING'
CONDITION = 'direct-process-evidence-v1'


def enabled():
    value = os.environ.get(ENV)
    if value not in (None, '', CONDITION):
        raise OwnershipConflict('Unknown dispatch tracking condition')
    return value == CONDITION


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    if type(pid) is not int or pid <= 1:
        raise OwnershipConflict('Invalid dispatch process')
    folder = Path('/proc') / str(pid)
    fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in {'Z', 'X'}:
        raise OwnershipConflict('Dispatch process already ended')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return {'pid': pid, 'start_ticks': int(fields[19]), 'boot_id': boot,
            'uid': folder.stat().st_uid}


def sender_identity(supplied=None):
    # The Node transport invokes a short local Python helper. It may name only
    # its actual immediate parent, checked against boot and process start time.
    if supplied is None:
        return process_identity()
    if supplied.get('pid') != os.getppid() or supplied != process_identity(os.getppid()):
        raise OwnershipConflict('Accounting helper sender identity differs')
    return supplied


_fresh = ContextVar('new_workflow_effect', default=None)


@contextmanager
def fresh_effect(progress, phase, key, attempt):
    """Only the newly inserted external callback receives this transient execution context."""
    token = _fresh.set((progress, phase, key, attempt))
    try:
        yield
    finally:
        _fresh.reset(token)


def owns_fresh_effect(progress, claim):
    return _fresh.get() == (progress, claim['phase'], claim['key'], claim['attempt_id'])


def operation_id(row):
    slot = row.get('workflow_slot') or row.get('checkpoint_full_run_slot') or row.get('checkpoint_canary_slot') or {}
    scope = {k:slot[k] for k in ('kind','provider','group_id','batch_id','question_id') if k in slot}
    effect = slot.get('checkpoint_effect')
    if effect is not None and slot.get('provider') == 'memcal' and row.get('workflow_slot'):
        scope.update(checkpoint_effect=effect, project=row['project'])
        if slot.get('native_work') is not None:
            scope['native_work'] = slot['native_work']
    if (slot.get('provider')=='hindsight' and row.get('workflow_slot') and slot.get('native_work')
            and row.get('accounting_role')=='memory_provider'):
        scope['native_work'] = slot['native_work']
    return hashlib.sha256(json.dumps({'request':row['request_sha256'],'scope':scope},sort_keys=True).encode()).hexdigest()


def attach(row, prior_retry=None, sender=None):
    if not enabled():
        return
    owner = sender_identity(sender)
    slot = row.get('workflow_slot') or row.get('checkpoint_full_run_slot') or row.get('checkpoint_canary_slot') or {}
    role = row.get('accounting_role')
    phase = ('judging' if role == 'judge' else 'answering' if role == 'hermes' else
             'ingest' if slot.get('kind') == 'ingest' else 'background')
    run = row.get('funded_run_id') or 'direct-unscoped'
    arm = slot.get('provider') or 'unattributed'
    instance = f"linux:{owner['boot_id']}:{owner['pid']}:{owner['start_ticks']}"
    retry = None
    number = 1
    authorization = None
    if prior_retry is not None:
        old = prior_retry.get('dispatch_context')
        if not old:
            raise OwnershipConflict('Unknown legacy dispatch owner cannot be imported')
        retry = old['attempt_id']
        number = old['attempt_number'] + 1
        authorization = hashlib.sha256(str(prior_retry['authorized_retry']).encode()).hexdigest()
    context = AttemptContext(run, arm, phase, operation_id(row), row['id'], number,
        row['project'], instance, row['id'], row['reserved'], retry, authorization)
    row.update(dispatch_tracking_condition=CONDITION, dispatch_owner=owner,
               dispatch_context=asdict(context), dispatch_events=[{'kind': 'prepared',
               'at': row['started']}], dispatch_execution='prepared')


def check_owner(row, sender=None):
    if not row.get('dispatch_context'):
        if enabled():
            raise OwnershipConflict('Unknown legacy dispatch owner; transport callback refused')
        return False
    if row.get('dispatch_tracking_condition') != CONDITION:
        raise OwnershipConflict('Unknown dispatch evidence condition')
    if row['dispatch_owner'] != sender_identity(sender):
        raise OwnershipConflict('Dispatch process changed or PID reused; no takeover')
    return True


def mark_possible(row, at, sender=None):
    if not check_owner(row, sender):
        return
    slot = row.get('workflow_slot') or {}
    if slot.get('provider') == 'memcal' and slot.get('checkpoint_effect'):
        from .workflow_capability import confirm_memcal_sender
        confirm_memcal_sender(row)
    elif slot.get('provider') in ('mem0','hindsight','openviking','reme','cognee','memos') and slot.get('journal_stage_sha256'):
        from .workflow_capability import confirm_document_sender
        confirm_document_sender(row)
    elif slot.get('kind') == 'judge' and slot.get('checkpoint_effect'):
        from .workflow_capability import confirm_workflow_judge_sender
        confirm_workflow_judge_sender(row)
    if row['dispatch_execution'] != 'prepared':
        raise OwnershipConflict('Dispatch already possible; no automatic send replay')
    row['dispatch_events'].append({'kind': 'dispatch_possible', 'at': at})
    row['dispatch_execution'] = 'ambiguous'


def record_end(row, at, complete, sender=None):
    if not check_owner(row, sender):
        return
    if row['dispatch_execution'] == 'prepared':
        raise OwnershipConflict('Response cannot precede durable dispatch boundary')
    kind = 'response_ended' if complete else 'local_disconnect'
    if row['dispatch_events'][-1]['kind'] != kind:
        row['dispatch_events'].append({'kind': kind, 'at': at})
    if complete:
        row['dispatch_execution'] = 'response_ended'
