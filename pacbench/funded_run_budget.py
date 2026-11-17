"""Explicit prepaid run budget, separate from historical task forecasts/holds.

Historical rows stay intact. Every new request reserves its conservative bound
under one atomic run limit and freshly observed prepaid credit. No top-ups,
account mutations, key creation or provider fallback are implemented.
"""
import argparse
import hashlib
import json
import os
import re
import urllib.request
from decimal import Decimal
from .direct_accounting import money, now

ENV = 'PACBENCH_FUNDED_RUN_ID'


def credit_snapshot():
    from .openrouter_direct import active
    if active():
        # Node's reserve/settle helper deliberately runs with repository-only
        # PYTHONPATH. Install the free-read capability translation here too;
        # otherwise its local marker would be sent upstream as a real key.
        from .direct_http import install
        install()
    key=('pbdirect-budget-credit-probe' if active() else os.environ['OPENROUTER_API_KEY'])
    start=now();req=urllib.request.Request('https://openrouter.ai/api/v1/credits',headers={'Authorization':'Bearer '+key})
    with urllib.request.urlopen(req,timeout=15) as response:
        if response.status!=200:raise RuntimeError('Prepaid credit could not be verified')
        raw=response.read(65537)
    if len(raw)>65536:raise ValueError('Credit response too large')
    d=json.loads(raw)['data'];credits=money(d['total_credits']);usage=money(d['total_usage'])
    if usage>credits:raise RuntimeError('Prepaid credit exhausted')
    from .openrouter_task import STATE
    folder=STATE/'prepaid-credit-receipts';folder.mkdir(mode=0o700,exist_ok=True)
    path=folder/(hashlib.sha256(raw).hexdigest()+'.private.json')
    if not path.exists():
        with path.open('xb') as stream:path.chmod(0o600);stream.write(raw)
    return {'request_started':start,'observed_at':now(),'remaining_usd':str(credits-usage),
        'total_usage_usd':str(usage),'receipt_sha256':hashlib.sha256(raw).hexdigest(),
        'source':'OpenRouter GET /api/v1/credits'}


def selected(state):
    ident=os.environ.get(ENV)
    if not ident:return None
    entry=state.get('funded_runs',{}).get(ident)
    if (not entry or entry.get('id')!=ident or entry.get('status')!='active' or
        not Decimal(0)<money(entry['limit_usd'])):
        raise RuntimeError('Explicit funded run authorization unavailable')
    if money(entry['limit_usd'])>20:
        alignment=entry.get('prepaid_limit_alignment',{})
        if (alignment.get('protocol')!='verified-prepaid-cumulative-limit-v1' or
            not alignment.get('authorization') or alignment.get('new_limit_usd')!=entry['limit_usd'] or
            money(alignment['new_limit_usd'])!=money(alignment['cumulative_exposure_usd'])+money(alignment['payable_room_usd'])):
            raise RuntimeError('Explicit prepaid cumulative-limit authorization unavailable')
    safety=entry.get('prepaid_safety',{})
    if not safety.get('evidence') or safety.get('auto_recharge') not in {'off','verified-threshold'}:
        raise RuntimeError('Auto Recharge verification unavailable; paid admission paused')
    if safety['auto_recharge']=='verified-threshold' and money(safety.get('local_stop_floor_usd',0))<=money(safety['threshold_usd']):
        raise RuntimeError('Prepaid stop floor must exceed verified Auto Recharge threshold')
    return entry


def align_existing_prepaid_limit(budget, ident, authorization, snapshot):
    """Replace only an obsolete cumulative envelope; retain every liability.

    Run under the exclusive admission-owner lease. Posted costs form the existing
    cumulative exposure, not another debit from the observed wallet. Each later
    admission still independently checks fresh credit and unresolved ceilings.
    """
    if ident!=os.environ.get(ENV) or not authorization.strip():raise ValueError('Existing named authorization required')
    with budget.locked() as state:
        entry=selected(state)
        if entry is None:raise RuntimeError('Original funded run missing')
        if any(r['state']=='reserved' for r in state['calls']):raise RuntimeError('In-flight work must settle before limit alignment')
        arithmetic=describe_admission(state,entry,snapshot)
        room=money(arithmetic['prepaid_room_after_conservative_exposure_usd'])
        if room<=0:raise RuntimeError('No conservative prepaid room')
        exposure=money(arithmetic['named_run_settled_and_ceiling_exposure_usd'])
        event={'protocol':'verified-prepaid-cumulative-limit-v1','authorization':authorization,'at':now(),
            'previous_limit_usd':entry['limit_usd'],'new_limit_usd':str(exposure+room),
            'cumulative_exposure_usd':str(exposure),'payable_room_usd':str(room),
            'credit':snapshot,'arithmetic':arithmetic,
            'calls_sha256':hashlib.sha256(json.dumps(state['calls'],sort_keys=True).encode()).hexdigest()}
        entry.setdefault('limit_history',[]).append(event)
        entry.update(limit_usd=event['new_limit_usd'],prepaid_limit_alignment=event,last_credit=snapshot)
        return event


