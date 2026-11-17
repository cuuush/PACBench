"""Explicit, provider-scoped generation reconciliation. No inference routing."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_CEILING
from .transport import open_without_redirect

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
    from datetime import datetime, timedelta, timezone
    import urllib.error
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
