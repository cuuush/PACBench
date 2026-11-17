"""Read-only live/partial scored cohort report; missing cases stay ungraded."""
import argparse,hashlib,json,fcntl,time
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal, ROUND_HALF_UP
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


def display_usd(value):
    """Presentation only: exact ledger arithmetic and receipts stay untouched."""
    amount=Decimal(str(value))
    if not amount.is_finite() or amount<0:raise ValueError('Invalid USD amount')
    if 0<amount<Decimal('.01'):return '<$0.01'
    return '$'+str(amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def budget_display(ledger):
    with ledger.open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH);state=json.load(stream)
    from .funded_run_budget import selected
    funded=selected(state)
    totals=defaultdict(lambda:Decimal(0))
    for row in state['calls']:
        if not funded or row.get('funded_run_id')==funded['id']:totals[row['state']]+=Decimal(row['accounted'])
    exposure=sum(totals.values(),Decimal(0));cap=Decimal(funded['limit_usd'] if funded else state['config']['cap'])
    return {'verified_charges':display_usd(totals['settled']),
        'unresolved_charge_bounds':display_usd(totals['uncertain']+totals['abandoned_unknown']),
        'outstanding_reservations':display_usd(totals['reserved']),
        'external_allocations':display_usd(totals['allocated']),
        'remaining_after_all_exposure':display_usd(max(Decimal(0),cap-exposure)),
        'authorized_cap':display_usd(cap),
        'funded_run_id':funded['id'] if funded else None,
        'scope':'Cents for display only; charges, unknowns, reservations and allocations remain separate. No refund inferred.'}


