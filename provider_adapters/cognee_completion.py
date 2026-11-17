"""Read native local pipeline records without changing provider state."""
import sqlite3
from pathlib import Path
from .cognee_local import QualificationError


def pipeline_rows(root):
    db=Path(root)/'cognee-system/databases/cognee_db'
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        return [dict(r) for r in connection.execute('select id,pipeline_run_id,operation_name,status,outcome,ended_at,background from pipeline_runs order by created_at')]


def completed(rows, prior_ids):
    """STARTED history is retained: each run needs a later terminal witness.

    A background remember's succeeded operation row witnesses acceptance only.
    Native automatic improve must also finish before the next daily import.
    """
    new=[r for r in rows if r['id'] not in prior_ids]
    groups={}
    for r in new:groups.setdefault(r['pipeline_run_id'],[]).append(r)
    if not groups:return False
    improve=False
    for group in groups.values():
        terminal=[r for r in group if r.get('ended_at') and r.get('outcome')]
        if not terminal:return False
        r=terminal[-1]
        if r['outcome']!='succeeded':raise QualificationError('Native operation failed: '+str(r['operation_name']))
        if r['operation_name']=='improve':improve=True
    return improve


def require_idle(rows):
    groups={}
    for r in rows:groups.setdefault(r['pipeline_run_id'],[]).append(r)
    if any(not any(r.get('ended_at') and r.get('outcome') for r in group) for group in groups.values()):
        raise QualificationError('Previous native operation has no terminal witness; do not add another document')
