"""Durable progress for one ordered workflow account and independent judge workers.

An effect row is committed *before* dispatch. An interrupted dispatch remains
pending until its external outcome can be investigated. Grade claims follow the
same rule. SQLite WAL permits independent readers during either worker's calls.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from .semantic_grade import Claim, VERDICTS
from .workflow_spec import Turn, instant


class UnknownEffect(RuntimeError):
    """An earlier dispatch may have changed external state or incurred cost."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _turn(encoded):
    data = json.loads(encoded)
    data['claims'] = tuple(Claim(c['id'], c['description'], tuple(c['evidence'])) for c in data['claims'])
    return Turn(**data)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), run_key TEXT NOT NULL, version INTEGER NOT NULL,
    last_cutoff TEXT, execution_sealed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bindings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effects (
    phase TEXT NOT NULL, op_key TEXT NOT NULL, input_sha256 TEXT NOT NULL, result TEXT,
    role TEXT, attempt_id TEXT NOT NULL UNIQUE, reconciliation TEXT, PRIMARY KEY(phase, op_key)
);
CREATE TABLE IF NOT EXISTS observations (
    attempt_id TEXT NOT NULL REFERENCES effects(attempt_id), name TEXT NOT NULL, artifact TEXT NOT NULL,
    PRIMARY KEY(attempt_id, name)
);
CREATE INDEX IF NOT EXISTS pending_effects ON effects(phase, op_key) WHERE result IS NULL;
CREATE TABLE IF NOT EXISTS turns (
    ordinal INTEGER PRIMARY KEY CHECK(ordinal >= 0), id TEXT NOT NULL UNIQUE, session TEXT NOT NULL,
    measure TEXT NOT NULL, criteria_count INTEGER NOT NULL, plan TEXT NOT NULL,
    surface TEXT, activity TEXT, done INTEGER NOT NULL DEFAULT 0,
    grade_state TEXT NOT NULL DEFAULT 'waiting', grade TEXT, failure TEXT, correct INTEGER, grade_attempt TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS unfinished_turns ON turns(ordinal) WHERE done = 0;
CREATE INDEX IF NOT EXISTS chat_history ON turns(session, ordinal) WHERE surface IS NOT NULL;
CREATE INDEX IF NOT EXISTS summary_counts ON turns(measure, grade_state, criteria_count, correct);
CREATE INDEX IF NOT EXISTS grade_queue ON turns(grade_state, ordinal);
CREATE TABLE IF NOT EXISTS sessions (native_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT NOT NULL, turn_id TEXT NOT NULL REFERENCES turns(id), PRIMARY KEY(request_id, turn_id)
);
"""


class WorkflowProgress:
    def __init__(self, directory, run_key, *, create=False, read_only=False):
        if not isinstance(run_key, str) or not run_key.strip():
            raise ValueError('a run binding is required')
        self.directory = Path(directory).resolve()
        self.run_key = run_key
        if read_only and create:
            raise ValueError('a read-only progress reader cannot initialize a run')
        self.read_only = read_only
        self.create = create

    def __enter__(self):
        path = self.directory / 'workflow.sqlite3'
        if self.create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Keep private content protected even when the parent directory already exists.
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
        mode = 'ro' if self.read_only else 'rw'
        self.db = sqlite3.connect(path.as_uri() + '?mode='+mode, uri=True, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA synchronous=FULL')
            if self.create:
                self.db.execute('PRAGMA journal_mode=WAL')
                self.db.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA)
                self.db.execute('INSERT OR IGNORE INTO run(singleton,run_key,version) VALUES (1,?,2)', (self.run_key,))
            row = self.db.execute('SELECT run_key,version FROM run WHERE singleton=1').fetchone()
            if row is None or tuple(row) != (self.run_key, 2):
                raise ValueError('workflow progress belongs to a different run or format')
            self.db.commit()
        except BaseException:
            self.db.close()
            raise
        return self

    def __exit__(self, *exc):
        self.db.close()

    @contextmanager
    def _write(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @contextmanager
    def account_owner(self):
        """Exclude other native stream owners without locking out a judge."""
        with (self.directory / 'workflow-account.lock').open('ab') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('workflow account stream already has an owner') from exc
            yield

    def _bind(self, name, value):
        encoded = _json(value)
        self.db.execute('INSERT OR IGNORE INTO bindings VALUES (?,?)', (name, encoded))
        if self.db.execute('SELECT value FROM bindings WHERE name=?', (name,)).fetchone()[0] != encoded:
            raise ValueError(f'workflow binding changed: {name}')

    def bind(self, name, value):
        with self._write():
            self._bind(name, value)

    def binding(self, name):
        row = self.db.execute('SELECT value FROM bindings WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def plan_turn(self, ordinal, turn):
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('invalid plan ordinal')
        plan = _json(asdict(turn))
        with self._write():
            prior = self.db.execute('SELECT ordinal,plan FROM turns WHERE id=?', (turn.id,)).fetchone()
            if prior is not None:
                if tuple(prior) != (ordinal, plan):
                    raise ValueError('turn plan changed')
                return
            self._require_open()
            self.db.execute('INSERT INTO turns(ordinal,id,session,measure,criteria_count,plan) VALUES (?,?,?,?,?,?)',
                            (ordinal, turn.id, turn.session, turn.measure, len(turn.claims), plan))

    def _require_open(self):
        if self.db.execute('SELECT execution_sealed FROM run').fetchone()[0]:
            raise ValueError('native execution already sealed')

    @staticmethod
    def _sync_directory(path):
        fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _blob_path(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid workflow artifact digest')
        return self.directory / 'workflow-artifacts' / digest[:2] / (digest + '.json')

    def _bytes(self, digest):
        path = self._blob_path(digest)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f'missing workflow artifact: {digest}') from exc
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f'workflow artifact hash mismatch: {digest}')
        return raw

    def _load(self, digest):
        return json.loads(self._bytes(digest))

    def _store(self, value):
        raw = _json(value).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self._blob_path(digest)
        if path.exists():
            self._bytes(digest)
            self._sync_directory(path.parent)
            return digest
        for directory in (path.parent.parent, path.parent):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self._sync_directory(directory.parent)
        # Publish atomically with no replacement. An interrupted writer can leave
        # an unreferenced temp/blob; it cannot publish a partial referenced object.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.partial-', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                tmp.write(raw)
                tmp.flush()
                os.fchmod(tmp.fileno(), 0o400)
                os.fsync(tmp.fileno())
                try:
                    os.link(tmp_path, path)
                except FileExistsError:
                    self._bytes(digest)
                self._sync_directory(path.parent)
            finally:
                tmp_path.unlink(missing_ok=True)
        return digest

    def _result(self, phase, key, row):
        if row['result'] is None:
            raise UnknownEffect(f'uncertain external effect {phase}/{key}; automatic replay disabled')
        return self._load(row['result'])

    def get_effect(self, phase, key):
        row = self.db.execute('SELECT result FROM effects WHERE phase=? AND op_key=?', (phase, key)).fetchone()
        return self._result(phase, key, row) if row else None

    def effect(self, phase, key, inputs, call, *, role=None):
        """Call once and persist the validated JSON result. The callback owns validation."""
        digest = hashlib.sha256(_json(inputs).encode()).hexdigest()
        with self._write():
            row = self.db.execute('SELECT input_sha256,result,role FROM effects WHERE phase=? AND op_key=?',
                                  (phase, key)).fetchone()
            if row:
                if row['input_sha256'] != digest or row['role'] != role:
                    raise ValueError(f'workflow effect input changed: {phase}/{key}')
                return self._result(phase, key, row)
            self._require_open()
            attempt = uuid4().hex
            self.db.execute('INSERT INTO effects(phase,op_key,input_sha256,role,attempt_id) VALUES (?,?,?,?,?)',
                            (phase, key, digest, role, attempt))
        from .live_dispatch import fresh_effect
        with fresh_effect(self, phase, key, attempt):
            result = call()
        if result is None:
            raise ValueError(f'missing verified result: {phase}/{key}')
        stored = self._store(result)
        with self._write():
            row = self.db.execute('SELECT result FROM effects WHERE attempt_id=?', (attempt,)).fetchone()
            if row['result'] is not None and row['result'] != stored:
                raise ValueError('external result conflicts with reconciled evidence')
            self.db.execute('UPDATE effects SET result=? WHERE attempt_id=?', (stored, attempt))
        return self._load(stored)

    def record_observation(self, phase, key, name, value):
        """Capture raw external evidence before any later checks or parsing."""
        if not isinstance(name, str) or not name:
            raise ValueError('observation name required')
        stored = self._store(value)
        with self._write():
            self._bind_observation(phase, key, name, stored)
        return stored

    def _bind_observation(self, phase, key, name, stored):
        row = self.db.execute('SELECT attempt_id FROM effects WHERE phase=? AND op_key=?', (phase, key)).fetchone()
        if row is None:
            raise ValueError('observation requires an existing operation')
        self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)', (row[0], name, stored))
        same = self.db.execute('SELECT artifact FROM observations WHERE attempt_id=? AND name=?',
                               (row[0], name)).fetchone()
        if same[0] != stored:
            raise ValueError('immutable native observation changed')

    def observation(self, phase, key, name):
        row = self.db.execute('SELECT artifact FROM observations JOIN effects USING(attempt_id) '
                              'WHERE phase=? AND op_key=? AND name=?', (phase, key, name)).fetchone()
        return self._load(row[0]) if row else None

    def active_effect(self, phase, key, inputs=None):
        row = self.db.execute('SELECT input_sha256,role,attempt_id FROM effects '
                              'WHERE phase=? AND op_key=? AND result IS NULL', (phase, key)).fetchone()
        if row is None:
            return None
        if inputs is not None and row['input_sha256'] != hashlib.sha256(_json(inputs).encode()).hexdigest():
            raise ValueError('active operation input changed')
        return {'run_key': self.run_key, 'phase': phase, 'key': key, **dict(row)}

    def reconcile_effect(self, phase, key, inputs, verify):
        """Adopt a proven prior result via a caller-supplied read-only native verifier.

        The verifier must authenticate authoritative evidence for this precise
        attempt. Merely reflecting its operation ID or a ready flag is insufficient.
        """
        claim = self.active_effect(phase, key, inputs)
        if claim is None:
            raise ValueError('reconciliation requires a pending external effect')
        result, witness = verify(dict(claim))
        if (result is None or not isinstance(witness, dict) or witness.get('operation') != claim
                or not isinstance(witness.get('native_evidence'), dict) or not witness['native_evidence']):
            raise ValueError('native reconciliation lacks operation-bound evidence')
        stored, evidence = self._store(result), self._store(witness)
        with self._write():
            written = self.db.execute('UPDATE effects SET result=?,reconciliation=? '
                                      'WHERE attempt_id=? AND result IS NULL',
                                      (stored, evidence, claim['attempt_id']))
            if written.rowcount != 1:
                raise ValueError('effect was settled during native reconciliation')
        return self._load(stored)


    def next_ordinal(self):
        row = self.db.execute('SELECT ordinal FROM turns WHERE done=0 ORDER BY ordinal LIMIT 1').fetchone()
        return row[0] if row else None

    def last_cutoff(self):
        return self.db.execute('SELECT last_cutoff FROM run').fetchone()[0]

    def advance_cutoff(self, cutoff, *, native_ready=None):
        moment = instant(cutoff)
        ready = self._store(native_ready) if native_ready is not None else None
        with self._write():
            if ready is not None:
                self._bind_observation('dream', cutoff, 'native-ready', ready)
            previous = self.last_cutoff()
            if previous == cutoff:
                return
            self._require_open()
            if previous is not None and instant(previous) >= moment:
                raise ValueError('source cursor must move forward')
            self.db.execute('UPDATE run SET last_cutoff=?', (cutoff,))

    def latest_surface(self, session, *, before=None):
        sql = 'SELECT surface FROM turns WHERE session=? AND surface IS NOT NULL'
        args = [session]
        if before is not None:
            sql += ' AND ordinal<?'
            args.append(before)
        row = self.db.execute(sql + ' ORDER BY ordinal DESC LIMIT 1', args).fetchone()
        return self._load(row[0]) if row else None

    def session_owner(self, session_id):
        row = self.db.execute('SELECT logical_id FROM sessions WHERE native_id=?', (session_id,)).fetchone()
        return row[0] if row else None

    def iter_sessions(self, page_size=32):
        """Visit each established chat once for native session/store re-audits."""
        if type(page_size) is not int or page_size < 1:
            raise ValueError('page size must be positive')
        last = ''
        while True:
            rows = self.db.execute('SELECT logical_id,native_id FROM sessions WHERE logical_id>? '
                                   'ORDER BY logical_id LIMIT ?', (last, page_size)).fetchall()
            if not rows:
                return
            for logical, native in rows:
                last = logical
                yield logical, native

    def reused_request_ids(self, ids):
        # Each lookup touches only the request index, including on very long runs.
        query = 'SELECT 1 FROM requests WHERE request_id=? LIMIT 1'
        return sorted({ident for ident in ids if self.db.execute(query, (ident,)).fetchone()})

    def verified_turn(self, turn, surface, activity):
        """Capture and queue one verified answer in the same transaction."""
        answer, metadata = self._store(surface), self._store(activity)
        with self._write():
            row = self.db.execute('SELECT ordinal,plan,surface,activity FROM turns WHERE id=?', (turn.id,)).fetchone()
            if row is None or row['plan'] != _json(asdict(turn)):
                raise ValueError('answer does not match the frozen turn')
            if row['surface'] is not None:
                if (row['surface'], row['activity']) != (answer, metadata):
                    raise ValueError('verified answer or evidence changed')
                return
            if row['ordinal'] != self.next_ordinal():
                raise ValueError('turn is out of account order')
            session = surface['session_id']
            gateway = surface['gateway_identity']
            if not session or not gateway:
                raise ValueError('missing native session or gateway')
            self._bind('gateway', gateway)
            self.db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?)', (session, turn.session))
            if self.session_owner(session) != turn.session:
                raise ValueError('native or logical session changed')
            self.db.executemany('INSERT OR IGNORE INTO requests VALUES (?,?)',
                                ((r['request_id'], turn.id) for r in surface['model_requests']))
            self.db.execute('UPDATE turns SET surface=?, activity=?, grade_state=? WHERE id=?',
                            (answer, metadata, 'ready' if turn.claims else 'unscored', turn.id))

    def mark_turn_done(self, turn_id):
        """Advance only after the caller has checked all native close/write barriers."""
        with self._write():
            row = self.db.execute('SELECT ordinal,done,surface IS NOT NULL FROM turns WHERE id=?', (turn_id,)).fetchone()
            if row is None or not row[2]:
                raise ValueError('turn lacks verified answer')
            if not row['done']:
                if row['ordinal'] != self.next_ordinal():
                    raise ValueError('turn is out of account order')
                self.db.execute('UPDATE turns SET done=1 WHERE id=?', (turn_id,))

    def get_verified(self, turn_id):
        row = self.db.execute('SELECT plan,surface,activity FROM turns WHERE id=? AND surface IS NOT NULL',
                              (turn_id,)).fetchone()
        return (_turn(row[0]), self._load(row[1]), self._load(row[2])) if row else None

    def _pages(self, selection, predicate, page_size, ordinal=-1):
        if type(page_size) is not int or page_size < 1:
            raise ValueError('page size must be positive')
        while True:
            rows = self.db.execute(f'SELECT ordinal,{selection} FROM turns WHERE {predicate} AND ordinal>? '
                                   'ORDER BY ordinal LIMIT ?', (ordinal, page_size)).fetchall()
            if not rows:
                return
            for row in rows:
                ordinal = row['ordinal']
                yield row

    def _after(self, turn_id):
        if turn_id is None:
            return -1
        row = self.db.execute('SELECT ordinal FROM turns WHERE id=?', (turn_id,)).fetchone()
        if row is None:
            raise ValueError('unknown turn cursor')
        return row[0]

    def iter_verified(self, page_size=32, *, after=None):
        for row in self._pages('plan,surface,activity', 'surface IS NOT NULL', page_size, self._after(after)):
            yield _turn(row['plan']), self._load(row['surface']), self._load(row['activity'])

    def claim_grade(self):
        with self._write():
            row = self.db.execute("SELECT id,plan,surface FROM turns WHERE grade_state='ready' "
                                  "ORDER BY ordinal LIMIT 1").fetchone()
            if row is None:
                return None
            surface = self._load(row['surface'])
            self.db.execute("UPDATE turns SET grade_state='claimed', grade_attempt=? WHERE id=?",
                            (uuid4().hex, row['id']))
            return _turn(row['plan']), surface

    def active_grade(self, turn_id):
        row = self.db.execute("SELECT plan,surface,grade_attempt FROM turns WHERE id=? AND grade_state='claimed'",
                              (turn_id,)).fetchone()
        if row is None:
            return None
        digest = hashlib.sha256(_json([row['plan'], row['surface']]).encode()).hexdigest()
        return {'run_key': self.run_key, 'phase': 'grade', 'key': turn_id, 'input_sha256': digest,
                'role': 'judge', 'attempt_id': row['grade_attempt']}

    def get_claimed_grade(self, turn_id):
        row = self.db.execute("SELECT plan,surface FROM turns WHERE id=? AND grade_state='claimed'",
                              (turn_id,)).fetchone()
        return (_turn(row['plan']), self._load(row['surface'])) if row else None

    def claimed_grades(self, page_size=32, *, after=None):
        for row in self._pages('plan,surface', "grade_state='claimed'", page_size, self._after(after)):
            yield _turn(row['plan']), self._load(row['surface'])

    def finish_grade(self, turn_id, criteria, *, failure=None):
        """Settle an individual claimed judgment. Unknown dispatches stay claimed."""
        with self._write():
            row = self.db.execute('SELECT plan,grade_state,grade,failure FROM turns WHERE id=?', (turn_id,)).fetchone()
            if row is None:
                raise ValueError('unknown grade turn')
            if criteria is None:
                if not isinstance(failure, str) or not failure.strip():
                    raise ValueError('ungraded attempt requires a recorded failure')
                state, grade, correct = 'failed', None, None
            else:
                expected = [c.id for c in _turn(row['plan']).claims]
                if (failure is not None or not isinstance(criteria, list) or not expected
                        or [c['id'] for c in criteria] != expected or any(c['verdict'] not in VERDICTS
                        or not isinstance(c['reason'], str) or not c['reason'].strip() for c in criteria)):
                    raise ValueError('grade differs from planned criteria')
                state, grade = 'graded', self._store(criteria)
                correct = sum(c['verdict'] == 'correct' for c in criteria)
            if (row['grade_state'], row['grade'], row['failure']) == (state, grade, failure):
                return
            if row['grade_state'] != 'claimed':
                raise ValueError('judgment is unclaimed or already settled differently')
            self.db.execute('UPDATE turns SET grade_state=?,grade=?,failure=?,correct=? WHERE id=?',
                            (state, grade, failure, correct, turn_id))

    def grade_record(self, turn_id):
        row = self.db.execute('SELECT grade_state,grade,failure FROM turns WHERE id=?', (turn_id,)).fetchone()
        if row is None:
            return None
        return {'grade_state': row['grade_state'], 'criteria': self._load(row['grade']) if row['grade'] else None,
                'failure': row['failure']}

    def grade_states(self):
        return dict(self.db.execute('SELECT grade_state,count(*) FROM turns GROUP BY grade_state'))

    def summary(self):
        rows = self.db.execute("""
            SELECT measure, count(*) planned_turns, sum(criteria_count) planned_criteria,
                   sum(grade_state!='waiting') answered, sum(CASE WHEN grade_state='graded' THEN criteria_count ELSE 0 END) graded_criteria,
                   sum(correct) correct, sum(grade_state='failed') judge_failures FROM turns GROUP BY measure
        """)
        return {row['measure']: {k: row[k] for k in row.keys() if k != 'measure'} for row in rows}

    def audit_artifacts(self):
        """Read each distinct referenced object once, verifying its SHA-256 on disk."""
        count = 0
        for row in self.db.execute('''SELECT result FROM effects WHERE result IS NOT NULL
                    UNION SELECT reconciliation FROM effects WHERE reconciliation IS NOT NULL
                    UNION SELECT artifact FROM observations
                    UNION SELECT surface FROM turns WHERE surface IS NOT NULL
                    UNION SELECT activity FROM turns WHERE activity IS NOT NULL
                    UNION SELECT grade FROM turns WHERE grade IS NOT NULL'''):
            self._bytes(row[0])
            count += 1
        return count

    def seal_execution(self, expected_turns, expected_criteria):
        """Caller validates final native inventory/readiness. Bind its frozen denominator here."""
        with self._write():
            row = self.db.execute('SELECT count(*),coalesce(sum(criteria_count),0),coalesce(max(ordinal),-1),'
                                  'coalesce(sum(done),0) FROM turns').fetchone()
            if tuple(row) != (expected_turns, expected_criteria, expected_turns - 1, expected_turns):
                raise ValueError('native execution or planned denominator incomplete')
            if self.db.execute('SELECT 1 FROM effects WHERE result IS NULL LIMIT 1').fetchone():
                raise UnknownEffect('unsettled external effects prevent execution seal')
            self.audit_artifacts()
            self.db.execute('UPDATE run SET execution_sealed=1')

    @property
    def execution_sealed(self):
        return bool(self.db.execute('SELECT execution_sealed FROM run').fetchone()[0])

    @property
    def complete(self):
        if self.binding('evidence_kind') != 'native' or self.binding('canary') is not False:
            return False
        return bool(self.db.execute("""
            SELECT execution_sealed AND EXISTS(SELECT 1 FROM turns WHERE criteria_count>0) AND NOT EXISTS(
                SELECT 1 FROM turns WHERE criteria_count>0 AND grade_state!='graded'
            ) FROM run
        """).fetchone()[0])
