"""Unique-request cost reporting; shared judge requests are never duplicated."""
from __future__ import annotations
import argparse,hashlib,json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def total(rows):
    rows=list(rows);known=[r for r in rows if r['state']=='settled']
    return {'requests':len(rows),'settled_requests':len(known),
        'actual_reported_usd':str(sum((Decimal(r['accounted']) for r in known),Decimal(0))),
        'unresolved_requests':len(rows)-len(known),
        'unresolved_bound_usd':str(sum((Decimal(r['accounted']) for r in rows if r['state']!='settled'),Decimal(0))),
        'observed_input_tokens':sum(r.get('input_tokens') or 0 for r in rows),
        'observed_output_tokens':sum(r.get('output_tokens') or 0 for r in rows),
        'requests_missing_token_counts':sum(r.get('input_tokens') is None or r.get('output_tokens') is None for r in rows)}


def report(calls,components):
    if len({r['id'] for r in calls})!=len(calls):raise ValueError('Duplicate ledger request ID')
    projects={};attempts=[]
    for component in components:
        component=Path(component).resolve();summary=json.loads((component/'summary.json').read_text())
        if not (component/'repeat-1').is_dir():continue
        for arm in sorted((component/'repeat-1').iterdir()):
            if not arm.is_dir():continue
            provider=arm.name;run=hashlib.sha256(str(arm).encode()).hexdigest()[:12]
            record={'attempt':component.parent.name+'/'+provider,'provider':provider,
                'mode':summary.get('mode'),'recorded_state':summary['state'],
                'attempt_kind':('import_qualification' if summary.get('mode')=='native-import-validation' else 'benchmark_attempt'),
                'retry':('retry' in component.parent.name or 'fixed' in component.parent.name),
                'processing_completeness':'See import qualification evidence; stale running summaries do not prove live work',
                'phases':{}}
            observed=defaultdict(set)
            for pattern,phase in [('ingestion/*.json','import_processing'),('turns/**/usage.json','answering_retrieval')]:
                for path in arm.glob(pattern):
                    d=json.loads(path.read_text());u=d.get('usage',d)
                    for r in u.get('model_calls',[]):observed[r['id']].add(phase)
            record['_observed']=observed
            # Persisted per-turn scores survive an interrupted controller whose
            # top-level summary was never updated. Count each question once.
            scores=[]
            for path in sorted(arm.glob('turns/*/followups/recovery.json')):
                value=json.loads(path.read_text()).get('score')
                if isinstance(value,dict):scores.append(value)
            if not scores:
                scores=[c['recovery']['score'] for r in summary.get('results',[]) if r.get('provider')==provider
                    for c in r.get('checks',[]) if isinstance(c.get('recovery',{}).get('score'),dict)]
            record['score']={'graded_questions':len(scores),'total_questions':37,
                'initial_correct_criteria':sum(s['initial_correct'] for s in scores),
                'graded_criteria':sum(s['max_points'] for s in scores),'total_criteria':155,
                'retention_points':sum(s['points'] for s in scores),
                'complete':len(scores)==37 and sum(s['max_points'] for s in scores)==155}
            for role in ['driver','memory']:
                key=f'pacbench-{run}-{provider}-{role}'
                if key in projects:raise ValueError('Component/project supplied twice')
                projects[key]=(record,role)
            attempts.append(record)
    bins=defaultdict(list);shared=[];unattributed=[]
    for row in calls:
        if row['project']=='pacbench-judge':shared.append(row);continue
        binding=projects.get(row['project'])
        if not binding:unattributed.append(row);continue
        record,role=binding
        if role=='driver':phase='answering_retrieval'
        elif record['mode']=='native-import-validation':phase='import_processing'
        else:
            phases=record['_observed'].get(row['id'],set())
            phase=next(iter(phases)) if len(phases)==1 else 'provider_background_or_ambiguous'
        bins[(record['attempt'],phase)].append(row)
    for record in attempts:
        for phase in ['import_processing','answering_retrieval','provider_background_or_ambiguous']:
            record['phases'][phase]=total(bins[(record['attempt'],phase)])
        record['total']=total(r for (attempt,phase),rows in bins.items() if attempt==record['attempt'] for r in rows)
        del record['_observed']
    assert sum(a['total']['requests'] for a in attempts)+len(shared)+len(unattributed)==len(calls)
    return {'version':'request-phase-costs-v1','attempts':attempts,
        'shared_judging':total(shared),'other_setup_or_unmapped':total(unattributed),'all_in_scope':total(calls),
        'attribution':'Memory call phases use IDs observed at request start in import/turn receipts. Background calls without a unique observation remain separate. Driver projects identify answering. Shared judge calls are counted once and are not assigned to providers because concurrent receipt windows overlap.',
        'excludes':['Local CPU/GPU, storage and electricity','Provider-independent frozen source generation and historical spending before the selected ledger offset'],
        'comparison_limit':'Partial scores use only graded criteria; missing grades are not counted as incorrect or zero cost. Import-only qualification has no benchmark score. Provider totals exclude shared judging.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--ledger',type=Path,required=True);p.add_argument('--since-index',type=int,required=True);p.add_argument('--components',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    ledger=json.loads(a.ledger.read_text());r=report(ledger['calls'][a.since_index:],a.components)
    r['ledger_offset']=a.since_index;r['excluded_earlier_ledger']=total(ledger['calls'][:a.since_index]);r['excluded_earlier_ledger_scope']='Historical accounting entries include external allocations; their counts are accounting items, not solely model requests.';r['shared_cap_accounted_usd']=str(sum(Decimal(c['accounted']) for c in ledger['calls']))
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,indent=2)+'\n')

if __name__=='__main__':main()
