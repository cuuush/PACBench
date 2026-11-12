"""Observed run costs by phase and role; no invented per-tool causal prices."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

from .followups import save
from .gateway_client import STATE
from .usage import ledger_call, summarize_calls


def read(path):
    return json.loads(path.read_text())


def summarize(calls):
    unknown=[c for c in calls if c is None]
    known=[c for c in calls if c is not None]
    extra = summarize_calls(known)
    if unknown:
        for key in ('actual_cost_usd', 'token_price_estimate_usd', 'unconfirmed_budget_bound_usd'):
            extra[key] = None
    return {**extra, 'model_requests':len(calls),'unobserved_requests':len(unknown),
        'settled_upper_bound_usd':sum(c['accounted_upper_bound_usd'] for c in known if c['state']=='settled') if not unknown else None,
        'held_usd':sum(c['accounted_upper_bound_usd'] for c in known if c['state']!='settled') if not unknown else None,
        'input_tokens':sum(c['input_tokens'] for c in known) if not unknown and all(c.get('input_tokens') is not None for c in known) else None,
        'output_tokens':sum(c['output_tokens'] for c in known) if not unknown and all(c.get('output_tokens') is not None for c in known) else None,
        'unsettled_requests':sum(c['state']!='settled' for c in known)}


def processing_debt(arm,provider):
    """Inspect a native queue when available; absence is not proof of no work."""
    receipt=arm/'processing-debt.json'
    database=arm/'memcal-home/memcal.db'
    if provider=='memcal' and database.exists():
        with sqlite3.connect(f'file:{database.resolve()}?mode=ro',uri=True) as connection:
            rows=connection.execute('SELECT a.channel,count(*) FROM spool s JOIN archive a '
                'ON a.id=s.archive_id WHERE s.processed_at IS NULL GROUP BY a.channel').fetchall()
        counts=dict(rows)
        record={'state':'observed-pending' if counts else 'observed-empty',
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'method':'Read-only native Memcal spool inspection after the benchmark workload',
            'pending_by_channel':counts,'pending_items':sum(counts.values()),
            'future_model_cost_usd':None}
        save(receipt,record)
        return record
    if receipt.exists():return read(receipt)
    return {'state':'unobserved','future_model_cost_usd':None,
        'reason':'No native queue inspection receipt; the completed workload does not prove all deferred processing finished.'}


def refreshed_calls(calls,connection):
    actual={}
    columns = {r['name'] for r in connection.execute('PRAGMA table_info(calls)')} if connection else set()
    fields = ('id','project','model','endpoint','started','finished','state','input_tokens','output_tokens',
              'accounted','reserved','http_status','error_label','finish_reason','reasoning_tokens',
              'upstream_request_id','generation_id','usage_detail','actual_cost_nano','actual_cost_source','upstream_origin','reconciled_at',
              'actual_cost_usd_decimal','generation_usage_detail','reconcile_status','reconcile_attempts',
              'reconcile_last_attempt','reconcile_next_attempt')
    select = ','.join(k if k in columns else 'NULL AS '+k for k in fields)
    for call in calls:
        current=connection.execute(f'SELECT {select} FROM calls WHERE id=?',(call['id'],)).fetchone() if connection else None
        actual[call['id']]={**call, **(ledger_call(current, call.get('role')) if current else {})}
    return actual


def role_totals(calls):
    return {role:summarize([c for c in calls if c.get('role')==role])
            for role in ('hermes','memory_provider')}


def model_groups(calls):
    groups = {}
    for call in calls:
        key = (call.get('role'), call.get('project'), call.get('model'))
        groups.setdefault(key, []).append(call)
    return [{'role': k[0], 'project': k[1], 'model': k[2], **summarize(v)} for k, v in groups.items()]


def review(root,*,ledger=STATE/'ledger.sqlite3'):
    summary=read(root/'summary.json');results=[];other_attempts=[];connection=None
    if ledger.exists():
        connection=sqlite3.connect(f'file:{ledger}?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    try:
        for result in summary['results']:
            arm=root/f"repeat-{result['repetition']}"/result['provider']
            if result['state']=='preflight-failed':
                results.append({'provider':result['provider'],'state':'not-run','reason':result['reason'],
                    'complete_for_cost_comparison':False,'lifecycle_cost_complete':False,'cost':None});continue
            snapshot=read(arm/'model-usage.json') if (arm/'model-usage.json').exists() else {'calls':[]}
            capture_available=snapshot.get('state')=='captured'
            actual=refreshed_calls(snapshot['calls'],connection)
            phases={}
            patterns={'ingestion':'ingestion/*.json','initial':'turns/*/usage.json',
                      'followups':'turns/*/followups/*/usage.json','write_processing':'turns/*/write-readiness.json'}
            assigned=set()
            for phase,pattern in patterns.items():
                ids=set()
                for path in arm.glob(pattern):
                    data=read(path);usage=data.get('usage',data)
                    ids.update(c['id'] for c in usage.get('model_calls',[]))
                    missing=[call for call in usage.get('model_calls',[]) if call['id'] not in actual]
                    actual.update(refreshed_calls(missing,connection))
                phases[phase]=ids;assigned.update(ids)
            phases['outside_captured_windows']=set(actual)-assigned
            def totals(ids):
                return role_totals([actual[id] for id in ids])
            records=[read(p) for p in sorted((arm/'ingestion').glob('*.json'))]
            evaluation=read(arm/'evaluation.json') if (arm/'evaluation.json').exists() else {'checks':[],'counts':{}}
            scores=[c.get('recovery',{}).get('score',{}) for c in evaluation['checks']]
            expected=len(read(root/'evaluation-plan.json')['checks'])
            debt=processing_debt(arm,result['provider'])
            results.append({'provider':result['provider'],'state':result['state'],
                'daily_batches_attempted':len(records),'daily_batches_ready':sum(r.get('ready') is True for r in records),
                'ingestion_wall_s':round(sum(r.get('latency_s',0) for r in records),2),
                'initial_counts':evaluation.get('counts',{}),
                'retention_points':sum(s.get('points',0) for s in scores),'retention_max_graded':sum(s.get('max_points',0) for s in scores),
                'complete_for_cost_comparison':len(records)==len(summary['ingestion_schedule']) and all(r.get('ready') is True for r in records)
                    and len(evaluation['checks'])==expected and all(c.get('verdict') in ('pass','fail')
                        and c.get('recovery',{}).get('state','complete')=='complete' for c in evaluation['checks']),
                'deferred_processing':debt,
                'lifecycle_cost_complete':False if debt['state']=='observed-pending' else None,
                'cost':totals(set(actual)) if capture_available else None,
                'phases':{phase:totals(ids) for phase,ids in phases.items()},
                'request_costs':[{**call, 'provider':result['provider'],
                                  'stages':[phase for phase,ids in phases.items() if id in ids]}
                                 for id,call in actual.items()] if capture_available else None,
                'model_groups':model_groups(list(actual.values())) if capture_available else None,
                'phase_model_groups':{phase:model_groups([actual[id] for id in ids]) for phase,ids in phases.items()},
                'terminal_snapshot_missing_requests':sorted(assigned-{c['id'] for c in snapshot['calls']}),
                'overlapping_phase_request_ids':{id:[p for p,ids in phases.items() if id in ids]
                    for id in assigned if sum(id in ids for ids in phases.values())>1}})
        for attempt in summary.get('other_attempts',[]):
            arm=(root/attempt['evidence']).resolve()
            if not arm.is_relative_to(root.resolve()):raise ValueError('Attempt evidence must stay inside the run')
            captured=read(arm/'model-usage.json') if (arm/'model-usage.json').exists() else {}
            calls=refreshed_calls(captured.get('calls',[]),connection)
            other_attempts.append({**attempt,'cost':role_totals(list(calls.values())) if captured.get('state')=='captured' else None,
                'daily_batches_ready':sum(read(p).get('ready') is True for p in (arm/'ingestion').glob('*.json')),
                'scope':'Previous or interrupted attempt; excluded from the selected arm costs, preserved as real trial spending.'})
    finally:
        if connection:connection.close()
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'providers':results,'other_attempts':other_attempts,
        'ledger_refreshed':connection is not None,'input_hash':summary['input_hash'],
        'scope':['All native arm requests, including setup, automatic recall and background capture; benchmark authoring/judging are separate overhead.',
            'Phase windows identify request start times, not causal per-tool prices. Outside-window work is included in total costs.',
            'Usage-priced figures are conservative uncached-price bounds, not invoices. Unknown actual charges retain safety bounds.',
            'Provider identifies the benchmark arm, not a claim that every Hermes request was made by that memory provider. Stages are observed receipt windows; overlapping windows do not prove causal attribution.',
            'Only arms completing identical daily batches, initial questions and required recovery can be compared as observed workload costs.',
            'Observed workload costs stop at the recorded arm cutoff. Pending or unobserved deferred processing prevents a full lifecycle cost claim; future costs are unknown, not zero.',
            'Native canary execution and failed attempts are separate from month costs. Semantic correctness and source-assisted retention are separate scores.']}
    save(root/'cost-comparison.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    args=parser.parse_args();report=review(args.root)
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
