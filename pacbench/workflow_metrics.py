"""Metrics from verified v2 turns, with missing criteria and partial coverage explicit."""
from collections import Counter
import json
import os
import tempfile
from math import ceil
from statistics import mean, median

from .followups import SOURCE_CATEGORIES, tool_category


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
    calls = surface['tool_calls']
    results = {r['tool_call_id']:r for r in surface['tool_results']}
    source_calls = [c for c in calls if tool_category(c['name'], c['input']) in SOURCE_CATEGORIES]
    history = surface['history']
    last_user = max(i for i, row in enumerate(history) if row['role']=='user')
    prior_source = []
    for row in history[:last_user]:
        group = row.get('tool_calls') or []
        if isinstance(group, str):
            group = json.loads(group)
        for call in group:
            fn = call['function']
            if tool_category(fn['name'], json.loads(fn['arguments'])) in SOURCE_CATEGORIES:
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
    from .canary_capability import sha
    from .workflow_spec import SPEC, compile_week
    if spec.get('sha256') != sha(SPEC):
        return None
    _, _, frozen, basis = compile_week(condition['state'])
    if spec.get('source_basis') != basis:
        return None
    if spec.get('canary'):
        frozen = frozen[:4]
    if set(condition['channels']) != {'email','imessage'}:
        frozen = []
    expected = [{'id':t.id,'at':t.at,'chat':t.session,'measure':t.measure} for t in frozen]
    if schedule != expected:
        return None
    return {t.id:(t.measure,len(t.claims)) for t in frozen}


def summarize(path):
    """Read legacy journals once, retaining compact metrics instead of full histories."""
    if not path.is_file():
        return None
    spec, plan, pending_answer = {}, None, None
    answers = 0
    turns = {}
    starts, durations = {}, Counter()
    pending_sources, last = 0, {}
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
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
    counts = [t['explicit_tool_attempts'] for t in turns]
    return {'status':last.get('phase','empty'), 'verified_turns':len(turns), 'unverified_answers':answers-len(turns),
            'coverage':coverage(by_measure, complete=last.get('phase')=='completion' and not spec.get('canary'))
                       if by_measure is not None else None,
            'turns':turns, 'workflow_elapsed_s':last.get('elapsed_s'),
            'completed_phase_wall_s':dict(durations), 'open_phases':[phase for phase, _ in starts],
            'answer_ready_upper_bound_s':distribution([t['answer_ready_upper_bound_s'] for t in turns
                                                      if t['answer_ready_upper_bound_s'] is not None]),
            'explicit_tool_attempts':{'total':sum(counts), **distribution(counts)},
            'automatic_recall_by_state':dict(Counter(t['automatic_recall'] for t in turns)),
            'percentile_method':'median p50, nearest-rank p95', 'prompt_cache_measurement':'unmeasured'}
