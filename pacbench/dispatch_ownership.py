"""Staged durable ownership protocol; deliberately disconnected from live dispatch.

This journal is neither a billing ledger nor permission to send. Production needs
atomic integration with the existing exposure reservation and a fenced executor.
There is no legacy import, takeover, lease-expiry release or automatic retry API.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from .receipt_costs import amount, usd

_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}')
_HASH = re.compile(r'[0-9a-f]{64}')


class OwnershipConflict(RuntimeError):
    pass


def identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError('Expected opaque identity, not free text')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True)
class AttemptContext:
    run_id: str
    arm_id: str
    phase: str
    operation_id: str
    attempt_id: str
    attempt_number: int
    worker_id: str
    process_instance_id: str
    reservation_id: str
    exposure_bound_usd: str
    retry_of: str | None = None
    retry_authorization_sha256: str | None = None

    def __post_init__(self):
        for name in ('run_id', 'arm_id', 'phase', 'operation_id', 'attempt_id', 'worker_id',
                     'process_instance_id', 'reservation_id'):
            identifier(getattr(self, name))
        if self.phase not in {'ingest', 'retrieval', 'answering', 'judging', 'background', 'embeddings'}:
            raise ValueError('Unknown causal phase')
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError('Invalid attempt number')
        if amount(self.exposure_bound_usd) is None or amount(self.exposure_bound_usd) <= 0:
            raise ValueError('A positive bounded reservation is required')
        if self.retry_of is not None:
            identifier(self.retry_of)
            if not isinstance(self.retry_authorization_sha256, str) or not _HASH.fullmatch(self.retry_authorization_sha256):
                raise ValueError('Explicit retry authorization evidence required')
        elif self.attempt_number != 1 or self.retry_authorization_sha256 is not None:
            raise ValueError('Retry causality required')

    def queue_envelope(self):
        """Exact identity crosses queues; consumers must verify it against the journal."""
        return canonical(asdict(self))

    @classmethod
    def from_queue(cls, value):
        return cls(**json.loads(value))


def create_staging_journal(path):
    """Explicit offline staging only. Never called by any PACBench runtime."""
    path = Path(path)
    # Exclusive creation prevents replacing any existing evidence or ledger.
    with path.open('xb'):
        pass
    with sqlite3.connect(path) as db:
        db.executescript('''
            PRAGMA synchronous=FULL;
            CREATE TABLE staging_metadata (purpose TEXT NOT NULL);
            INSERT INTO staging_metadata VALUES ('offline-ownership-protocol-v1');
            CREATE TABLE attempts (
                id TEXT PRIMARY KEY, run TEXT NOT NULL, arm TEXT NOT NULL,
                operation TEXT NOT NULL, number INTEGER NOT NULL, context TEXT NOT NULL,
                reservation TEXT UNIQUE NOT NULL,
                UNIQUE(run,arm,operation,number));
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                attempt_id TEXT NOT NULL REFERENCES attempts(id), kind TEXT NOT NULL,
                at TEXT NOT NULL, evidence TEXT NOT NULL);
            CREATE UNIQUE INDEX one_dispatch_boundary ON events(attempt_id)
                WHERE kind='dispatch_possible';
            CREATE TABLE provider_ids (
                billing_provider TEXT NOT NULL, generation_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL REFERENCES attempts(id),
                PRIMARY KEY(billing_provider,generation_id));
        ''')
        for table in ('attempts', 'events', 'provider_ids'):
            for action in ('UPDATE', 'DELETE'):
                db.execute(f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} "
                           "BEGIN SELECT RAISE(ABORT,'Ownership evidence is immutable'); END")


class StagedOwnership:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            if db.execute('SELECT purpose FROM staging_metadata').fetchone()[0] != 'offline-ownership-protocol-v1':
                raise OwnershipConflict('Unsupported journal; legacy migration is blocked')

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _context(self, db, context):
        row = db.execute('SELECT context FROM attempts WHERE id=?', (context.attempt_id,)).fetchone()
        if row is None or row['context'] != context.queue_envelope():
            raise OwnershipConflict('Unknown attempt or immutable ownership mismatch')

    def _event(self, db, context, event_id, kind, evidence):
        identifier(event_id)
        self._context(db, context)
        encoded = canonical(evidence)
        old = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
        if old:
            if (old['attempt_id'], old['kind'], old['evidence']) != (context.attempt_id, kind, encoded):
                raise OwnershipConflict('Event identity already records different evidence')
            return False
        db.execute('INSERT INTO events(event_id,attempt_id,kind,at,evidence) VALUES (?,?,?,?,?)',
            (event_id, context.attempt_id, kind, datetime.now(timezone.utc).isoformat(), encoded))
        return True

    def prepare(self, context):
        with self.transaction() as db:
            old = db.execute('SELECT context FROM attempts WHERE id=?', (context.attempt_id,)).fetchone()
            if old:
                self._context(db, context)
                return False
            if context.retry_of:
                prior = db.execute('SELECT context FROM attempts WHERE id=?', (context.retry_of,)).fetchone()
                if not prior:
                    raise OwnershipConflict('Unknown legacy owner; retry blocked')
                previous = AttemptContext.from_queue(prior['context'])
                if ((previous.run_id, previous.arm_id, previous.operation_id, previous.attempt_number + 1) !=
                        (context.run_id, context.arm_id, context.operation_id, context.attempt_number)):
                    raise OwnershipConflict('Retry must preserve operation causality')
                if not db.execute("SELECT 1 FROM events WHERE attempt_id=? AND kind='response_ended'",
                                  (previous.attempt_id,)).fetchone():
                    raise OwnershipConflict('Ambiguous prior execution; retry blocked')
                if previous.reservation_id == context.reservation_id:
                    raise OwnershipConflict('Retry requires separately bounded exposure')
            try:
                db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',
                    (context.attempt_id, context.run_id, context.arm_id, context.operation_id,
                     context.attempt_number, context.queue_envelope(), context.reservation_id))
            except sqlite3.IntegrityError:
                raise OwnershipConflict('Logical attempt already has an immutable owner') from None
            return True

    def mark_dispatch_possible(self, context):
        """Commit before any send. A crash after this boundary is ambiguous forever without proof."""
        with self.transaction() as db:
            return self._event(db, context, 'dispatch:' + context.attempt_id, 'dispatch_possible', {})

    def observe_receipt(self, context, event_id, *, generation_id, cost_usd=None, token_counts=None):
        identifier(generation_id)
        charge = amount(cost_usd)
        if cost_usd is not None and charge is None:
            raise ValueError('Invalid exact receipt cost')
        tokens = token_counts or {}
        if any(k not in {'input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens'} or
               type(v) is not int or v < 0 for k, v in tokens.items()):
            raise ValueError('Only normalized token counts are accepted')
        evidence = {'billing_provider': 'OpenRouter', 'generation_id': generation_id,
                    'usage_cost_usd': usd(charge), 'tokens': tokens}
        with self.transaction() as db:
            self._context(db, context)
            owner = db.execute('SELECT attempt_id FROM provider_ids WHERE billing_provider=? AND generation_id=?',
                               ('OpenRouter', generation_id)).fetchone()
            if owner and owner['attempt_id'] != context.attempt_id:
                self._event(db, context, event_id, 'receipt_identity_conflict', evidence)
                return 'conflict'
            db.execute('INSERT OR IGNORE INTO provider_ids VALUES (?,?,?)',
                       ('OpenRouter', generation_id, context.attempt_id))
            self._event(db, context, event_id, 'receipt_observed', evidence)
            return 'recorded'

    def record_outcome(self, context, event_id, *, status, proof_sha256=None):
        """Response EOF, disconnect and result/grade status never settle billing or release exposure."""
        if status not in {'response_ended', 'local_disconnect', 'answer_saved_grade_pending', 'grade_saved'}:
            raise ValueError('Unsupported outcome')
        if status != 'local_disconnect' and (not isinstance(proof_sha256, str) or not _HASH.fullmatch(proof_sha256)):
            raise ValueError('Hash-bound evidence required')
        with self.transaction() as db:
            if status == 'response_ended' and not db.execute(
                    "SELECT 1 FROM events WHERE attempt_id=? AND kind='dispatch_possible'", (context.attempt_id,)).fetchone():
                raise OwnershipConflict('Response cannot predate dispatch boundary')
            return self._event(db, context, event_id, status, {'proof_sha256': proof_sha256})

    def inspect(self, context):
        with self.connect() as db:
            self._context(db, context)
            events = [dict(r) for r in db.execute('SELECT * FROM events WHERE attempt_id=? ORDER BY sequence',
                                                (context.attempt_id,))]
        kinds = {e['kind'] for e in events}
        return {'context': asdict(context), 'events': events,
                'execution': 'ended' if 'response_ended' in kinds else 'ambiguous' if 'dispatch_possible' in kinds else 'prepared',
                'billing': 'unchanged_in_original_ledger',
                'grade_pending': 'answer_saved_grade_pending' in kinds and 'grade_saved' not in kinds,
                'production_dispatch_allowed': False, 'legacy_takeover_supported': False}
