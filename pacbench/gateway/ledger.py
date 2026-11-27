"""Reserve worst-case request costs before transport; never erase unknown costs."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from pacbench.gateway import open_without_redirect

NANO = 1_000_000_000
DAILY_LIMIT = 10 * NANO
MODEL = 'muse-spark-1.3-contributor'
CONTEXT_TOKENS = 1_048_576
# Conservative uncached prices: $0.10 input / $0.20 output per million tokens.
INPUT_NANO = 100
OUTPUT_NANO = 200
RESERVATION = CONTEXT_TOKENS * (INPUT_NANO + OUTPUT_NANO)


def clock():
    return datetime.now(timezone.utc).isoformat()


class BudgetRefused(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, *, now=clock, existing_only=False):
        self.path, self.now, self.existing_only = Path(path), now, existing_only
        if not existing_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            with self.connect() as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calls'").fetchone():
                    raise ValueError('Existing gateway ledger required')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, model TEXT NOT NULL,
                    endpoint TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
                    state TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                    accounted INTEGER NOT NULL, reserved INTEGER NOT NULL,
                    http_status INTEGER, error_label TEXT);
                CREATE TABLE IF NOT EXISTS refusals (
                    day TEXT PRIMARY KEY, at TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS service_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(calls)')}
            for name,kind in (('finish_reason','TEXT'),('reasoning_tokens','INTEGER'),('protocol_diagnostic','TEXT'),('upstream_request_id','TEXT'),('generation_id','TEXT'),('usage_detail','TEXT'),('actual_cost_nano','INTEGER'),('actual_cost_source','TEXT'),('upstream_origin','TEXT'),('reconciled_at','TEXT'),('reconcile_attempts','INTEGER'),('reconcile_last_attempt','TEXT'),('reconcile_status','TEXT'),('reconcile_next_attempt','TEXT'),('generation_usage_detail','TEXT'),('actual_cost_usd_decimal','TEXT')):
                if name not in columns:db.execute(f'ALTER TABLE calls ADD COLUMN {name} {kind}')

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", timeout=30, uri=True) if self.existing_only else sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def spent(db, day):
        # Only still-running requests span days. A completed unknown request
        # keeps its full bound on its original UTC request date indefinitely.
        return db.execute("SELECT COALESCE(SUM(accounted),0) FROM calls "
                          "WHERE substr(started,1,10)=? OR state='reserved'", (day,)).fetchone()[0]

    def reserve(self, project, endpoint):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stamp = self.now()
            day = stamp[:10]
            blocked = db.execute('SELECT 1 FROM refusals WHERE day=?', (day,)).fetchone()
            if blocked or self.spent(db, day) + RESERVATION > DAILY_LIMIT:
                db.execute('INSERT OR IGNORE INTO refusals VALUES (?,?,?)',
                           (day, stamp, 'worst_case_request_exceeds_daily_limit'))
                db.commit()
                raise BudgetRefused('Daily spending boundary reached; wait until the next UTC day')
            request_id = uuid.uuid4().hex
            db.execute('INSERT INTO calls '
                       '(id,project,model,endpoint,started,state,accounted,reserved,upstream_origin) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (request_id, project, MODEL, endpoint, stamp, 'reserved', RESERVATION, RESERVATION, 'direct_meta'))
            return request_id

    def observe_metadata(self, request_id, metadata):
        """Persist safe partial stream metadata before the next network read."""
        from pacbench.gateway.server import safe_metadata
        metadata = safe_metadata(metadata)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,upstream_request_id,generation_id,usage_detail FROM calls WHERE id=?',
                             (request_id,)).fetchone()
            if row is None or row['state'] != 'reserved':
                return
            old = json.loads(row['usage_detail']) if row['usage_detail'] else {}
            old.update(metadata.get('usage', {}))
            db.execute("UPDATE calls SET upstream_request_id=?, generation_id=?, usage_detail=?, reconcile_status=CASE WHEN ? IS NOT NULL AND reconcile_status='missing_generation_id' THEN NULL ELSE reconcile_status END WHERE id=?",
                       (metadata.get('upstream_request_id') or row['upstream_request_id'],
                        metadata.get('generation_id') or row['generation_id'],
                        json.dumps(old,sort_keys=True) if old else None, metadata.get('generation_id'), request_id))

    def finish(self, request_id, *, usage=None, status=None, error=None, diagnostic=None, metadata=None):
        valid = (isinstance(usage, tuple) and len(usage) == 2 and
                 all(type(n) is int and 0 <= n <= CONTEXT_TOKENS for n in usage))
        diagnostic=diagnostic if isinstance(diagnostic,dict) else {}
        reason=diagnostic.get('finish_reason')
        if reason not in ('stop','length','tool_calls','function_call','content_filter'):reason=None
        reasoning=diagnostic.get('reasoning_tokens')
        if not (type(reasoning) is int and 0<=reasoning<=(usage[1] if valid else CONTEXT_TOKENS)):reasoning=None
        from .server import safe_protocol
        protocol=safe_protocol(diagnostic.get('protocol'))
        from pacbench.gateway.server import safe_metadata
        metadata = safe_metadata(metadata)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM calls WHERE id=?', (request_id,)).fetchone()
            if row is None or row['state'] != 'reserved':
                raise ValueError('Only an active request can be completed')
            price = usage[0]*INPUT_NANO + usage[1]*OUTPUT_NANO if valid else row['reserved']
            db.execute('UPDATE calls SET finished=?,state=?,input_tokens=?,output_tokens=?, '
                       'accounted=?,http_status=?,error_label=?,finish_reason=?,reasoning_tokens=?,protocol_diagnostic=?, '
                       'upstream_request_id=?,generation_id=?,usage_detail=? WHERE id=?',
                       (self.now(), 'settled' if valid else 'unknown',
                        usage[0] if valid else None, usage[1] if valid else None,
                        price, status, error, reason, reasoning,
                        json.dumps(protocol,sort_keys=True) if protocol else None,
                        metadata.get('upstream_request_id') or row['upstream_request_id'], metadata.get('generation_id') or row['generation_id'],
                        json.dumps({**(json.loads(row['usage_detail']) if row['usage_detail'] else {}), **metadata.get('usage', {})},sort_keys=True) if row['usage_detail'] or metadata.get('usage') else None,request_id))

    def recover_abandoned(self):
        # Caller must first acquire the exclusive server lock: no old gateway
        # worker can still hold this lock or complete a request afterward.
        with self.connect() as db:
            db.execute("UPDATE calls SET state='unknown',finished=?,error_label='process_restart' "
                       "WHERE state='reserved'", (self.now(),))

    def health(self):
        day = self.now()[:10]
        with self.connect() as db:
            spent = self.spent(db, day)
            blocked = bool(db.execute('SELECT 1 FROM refusals WHERE day=?', (day,)).fetchone())
            active = db.execute("SELECT COUNT(*) FROM calls WHERE state='reserved'").fetchone()[0]
            auth_blocked = bool(db.execute("SELECT 1 FROM service_state WHERE key='upstream_auth_blocked'").fetchone())
        return {'service':'pacbench-budget-gateway', 'daily_limit_usd':10.0,
                'total_limit_usd':None, 'accounted_today_usd':spent/NANO,
                'budget_blocked':blocked or spent >= DAILY_LIMIT,
                'active_reservations':active, 'utc_day':day,
                'upstream_auth_blocked':auth_blocked}

    def block_auth(self):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO service_state VALUES ('upstream_auth_blocked',?)",(self.now(),))

    def confirm_auth(self):
        # Called only after a successful free upstream model inventory.
        with self.connect() as db:
            db.execute("DELETE FROM service_state WHERE key='upstream_auth_blocked'")


_GENERATION = re.compile(r'^gen-[0-9A-Za-z-]{1,123}$')


_URL = 'https://openrouter.ai/api/v1/generation'


def fetch_generation(generation_id, api_key, *, opener=open_without_redirect):
    """Fetch only documented metadata, never the generation/content endpoint."""
    if not _GENERATION.fullmatch(generation_id):
        raise ValueError('Invalid OpenRouter generation ID')
    request = urllib.request.Request(_URL + '?' + urllib.parse.urlencode({'id': generation_id}),
                                     headers={'Authorization': 'Bearer ' + api_key})
    with opener(request, timeout=30) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError('Generation metadata exceeds bound')
        return json.loads(body, parse_float=Decimal)


def reconcile(ledger, request_id, fetch):
    """Attempt one lookup; callers can retry unresolved rows after restart.

    `fetch` receives an OpenRouter generation ID. Network I/O occurs outside
    the SQLite write transaction. Failures and incomplete responses preserve
    the previous accounting; an authoritative zero is distinct from unknown.
    """
    with ledger.connect() as db:
        row = db.execute('SELECT upstream_origin,generation_id,state,actual_cost_nano FROM calls WHERE id=?', (request_id,)).fetchone()
    if row is None or row['upstream_origin'] != 'openrouter' or row['state'] == 'reserved':
        return 'ineligible'
    if row['actual_cost_nano'] is not None:
        return 'already_reconciled'
    generation_id = row['generation_id']
    if not isinstance(generation_id, str) or not _GENERATION.fullmatch(generation_id):
        return 'missing_generation_id'
    try:
        payload = fetch(generation_id)
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get('id') != generation_id:
            return 'unresolved'
        cost = data.get('total_cost')
        if isinstance(cost, bool) or not isinstance(cost, (int, float, Decimal)):
            return 'unresolved'
        amount = Decimal(str(cost))
        if not amount.is_finite() or amount < 0:
            return 'unresolved'
        nanos = int((amount * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
        if nanos > 2**63-1:
            return 'unresolved'
        fields = ('native_tokens_prompt','native_tokens_completion','native_tokens_reasoning','native_tokens_cached',
                  'tokens_prompt','tokens_completion','native_tokens_completion_images')
        generation_usage = {k:data[k] for k in fields if type(data.get(k)) is int and 0 <= data[k] <= 1_048_576}
    except Exception:
        return 'lookup_failed'
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        duplicate = db.execute("SELECT 1 FROM calls WHERE upstream_origin='openrouter' AND generation_id=? AND id!=? AND actual_cost_nano IS NOT NULL", (generation_id, request_id)).fetchone()
        if duplicate:
            return 'duplicate_generation_id'
        updated = db.execute("UPDATE calls SET actual_cost_nano=?,actual_cost_source='openrouter_generation_total_cost', "
                             "reconciled_at=?,accounted=?,generation_usage_detail=?,actual_cost_usd_decimal=?,state='settled' WHERE id=? AND upstream_origin='openrouter' "
                             "AND generation_id=? AND state!='reserved' AND actual_cost_nano IS NULL",
                             (nanos,ledger.now(),nanos,json.dumps(generation_usage,sort_keys=True) if generation_usage else None,str(amount),request_id,generation_id)).rowcount
        if updated:
            day = db.execute('SELECT substr(started,1,10) FROM calls WHERE id=?',(request_id,)).fetchone()[0]
            if ledger.spent(db,day) > 10_000_000_000:
                db.execute('INSERT OR IGNORE INTO refusals VALUES (?,?,?)', (day,ledger.now(),'authoritative_charge_exceeds_daily_limit'))
    return 'reconciled' if updated else 'already_reconciled'


def reconcile_pending(ledger, fetch):
    """Retry each eligible unresolved row once; safe to invoke repeatedly."""
    with ledger.connect() as db:
        ids = [row[0] for row in db.execute("SELECT id FROM calls WHERE upstream_origin='openrouter' "
                "AND state!='reserved' AND actual_cost_nano IS NULL ORDER BY started,id")]
    return {request_id: reconcile(ledger, request_id, fetch) for request_id in ids}


def retry_due(ledger, fetch, *, limit=100):
    """Attempt a bounded batch, persisting retry state without storing errors."""
    import urllib.error
    from datetime import datetime, timedelta, timezone
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Batch limit must be between 1 and 1000')
    now = datetime.fromisoformat(ledger.now()).astimezone(timezone.utc)
    stamp = now.isoformat()
    with ledger.connect() as db:
        rows = db.execute("SELECT id,generation_id FROM calls WHERE upstream_origin='openrouter' "
                          "AND state!='reserved' AND actual_cost_nano IS NULL "
                          "AND (reconcile_next_attempt IS NULL OR reconcile_next_attempt<=?) "
                          "AND COALESCE(reconcile_status,'') NOT IN ('auth_blocked','missing_generation_id','duplicate_generation_id') "
                          "ORDER BY started,id LIMIT ?", (stamp,limit)).fetchall()
    results = {}
    for row in rows:
        category = ['lookup_failed']
        def checked_fetch(generation_id):
            try:
                return fetch(generation_id)
            except urllib.error.HTTPError as exc:
                category[0] = 'not_ready' if exc.code == 404 else 'auth_blocked' if exc.code in (401,403) else 'lookup_failed'
                raise
            except TimeoutError:
                category[0] = 'timeout'
                raise
        result = reconcile(ledger,row['id'],checked_fetch)
        if result == 'lookup_failed': result = category[0]
        results[row['id']] = result
        if result in ('already_reconciled','ineligible'): continue
        with ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT reconcile_attempts,actual_cost_nano FROM calls WHERE id=?',(row['id'],)).fetchone()
            if not current: continue
            # Another worker may have completed while this one was fetching.
            if current['actual_cost_nano'] is not None and result != 'reconciled': continue
            attempts = (current['reconcile_attempts'] or 0) + (result != 'missing_generation_id')
            delay = min(3600, 60 * (2 ** min(max(attempts - 1,0), 6)))
            retryable = result in ('not_ready','timeout','lookup_failed','unresolved')
            next_at = (now + timedelta(seconds=delay)).isoformat() if retryable else None
            db.execute('UPDATE calls SET reconcile_attempts=?,reconcile_last_attempt=?,reconcile_status=?,reconcile_next_attempt=? WHERE id=?',
                       (attempts,stamp,result,next_at,row['id']))
    return results


def reconcile_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='Existing gateway state directory')
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args(argv)
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        parser.error('OPENROUTER_API_KEY is required')
    try:
        ledger = Ledger(args.state/'ledger.sqlite3', existing_only=True)
        result = retry_due(ledger, lambda generation_id: fetch_generation(generation_id,key), limit=args.limit)
    except (OSError, ValueError, __import__('sqlite3').Error):
        parser.error('Existing gateway ledger unavailable or invalid')
    counts = {}
    for value in result.values(): counts[value] = counts.get(value,0)+1
    print(json.dumps({'results':counts},sort_keys=True))
