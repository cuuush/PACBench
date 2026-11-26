"""Read workflow evidence, summarize verified turns, and reconcile saved receipts."""
from collections import Counter
from decimal import Decimal
import json
import os
import tempfile
from math import ceil
from statistics import mean, median

from ..followups import SOURCE_CATEGORIES, tool_category


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
    from pacbench.campaigns.pizza_week import SPEC, compile_pizza_week
    from ..canary_capability import sha

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
        from .progress import WorkflowProgress
        from .document_progress import PROTOCOL
        from .stream import fingerprint
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
    from ..response_receipts import PROTOCOL
    from ..canary_capability import binding, read, sha
    from ..live_dispatch import operation_id

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
