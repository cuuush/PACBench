"""Summarize retained request receipts for a native workflow."""
from decimal import Decimal
import json


def receipt_summary(root, rows, run_id, *, attempts=None):
    """Reconcile only receipts bound to this run, attempt and upstream generation."""
    from .response_receipts import PROTOCOL
    from .canary_capability import binding, read, sha
    from .live_dispatch import operation_id

    capability = binding(root/'workflow-scope.json')
    scope = read(capability)
    if scope['root'] != str(root) or scope['run_id'] != run_id:
        raise ValueError('foreign receipt attribution')
    generations = {}
    phases = {}
    missing = []
    for row in rows:
        ctx = row['dispatch_context']
        if (ctx['run_id'] != run_id or ctx['attempt_id'] != row['id'] or ctx['arm_id'] != root.name
                or row['workflow_root'] != str(root) or row['workflow_capability'] != capability
                or row['project'] not in scope['projects'][root.name].values()
                or ctx['operation_id'] != operation_id(row)):
            raise ValueError('foreign receipt attribution')
        if attempts is not None:
            attempts[row['id']] = dict(actual_usd=None, retry_of=row.get('retry_of'), operation_id=ctx['operation_id'],
                                       phase=ctx['phase'], attempt_state=row.get('state'))
        item = row.get('billing_receipt')
        if row.get('state') != 'settled' or not item:
            missing.append(row['id'])
            continue
        path = root/item['relative_path']
        if path != path.resolve() or path.parent != root/'billing-receipts' or sha(path) != item['sha256']:
            raise ValueError('incorrect billing artifact')
        receipt = json.loads(path.read_text())
        generation = receipt['generation_id']
        cost = Decimal(str(receipt['usage']['cost']))
        if (receipt['protocol'] != PROTOCOL or receipt['attempt_id'] != row['id']
                or not generation or generation != row['upstream_request_id']
                or cost != Decimal(str(row['settled_charge_usd'])) or not cost.is_finite() or cost < 0):
            raise ValueError('invalid generation receipt')
        if attempts is not None:
            attempts[row['id']].update(actual_usd=str(cost), generation_id=generation, receipt_sha256=item['sha256'])
        identity = (cost, ctx['phase'], row['project'], ctx['operation_id'])
        if generation in generations:
            if generations[generation] != identity:
                raise ValueError('upstream generation has conflicting attribution')
            continue
        generations[generation] = identity
        phases[ctx['phase']] = phases.get(ctx['phase'],Decimal(0))+cost
    total = sum(phases.values(),Decimal(0))
    return dict(attempts=len(rows),generations=len(generations),missing_attempt_ids=missing,
                receipted_usd=str(total) if generations else None,
                complete_cost_usd=str(total) if generations and not missing else None,
                receipted_cents=str((100*total).quantize(Decimal('.01'))) if generations else None,
                retried_attempts=sum(bool(r.get('retry_of')) for r in rows),
                by_phase_usd={k:str(v) for k,v in phases.items()})
