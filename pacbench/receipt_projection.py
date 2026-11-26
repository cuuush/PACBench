"""An indexed view of a run's append-only JSONL, serialized by the journal flock."""
from contextlib import closing, contextmanager
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


class IndexedAttempts:
    def __init__(self, db, *, strict=True):
        self.db = db
        self.strict = strict

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
                elif version != 1:
                    raise ValueError('Unsupported receipt projection version')
                if db.execute('PRAGMA application_id').fetchone()[0] != application_id:
                    raise ValueError('Receipt projection belongs to a different journal protocol')
                rows = IndexedAttempts(db, strict=strict)
                cursor = db.execute('SELECT device,inode,end,mtime_ns,ctime_ns,tail_hash,change_counter,pending FROM cursor WHERE id=1').fetchone()
                if cursor is None:
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
