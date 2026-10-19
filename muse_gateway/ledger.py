"""Reserve worst-case request costs before transport; never erase unknown costs."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
    def __init__(self, path: Path, *, now=clock):
        self.path, self.now = Path(path), now
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def spent(db, day):
        # Only still-running requests span days. A completed unknown request
        # keeps its full bound on its original UTC request date indefinitely.
        return db.execute("SELECT COALESCE(SUM(accounted),0) FROM calls "
                          "WHERE substr(started,1,10)=? OR state='reserved'", (day,)).fetchone()[0]

    def reserve(self, project, endpoint):
        stamp = self.now()
        day = stamp[:10]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            blocked = db.execute('SELECT 1 FROM refusals WHERE day=?', (day,)).fetchone()
            if blocked or self.spent(db, day) + RESERVATION > DAILY_LIMIT:
                db.execute('INSERT OR IGNORE INTO refusals VALUES (?,?,?)',
                           (day, stamp, 'worst_case_request_exceeds_daily_limit'))
                db.commit()
                raise BudgetRefused('Daily spending boundary reached; wait until the next UTC day')
            request_id = uuid.uuid4().hex
            db.execute('INSERT INTO calls '
                       '(id,project,model,endpoint,started,state,accounted,reserved) '
                       'VALUES (?,?,?,?,?,?,?,?)',
                       (request_id, project, MODEL, endpoint, stamp, 'reserved', RESERVATION, RESERVATION))
            return request_id

    def finish(self, request_id, *, usage=None, status=None, error=None):
        valid = (isinstance(usage, tuple) and len(usage) == 2 and
                 all(type(n) is int and 0 <= n <= CONTEXT_TOKENS for n in usage))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM calls WHERE id=?', (request_id,)).fetchone()
            if row is None or row['state'] != 'reserved':
                raise ValueError('Only an active request can be completed')
            price = usage[0]*INPUT_NANO + usage[1]*OUTPUT_NANO if valid else row['reserved']
            db.execute('UPDATE calls SET finished=?,state=?,input_tokens=?,output_tokens=?, '
                       'accounted=?,http_status=?,error_label=? WHERE id=?',
                       (self.now(), 'settled' if valid else 'unknown',
                        usage[0] if valid else None, usage[1] if valid else None,
                        price, status, error, request_id))

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
        return {'service':'muse-budget-gateway', 'daily_limit_usd':10.0,
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
