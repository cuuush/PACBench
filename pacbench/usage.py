"""Read-only model-call attribution; never guess a tool's token cost."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .gateway_client import STATE


def snapshot(roles, state=STATE):
    if not roles:
        return {'state':'unavailable','reason':'No isolated gateway project mapping','calls':[]}
    try:
        with sqlite3.connect(f'file:{Path(state) / "ledger.sqlite3"}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            marks = ','.join('?' for _ in roles)
            columns = {r['name'] for r in db.execute('PRAGMA table_info(calls)')}
            status = 'http_status' if 'http_status' in columns else 'NULL AS http_status'
            error = 'error_label' if 'error_label' in columns else 'NULL AS error_label'
            diagnostics=','.join(name if name in columns else 'NULL AS '+name
                                 for name in ('finish_reason','reasoning_tokens','upstream_request_id','generation_id',
                                              'usage_detail','actual_cost_nano','actual_cost_source','upstream_origin','reconciled_at'))
            rows = db.execute(f'SELECT id,project,model,endpoint,started,finished,state,input_tokens,output_tokens,accounted,reserved,{status},{error},{diagnostics} FROM calls WHERE project IN ({marks}) ORDER BY started,id',tuple(roles)).fetchall()
        return {'state':'captured','at':datetime.now(timezone.utc).isoformat(), 'calls':[ledger_call(r, roles[r['project']]) for r in rows]}
    except (OSError,sqlite3.Error) as exc:
        return {'state':'unavailable','reason':type(exc).__name__,'calls':[]}


# Only numeric token dimensions are exported; raw upstream payloads never leave the ledger.
TOKEN_DETAILS = ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens',
                 'audio_tokens', 'image_tokens', 'accepted_prediction_tokens', 'rejected_prediction_tokens')

def ledger_call(row, role):
    call = dict(row)
    raw = call.pop('usage_detail', None)
    try:
        detail = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        detail = None
    call['usage_detail'] = {k: v for k, v in (detail or {}).items()
                            if (k in TOKEN_DETAILS or (k.rpartition('.')[0] in ('prompt_tokens_details', 'completion_tokens_details', 'input_tokens_details', 'output_tokens_details') and k.rpartition('.')[2] in TOKEN_DETAILS)) and type(v) is int and v >= 0} if isinstance(detail, dict) else {}
    nano = call.pop('actual_cost_nano', None)
    call['actual_cost_usd'] = nano / 1e9 if type(nano) is int and nano >= 0 else None
    call['role'] = role
    call['accounted_upper_bound_usd'] = call['accounted'] / 1e9
    return call

def summarize_calls(calls):
    """Keep measured charges, estimates and unresolved guard amounts separate."""
    def complete_sum(key):
        values = [c.get(key) for c in calls]
        return sum(values) if all(v is not None for v in values) else None
    actual = [c.get('actual_cost_usd') for c in calls]
    return {'model_calls': len(calls),
            'input_tokens': complete_sum('input_tokens'), 'output_tokens': complete_sum('output_tokens'),
            'token_details': {key: sum(c.get('usage_detail', {}).get(key, 0) for c in calls)
                              if all(key in c.get('usage_detail', {}) for c in calls) else None
                              for key in sorted(set(TOKEN_DETAILS) | {k for c in calls for k in c.get('usage_detail', {})})},
            'actual_cost_usd': sum(actual) if all(v is not None for v in actual) else None,
            'actual_cost_known_usd': sum(v for v in actual if v is not None),
            'actual_cost_unknown_calls': sum(v is None for v in actual),
            'token_price_estimate_usd': sum(c['accounted_upper_bound_usd'] for c in calls
                                            if c.get('state') == 'settled' and c.get('actual_cost_usd') is None),
            'unconfirmed_budget_bound_usd': sum(c['accounted_upper_bound_usd'] for c in calls
                                                if c.get('state') != 'settled'),
            'accounted_upper_bound_usd': sum(c['accounted_upper_bound_usd'] for c in calls),
            'unsettled_calls': sum(c.get('state') != 'settled' for c in calls)}


def provider_model_health(capture):
    """Stop paid evaluation after repeated bad requests, without guessing recall quality.

    A single rejected request can be a successful native fallback. Count only
    completed, definitive 400/422 responses in this arm's provider projects;
    pending requests and successful responses with missing usage aren't HTTP
    errors. Repeated completed unmetered requests have a separate cost stop.
    """
    groups = {};unmetered={}
    for call in capture.get('calls', []):
        if (call.get('role') == 'memory_provider' and call.get('finished') and
                call.get('http_status') in (400, 422)):
            groups.setdefault(call['project'], []).append(call)
        if (call.get('role')=='memory_provider' and call.get('finished') and
                call.get('state') in {'unknown','uncertain'} and call.get('http_status')==200 and
                (call.get('input_tokens') is None or call.get('output_tokens') is None)):
            unmetered.setdefault(call['project'],[]).append(call)
    repeated = [c for calls in groups.values() if len(calls) >= 3 for c in calls]
    unknown = [c for calls in unmetered.values() if len(calls)>=3 for c in calls]
    return {'state': 'stop' if repeated or unknown else capture.get('state', 'unavailable'),
            'failed_requests': [{k: c.get(k) for k in
                ('id', 'project', 'http_status', 'state', 'accounted_upper_bound_usd')}
                for c in repeated],
            'unmetered_requests':[{k:c.get(k) for k in
                ('id','project','http_status','state','error_label','accounted_upper_bound_usd')}
                for c in unknown],
            'reason': ('Repeated provider-side HTTP 400/422 responses; stop further paid checks '
                       'in this arm. Native completion alone does not establish healthy model execution.'
                       if repeated else
                       'Three completed provider requests lack usable token usage; stop this arm '
                       'before unmetered work accumulates. This is a cost-observability blocker, '
                       'not an HTTP or memory-recall failure.' if unknown else ''),
            'scope': 'Execution/cost barrier, not a semantic memory grade. Held charges remain held.'}


def window(before, after):
    if before['state'] != 'captured' or after['state'] != 'captured':
        return {'state':'unavailable','reason':'Gateway ledger capture unavailable','per_tool_tokens':None}
    prior = {c['id']:c for c in before['calls']}
    new = [c for c in after['calls'] if c['id'] not in prior]
    late = [c for c in after['calls'] if c['id'] in prior and c != prior[c['id']]]
    totals = {}
    for role in ('hermes','memory_provider'):
        calls = [c for c in new if c['role'] == role]
        totals[role] = summarize_calls(calls)
    grouped = {}
    for call in new:
        key = (call.get('role'), call.get('project'), call.get('model'))
        grouped.setdefault(key, []).append(call)
    groups = [{'role': key[0], 'project': key[1], 'model': key[2], **summarize_calls(calls)}
              for key, calls in grouped.items()]
    return {'state':'captured','started':before['at'],'finished':after['at'],'roles':totals,
        'model_calls':new,'groups':groups,'late_settlements':late,'per_tool_tokens':None,
        'scope':'Actual model requests started during this turn in isolated run projects. Background calls may outlive the turn. Late settlements belong to earlier requests. Per-tool tokens are not causally attributable from native traces.'}


def refresh_window(capture, latest):
    """Refresh only the window's request IDs; keep the original receipt untouched."""
    if not capture or capture.get('state')!='captured' or not latest or latest.get('state')!='captured':
        return None
    known={c['id']:c for c in latest.get('calls',[])}
    calls=[known.get(c['id'],c) for c in capture.get('model_calls',[])]
    if calls==capture.get('model_calls',[]):return None
    updated=window({'state':'captured','at':capture['started'],'calls':[]},
        {'state':'captured','at':capture['finished'],'calls':calls})
    return {**capture,'model_calls':calls,'roles':updated['roles'],'groups':updated['groups'],
            'settlement_observed_at':latest.get('at')}