def build(root,ledger):
    cohort=load(root/'cohort.json')
    planned_questions=cohort.get('questions',37)
    planned_criteria=cohort.get('criteria',155)
    with ledger.open() as stream:
        fcntl.flock(stream,fcntl.LOCK_SH);calls=json.load(stream)['calls']
    judges={r.get('upstream_request_id'):r for r in calls if r.get('upstream_request_id')}
    by_id={r['id']:r for r in calls};results=[]
    for name in cohort['providers']:
        component=root/cohort.get('provider_outputs',{}).get(name,name);arm=component/'repeat-1'/name
        summary=load(component/'summary.json') if (component/'summary.json').exists() else {}
        grades=[load(p) for p in arm.glob('turns/*/grade.json')]
        statuses=[load(p) for p in arm.glob('turns/*/status.json')]
        turns=[load(p) for p in arm.glob('turns/*/turn.json')]
        from .turn_timings import gateway_events,measure,summary as timing_summary
        resolved=load(component/'resolved-spec.json') if (component/'resolved-spec.json').exists() else {}
        gateway_log=arm/'hermes-home/logs/gateway.log'
        events=gateway_events(gateway_log.read_text(),resolved.get('timezone')) if gateway_log.exists() else []
        lifecycle=[{'question_id':p.parent.name,**measure(load(p),events)} for p in arm.glob('turns/*/turn.json')]
        project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-'+name
        own=[r for r in calls if r.get('project') in {project+'-driver',project+'-memory'}]
        judge_ids=set();judge_phases={}
        for p in (component/'model-calls').glob('*.json'):
            data=load(p);identifier=(data.get('response') or {}).get('id')
            receipt_id=(data.get('accounting_receipt') or {}).get('id')
            recorded=by_id.get(receipt_id);generated=judges.get(identifier)
            if recorded and generated and recorded['id']!=generated['id']:raise ValueError('Model receipt and generation attribution conflict')
            bound=recorded or generated
            if bound:
                ident=bound['id'];judge_ids.add(ident)
                prompt=next((m.get('content','') for m in reversed((data.get('request') or {}).get('messages',[])) if m.get('role')=='user'),'')
                judge_phases[ident]=('followup_authoring_review' if isinstance(prompt,str) and prompt.startswith((
                    'Write one short user message asking for each missing detail.',
                    'Does this message naturally ask for all missing information')) else 'judging')
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
        for p in arm.glob('turns/*/followups/**/usage.json'):
            u=load(p)
            if u.get('started') and u.get('finished'):
                interval=(stamp(u['started']),stamp(u['finished']));windows.append((*interval,'followup_answer_retrieval'));phase_wall['followup_answer_retrieval'].append(interval)
        groups=defaultdict(list)
        for r in own:
            phase=judge_phases[r['id']] if r['id'] in judge_ids else next((phase for a,b,phase in windows if a<=stamp(r['started'])<=b),'other_native_or_setup')
            groups[phase].append(r)
            if r['id'] in judge_ids and r.get('finished'):phase_wall[phase].append((stamp(r['started']),stamp(r['finished'])))
        phase_rows={}
        for phase in sorted(set(groups)|set(phase_wall)):
            items=groups[phase]
            phase_rows[phase]={'calls':len(items),'definitively_not_dispatched':sum(r['state']=='rejected_no_dispatch' for r in items),'settled_actual_usd':str(sum((Decimal(r['accounted']) for r in items if r['state']=='settled'),Decimal(0))),
                'unresolved_or_inflight_usd':str(sum((Decimal(r['accounted']) for r in items if r['state'] not in {'settled','rejected_no_dispatch'}),Decimal(0))),
                'outstanding_reservations_usd':str(sum((Decimal(r['accounted']) for r in items if r['state']=='reserved'),Decimal(0))),
                'unresolved_charge_ceilings_usd':str(sum((Decimal(r['accounted']) for r in items if r['state'] not in {'settled','reserved','allocated','rejected_no_dispatch'}),Decimal(0))),
                'reported_zero_charge_attempts':sum(r['state']=='settled' and Decimal(r['accounted'])==0 for r in items),
                'sanitized_response_receipt_attempts':sum(bool(r.get('billing_receipt')) for r in items),
                'input_tokens_observed':sum(r.get('input_tokens') or 0 for r in items),'output_tokens_observed':sum(r.get('output_tokens') or 0 for r in items),
                'reasoning_tokens_observed':sum((r.get('usage_detail') or {}).get('completion_tokens_details.reasoning_tokens',0) or 0 for r in items),
                'requests_missing_usage':sum(r.get('input_tokens') is None for r in items),
                'explicit_retry_attempts':sum(bool(r.get('explicit_retry_of')) for r in items),
                'http_statuses':dict(Counter(str(r.get('http_status')) for r in items)),
                'wall_interval_union_s':union(phase_wall[phase]) if phase_wall[phase] else None,
                'observed_api_duration_union_s':union([(stamp(r['started']),stamp(r['finished'])) for r in items if r.get('finished')]),'per_call':[{'id':r['id'],'generation_id':r.get('upstream_request_id'),'model':r.get('served_model',r['model']),
                    'provider':r.get('served_provider'),'role':r.get('accounting_role'),'state':r['state'],'started':r['started'],'finished':r.get('finished'),
                    'duration_s':stamp(r['finished'])-stamp(r['started']) if r.get('finished') else None,'finish_reason':r.get('finish_reason'),
                    'cost_usd':r['accounted'] if r['state']=='settled' else None,'unknown_bound_usd':r['accounted'] if r['state'] not in {'settled','rejected_no_dispatch'} else None,
                    'admission_reservation_usd':r['accounted'] if r['state']=='reserved' else '0',
                    'unresolved_charge_ceiling_usd':r['accounted'] if r['state'] not in {'settled','reserved','allocated','rejected_no_dispatch'} else '0',
                    'input_tokens':r.get('input_tokens'),'output_tokens':r.get('output_tokens'),
                    'explicit_retry_of':r.get('explicit_retry_of'),'usage_detail':r.get('usage_detail'),
                    'charge_evidence':('sanitized-response-receipt' if r.get('billing_receipt') else 'authoritative-generation-reconciliation' if
                        (r.get('reconciliation') or {}).get('cost_source')=='total_cost' else 'legacy-ledger-only' if r['state']=='settled' else 'unknown')} for r in items]}
        source_states=[load(p) for p in arm.glob('turns/*/source-state.json')];schedule=summary.get('ingestion_schedule',[])
        from .document_ingestion import ready_sources
        chronological=all(set(s['ingested_batches'])=={b['id'] for b in ready_sources(schedule,name,s['at'],summary.get('process_timezone') or 'America/New_York')} for s in source_states)
        strict=[load(p) for p in arm.glob('turns/*/strict-memory.json')]
        strict_counts=dict(Counter(x['state'] for x in strict))
        attempted=sum(len(t.get('tool_calls',[])) for t in turns)
        completed=sum(r.get('success') is True for t in turns for r in t.get('tool_results',[]))
        correct=sum(g['correct'] for g in grades);missed=sum(g['missed'] for g in grades);wrong=sum(g['misrepresented'] for g in grades)
        results.append({'provider':name,'state':summary.get('state','not-started'),'planned_questions':planned_questions,'answers_returned':sum(t.get('returncode')==0 and bool(t.get('answer','').strip()) and bool(t.get('session_id')) for t in turns),'turn_receipts':len(turns),'graded_answers':len(grades),
            'planned_atomic_criteria':planned_criteria,'graded_atomic_criteria':correct+missed+wrong,'initial_semantic_correct':correct,'initial_semantic_missed':missed,'initial_semantic_misrepresented':wrong,
            'whole_suite_score':None if len(grades)<planned_questions or correct+missed+wrong!=planned_criteria else {'correct':correct,'criteria':planned_criteria},
            'retention_recovery_points_on_completed_cases':sum((x.get('recovery',{}).get('score') or {}).get('points',0) for x in statuses),
            'retention_scope':'Separate from raw semantic correctness; source-assisted or uncertain paths do not receive retention credit.',
            'input_batches':integrity,'source_records_delivered':sum(x['messages'] for x in integrity if x['ready'] is True and x['source_hash_matches']),'source_records_in_import_receipts':len(inputs),'source_ids_unique':len(inputs)==len(set(inputs)),
            'chronological_gates_match_native_schedule':chronological if source_states else None,'context_isolation_passed':sum(t.get('context_isolation',{}).get('state')=='pass' for t in turns),
            'judge_contract_protocols':dict(Counter(load(p).get('judge_protocol') for p in arm.glob('turns/*/judgment.json'))),
            'native_network_recovery_calls_observed':sum(len((t.get('clock_context') or {}).get('native_network_continuation_api_input_ids',[])) for t in turns),
            'identical_request_groups':sum(n>1 for n in Counter(r.get('request_sha256') for r in own if r.get('request_sha256')).values()),
            'retry_note':'Identical fingerprints are observable repetitions, not proof of retry loops. SDK-internal retries without outgoing attempts cannot be counted.',
            'strict_memory_question_counts':strict_counts,
            'questions_without_strict_receipt':planned_questions-len(strict),
            'questions_without_turn_receipt':planned_questions-len(turns),
            'eligible_initial_criteria_graded':correct+missed+wrong,
            'strict_correct_on_graded_eligible':correct,
            'memory_tool_calls_initial':{'attempted':attempted,'completed_successfully':completed,
                'complete_turn_tool_logs':sum(t.get('returncode')==0 and 'tool_calls' in t and 'tool_results' in t for t in turns),
                'failed_or_missing_turn_tool_logs':sum(t.get('returncode')!=0 or 'tool_calls' not in t or 'tool_results' not in t for t in turns),
                'missing_results':sum(max(0,len(t.get('tool_calls',[]))-len(t.get('tool_results',[]))) for t in turns),
                'scope':'Observed explicit native memory tools in initial turns only; failed/missing streams may hide calls and never prove zero. Inference calls and follow-ups separate.'},
            'turn_lifecycle_timing':{'summary':timing_summary(lifecycle),'per_question':lifecycle,
                'scope':'Answer-ready and post-answer cleanup are separate; missing/ambiguous boundaries stay unknown. Native capture and isolation unchanged.'},
            'phases':phase_rows})
    return {'version':'scored-cohort-live-report-v1','observed_at':datetime.now(timezone.utc).isoformat(),'cohort_state':cohort['state'],
        'condition':cohort.get('role_routes'),'reservation_policy':cohort.get('reservation_policy'),'providers':results,
        'denominator':f"{len(cohort['providers'])} executed arms retain {planned_questions} questions and {planned_criteria} criteria each. Blocked arms remain explicit; missing/excluded/unknown cases are ungraded. Partial coverage is not a complete leaderboard.",
        'blocked_arms':cohort.get('blocked_arms',[]),
        'timing_scope':'Observed phase interval unions, including native processing/API/local overhead; phases may overlap. Background work outside windows stays other_native_or_setup. Local compute unpriced.',
        'cost_scope':'Unique attempts only. Legacy settled amounts are ledger-recorded reported charges; independent raw-response verification requires a retained receipt audit. New sanitized receipt and generation reconciliation evidence is identified separately. Judge attribution uses exact receipt/generation IDs, never time-only allocation. Reservations and unknown ceilings are separate; the legacy combined field remains for compatibility.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--ledger',type=Path,default=Path('/workspace/shared/pacbench-openrouter-pizza-day-20261112/ledger.json'));p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=build(a.root.resolve(),a.ledger);a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'providers':[{'provider':r['provider'],'state':r['state'],'graded':r['graded_answers'],'semantic_correct':r['initial_semantic_correct'],'criteria_graded':r['graded_atomic_criteria'],
        'verified_charges':display_usd(sum((Decimal(p['settled_actual_usd']) for p in r['phases'].values()),Decimal(0)))} for r in report['providers']],
        'task_accounting':budget_display(a.ledger)}))

if __name__=='__main__':main()