def enroll(budget, ident, authorization, snapshot):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',ident) or not authorization.strip():raise ValueError('Named authorization required')
    with budget.locked() as state:
        if ident in state.get('funded_runs',{}):raise ValueError('Run already enrolled; resume its existing budget')
        if any(r.get('status')=='active' for r in state.get('funded_runs',{}).values()):raise ValueError('Resume the existing active funded run')
        # Caller verifies owned workers have stopped before this one-time boundary.
        limit=min(Decimal(20),money(snapshot['remaining_usd']))
        if limit<=0:raise RuntimeError('No verified prepaid credit')
        entry={'id':ident,'status':'awaiting-auto-recharge-verification','authorized_at':now(),'authorization':authorization,
            'prepaid_safety':{'auto_recharge':'unverified','evidence':None},
            'limit_usd':str(limit),'baseline_credit':snapshot,'baseline_ledger_rows':len(state['calls']),
            'historical_rows_sha256':hashlib.sha256(json.dumps(state['calls'],sort_keys=True).encode()).hexdigest(),
            'scope':'New inference only; historical charges already reflected in provider prepaid credit are not charged twice. Original receipts, allocations and unknown bounds remain historical audit records.',
            'last_credit':snapshot}
        state.setdefault('funded_runs',{})[ident]=entry
        return entry


def describe_admission(state, entry, snapshot):
    """Read-only arithmetic, distinguishing wallet credit from a named run guard.

    Unknown ceilings are conservative exposure, not actual charges or proof they
    are still unreflected. A balance observation cannot settle an individual row.
    This function neither admits work nor updates limits, receipts or holds.
    """
    own=[r for r in state['calls'] if r.get('funded_run_id')==entry['id']]
    new=state['calls'][entry['baseline_ledger_rows']:]
    unsettled=[r for r in new if r['state'] not in {'settled','rejected_no_dispatch'}]
    after=[r for r in new if r['state']=='settled' and r.get('finished','')>=snapshot['request_started']]
    before=[r for r in new if r['state']=='settled' and r.get('finished','')<snapshot['request_started']]
    total=lambda rows:sum((money(r['accounted']) for r in rows),Decimal(0))
    exposure=total(own);guard_room=money(entry['limit_usd'])-exposure
    credit=money(snapshot['remaining_usd']);floor=money(entry.get('prepaid_safety',{}).get('local_stop_floor_usd',0))
    pending=total(unsettled)+total(after);wallet_room=credit-pending-floor
    states={name:{'rows':sum(r['state']==name for r in own),
        'accounted_usd':str(total([r for r in own if r['state']==name]))} for name in sorted({r['state'] for r in own})}
    return {'protocol':'prepaid-versus-run-guard-v1','funded_run_id':entry['id'],
        'credit_observed_at':snapshot['observed_at'],'observed_available_prepaid_usd':str(credit),
        'named_run_limit_usd':entry['limit_usd'],'named_run_settled_and_ceiling_exposure_usd':str(exposure),
        'named_run_guard_headroom_usd':str(guard_room),'run_states':states,
        'post_baseline_settled_before_credit_read_usd':str(total(before)),
        'post_baseline_settled_after_credit_read_usd':str(total(after)),
        'unresolved_ceiling_usd':str(total(unsettled)),'unresolved_rows':len(unsettled),
        'unresolved_rows_with_generation_id':sum(bool(r.get('upstream_request_id') or r.get('generation_id')) for r in unsettled),
        'conservative_still_unreflected_bound_usd':str(pending),
        'proven_still_unreflected_actual_usd':None,
        'local_stop_floor_usd':str(floor),'prepaid_room_after_conservative_exposure_usd':str(wallet_room),
        'currently_enforced_admission_room_usd':str(min(guard_room,wallet_room)),
        'interpretation':'Named-run headroom is not wallet balance. Posted settled debits are not subtracted again. Unknown ceilings remain conservative; individual posting/reconciliation proof is absent. No limit, reservation or ledger row changed.'}


def check_admission(state, entry, bound, snapshot):
    own=[r for r in state['calls'] if r.get('funded_run_id')==entry['id']]
    exposure=sum((money(r['accounted']) for r in own),Decimal(0))
    if exposure+bound>money(entry['limit_usd']):raise RuntimeError('Funded run spending cap reached')
    # Reserve all new unresolved/in-flight liability and any settlements newer
    # than the credit read. Completed historical charges are already reflected
    # by prepaid credit, rather than added to the new run a second time.
    new=state['calls'][entry['baseline_ledger_rows']:]
    unreflected=sum((money(r['accounted']) for r in new if r['state'] not in {'settled','rejected_no_dispatch'} or
        r['state']=='settled' and r.get('finished','')>=snapshot['request_started']),Decimal(0))
    floor=money(entry.get('prepaid_safety',{}).get('local_stop_floor_usd',0))
    if unreflected+bound+floor>money(snapshot['remaining_usd']):raise RuntimeError('Verified prepaid credit cannot cover pending work and stop floor')
    entry['last_credit']=snapshot


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--enroll',required=True);p.add_argument('--authorization',required=True);a=p.parse_args()
    from .direct_accounting import live_budget
    r=enroll(live_budget(),a.enroll,a.authorization,credit_snapshot())
    from .scored_run_report import display_usd
    print(json.dumps({'run_budget':r['id'],'limit':display_usd(r['limit_usd']),'status':r['status'],'historical_ledger':'preserved','top_up':False}))


if __name__=='__main__':main()
