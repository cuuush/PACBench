"""Read-only live/partial scored cohort report; missing cases stay ungraded."""
import argparse,hashlib,json,fcntl,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path


def load(p):
    for attempt in range(5):
        try:return json.loads(p.read_text())
        except json.JSONDecodeError:
            if attempt==4:raise
            time.sleep(.01)
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def union(intervals):
    total=0;end=None
    for a,b in sorted(intervals):
        total+=max(0,b-max(a,end if end is not None else a));end=max(b,end if end is not None else b)
    return total


def build(root,ledger):
    cohort=load(root/'cohort.json')
    with ledger.open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH);calls=json.load(stream)['calls']
    judges={r.get('upstream_request_id'):r for r in calls if r.get('upstream_request_id')};results=[]
    for name in cohort['providers']:
        component=root/name;arm=component/'repeat-1'/name
        summary=load(component/'summary.json') if (component/'summary.json').exists() else {}
        grades=[load(p) for p in arm.glob('turns/*/grade.json')]
        statuses=[load(p) for p in arm.glob('turns/*/status.json')]
        turns=[load(p) for p in arm.glob('turns/*/turn.json')]
        project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-'+name
        own=[r for r in calls if r.get('project') in {project+'-driver',project+'-memory'}]
        judge_ids=set()
        for p in (component/'model-calls').glob('*.json'):
            data=load(p);identifier=(data.get('response') or {}).get('id')
            if identifier in judges:judge_ids.add(judges[identifier]['id'])
        own.extend(r for r in calls if r['id'] in judge_ids)
        windows=[];phase_wall=defaultdict(list);inputs=[];integrity=[]
        for p in arm.glob('ingestion/*.json'):
            d=load(p);u=d.get('usage',{});day=p.stem
            if u.get('started') and u.get('finished'):
                interval=(stamp(u['started']),stamp(u['finished']));windows.append((*interval,'ingest_processing'));phase_wall['ingest_processing'].append(interval)
            source=component/'sources'/day/'messages.txt'
            exact=source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==d.get('document_sha256')
            integrity.append({'day':day,'ready':d.get('ready'), 'source_hash_matches':exact,'messages':len(d.get('source_ids',[]))});inputs.extend(d.get('source_ids',[]))
        for p in arm.glob('turns/*/usage.json'):
            u=load(p)
            if u.get('started') and u.get('finished'):
                interval=(stamp(u['started']),stamp(u['finished']));windows.append((*interval,'answer_retrieval'));phase_wall['answer_retrieval'].append(interval)
        groups=defaultdict(list)
        for r in own:
            phase='judging' if r['id'] in judge_ids else next((phase for a,b,phase in windows if a<=stamp(r['started'])<=b),'other_native_or_setup')
            groups[phase].append(r)
            if phase=='judging' and r.get('finished'):phase_wall[phase].append((stamp(r['started']),stamp(r['finished'])))
        phase_rows={}
        for phase,items in groups.items():
            phase_rows[phase]={'calls':len(items),'settled_actual_usd':str(sum((Decimal(r['accounted']) for r in items if r['state']=='settled'),Decimal(0))),
                'unresolved_or_inflight_usd':str(sum((Decimal(r['accounted']) for r in items if r['state'] not in {'settled','rejected_no_dispatch'}),Decimal(0))),
                'input_tokens_observed':sum(r.get('input_tokens') or 0 for r in items),'output_tokens_observed':sum(r.get('output_tokens') or 0 for r in items),
                'reasoning_tokens_observed':sum((r.get('usage_detail') or {}).get('completion_tokens_details.reasoning_tokens',0) or 0 for r in items),
                'requests_missing_usage':sum(r.get('input_tokens') is None for r in items),'http_statuses':dict(Counter(str(r.get('http_status')) for r in items)),
                'wall_interval_union_s':union(phase_wall[phase]) if phase_wall[phase] else None,
                'observed_api_duration_union_s':union([(stamp(r['started']),stamp(r['finished'])) for r in items if r.get('finished')]),'per_call':[{'id':r['id'],'generation_id':r.get('upstream_request_id'),'model':r.get('served_model',r['model']),
                    'provider':r.get('served_provider'),'role':r.get('accounting_role'),'state':r['state'],'started':r['started'],'finished':r.get('finished'),
                    'duration_s':stamp(r['finished'])-stamp(r['started']) if r.get('finished') else None,'finish_reason':r.get('finish_reason'),
                    'cost_usd':r['accounted'] if r['state']=='settled' else None,'unknown_bound_usd':r['accounted'] if r['state'] not in {'settled','rejected_no_dispatch'} else None,
                    'input_tokens':r.get('input_tokens'),'output_tokens':r.get('output_tokens'),'usage_detail':r.get('usage_detail')} for r in items]}
        source_states=[load(p) for p in arm.glob('turns/*/source-state.json')];schedule=summary.get('ingestion_schedule',[])
        from .document_ingestion import ready_sources
        chronological=all(set(s['ingested_batches'])=={b['id'] for b in ready_sources(schedule,name,s['at'],summary.get('process_timezone') or 'America/New_York')} for s in source_states)
        correct=sum(g['correct'] for g in grades);missed=sum(g['missed'] for g in grades);wrong=sum(g['misrepresented'] for g in grades)
        results.append({'provider':name,'state':summary.get('state','not-started'),'planned_questions':37,'answers_returned':len(turns),'graded_answers':len(grades),
            'planned_atomic_criteria':155,'graded_atomic_criteria':correct+missed+wrong,'initial_semantic_correct':correct,'initial_semantic_missed':missed,'initial_semantic_misrepresented':wrong,
            'whole_suite_score':None if len(grades)<37 or correct+missed+wrong!=155 else {'correct':correct,'criteria':155},
            'retention_recovery_points_on_completed_cases':sum((x.get('recovery',{}).get('score') or {}).get('points',0) for x in statuses),
            'retention_scope':'Separate from raw semantic correctness; source-assisted or uncertain paths do not receive retention credit.',
            'input_batches':integrity,'source_records_delivered':len(inputs),'source_ids_unique':len(inputs)==len(set(inputs)),
            'chronological_gates_match_native_schedule':chronological if source_states else None,'context_isolation_passed':sum(t.get('context_isolation',{}).get('state')=='pass' for t in turns),
            'judge_contract_protocols':dict(Counter(load(p).get('judge_protocol') for p in arm.glob('turns/*/judgment.json'))),
            'identical_request_groups':sum(n>1 for n in Counter(r.get('request_sha256') for r in own if r.get('request_sha256')).values()),
            'retry_note':'Identical fingerprints are observable repetitions, not proof of retry loops. SDK-internal retries without outgoing attempts cannot be counted.',
            'phases':phase_rows})
    return {'version':'scored-cohort-live-report-v1','observed_at':datetime.now(timezone.utc).isoformat(),'cohort_state':cohort['state'],
        'condition':cohort.get('role_routes'),'reservation_policy':cohort.get('reservation_policy'),'providers':results,
        'denominator':'Nine providers each retain37 questions and155 criteria. Missing cases are ungraded, not silently removed or counted as wrong. Partial coverage is not a complete leaderboard.',
        'timing_scope':'Observed phase interval unions, including native processing/API/local overhead; phases may overlap. Background work outside windows stays other_native_or_setup. Local compute unpriced.',
        'cost_scope':'Unique ledger attempts only; response usage.cost is authoritative settled spend. Judge calls bound by saved generation IDs, not a time-only shared allocation. Pending/unknown bounds separate; no authoring or prior-run costs attributed here.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--ledger',type=Path,default=Path('/workspace/shared/pacbench-openrouter-pizza-day-20261112/ledger.json'));p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=build(a.root.resolve(),a.ledger);a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps([{'provider':r['provider'],'state':r['state'],'graded':r['graded_answers'],'semantic_correct':r['initial_semantic_correct'],'criteria_graded':r['graded_atomic_criteria']} for r in report['providers']]))

if __name__=='__main__':main()
