"""Receipt-bound, first-release stock Supermemory source admission.

Assistant turns, tool requests, chat writes and later source releases are
outside this scope. Missing attribution closes the gate before transport.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path

from ...canary_capability import sha
from ...dispatch_ownership import OwnershipConflict
from ...live_dispatch import CONDITION, process_identity
from ...observational_receipts import COHORT_STOP, RunReceiptJournal
from ...openrouter_route import validate_request
from .source_effect import BOUNDARY, NATIVE_ID, SourceEffect, check_update_history
from pacbench.campaigns.pizza_week import SPEC, compile_week, cache_bound_schedule

SCOPE = 'supermemory-stock-source-qualification-v1'
PROVIDER = 'supermemory'
PROFILE = 'luna-bedrock-global'
BINARY_SHA = 'd7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984'


@cache_bound_schedule(maxsize=1)
def source_release():
    _, documents, _, basis = compile_week('continuing-account')
    first = tuple(doc for doc in documents if doc.cutoff == documents[0].cutoff)
    if len(first) != 12 or sum(len(doc.source_ids) for doc in first) != 39:
        raise ValueError('Supermemory requires the first original v2 source release')
    return first, documents, basis


def check_lock(scope, root):
    from ...live_dispatch import holds_flock

    owner = scope['controller']
    pid, fd = owner['pid'], scope['lock_fd']
    lock = root.parent.parent/'execution.lock'
    if (type(fd) is not int or fd < 0 or process_identity(pid) != owner or lock.is_symlink()
            or not os.path.samestat(lock.stat(), Path(f'/proc/{pid}/fd/{fd}').stat())
            or not holds_flock(pid, fd)):
        raise OwnershipConflict('Supermemory source qualification requires the shared paid execution lock')


def validate(scope, bound):
    root = Path(scope['root'])
    campaign = root.parent.parent
    first, _, basis = source_release()
    if (root.name != PROVIDER or root != root.resolve() or Path(bound['path']) != root/'workflow-scope.json'
            or scope['provider'] != PROVIDER or scope['run_id'] != root.parent.name
            or scope['projects'] != {PROVIDER: {'memory':project_name(root)}}
            or scope['spec_sha256'] != sha(SPEC) or scope['frozen_inputs'] != basis
            or scope['cutoff'] != first[0].cutoff or scope['native_effect'] != BOUNDARY
            or scope['namespace'] != 'supermemory-data-'+root.parent.name+'-supermemory'
            or os.environ.get('PACBENCH_STOP_FILE') != str(root/'STOP')
            or os.environ.get(COHORT_STOP) != str(campaign/'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != CONDITION):
        raise ValueError('Unbound Supermemory source qualification')
    check_lock(scope, root)


def project_name(root):
    from hashlib import sha256
    return f'pacbench-{sha256(str(root).encode()).hexdigest()[:12]}-supermemory-memory'


def physical_effect(scope):
    root = Path(scope['root'])
    stop = root/'STOP'
    if stop.exists() or (root.parent.parent/'ACCOUNT_STOP').exists():
        raise OwnershipConflict('Supermemory source qualification is stopped')
    native = json.loads((root/'native-server-setup.json').read_text())
    pid = native['pid']
    if (os.environ.get('PACBENCH_SUPERMEMORY_STOCK_SOURCE') != '1' or os.getppid() != pid
            or native.get('dispatch_operation_boundary') != BOUNDARY
            or native['installation']['sha256'] != BINARY_SHA
            or process_identity(pid) != native.get('process_identity')):
        raise OwnershipConflict('No bound physical Supermemory worker')
    path = Path(f'/proc/{pid}/exe').resolve(strict=True)
    local = Path(__file__).resolve().parents[3]/'vendor/supermemory-local/supermemory-server-linux-x64'
    if path != local.resolve():
        raise OwnershipConflict('Supermemory worker differs from the pinned local binary')
    update = json.loads(os.environ.get('PACBENCH_SUPERMEMORY_EFFECT_WITNESS', 'null'))
    if not isinstance(update, dict):
        raise OwnershipConflict('No uniquely attributed stock Supermemory document update')
    ident = update.get('documentId', '')
    if not isinstance(ident, str) or not NATIVE_ID.fullmatch(ident):
        raise OwnershipConflict('Invalid stock Supermemory document identity')
    return SourceEffect.from_native(update, {ident})


def effect_for(scope):
    effect = physical_effect(scope)
    root = Path(scope['root'])
    ident = effect.document_id
    path = root/'source-membership'/f'{ident}.json'
    if path != path.resolve() or not path.is_file():
        raise OwnershipConflict('No native document binding to the admitted first release')
    entry = json.loads(path.read_text())
    first, _, _ = source_release()
    doc = next((item for item in first if item.filename == entry.get('public_id')), None)
    if (doc is None or entry != dict(native_id=ident, public_id=doc.filename, submitted_sha256=doc.sha256,
                                     cutoff=scope['cutoff'], namespace=scope['namespace'])):
        raise OwnershipConflict('Supermemory worker document binding differs from frozen source')
    return effect


def admit(scope, project, body):
    from pacbench.workflows.capability import ENV
    from pacbench.workflows.runner import inspect_source_cutoff

    from ...direct_accounting import stop_measured_admission

    validate_request(body, profile=PROFILE)
    try:
        effect = effect_for(scope)
    except OwnershipConflict:
        stop_measured_admission('supermemory_native_effect_unverified')
        raise
    root = Path(scope['root'])
    first, all_docs, _ = source_release()
    visible = {json.loads(path.read_text())['public_id'] for path in (root/'source-membership').glob('*.json')}
    prefix = [doc.filename for doc in first if doc.filename in visible]
    inspect_source_cutoff({'model_requests': [body], 'prefetch': None}, all_docs, prefix, [])
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=dict(kind='ingest', provider=PROVIDER, batch_id=scope['cutoff'],
                          stock_source_effect=asdict(effect)), projects=[project])


class SourceJournal(RunReceiptJournal):
    def check_prepared(self, rows, row):
        from ...direct_accounting import stop_measured_admission

        effect = SourceEffect(**row['workflow_slot']['stock_source_effect'])
        try:
            check_update_history(rows.values(), effect)
        except OwnershipConflict:
            stop_measured_admission('supermemory_duplicate_or_ambiguous_update')
            raise

    def mark_dispatch_possible(self, identifier, *, sender=None):
        from pacbench.workflows.capability import load

        scope = load()
        effect = effect_for(scope)
        row = next(row for row in self.snapshot() if row['id'] == identifier)
        if (row['workflow_slot']['stock_source_effect'] != asdict(effect)
                or sender != process_identity(os.getppid())):
            raise OwnershipConflict('Supermemory durable update identity changed before dispatch')
        super().mark_dispatch_possible(identifier, sender=sender)

    def retry_delay(self, identifier):
        return None
