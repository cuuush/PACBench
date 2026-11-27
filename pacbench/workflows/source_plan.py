"""Immutable, ordered workflow inputs and an indexed verbatim-source cutoff check."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from bisect import bisect_right
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
from itertools import chain, zip_longest
from pathlib import Path
from zoneinfo import ZoneInfo

from pacbench.judging.grade import Claim


def _clock(value):
    from .spec import instant

    return instant(value).isoformat(timespec='microseconds')


def _document(payload):
    from .spec import Document

    row = json.loads(payload)
    return Document(**{**row, 'source_ids': tuple(row['source_ids']), 'bodies': tuple(row['bodies'])})


def _turn(payload):
    from .spec import Turn

    row = json.loads(payload)
    row['claims'] = tuple(Claim(c['id'], c['description'], tuple(c['evidence'])) for c in row['claims'])
    return Turn(**row)


def _encode(obj):
    return json.dumps(asdict(obj), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_manifest(digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_docs(n INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
                                channel TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS plan_nights ON plan_docs(at, n);
CREATE TABLE IF NOT EXISTS plan_turns(n INTEGER PRIMARY KEY, at TEXT NOT NULL, id TEXT NOT NULL UNIQUE,
                                 chat TEXT NOT NULL, criteria INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS plan_chat_end ON plan_turns(chat, n);
CREATE INDEX IF NOT EXISTS plan_turn_time ON plan_turns(at);
CREATE TABLE IF NOT EXISTS plan_bodies(id INTEGER PRIMARY KEY, body TEXT NOT NULL UNIQUE,
                                  last_cutoff TEXT NOT NULL, source_at TEXT, prompt_at TEXT);
CREATE TABLE IF NOT EXISTS plan_anchors(key TEXT NOT NULL, body_id INTEGER NOT NULL, escaped INTEGER NOT NULL,
                                   offset INTEGER NOT NULL, PRIMARY KEY(key, body_id, escaped)) WITHOUT ROWID;
"""
_ANCHOR = 16
_PAGE = 256


