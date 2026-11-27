"""Read workflow evidence, summarize verified turns, and reconcile saved receipts."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import statistics
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from math import ceil
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from pacbench import process_cleanup as process_cleanup
from pacbench.judging.recovery import SOURCE_CATEGORIES, tool_category


def distribution(values):
    if not values:
        return {'observations': 0}
    values = sorted(values)
    return {'observations':len(values), 'mean':mean(values), 'p50':median(values),
            'p95':values[ceil(.95 * len(values))-1], 'max':values[-1]}


def coverage(by_measure, *, complete=None):
    planned = sum(row['planned_criteria'] for row in by_measure.values())
    graded = sum(row['graded_criteria'] for row in by_measure.values())
    correct = sum(row['correct'] or 0 for row in by_measure.values()) if graded else None
    planned_turns = sum(row['planned_turns'] for row in by_measure.values())
    verified = sum(row['answered'] for row in by_measure.values())
    full = complete is not False and planned and graded==planned and planned_turns==verified
    return {'planned_turns':planned_turns, 'verified_turns':verified,
            'planned_criteria':planned, 'graded_criteria':graded, 'ungraded_criteria':planned-graded,
            'correct':correct, 'accuracy_on_graded':correct/graded if graded else None,
            'full_accuracy':correct/planned if full else None}


def _answer_metrics(surface):
    from ..source_policy import viking_read_result_category

    calls = surface['tool_calls']
    results = {r['tool_call_id']:r for r in surface['tool_results']}
    observed = {row['tool_call_id']:row['category'] for row in surface.get('tool_provenance',[])}
    source_calls = [c for c in calls if observed.get(c['tool_call_id'],tool_category(c['name'], c['input'])) in SOURCE_CATEGORIES]
    history = surface['history']
    last_user = max(i for i, row in enumerate(history) if row['role']=='user')
    prior_source = []
    prior = history[:last_user]
    prior_results = {row.get('tool_call_id'):row for row in prior if row['role']=='tool'}
    for row in prior:
        group = row.get('tool_calls') or []
        if isinstance(group, str):
            group = json.loads(group)
        for call in group:
            fn = call['function']
            category = tool_category(fn['name'], json.loads(fn['arguments']))
            result = prior_results.get(call.get('id'))
            if fn['name']=='viking_read' and result is not None:
                category = viking_read_result_category(result.get('api_content') or result.get('content'))
            if category in SOURCE_CATEGORIES:
                prior_source.append(fn['name'])
    timing = surface.get('timing', {})
    delivery = surface.get('memory_delivery') or {}
    return {'session':surface['session_id'], 'answer_ready_upper_bound_s':timing.get('cli_result_received_s'),
            'post_result_trace_capture_s':timing.get('post_result_trace_capture_s'),
            'native_write_barrier_s':timing.get('native_write_barrier_s'),
            'automatic_recall':delivery.get('state','unknown'),
            'explicit_tool_names':dict(Counter(c['name'] for c in calls)),
            'source_lookup_calls':[{'name':c['name'], 'tool_call_id':c['tool_call_id'],
                                    'success':results.get(c['tool_call_id'],{}).get('success')} for c in source_calls],
            'generated_source_summary_calls':sum(category=='source_summary' for category in observed.values()),
            'prior_chat_source_lookups':dict(Counter(prior_source))}


def _verified_metrics(question, measure, answer, activity, criteria=None):
    return {'question':question, 'measure':measure, **answer,
            'explicit_tool_attempts':activity['explicit_attempts'], 'repeated_tool_requests':activity['repeat_requests'],
            'driver_attempt_coverage':activity.get('driver_receipts', {'coverage':'unrecorded'}), 'criterion_grades':criteria}


def progress_metrics(progress, *, after=None, limit=32):
    """Full SQL denominators with a bounded detail page ordered by turn ordinal."""
    from itertools import islice
    by_measure = progress.summary()
    turns = []
    for turn, surface, activity in islice(progress.iter_verified(page_size=limit, after=after), limit):
        grade = progress.grade_record(turn.id)
        row = _verified_metrics(turn.id, turn.measure, _answer_metrics(surface), activity, grade['criteria'])
        row['criterion_evidence'] = {c.id:list(c.evidence) for c in turn.claims}
        row['grade_state'] = grade['grade_state']
        turns.append(row)
    return {'complete':progress.complete, 'by_measure':by_measure, 'coverage':coverage(by_measure, complete=progress.complete),
            'grade_states':progress.grade_states(), 'turns':turns, 'last_turn_id':turns[-1]['question'] if turns else None}


def export_verified_metrics(progress, path):
    """Write human-readable criterion, tool and latency evidence with bounded memory."""
    cursor, count = None, 0
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        try:
            while True:
                page = progress_metrics(progress, after=cursor)
                for turn in page['turns']:
                    stream.write(json.dumps(turn, ensure_ascii=False) + '\n')
                count += len(page['turns'])
                if len(page['turns']) < 32:
                    break
                cursor = page['last_turn_id']
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(stream.name, path)
        finally:
            if os.path.exists(stream.name):
                os.unlink(stream.name)
    return {'path':path.name, 'verified_turns':count}


def _plan(spec, condition):
    schedule = spec.get('schedule')
    if schedule is None:
        return None
    if all('criteria' in row for row in schedule):
        return {row['id']:(row['measure'],row['criteria']) for row in schedule}
    # Legacy v2 kept the frozen spec/basis and turn schedule, omitting counts.
    from pacbench.corpus.r2 import SPEC, compile_pizza_week
    from pacbench.evidence_store import file_sha as sha

    canary = bool(spec.get('canary'))
    if spec.get('sha256') != sha(SPEC) or (canary and condition['state'] != 'continuing-account'):
        return None
    plan = compile_pizza_week(condition['state'], canary=canary)
    if spec.get('source_basis') != plan.source_basis:
        return None
    frozen = plan.turns if set(condition['channels']) == set(plan.channels) else []
    expected = [{'id':t.id,'at':t.at,'chat':t.session,'measure':t.measure} for t in frozen]
    if schedule != expected:
        return None
    return {t.id:(t.measure,len(t.claims)) for t in frozen}


def summarize(path, *, events=None):
    """Read a legacy journal once, optionally collecting the decoded audit events."""
    if not path.is_file():
        return None
    spec, plan, pending_answer = {}, None, None
    answers = 0
    turns = {}
    starts, durations = {}, Counter()
    pending_sources, last = 0, {}
    incomplete_tail = None
    with path.open('rb') as stream:
        for line in stream:
            try:
                event = json.loads(line if events is None else line.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if line.endswith(b'\n'):
                    raise
                incomplete_tail = {'offset': stream.tell() - len(line), 'bytes': len(line)}
                break
            if events is not None:
                events.append(event)
            phase, elapsed = event['phase'], event['elapsed_s']
            if phase == 'spec':
                spec = event
            elif phase == 'condition':
                plan = _plan(spec, event['condition'])
            elif phase == 'answer-visible':
                answers += 1
                pending_answer = (event['question'], event['surface'])
            elif phase == 'verified-turn':
                q = event['question']
                if pending_answer is None or pending_answer[0] != q or q in turns:
                    raise ValueError('verified turn lacks its unique answer')
                turns[q] = _verified_metrics(q, event['measure'], _answer_metrics(pending_answer[1]), event['activity'])
                pending_answer = None
                durations['dispatch'] += elapsed - starts.pop(('dispatch', q))
            elif phase == 'grade':
                q = event['question']
                if turns[q]['criterion_grades'] is not None:
                    raise ValueError('duplicate grade')
                turns[q]['criterion_grades'] = event['criteria']
            if phase in ('submit','sync'):
                pending_sources += 1
                starts.setdefault(('ingest',None), elapsed)
            elif phase == 'readback':
                pending_sources -= 1
                if pending_sources == 0:
                    durations['ingest'] += elapsed - starts.pop(('ingest',None))
            elif phase in ('dispatch','judge-started'):
                starts[(phase,event['question'])] = elapsed
            elif phase in ('grade','ungraded'):
                durations['judge-started'] += elapsed - starts.pop(('judge-started',event['question']))
            elif phase == 'chat-closed':
                durations['chat-close'] += event['cleanup_s']
            last = event
    turns = list(turns.values())
    by_measure = last.get('by_measure') if last.get('phase')=='completion' else None
    if by_measure is None and plan is not None:
        by_measure = {}
        for measure, count in plan.values():
            row = by_measure.setdefault(measure, {'planned_turns':0,'planned_criteria':0,'answered':0,
                                                  'graded_criteria':0,'correct':None})
            row['planned_turns'] += 1
            row['planned_criteria'] += count
        for turn in turns:
            row = by_measure[turn['measure']]
            row['answered'] += 1
            criteria = turn['criterion_grades']
            if criteria is not None:
                row['graded_criteria'] += len(criteria)
                row['correct'] = (row['correct'] or 0)+sum(c['verdict']=='correct' for c in criteria)
    complete = last.get('phase')=='completion' and not spec.get('canary')
    # External judge workers have their own journals. Rebuild current grade
    # coverage from one consistent database snapshot, retaining the native
    # journal's original shutdown status and observed phase timings.
    if (path.parent/'workflow.sqlite3').exists():
        from pacbench.workflows.progress import DOCUMENT_PROGRESS_PROTOCOL as PROTOCOL
        from pacbench.workflows.progress import workflow_fingerprint as fingerprint

        from .progress import WorkflowProgress
        metadata = json.loads((path.parent/'run.json').read_text())
        identity = metadata['identity']
        if fingerprint(identity) != metadata['run_key']:
            raise ValueError('invalid progress identity')
        if identity.get('protocol') == PROTOCOL:
            with WorkflowProgress(path.parent, metadata['run_key'], read_only=True) as progress:
                progress.db.execute('BEGIN')
                if progress.binding('identity') != identity:
                    raise ValueError('progress metadata mismatch')
                by_measure = progress.summary()
                turns = [_verified_metrics(t.id,t.measure,_answer_metrics(surface),activity,
                                           progress.grade_record(t.id)['criteria'])
                         for t,surface,activity in progress.iter_verified()]
                complete = progress.complete
    counts = [t['explicit_tool_attempts'] for t in turns]
    return {'terminal_event':last if last.get('phase') == 'stopped' else None,
            'incomplete_tail':incomplete_tail, 'status':last.get('phase','empty'), 'verified_turns':len(turns), 'unverified_answers':max(0,answers-len(turns)),
            'coverage':coverage(by_measure, complete=complete)
                       if by_measure is not None else None,
            'turns':turns, 'workflow_elapsed_s':last.get('elapsed_s'),
            'completed_phase_wall_s':dict(durations), 'open_phases':[phase for phase, _ in starts],
            'answer_ready_upper_bound_s':distribution([t['answer_ready_upper_bound_s'] for t in turns
                                                      if t['answer_ready_upper_bound_s'] is not None]),
            'explicit_tool_attempts':{'total':sum(counts), **distribution(counts)},
            'automatic_recall_by_state':dict(Counter(t['automatic_recall'] for t in turns)),
            'percentile_method':'median p50, nearest-rank p95', 'prompt_cache_measurement':'unmeasured'}


def receipt_summary(root, rows, run_id, *, attempts=None):
    """Reconcile only receipts bound to this run, attempt and upstream generation."""
    from pacbench.evidence_store import file_binding as binding
    from pacbench.evidence_store import file_sha as sha
    from pacbench.judging.retained_scope import read_canary_binding as read
    from pacbench.receipt_costs import RESPONSE_PROTOCOL as PROTOCOL
    from pacbench.workflows.progress import operation_id

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
                                       phase=ctx['phase'], attempt_state=row.get('state'),
                                       question_id=(row.get('workflow_slot') or {}).get('question_id'),
                                       role=row.get('accounting_role'))
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


def tool_activity(calls, results):
    if len(calls) != len(results):
        raise ValueError('incomplete tool trace')
    ids, requests, output = set(), Counter(), Counter()
    for call in calls:
        ident = call['tool_call_id']
        if not ident or ident in ids:
            raise ValueError('duplicate or missing tool attempt ID')
        ids.add(ident)
        requests[call['name'], json.dumps(call['input'], sort_keys=True)] += 1
    names = {c['tool_call_id']:c['name'] for c in calls}
    delivered = set()
    for result in results:
        ident = result['tool_call_id']
        if ident in delivered or ident not in names or names[ident] != result['tool']:
            raise ValueError('duplicate or mismatched tool result')
        delivered.add(ident)
        output[result['tool'], result['text']] += 1
    return {'explicit_attempts': len(ids), 'repeat_requests': len(ids)-len(requests),
            'repeat_results': len(ids)-len(output),
            'failed': sum(not r['success'] for r in results)}


def retry_activity(attempts):
    indexed = {}
    counts = Counter()
    for row in attempts:
        key, parent, origin = row['id'], row['parent'], row['origin']
        if not key or origin not in ('provider', 'harness', 'unknown'):
            raise ValueError('invalid attempt identity or origin')
        if key in indexed:
            if indexed[key] != row:
                raise ValueError('conflicting copies of one attempt')
            continue
        if parent is not None:
            if parent not in indexed or indexed[parent]['operation'] != row['operation']:
                raise ValueError('unresolved or unrelated retry parent')
            counts[origin] += 1
        indexed[key] = row
    return {'unique_attempts':len(indexed), 'linked_retries':dict(counts)}


def clip_evidence(stored, native, visible):
    """Only attribute clipping when both sides of that boundary were captured."""
    def comparison(a, b):
        if a is None or b is None:
            return 'unknown'
        if a == b:
            return 'identical'
        if b.endswith('...') and len(b[:-3]) < len(a) and a.startswith(b[:-3]):
            return 'clipped-prefix'
        return 'different'
    return {'store_to_provider_surface':comparison(stored, native),
            'provider_to_assistant':comparison(native, visible)}


def tool_text_boundary(witness, answer_visible):
    """Compare the same field when a native reader wraps source text in JSON."""
    path = witness.get('field_path', [])
    if not isinstance(path, list) or any(not isinstance(key, str) for key in path):
        raise ValueError('invalid tool field path')
    if path:
        answer_visible = json.loads(answer_visible)
        for key in path:
            answer_visible = answer_visible[key]
        if not isinstance(answer_visible, str):
            raise ValueError('tool text witness must identify a string')
    return {**clip_evidence(witness.get('stored'), witness.get('native'), answer_visible),
            'field_path':path}


def import_timestamp(value):return datetime.fromisoformat(value).timestamp()


def union_seconds(intervals):
    total=0.;end=None
    for left,right in sorted(intervals):
        if right<left:raise ValueError('Reversed timing interval')
        total+=max(0,right-max(left,end if end is not None else left));end=max(right,end if end is not None else right)
    return total


def import_timings(components,calls):
    result=[]
    for component in map(Path,components):
        summary=json.loads((component/'summary.json').read_text())
        if summary.get('mode')!='native-import-validation':continue
        cohort=json.loads((component.parent/'cohort.json').read_text());launch=next((r['started'] for r in cohort.get('results',[]) if r['provider']==component.name),cohort['started'])
        arm=component/'repeat-1'/component.name
        project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-'+component.name+'-memory'
        own=[c for c in calls if c['project']==project];batches=[]
        for path in sorted((arm/'ingestion').glob('*.json')):
            r=json.loads(path.read_text());usage=r.get('usage',{});start=usage.get('started');finish=usage.get('finished');intervals=[];unknown=[]
            if start and finish:
                left,right=import_timestamp(start),import_timestamp(finish)
                for c in own:
                    a=import_timestamp(c['started']);b=import_timestamp(c['finished']) if c.get('finished') else None
                    if a<right and (b is None or b>left):
                        if b is None:unknown.append(c['id'])
                        else:intervals.append((max(a,left),min(b,right)))
                span=union_seconds(intervals);wall=right-left
            else:span=wall=None
            batches.append({'day':path.stem,'recorded_ready':r.get('ready'),'adapter_wall_seconds':r.get('latency_s'),
                'observation_window_start':start,'observation_window_end':finish,'observation_window_seconds':wall,
                'finished_api_span_union_seconds':span,'unfinished_or_abandoned_api_requests_overlapping_window':unknown,
                'non_api_or_unresolved_window_seconds':max(0,wall-span) if wall is not None else None,
                'recovery_overlay_present':(arm/'ingestion-recovery'/path.name).exists()})
        completion=component/'recovered-processing-completion.json'
        if not completion.exists():completion=component/'import-verification.json'
        # File mtime is an explicit upper-bound observation, not a native
        # completion timestamp. It includes controller pauses and operator work.
        upper=datetime.fromtimestamp(completion.stat().st_mtime,timezone.utc).isoformat() if completion.exists() else None
        evidence=json.loads(completion.read_text()) if completion.exists() else {}
        complete=(summary['state']=='processing-complete' or evidence.get('completed_batches')==7 or (len(batches)==7 and all(b['recorded_ready'] is True for b in batches)))
        last_ready=next((b['observation_window_end'] for b in batches if b['day']=='2026-10-11' and b['recorded_ready']),None)
        result.append({'provider':component.name,'attempt':component.parent.name+'/'+component.name,
            'launch_at':launch,'native_last_batch_ready_at':last_ready if complete else None,
            'end_to_end_to_native_readiness_seconds':import_timestamp(last_ready)-import_timestamp(launch) if complete and last_ready else None,
            'launch_scope':'provider launch when persisted; otherwise shared concurrent cohort launch',
            'completion_evidence_file_written_at':upper,
            'elapsed_to_verification_observation_seconds':import_timestamp(upper)-import_timestamp(launch) if upper else None,
            'adapter_seconds_sum_available_batches':sum(b['adapter_wall_seconds'] or 0 for b in batches),
            'available_receipt_count':len(batches),'missing_or_recovered_batches':7-sum(b['recorded_ready'] is True for b in batches),
            'batches':batches})
    return {'version':'native-import-timings-v1','attempts':result,
        'interpretation':['Elapsed to verification includes provisioning, operator pauses and recovery. It is not an intrinsic provider latency ranking.',
            'Adapter wall includes native ingestion, model/API requests, embedding, native queues and readiness polling.',
            'API span is the union of observed admission-to-response intervals clipped to each receipt window; overlapping calls are not double-counted.',
            'Residual time cannot be separated into queueing, local provider work and harness overhead from these receipts alone. Unknown API intervals remain explicit.',
            'Original Cognee early receipts observed cognify only; full-operation timing additionally requires native improve completion records.',
            'Supermemory retained its completed interleaved benchmark; it has no comparable fresh import-only wall time.']}


def import_timings_main():
    p=argparse.ArgumentParser();p.add_argument('--ledger',type=Path,required=True);p.add_argument('--components',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    r=import_timings(a.components,json.loads(a.ledger.read_text())['calls']);a.output.write_text(json.dumps(r,indent=2)+'\n')


def turn_timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def gateway_events(text,timezone):
    if not timezone:return []
    zone=ZoneInfo(timezone);out=[]
    for line in text.splitlines():
        match=re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) ',line)
        if not match:continue
        kind=('answer_ready' if 'response ready:' in line else
              'shutdown_requested' if 'Shutdown context:' in line else None)
        if kind:
            naive=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S,%f')
            for ts in {naive.replace(tzinfo=zone,fold=f).timestamp() for f in [0,1]}:
                out.append((ts,kind))
    return out


def measure_turn(turn,events=()):
    t=turn if isinstance(turn,dict) else vars(turn);g=t.get('hermes_gateway') or {}
    result={'protocol':'turn-lifecycle-timing-v1','runner_wall_s':t.get('latency_s'),
        'native_reported_turn_s':t.get('native_result_duration_s'),
        'answer_ready_latency_s':None,'post_answer_cleanup_s':None,
        'answer_ready_evidence':None,'shutdown_cleanup_s':None}
    if t.get('timed_out') or t.get('returncode')!=0 or not t.get('answer'):return result
    try:a=turn_timestamp(g['real_started_at']);b=turn_timestamp(g['real_finished_at'])
    except (KeyError,ValueError,TypeError):return result
    if b<a:return result
    ready=[ts for ts,kind in events if kind=='answer_ready' and a<=ts<=b]
    if len(ready)==1:at=ready[0];source='native-gateway-response-ready-log'
    elif not ready and g.get('real_cli_result_received_at'):
        at=turn_timestamp(g['real_cli_result_received_at']);source='CLI-result-received: upper bound on answer availability'
    else:return result
    if not a<=at<=b:return result
    result.update(answer_ready_latency_s=at-a,post_answer_cleanup_s=b-at,answer_ready_evidence=source)
    shut=[ts for ts,kind in events if kind=='shutdown_requested' and at<=ts<=b]
    if len(shut)==1:result['shutdown_cleanup_s']=b-shut[0]
    elif g.get('real_shutdown_started_at'):
        s=turn_timestamp(g['real_shutdown_started_at'])
        if at<=s<=b:result['shutdown_cleanup_s']=b-s
    return result


def turn_timing_distribution(values):
    v=sorted(x for x in values if x is not None)
    if not v:return {'count':0,'mean':None,'median':None,'p95':None,'max':None}
    p=(len(v)-1)*.95;lo=int(p);hi=min(lo+1,len(v)-1)
    return {'count':len(v),'mean':statistics.mean(v),'median':statistics.median(v),
            'p95':v[lo]+(v[hi]-v[lo])*(p-lo),'max':v[-1]}


def turn_timing_summary(records):
    return {key:turn_timing_distribution(r[key] for r in records) for key in
            ['runner_wall_s','native_reported_turn_s','answer_ready_latency_s','post_answer_cleanup_s','shutdown_cleanup_s']}


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
    from pacbench.judging.retained_scope import recorded_funded_run as selected
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


def scored_report(root,ledger):
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
        from pacbench.workflows.recording import gateway_events
        from pacbench.workflows.recording import measure_turn as measure
        from pacbench.workflows.recording import turn_timing_summary as timing_summary
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
        from pacbench.workflows.source_plan import ready_sources
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


PROVIDERS = ['memcal','mem0','hindsight','supermemory','openviking','honcho','reme','cognee','memos']


EVENT_LOG = 'import-events.jsonl'


class ImportMetrics:
    def __init__(self, output):
        self.path = Path(output) / EVENT_LOG

    def emit(self, event, **values):
        row = {'event': event, 'utc': process_cleanup.now(),
               'monotonic_s': time.monotonic(), **values}
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        return row

    @contextmanager
    def phase(self, name, **values):
        identifier = str(uuid.uuid4())
        start = self.emit('phase-start', phase=name, phase_id=identifier, **values)
        try:
            yield
        except BaseException as exc:
            self.emit('phase-end', phase=name, phase_id=identifier,
                      duration_s=time.monotonic()-start['monotonic_s'],
                      state='failed', error_class=type(exc).__name__, **values)
            raise
        else:
            self.emit('phase-end', phase=name, phase_id=identifier,
                      duration_s=time.monotonic()-start['monotonic_s'], state='complete', **values)


def read_events(path):
    events=[];errors=[]
    if path.exists():
        for index,line in enumerate(path.read_text().splitlines()):
            try: events.append(json.loads(line))
            except ValueError: errors.append({'line':index+1,'state':'malformed-or-interrupted-event'})
    return events,errors


def observations(component, calls):
    component = Path(component); provider = component.name
    arm = component / 'repeat-1' / provider
    project = 'pacbench-' + hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12] + '-' + provider + '-memory'
    own = [c for c in calls if c.get('project') == project]
    events,event_errors = read_events(component / EVENT_LOG)
    starts = {e['phase_id']: e for e in events if e['event'] == 'phase-start'}
    ends = {e['phase_id']: e for e in events if e['event'] == 'phase-end'}
    phases = [{**start, 'end_utc': ends.get(key, {}).get('utc'),
               'duration_s': ends.get(key, {}).get('duration_s'),
               'state': ends.get(key, {}).get('state', 'interrupted-no-terminal-event')}
              for key, start in starts.items()]
    requests = []; repeated = {}
    for c in own:
        matches = [p for p in phases if import_timestamp(p['utc']) <= import_timestamp(c['started']) and
                   (not p['end_utc'] or import_timestamp(c['started']) <= import_timestamp(p['end_utc']))]
        phase = matches[-1] if matches else None
        row = {'id': c['id'], 'started': c['started'], 'finished': c.get('finished'),
               'duration_s': import_timestamp(c['finished'])-import_timestamp(c['started']) if c.get('finished') else None,
               'state': c['state'], 'http_status': c.get('http_status'),
               'model': c.get('served_model', c.get('model')), 'provider': c.get('served_provider'),
               'generation_id': c.get('upstream_request_id'), 'finish_reason': c.get('finish_reason'),
               'input_tokens': c.get('input_tokens'), 'output_tokens': c.get('output_tokens'),
               'usage_detail': c.get('usage_detail'), 'response_validation': c.get('response_validation'),
               'native_request_parameters': c.get('native_request_parameters'),
               'reserved_bound_usd':c.get('reserved'),
               'reservation_policy':c.get('reservation_policy','independent-input-output-v1'),
               'reported_cost_usd': c['accounted'] if c['state'] == 'settled' else None,
               'unresolved_bound_usd': c['accounted'] if c['state'] != 'settled' else None,
               'request_sha256': c.get('request_sha256'),
               'phase_id': phase['phase_id'] if phase else None,
               'phase': phase['phase'] if phase else 'unattributed-native-background',
               'batch': phase.get('batch') if phase else None}
        repeated.setdefault(c.get('request_sha256'), []).append(c['id']); requests.append(row)
    for phase in phases:
        left = import_timestamp(phase['utc']); right = import_timestamp(phase['end_utc']) if phase['end_utc'] else None
        intervals = [(max(left,import_timestamp(c['started'])),min(right,import_timestamp(c['finished']))) for c in own
            if right is not None and c.get('finished') and import_timestamp(c['started']) < right and import_timestamp(c['finished']) > left]
        span = union_seconds(intervals) if right is not None else None
        phase['observed_api_span_union_s'] = span
        phase['non_api_or_unresolved_s'] = max(0,phase['duration_s']-span) if span is not None else None
        admitted=[r for r in requests if r['phase_id']==phase['phase_id']]
        phase['requests_admitted']=len(admitted)
        phase['settled_reported_cost_usd']=str(sum(Decimal(r['reported_cost_usd']) for r in admitted if r['reported_cost_usd'] is not None))
        phase['unresolved_bound_usd']=str(sum(Decimal(r['unresolved_bound_usd']) for r in admitted if r['unresolved_bound_usd'] is not None))
    ready = next((e for e in events if e['event'] == 'native-all-batches-ready'), None)
    return {'provider': provider, 'request_count_observed': len(requests), 'requests': requests,'event_parse_errors':event_errors,
            'phases': phases, 'native_ready_observation': ready,
            'settled_reported_cost_usd': str(sum(Decimal(c['accounted']) for c in own if c['state']=='settled')),
            'unresolved_bound_usd': str(sum(Decimal(c['accounted']) for c in own if c['state']!='settled')),
            'input_tokens_observed': sum(c.get('input_tokens') or 0 for c in own),
            'output_tokens_observed': sum(c.get('output_tokens') or 0 for c in own),
            'requests_missing_token_counts': sum(c.get('input_tokens') is None or c.get('output_tokens') is None for c in own),
            'identical_request_groups': [v for k,v in repeated.items() if k and len(v)>1],
            'timing_scope': 'Real-clock phases; API duration includes network, provider queue and model time. Residual includes local compute, polling, unresolved API intervals and harness overhead; not individually identifiable. Identical bodies are reported without assuming retries or loops. Local compute and local embeddings are unpriced.'}


def select_conversation_probe(messages,source_ids,timezone):
    """One existing four-message conversation at one visibility checkpoint."""
    from zoneinfo import ZoneInfo
    if len(source_ids)!=4 or len(set(source_ids))!=4:raise ValueError('Probe requires four distinct source IDs')
    wanted=set(source_ids);selected=[m for m in messages if m['source_id'] in wanted]
    if len(selected)!=4:raise ValueError('Probe contains unknown source IDs')
    if len({(m['channel'],m['thread']) for m in selected})!=1:
        raise ValueError('Probe must remain one conversation')
    days={datetime.fromisoformat(m['collected_at']).astimezone(ZoneInfo(timezone)).date() for m in selected}
    if len(days)!=1:raise ValueError('Probe cannot combine different daily visibility checkpoints')
    return selected
