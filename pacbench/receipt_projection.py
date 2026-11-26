"""An indexed view of a run's append-only JSONL, serialized by the journal flock."""
from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat


def account_refused(row):
    status = row.get('http_status')
    code = row.get('provider_rejection')
    return (status in (401,403) or status == 429 and code != 'upstream_provider_shared_pool_rate_limit'
            or status == 402 and code not in ('openrouter_in_flight_budget','in_flight_budget_exhausted'))


def _tail_hash(journal, offset):
    begin = max(0, offset-4096)
    journal.seek(begin)
    return hashlib.sha256(journal.read(offset-begin)).digest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS cursor(
    id INTEGER PRIMARY KEY CHECK(id=1), device INTEGER, inode INTEGER, end INTEGER,
    mtime_ns INTEGER, ctime_ns INTEGER, tail_hash BLOB, change_counter INTEGER, pending BLOB
);
CREATE TABLE IF NOT EXISTS attempts(
    id TEXT PRIMARY KEY, payload TEXT NOT NULL, project TEXT, operation TEXT, role TEXT,
    refused INTEGER NOT NULL, active INTEGER NOT NULL, offline INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS operation ON attempts(project, operation);
CREATE INDEX IF NOT EXISTS refusal ON attempts(refused) WHERE refused=1;
CREATE INDEX IF NOT EXISTS active ON attempts(role) WHERE active=1;
CREATE INDEX IF NOT EXISTS offline ON attempts(offline) WHERE offline=1;
"""

ADMISSION_SCHEMA = (
    'CREATE TABLE admission_profile(name TEXT NOT NULL)',
    '''CREATE TABLE admission_verdicts(
        attempt INTEGER PRIMARY KEY, generation TEXT, invalid INTEGER NOT NULL
    )''',
    'CREATE INDEX admission_invalid ON admission_verdicts(attempt) WHERE invalid != 0',
    'CREATE TABLE admission_generations(id TEXT PRIMARY KEY, uses INTEGER NOT NULL)',
    'CREATE INDEX admission_duplicate ON admission_generations(uses) WHERE uses > 1',
    '''CREATE TABLE admission_scopes(
        kind TEXT NOT NULL, scope TEXT NOT NULL, attempt INTEGER NOT NULL,
        PRIMARY KEY(kind, scope, attempt)
    ) WITHOUT ROWID''',
    'CREATE INDEX admission_attempt_scopes ON admission_scopes(attempt)',
)


@dataclass(frozen=True)
class AdmissionSpec:
    # The name versions the verdicts and selectors persisted for a provider.
    name: str
    generation: Callable[[dict], str | None]
    invalid: Callable[[dict], bool]
    scopes: Callable[[dict], Iterable[tuple[str, object]]]


def _admission_spec(name):
    if name == 'honcho-v1':
        from pacbench.providers.honcho.receipt_index import HONCHO_RECEIPTS
        return HONCHO_RECEIPTS
    if name == 'supermemory-week-v1':
        from pacbench.providers.supermemory.week_admission import ADMISSION_SPEC
        return ADMISSION_SPEC
    raise ValueError('Unrecognized receipt admission projection')


def _scope_key(value):
    return json.dumps(value, separators=(',', ':'))


class IndexedAttempts:
    def __init__(self, db, *, strict=True, index=None, spec=None):
        self.db = db
        self.strict = strict
        self.index = index
        self.spec = spec

    def __len__(self):
        return self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]

    def __getitem__(self, ident):
        row = self.db.execute('SELECT payload FROM attempts WHERE id=?',(ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def values(self):
        # Only explicit whole-run reporting needs all attempts.
        return (json.loads(r[0]) for r in self.db.execute('SELECT payload FROM attempts ORDER BY rowid'))

    def refused(self):
        return self.db.execute('SELECT 1 FROM attempts WHERE refused=1 LIMIT 1').fetchone() is not None

    def previous(self, project, operation):
        # Three attempts exhaust ordinary retry admission. Retain the actual
        # last attempt for scope-specific checks that bind the predecessor.
        latest = self.db.execute(
            'SELECT payload FROM attempts WHERE project=? AND operation=? ORDER BY rowid DESC LIMIT 3',
            (project, operation)).fetchall()
        return [json.loads(row[0]) for row in reversed(latest)]

    def active(self):
        return self.db.execute('SELECT 1 FROM attempts WHERE active=1 LIMIT 1').fetchone() is not None

    def enable_admission(self, spec):
        """Materialize the provider's derived admission data under the journal lock."""
        if self.strict or spec is not _admission_spec(spec.name):
            raise ValueError('Unsupported receipt admission projection')
        if self.spec is not None:
            if self.spec is not spec:
                raise ValueError('Receipt admission projection belongs to a different provider')
            return
        next_counter = (_change_counter(self.index) + 1) % (2**32)
        with self.db:
            self.db.execute('BEGIN')
            for statement in ADMISSION_SCHEMA:
                self.db.execute(statement)
            for attempt, payload in self.db.execute('SELECT rowid,payload FROM attempts'):
                self._apply_admission(spec, attempt, json.loads(payload))
            self.db.execute('INSERT INTO admission_profile VALUES(?)', (spec.name,))
            self.db.execute('UPDATE cursor SET change_counter=? WHERE id=1', (next_counter,))
            self.db.execute('PRAGMA user_version=2')
        self.spec = spec

    def duplicate_generations(self):
        return self.db.execute('SELECT 1 FROM admission_generations WHERE uses > 1 LIMIT 1').fetchone() is not None

    def invalid_terminal_responses(self):
        result = self.db.execute(
            'SELECT attempt,invalid FROM admission_verdicts WHERE invalid != 0 ORDER BY attempt LIMIT 1'
        ).fetchone()
        if result is None:
            return False
        attempt, invalid = result
        if invalid == 2:
            payload = self.db.execute('SELECT payload FROM attempts WHERE rowid=?', (attempt,)).fetchone()[0]
            return bool(self.spec.invalid(json.loads(payload)))
        return True

    def for_scope(self, kind, *keys):
        if not keys:
            return []
        placeholders = ','.join('?' for _ in keys)
        result = self.db.execute(
            'SELECT payload FROM attempts WHERE rowid IN '
            f'(SELECT attempt FROM admission_scopes WHERE kind=? AND scope IN ({placeholders})) ORDER BY rowid',
            (kind, *map(_scope_key, keys)),
        )
        return [json.loads(row[0]) for row in result]

    def last_for_scope(self, kind, key):
        result = self.db.execute(
            'SELECT payload FROM attempts WHERE rowid = '
            '(SELECT attempt FROM admission_scopes WHERE kind=? AND scope=? ORDER BY attempt DESC LIMIT 1)',
            (kind, _scope_key(key)),
        ).fetchone()
        return None if result is None else json.loads(result[0])

    def count_for_scope(self, kind, key):
        return self.db.execute(
            'SELECT COUNT(*) FROM admission_scopes WHERE kind=? AND scope=?',
            (kind, _scope_key(key)),
        ).fetchone()[0]

    def _apply_admission(self, spec, attempt, row):
        generation = spec.generation(row)
        try:
            invalid = int(bool(spec.invalid(row)))
        except (KeyError, TypeError, ValueError, AttributeError):
            # Evidence must remain appendable even when legacy terminal fields
            # cannot satisfy admission. Re-evaluate that row on the failing gate.
            invalid = 2
        previous = self.db.execute(
            'SELECT generation FROM admission_verdicts WHERE attempt=?', (attempt,)
        ).fetchone()
        old_generation = previous[0] if previous else None
        if old_generation != generation:
            if old_generation is not None:
                self.db.execute('UPDATE admission_generations SET uses=uses-1 WHERE id=?', (old_generation,))
                self.db.execute('DELETE FROM admission_generations WHERE id=? AND uses=0', (old_generation,))
            if generation is not None:
                self.db.execute(
                    'INSERT INTO admission_generations VALUES(?,1) ON CONFLICT(id) DO UPDATE SET uses=uses+1',
                    (generation,),
                )
        self.db.execute('INSERT OR REPLACE INTO admission_verdicts VALUES(?,?,?)', (attempt, generation, invalid))
        self.db.execute('DELETE FROM admission_scopes WHERE attempt=?', (attempt,))
        self.db.executemany(
            'INSERT OR IGNORE INTO admission_scopes VALUES(?,?,?)',
            ((kind, _scope_key(key), attempt) for kind, key in spec.scopes(row)),
        )

    def apply(self, event):
        ident = event['id']
        if event['kind'] == 'prepared':
            row = event['row']
            if row['id'] != ident:
                raise ValueError('Prepared receipt identity differs')
        elif not self.strict or event['kind'] in ('dispatch','response','outcome','classification'):
            row = self[ident]
            row.update(event['patch'])
            if row['id'] != ident:
                raise ValueError('Receipt patch changed attempt identity')
        else:
            raise ValueError('Unsupported receipt journal event')
        fields = (json.dumps(row,separators=(',',':')), row['project'],
                  row.get('dispatch_context',{}).get('operation_id'), row.get('accounting_role'), int(account_refused(row)),
                  int(not row.get('finished')), int((row.get('upstream_request_id') or '').startswith('offline-native-')), ident)
        if event['kind']=='prepared':
            self.db.execute('INSERT INTO attempts(payload,project,operation,role,refused,active,offline,id) VALUES(?,?,?,?,?,?,?,?)', fields)
        else:
            self.db.execute('UPDATE attempts SET payload=?,project=?,operation=?,role=?,refused=?,active=?,offline=? WHERE id=?', fields)
        if self.spec is not None:
            attempt = self.db.execute('SELECT rowid FROM attempts WHERE id=?', (ident,)).fetchone()[0]
            self._apply_admission(self.spec, attempt, row)


def _change_counter(path):
    # SQLite's rollback-mode header change counter advances once per committing
    # writer. Reject independent SQL edits to the projection even when the
    # underlying JSONL (including its refusal evidence) is untouched.
    with path.open('rb') as stream:
        stream.seek(24)
        raw = stream.read(4)
    if len(raw) != 4:
        raise ValueError('Truncated receipt projection header')
    return int.from_bytes(raw, 'big')


def _checkpoint(db, journal, next_counter):
    stat = os.fstat(journal.fileno())
    db.execute('INSERT OR REPLACE INTO cursor VALUES(1,?,?,?,?,?,?,?,NULL)',
               (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, _tail_hash(journal,stat.st_size), next_counter))


class Projection:
    def __init__(self, path):
        self.path = Path(path)
        self.index = self.path.with_suffix('.index.sqlite3')

    def require_complete(self):
        """Check for an unfinished append while the caller holds the journal lock."""
        try:
            info = self.index.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Refusing a linked or non-regular receipt projection')
        uri = self.index.absolute().as_uri() + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True)) as db:
                pending = db.execute('SELECT pending IS NOT NULL FROM cursor WHERE id=1').fetchone()
        except sqlite3.Error as exc:
            raise ValueError('Cannot verify that the receipt journal has a complete append') from exc
        if pending and pending[0]:
            raise ValueError('Receipt journal has an unfinished append awaiting recovery')

    @contextmanager
    def open(self, *, initialize=False, strict=True):
        # Keep the JSONL authoritative even if its companion is lost. A missing
        # populated journal can never be silently replaced under an existing index.
        flags = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW
        if not os.path.lexists(self.index):
            flags |= os.O_CREAT
        with os.fdopen(os.open(self.path, flags, 0o600), 'r+b', buffering=0) as journal:
            fcntl.flock(journal, fcntl.LOCK_EX)
            # Create the private cache before SQLite can create it with the
            # process umask. SQLite inherits these permissions for its journal.
            index_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                os.close(os.open(self.index, index_flags, 0o600))
            except FileExistsError:
                info = self.index.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Refusing a linked or non-regular receipt projection')
                if stat.S_IMODE(info.st_mode) != 0o600:
                    self.index.chmod(0o600)
            with closing(sqlite3.connect(self.index)) as db:
                db.execute('PRAGMA synchronous=FULL')
                if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                    raise ValueError('Unexpected receipt projection journal mode')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                # Existing MemCal projections use the default application ID.
                # Keep generic legacy patch handling off those projections.
                application_id = 0 if strict else 1
                if not version:
                    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' LIMIT 1").fetchone():
                        raise ValueError('Unrecognized receipt projection')
                    db.executescript('BEGIN;'+SCHEMA+f'PRAGMA application_id={application_id}; PRAGMA user_version=1; COMMIT;')
                elif version not in (1, 2) or (strict and version == 2):
                    raise ValueError('Unsupported receipt projection version')
                if db.execute('PRAGMA application_id').fetchone()[0] != application_id:
                    raise ValueError('Receipt projection belongs to a different journal protocol')
                spec = None
                if version == 2:
                    name, = db.execute('SELECT name FROM admission_profile').fetchone()
                    spec = _admission_spec(name)
                rows = IndexedAttempts(db, strict=strict, index=self.index, spec=spec)
                cursor = db.execute('SELECT device,inode,end,mtime_ns,ctime_ns,tail_hash,change_counter,pending FROM cursor WHERE id=1').fetchone()
                if cursor is None:
                    if spec is not None:
                        raise ValueError('Receipt admission projection lost its cursor')
                    if os.fstat(journal.fileno()).st_mode & 0o777 != 0o600:
                        os.fchmod(journal.fileno(), 0o600)
                    if rows.db.execute('SELECT 1 FROM attempts LIMIT 1').fetchone():
                        raise ValueError('Receipt projection lost its cursor')
                    if os.fstat(journal.fileno()).st_size and not initialize:
                        raise ValueError('Initialize the existing receipt journal before dispatch')
                    next_counter = (_change_counter(self.index)+1) % (2**32)
                    with db, open(journal.fileno(), 'rb', closefd=False) as stream:
                        journal.seek(0)
                        for line in stream:
                            if not line.endswith(b'\n'):
                                raise ValueError('Incomplete event in existing receipt journal')
                            if line.strip():
                                rows.apply(json.loads(line))
                        _checkpoint(db, journal, next_counter)
                else:
                    self._validate(journal, db, rows, cursor)

                def append(kind, ident, patch):
                    event = {'kind':kind, 'id':ident, ('row' if kind=='prepared' else 'patch'):patch}
                    raw = (json.dumps(event,sort_keys=True)+'\n').encode()
                    # Persist the exact append intent before touching the authority.
                    # A killed writer can complete only this same byte sequence.
                    next_counter = (_change_counter(self.index)+1) % (2**32)
                    with db:
                        db.execute('UPDATE cursor SET pending=?, change_counter=? WHERE id=1',(raw,next_counter))
                    self._finish(journal, db, rows, raw, 0)
                yield rows, append

    def _validate(self, journal, db, rows, cursor):
        device, inode, offset, mtime, ctime, digest, counter, pending = cursor
        if counter != _change_counter(self.index):
            raise ValueError('Receipt projection changed outside its journal transaction')
        stat = os.fstat(journal.fileno())
        if (stat.st_dev, stat.st_ino) != (device,inode) or stat.st_size < offset or _tail_hash(journal,offset) != digest:
            raise ValueError('Receipt journal identity or validated prefix changed')
        extra = stat.st_size-offset
        if not extra and (stat.st_mtime_ns,stat.st_ctime_ns) != (mtime,ctime):
            raise ValueError('Receipt journal was modified outside its validated append')
        if pending is None:
            if extra:
                raise ValueError('Receipt journal has an unowned tail')
            return
        if extra > len(pending):
            raise ValueError('Receipt journal exceeds the durable append intent')
        journal.seek(offset)
        if journal.read(extra) != pending[:extra]:
            raise ValueError('Incomplete receipt append conflicts with durable intent')
        self._finish(journal, db, rows, pending, extra)

    def _finish(self, journal, db, rows, raw, written):
        view = memoryview(raw)[written:]
        while view:
            count = journal.write(view)
            if not count:
                raise OSError('Incomplete receipt journal write')
            view = view[count:]
        os.fsync(journal.fileno())
        next_counter = (_change_counter(self.index)+1) % (2**32)
        with db:
            rows.apply(json.loads(raw))
            _checkpoint(db, journal, next_counter)