def tracked_turn(run_turn, prompt, *, usage_path, **kwargs):
    from .followups import save
    roles = json.loads((kwargs.get('extra_env') or {}).get('PACBENCH_USAGE_ROLES','{}'))
    before = snapshot(roles)
    try:
        result=run_turn(prompt, **kwargs)
    finally:
        capture=window(before,snapshot(roles))
        capture['path_attribution_version']='native-completion-v1'
        save(usage_path,capture)
    attribute_provider_paths(result,capture)
    return result


def attribute_provider_paths(turn, capture):
    """A native response completed after every Hermes input cannot supply it.

    Honcho's journal and Hindsight's native trace API prove completion timestamps. Keep
    all operations and model cost, but do not turn unrelated late profile
    work into an answer's source-search penalty. Missing proof stays unknown.
    """
    starts=[]
    for call in capture.get('model_calls',[]):
        if call.get('role')!='hermes':continue
        try:starts.append(datetime.fromisoformat(call['started']).timestamp())
        except (KeyError,TypeError,ValueError):return
    if not starts:return
    cutoff=max(starts)
    for trace in getattr(turn,'provider_traces',[]) or []:
        # Honcho journals use the provider wall clock. Gateway timestamps stay
        # real; their ordering is unknowable across simulated clock advances.
        if trace.get('provider') == 'honcho' and getattr(turn,'provider_clock',None):
            continue
        completed=trace.get('record',{}).get('timestamp')
        if (trace.get('provider') in {'honcho','hindsight'} and type(completed) in (int,float) and completed>cutoff):
            trace['path_attribution']={
                'version':'native-completion-v1','affects_answer':False,
                'final_hermes_input_at':datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),
                'native_completed_at':datetime.fromtimestamp(completed,timezone.utc).isoformat(),
                'reason':'Native response completed after the final Hermes request input; background operations and costs remain recorded'}


def provider_paths_match(turn, capture):
    if capture.get('path_attribution_version')!='native-completion-v1':return True
    from copy import deepcopy
    expected=deepcopy(turn)
    for trace in expected.provider_traces:trace.pop('path_attribution',None)
    attribute_provider_paths(expected,capture)
    return expected.provider_traces==turn.provider_traces