class SourcePlan:
    """Stream each supplied iterable once per open. The SQLite file belongs to the run."""
    def __init__(self, directory, documents, turns, *, all_source_documents=None):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / 'workflow.sqlite3'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self._owns_connection = True
        self.db = sqlite3.connect(path, timeout=30)
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA)
            saved = self.db.execute('SELECT digest FROM plan_manifest').fetchone()
            building = saved is None
            if not building:
                self.db.rollback()  # A resumed input hash need not lock out the grading worker.
            else:
                has_run = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'").fetchone()
                if has_run and self.db.execute('SELECT 1 FROM run LIMIT 1').fetchone():
                    raise ValueError('existing run lacks its frozen source plan')
            digest = hashlib.sha256(b'pacbench-source-plan-v1\n')
            streams = [('documents', documents), ('turns', turns)]
            if all_source_documents is not None:
                streams.append(('all_source_documents', all_source_documents))
                if building:
                    self.db.execute('CREATE TEMP TABLE plan_full_names(name TEXT PRIMARY KEY) WITHOUT ROWID')
            for kind, entries in streams:
                digest.update(kind.encode() + b'\n')
                previous = None
                last_selected = -1
                for n, obj in enumerate(entries):
                    payload = _encode(obj)
                    # JSON strings escape newlines, giving each typed stream unambiguous boundaries.
                    digest.update(payload.encode() + b'\n')
                    if not building:
                        continue
                    at = _clock(obj.at if kind == 'turns' else obj.cutoff)
                    if previous and (at < previous or (kind == 'turns' and at == previous)):
                        raise ValueError('workflow events must have chronological order')
                    previous = at
                    if kind == 'turns':
                        self.db.execute('INSERT INTO plan_turns VALUES (?,?,?,?,?,?)',
                                        (n, at, obj.id, obj.session, len(obj.claims), payload))
                    elif kind == 'documents':
                        self.db.execute('INSERT INTO plan_docs VALUES (?,?,?,?,?)',
                                        (n, at, obj.filename, obj.channel, payload))
                        if all_source_documents is None:
                            self._add_bodies(obj, at)
                    else:
                        self.db.execute('INSERT INTO plan_full_names VALUES (?)', (obj.filename,))
                        selected = self.db.execute('SELECT n,payload FROM plan_docs WHERE name=?', (obj.filename,)).fetchone()
                        if selected:
                            ordinal, original = selected
                            if ordinal <= last_selected or original != payload:
                                raise ValueError('selected documents differ in order or content from full source')
                            last_selected = ordinal
                        self._add_bodies(obj, at if selected else '~')
            if building and all_source_documents is not None and self.db.execute(
                    'SELECT name FROM plan_docs EXCEPT SELECT name FROM plan_full_names LIMIT 1').fetchone():
                raise ValueError('selected document absent from full source')
            self.digest = digest.hexdigest()
            if saved and saved[0] != self.digest:
                raise ValueError('frozen source or turn schedule changed')
            if building:
                self._index_public_text()
                self.db.execute('INSERT INTO plan_manifest VALUES (?)', (self.digest,))
            self.db.commit()
            self.db.execute('PRAGMA query_only=ON')
            self._read_summary()
        except BaseException:
            self.db.close()
            raise

    @classmethod
    def from_connection(cls, db, expected_digest):
        """Borrow the existing run index for exact per-request source checks."""
        row = db.execute('SELECT digest FROM plan_manifest LIMIT 2').fetchall()
        if len(row) != 1 or row[0][0] != expected_digest:
            raise ValueError('indexed source plan differs from the frozen scope')
        plan = cls.__new__(cls)
        plan.db, plan.digest, plan._owns_connection = db, expected_digest, False
        return plan

    def _read_summary(self):
        self.turn_count, self.criteria_count = self.db.execute(
            'SELECT count(*), coalesce(sum(criteria),0) FROM plan_turns').fetchone()
        self.channels = tuple(r[0] for r in self.db.execute('SELECT DISTINCT channel FROM plan_docs ORDER BY channel'))

    def __getattr__(self, name):
        if name in {'turn_count', 'criteria_count', 'channels'}:
            self._read_summary()
            return self.__dict__[name]
        raise AttributeError(name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._owns_connection:
            self.db.close()

    def _add_bodies(self, doc, horizon):
        for body in doc.bodies:
            if len(body) < 40:
                continue
            cursor = self.db.execute('INSERT OR IGNORE INTO plan_bodies(body,last_cutoff) VALUES (?,?)', (body, horizon))
            if not cursor.rowcount:
                self.db.execute('UPDATE plan_bodies SET last_cutoff=max(last_cutoff,?) WHERE body=?', (horizon, body))
                continue
            escaped = json.dumps(body, ensure_ascii=False)[1:-1]
            for i, pattern in enumerate((body, escaped) if escaped != body else (body,)):
                # Prefer an unused anchor in templated text. Full string equality is always checked.
                offsets = (0, (len(pattern)-_ANCHOR)//2, len(pattern)-_ANCHOR)
                # Template fields can differ anywhere, including outside the three fast probes.
                for offset in chain(offsets, range(len(pattern)-_ANCHOR+1)):
                    anchor = pattern[offset:offset+_ANCHOR]
                    if not self.db.execute('SELECT 1 FROM plan_anchors WHERE key=? LIMIT 1', (anchor,)).fetchone():
                        break
                self.db.execute('INSERT INTO plan_anchors VALUES (?,?,?,?)', (anchor, cursor.lastrowid, i, offset))

    def _matches(self, text, predicate, params):
        """Page anchor probes, compare the complete body at the indexed offset, never scan the corpus."""
        for base in range(0, len(text)-_ANCHOR+1, _PAGE):
            positions = {}
            for pos in range(base, min(base+_PAGE, len(text)-_ANCHOR+1)):
                positions.setdefault(text[pos:pos+_ANCHOR], []).append(pos)
            placeholders = ','.join('?' for _ in positions)
            query = ('SELECT a.key,a.offset,a.escaped,b.id,b.body FROM plan_anchors a '
                     'JOIN plan_bodies b ON b.id=a.body_id WHERE a.key IN (' + placeholders + ') AND ' + predicate)
            for key, offset, escaped, ident, body in self.db.execute(query, (*positions, *params)):
                pattern = json.dumps(body, ensure_ascii=False)[1:-1] if escaped else body
                if any(pos >= offset and text.startswith(pattern, pos-offset) for pos in positions[key]):
                    yield ident, body

    def _index_public_text(self):
        # Each stream grows by appending entries joined with newline, as in inspect_source_cutoff.
        # Only multiline bodies can straddle entries. Retain at most one body's worth of suffix.
        max_multiline, = self.db.execute(
            "SELECT coalesce(max(length(CAST(body AS BLOB))),0) FROM plan_bodies WHERE instr(body,char(10))>0").fetchone()
        for table, field, when in [('plan_docs','text','source_at'), ('plan_turns','prompt','prompt_at')]:
            tail = ''
            for n, at, payload in self.db.execute(f'SELECT n,at,payload FROM {table} ORDER BY n'):
                current = json.loads(payload)[field]
                joined = tail + '\n' + current if n and max_multiline else current
                for ident, _ in self._matches(joined, f'a.escaped=0 AND b.{when} IS NULL', ()):
                    self.db.execute(f'UPDATE plan_bodies SET {when}=? WHERE id=?', (at, ident))
                if max_multiline:
                    tail = joined[-max_multiline:]

    def iter_turns(self, start_ordinal=0):
        query = ('SELECT t.n,t.payload,t.n=(SELECT max(n) FROM plan_turns WHERE chat=t.chat) '
                 'FROM plan_turns t WHERE t.n>=? ORDER BY t.n')
        # Finish each statement before yielding, so a long native stream never pins the WAL.
        while True:
            rows = self.db.execute(query + ' LIMIT ?', (start_ordinal, _PAGE)).fetchall()
            if not rows:
                return
            for n, payload, last in rows:
                yield n, _turn(payload), bool(last)
            start_ordinal = rows[-1][0] + 1

    def last_turn_id(self, logical_chat):
        row = self.db.execute('SELECT id FROM plan_turns WHERE chat=? ORDER BY n DESC LIMIT 1',
                              (logical_chat,)).fetchone()
        return row[0] if row is not None else None

    def get_turn(self, turn_id):
        row = self.db.execute('SELECT payload FROM plan_turns WHERE id=?', (turn_id,)).fetchone()
        if row is None:
            raise KeyError(turn_id)
        return _turn(row[0])

    def iter_nights(self, after_cutoff=None, through=None):
        lower = _clock(after_cutoff) if after_cutoff is not None else ''
        upper = _clock(through) if through else '~'
        while True:
            row = self.db.execute('SELECT at FROM plan_docs WHERE at>? AND at<=? ORDER BY at LIMIT 1',
                                  (lower, upper)).fetchone()
            if row is None:
                return
            lower, = row
            batch = [_document(payload) for (payload,) in self.db.execute(
                'SELECT payload FROM plan_docs WHERE at=? ORDER BY n', (lower,))]
            yield batch[0].cutoff, batch

    def check_inventory(self, actual, cutoff):
        if isinstance(actual, (str, bytes, dict)) or actual is None:
            raise ValueError('invalid native source inventory')
        expected = self.db.execute('SELECT name FROM plan_docs WHERE at<=? ORDER BY at,n',
                                   (_clock(cutoff) if cutoff is not None else '',))
        missing = object()
        with closing(expected):
            if any(a != b for a, b in zip_longest(actual, (r[0] for r in expected), fillvalue=missing)):
                raise ValueError('native source inventory differs from expected chronological prefix')

    def _public_bridge(self, body, at, prompt, isolated):
        """Check the single moving boundary between public source and public prompts."""
        if '\n' not in body:
            return False
        needed = len(body)
        suffix = None
        for (payload,) in self.db.execute('SELECT payload FROM plan_docs WHERE at<=? ORDER BY at DESC,n DESC', (at,)):
            text = json.loads(payload)['text']
            suffix = (text if suffix is None else text + '\n' + suffix)[-needed:]
            if len(suffix) == needed:
                break
        if suffix is None:
            return False
        if isolated:
            prefix = prompt[:needed]
        else:
            prefix = None
            for (payload,) in self.db.execute('SELECT payload FROM plan_turns WHERE at<=? ORDER BY at', (at,)):
                text = json.loads(payload)['prompt']
                prefix = (text if prefix is None else prefix + '\n' + text)[:needed]
                if len(prefix) == needed:
                    break
        return prefix is not None and body in suffix + '\n' + prefix

    def verify_no_leak(self, surface, at, prompt, *, isolated=False):
        at = _clock(at)
        row = self.db.execute('SELECT payload FROM plan_turns WHERE at=?', (at,)).fetchone()
        if row is None or json.loads(row[0])['prompt'] != prompt:
            raise ValueError('cutoff check requires the frozen prompt at its scheduled instant')
        predicate = 'b.last_cutoff>? AND (b.source_at IS NULL OR b.source_at>?)'
        params = (at, at)
        if not isolated:
            predicate += ' AND (b.prompt_at IS NULL OR b.prompt_at>?)'
            params += (at,)

        def observed():
            for request in surface['model_requests']:
                for row in request['messages']:
                    if row['role'] in ('system','user','tool') and isinstance(row.get('content'), str):
                        yield row['content']
            if surface['prefetch']:
                yield surface['prefetch']

        for text in observed():
            for _, body in self._matches(text, predicate, params):
                if not (isolated and body in prompt) and not self._public_bridge(body, at, prompt, isolated):
                    raise ValueError('answer input contains a verbatim unavailable source body')


VERSION = 'daily-source-documents-v3'


NIGHTLY_JSON_VERSION = 'daily-source-documents-v2'


LEGACY_VERSION = 'daily-source-documents-v1'


SUPPORTED_VERSIONS = (LEGACY_VERSION,NIGHTLY_JSON_VERSION,VERSION)


PUBLIC_FIELDS = {'source_id','channel','thread','sender','text','from_owner',
                 'written_at','arrived_at','collected_at','structured'}


def daily_batches(messages, timezone):
    zone = ZoneInfo(timezone)
    groups = {}
    seen = set()
    for m in messages:
        if set(m) - PUBLIC_FIELDS:
            raise ValueError('Document input contains undeclared or private fields')
        if m['source_id'] in seen:
            raise ValueError('Duplicate source ID in frozen input')
        seen.add(m['source_id'])
        collected = datetime.fromisoformat(m['collected_at'])
        written = datetime.fromisoformat(m['written_at'])
        if collected.utcoffset() is None or written.utcoffset() is None or written > collected:
            raise ValueError('Source timestamps must be timezone-aware and collected after writing')
        day=collected.astimezone(zone).date().isoformat()
        groups.setdefault(day,[]).append(m)
    return [(day,sorted(batch,key=lambda m:(datetime.fromisoformat(m['written_at']),m['source_id'])))
            for day,batch in sorted(groups.items())]


def daily_releases(messages, timezone):
    """Publish each dated batch at its latest collection time."""
    for day, batch in daily_batches(messages, timezone):
        yield day, max((row['collected_at'] for row in batch), key=datetime.fromisoformat), batch


def document_text(batch, collected_at, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unknown document format version')
    if version in (LEGACY_VERSION,NIGHTLY_JSON_VERSION):
        return '\n'.join(json.dumps(m,ensure_ascii=False,sort_keys=True) for m in batch)+'\n'
    # A practical message export: copy every body, keep source chronology and
    # introduce each conversation once per contiguous block. Full machine
    # records, including source IDs, remain in messages.json for native readers
    # and integrity checks; they need not inflate the LLM's transcript.
    lines=[f'Daily messages — collected through {collected_at}']
    previous=None
    for m in batch:
        metadata=m.get('structured') or {}
        conversation=(m['channel'],m['thread'],metadata)
        if conversation!=previous:
            lines.extend(['',f"Conversation: {m['channel']} | {m['thread']}"])
            if metadata.get('chat_name'):lines.append('Name: '+metadata['chat_name'])
            if metadata.get('participants'):lines.append('Participants: '+', '.join(metadata['participants']))
            if metadata.get('email_subject'):lines.append('Subject: '+metadata['email_subject'])
            extra={k:v for k,v in metadata.items() if k not in ('chat_name','participants','email_subject')}
            if extra:lines.append('Metadata: '+json.dumps(extra,ensure_ascii=False,sort_keys=True))
            previous=conversation
        lines.extend(['',f"[{m['written_at']}] {m['sender']}"+(' (sent by owner)' if m['from_owner'] else '')])
        for key,label in (('arrived_at','Delivered'),('collected_at','Captured')):
            if m.get(key) and m[key]!=m['written_at']:lines.append(f'{label}: {m[key]}')
        lines.append(m['text'])
    return '\n'.join(lines)+'\n'


def source_state(at,ingested_batches):
    """Reference the canonical per-day manifests instead of copying all IDs."""
    return {'reference_version':2,'at':at,'ingested_batches':list(ingested_batches)}


def compact_source_state(value,schedule):
    if value.get('reference_version')==2:return value
    by_id={row['id']:row for row in schedule}
    expected=[sid for id in value['ingested_batches'] for sid in by_id[id]['source_ids']]
    if set(value)!={'at','ingested_batches','source_ids'} or value['source_ids']!=expected:
        raise ValueError('Source state differs from its canonical daily manifests')
    return source_state(value['at'],value['ingested_batches'])


def processing_at(at, provider, timezone, *, version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('Unknown ingestion clock version')
    if version==LEGACY_VERSION and provider!='memcal':
        return at
    stamp=datetime.fromisoformat(at).astimezone(ZoneInfo(timezone))
    return datetime.combine(stamp.date()+timedelta(days=1),datetime.min.time(),ZoneInfo(timezone)).replace(hour=3).isoformat()


def ready_sources(schedule, provider, at, timezone, *, version=VERSION):
    clock=datetime.fromisoformat(at)
    ready=[]
    for row in schedule:
        source_time=datetime.fromisoformat(row['at'])
        finished=datetime.fromisoformat(processing_at(row['at'],provider,timezone,version=version))
        if source_time<=clock<finished:
            raise ValueError('Evaluation precedes native processing; do not use future memory state')
        if finished<=clock:ready.append(row)
    return ready


class _ReadinessTimeline:
    def __init__(self, schedule, provider, timezone, version):
        self.rows = tuple(schedule)
        boundaries = [
            (datetime.fromisoformat(row['at']),
             datetime.fromisoformat(processing_at(row['at'], provider, timezone, version=version)))
            for row in self.rows
        ]
        self.finishes = [finished for _, finished in boundaries]
        self.sorted_finishes = sorted(self.finishes)
        self.in_finish_order = self.finishes == self.sorted_finishes
        processing = [(started, finished) for started, finished in boundaries if started < finished]
        self.processing_starts = sorted(started for started, _ in processing)
        self.processing_finishes = sorted(finished for _, finished in processing)

    def ready_count(self, clock):
        # Count processing intervals whose inclusive start, but not end, has passed.
        if bisect_right(self.processing_starts, clock) > bisect_right(self.processing_finishes, clock):
            raise ValueError('Evaluation precedes native processing; do not use future memory state')
        return bisect_right(self.sorted_finishes, clock)

    def ready(self, at):
        clock = datetime.fromisoformat(at)
        count = self.ready_count(clock)
        if not count:
            return []
        if self.in_finish_order or count == len(self.rows):
            return list(self.rows[:count])
        return [row for row, finished in zip(self.rows, self.finishes, strict=True) if finished <= clock]


def validate_evaluation_clocks(schedule,providers,question_times,timezone, *, chronological=False,version=VERSION):
    """Verify source eligibility and return the same timelines for native import."""
    questions = [datetime.fromisoformat(value) for value in question_times]
    if not questions or any(value.utcoffset() is None for value in questions):
        raise ValueError('Evaluation clocks require explicit timezone offsets')
    final = max(questions)
    timelines = {}
    for provider in providers:
        timeline = _ReadinessTimeline(schedule, provider, timezone, version)
        for clock in questions:
            count = timeline.ready_count(clock)
            if not chronological and count != len(schedule):
                raise ValueError('Evaluation precedes source availability; chronological checkpoint ingestion is required')
        if timeline.ready_count(final) != len(schedule):
            raise ValueError('Final evaluation precedes source availability or native processing')
        timelines[provider] = timeline
    return timelines
