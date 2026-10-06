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
            rows = db.execute(f'SELECT id,project,model,endpoint,started,finished,state,input_tokens,output_tokens,accounted,reserved FROM calls WHERE project IN ({marks}) ORDER BY started,id',tuple(roles)).fetchall()
        return {'state':'captured','at':datetime.now(timezone.utc).isoformat(), 'calls':[{**dict(r), 'role':roles[r['project']],
            'accounted_upper_bound_usd':r['accounted']/1e9} for r in rows]}
    except (OSError,sqlite3.Error) as exc:
        return {'state':'unavailable','reason':type(exc).__name__,'calls':[]}


def window(before, after):
    if before['state'] != 'captured' or after['state'] != 'captured':
        return {'state':'unavailable','reason':'Gateway ledger capture unavailable','per_tool_tokens':None}
    prior = {c['id']:c for c in before['calls']}
    new = [c for c in after['calls'] if c['id'] not in prior]
    late = [c for c in after['calls'] if c['id'] in prior and c != prior[c['id']]]
    totals = {}
    for role in ('hermes','memory_provider'):
        calls = [c for c in new if c['role'] == role]
        totals[role] = {'model_calls':len(calls),
            **{field:sum(c[field] for c in calls) if all(c[field] is not None for c in calls) else None
               for field in ('input_tokens','output_tokens')},
            'accounted_upper_bound_usd':sum(c['accounted_upper_bound_usd'] for c in calls),
            'unsettled_calls':sum(c['state'] != 'settled' for c in calls)}
    return {'state':'captured','started':before['at'],'finished':after['at'],'roles':totals,
        'model_calls':new,'late_settlements':late,'per_tool_tokens':None,
        'scope':'Actual model requests started during this turn in isolated run projects. Background calls may outlive the turn. Late settlements belong to earlier requests. Per-tool tokens are not causally attributable from native traces.'}


def tracked_turn(run_turn, prompt, *, usage_path, **kwargs):
    from .followups import save
    roles = json.loads((kwargs.get('extra_env') or {}).get('PACBENCH_USAGE_ROLES','{}'))
    before = snapshot(roles)
    try:
        return run_turn(prompt, **kwargs)
    finally:
        save(usage_path, window(before,snapshot(roles)))
