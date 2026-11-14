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
        not Decimal(0)<money(entry['limit_usd'])<=Decimal(20)):
        raise RuntimeError('Explicit funded run authorization unavailable')
    return entry


def enroll(budget, ident, authorization, snapshot):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',ident) or not authorization.strip():raise ValueError('Named authorization required')
    with budget.locked() as state:
        if ident in state.get('funded_runs',{}):raise ValueError('Run already enrolled; resume its existing budget')
        if any(r.get('status')=='active' for r in state.get('funded_runs',{}).values()):raise ValueError('Resume the existing active funded run')
        # Caller verifies owned workers have stopped before this one-time boundary.
        limit=min(Decimal(20),money(snapshot['remaining_usd']))
        if limit<=0:raise RuntimeError('No verified prepaid credit')
        entry={'id':ident,'status':'active','authorized_at':now(),'authorization':authorization,
            'limit_usd':str(limit),'baseline_credit':snapshot,'baseline_ledger_rows':len(state['calls']),
            'historical_rows_sha256':hashlib.sha256(json.dumps(state['calls'],sort_keys=True).encode()).hexdigest(),
            'scope':'New inference only; historical charges already reflected in provider prepaid credit are not charged twice. Original receipts, allocations and unknown bounds remain historical audit records.',
            'last_credit':snapshot}
        state.setdefault('funded_runs',{})[ident]=entry
        return entry


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
    if unreflected+bound>money(snapshot['remaining_usd']):raise RuntimeError('Verified prepaid credit cannot cover pending work')
    entry['last_credit']=snapshot


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--enroll',required=True);p.add_argument('--authorization',required=True);a=p.parse_args()
    from .direct_accounting import live_budget
    r=enroll(live_budget(),a.enroll,a.authorization,credit_snapshot())
    from .scored_run_report import display_usd
    print(json.dumps({'run_budget':r['id'],'limit':display_usd(r['limit_usd']),'historical_ledger':'preserved','top_up':False}))


if __name__=='__main__':main()
