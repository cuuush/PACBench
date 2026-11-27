"""Compile authored source and turns before any provider is selected."""
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pacbench.judging.grade import CONTEXT_JUDGE_PROTOCOL, Claim
from pacbench.workflows.source_plan import PUBLIC_FIELDS, document_text

UTC = timezone.utc


def instant(value):
    value = datetime.fromisoformat(value)
    if value.utcoffset() is None:
        raise ValueError('source and question clocks need an explicit offset')
    return value.astimezone(UTC)


def cutoff_on(day: date, hour: time, zone: ZoneInfo):
    local = datetime.combine(day, hour, zone)
    if (local.utcoffset() != local.replace(fold=1).utcoffset() or
            local.astimezone(UTC).astimezone(zone) != local):
        raise ValueError('choose an unambiguous local nightly collection time')
    return local


@dataclass(frozen=True)
class Document:
    filename: str
    cutoff: str
    channel: str
    text: str
    source_ids: tuple[str, ...]
    bodies: tuple[str, ...] = ()

    @property
    def sha256(self):
        return hashlib.sha256(self.text.encode()).hexdigest()


def source_text(group, cutoff):
    if group[0]['channel'] != 'email':
        return document_text(group, cutoff)
    lines = [f'Email thread: {group[0]["thread"]}', f'Collected through: {cutoff}']
    for row in group:
        meta = row['structured']
        lines.extend(['', 'From: '+row['sender'], 'Date: '+row['written_at'],
                      'Folder: '+('Sent' if row['from_owner'] else 'Inbox')])
        if meta.get('email_subject'):
            lines.append('Subject: '+meta['email_subject'])
        if 'participants' in meta:
            lines.append('Observed participants: '+', '.join(meta['participants']))
        # Only preserve headers already present in the public export. Never
        # derive a reply-to relationship or a To/Cc split from thread order.
        for name,val in meta.get('email_headers', {}).items():
            lines.append(name+': '+val)
        for field,label in [('arrived_at','Delivered'),('collected_at','Collected')]:
            if row.get(field) and row[field] != row['written_at']:
                lines.append(f'{label}: {row[field]}')
        lines.extend(['',row['text']])
    return '\n'.join(lines)+'\n'


def nightly_documents(messages, zone_name, cutoff):
    """One unabridged file per channel/thread/night. Never infer a join or reply time."""
    hour, zone = time.fromisoformat(cutoff), ZoneInfo(zone_name)
    if hour.tzinfo is not None:
        raise ValueError('nightly cutoff is a local time')
    groups = defaultdict(list)
    seen = set()
    for row in messages:
        if set(row) - {'available_at'} != PUBLIC_FIELDS or row['channel'] not in ('email', 'imessage'):
            raise ValueError('undeclared source fields or unsupported channel')
        for key in ('source_id', 'thread', 'sender', 'text'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f'missing public source {key}')
        if type(row['from_owner']) is not bool or row['source_id'] in seen:
            raise ValueError('invalid ownership or duplicate source ID')
        seen.add(row['source_id'])
        metadata = row['structured']
        if not isinstance(metadata, dict) or set(metadata) - {'participants', 'chat_name', 'email_subject', 'email_headers'}:
            raise ValueError('unsupported public source metadata')
        roster = metadata.get('participants')
        if roster is not None and (not isinstance(roster, list) or
                any(not isinstance(n, str) or not n for n in roster) or len(set(roster)) != len(roster)
                or row['sender'] not in roster):
            raise ValueError('invalid observed participant roster')
        headers = metadata.get('email_headers', {})
        if (not isinstance(headers, dict) or set(headers) - {'To','Cc','Message-ID','In-Reply-To','References'} or
                (headers and row['channel'] != 'email') or
                any(not isinstance(v,str) or '\r' in v or '\n' in v for v in headers.values())):
            raise ValueError('invalid deidentified mail headers')
        if any(not isinstance(v, str) for k, v in metadata.items() if k not in ('participants','email_headers')):
            raise ValueError('invalid source header')
        written, arrived, collected = (instant(row[k]) for k in ('written_at','arrived_at','collected_at'))
        if not written <= arrived <= collected:
            raise ValueError('invalid written, arrival or collection order')
        # Arrival is when the record reaches the account. An explicit access
        # event can delay visibility, for example a historical thread backfill.
        available = instant(row.get('available_at', row['arrived_at']))
        if available < arrived:
            raise ValueError('access cannot precede arrival')
        day = available.astimezone(zone).date()
        boundary = cutoff_on(day, hour, zone)
        if available > boundary.astimezone(UTC):
            boundary = cutoff_on(day + timedelta(days=1), hour, zone)
        # The old collector's timestamp is evidence about a different
        # workflow; it is not data available to this evening's user.
        public = {k:v for k,v in row.items() if k not in ('collected_at', 'available_at')}
        groups[boundary.isoformat(), row['channel'], row['thread']].append(public)
    documents = []
    for (at, channel, thread), group in sorted(groups.items(), key=lambda pair: (instant(pair[0][0]), *pair[0][1:])):
        group.sort(key=lambda r: (instant(r['written_at']), r['source_id']))
        thread_key = hashlib.sha256(thread.encode()).hexdigest()
        filename = f'{at[:10]}-{channel}-{thread_key}.txt'
        documents.append(Document(filename, at, channel, source_text(group, at), tuple(r['source_id'] for r in group),
                                  tuple(r['text'] for r in group)))
    return documents


@dataclass(frozen=True)
class Turn:
    id: str
    at: str
    prompt: str
    session: str
    measure: str
    claims: tuple[Claim, ...]
    after: str | None = None


@dataclass(frozen=True)
class WorkflowPlan:
    spec: dict
    spec_sha256: str
    source_basis: dict
    state: str
    documents: tuple[Document, ...]
    turns: tuple[Turn, ...]
    all_documents: tuple[Document, ...]
    channels: tuple[str, ...]
    canary: bool = False
    layout: dict | None = None
    prepared: bool = True

    def documents_for(self, channels):
        if self.layout is not None:
            if set(channels) != set(self.channels):
                raise ValueError('combined documents require the complete declared channel scope')
            return list(self.documents)
        return [doc for doc in self.documents if doc.channel in channels]


def compile_prepared(source, state):
    """Use the same audited preparation that the native runner consumes."""
    from pacbench.corpus.audit import open_prepared

    if state not in ('continuing-account', 'isolated-control'):
        raise ValueError('unknown account state mode')
    with open_prepared(source, portable=True) as corpus:
        if corpus.spec_sha256 is None:
            raise ValueError('prepared workflow requires the frozen authored spec')
        documents = _prepared_documents(corpus.messages)
        channels = tuple(sorted({doc.channel for doc in documents}))
        checks = [(row, contract) for row, contract, _ in corpus.questions]
        turns = _prepared_turns(checks, corpus.resolved['evaluation_context'], state)
        spec = {'version': corpus.resolved['id'], 'judge': CONTEXT_JUDGE_PROTOCOL,
                'format': 'prepared-recording-workflow-v1'}
        return WorkflowPlan(spec, corpus.spec_sha256, corpus.source_basis, state,
                            documents, turns, documents, channels)


def _prepared_documents(messages):
    groups = defaultdict(list)
    for message in messages:
        at = instant(message['collected_at'])
        groups[at, message['channel'], message['thread']].append(message)
    documents = []
    for index, ((at, channel, _), group) in enumerate(sorted(groups.items()), 1):
        group.sort(key=lambda message: (instant(message['written_at']), message['source_id']))
        cutoff = group[0]['collected_at']
        filename = f'{at.date().isoformat()}-{channel}-{index:04d}.txt'
        documents.append(Document(filename, cutoff, channel, source_text(group, cutoff),
                                  tuple(row['source_id'] for row in group), tuple(row['text'] for row in group)))
    return tuple(documents)


def _prepared_turns(checks, context, state):
    turns, prior = [], {}
    for row, contract in checks:
        key = row['id']
        # Conformance exercises the authored prompts without simulating an
        # assistant's behavioral verdict or awarding a grade.
        session = row.get('session') or ('shared' if context == 'shared' else key)
        after = prior.get(session)
        prior[session] = key
        if state == 'isolated-control':
            measure, session, after = 'isolated-control', key, None
        else:
            measure = 'conversational-followup' if after else 'initial'
        claims = tuple(Claim(claim.id, claim.statement, claim.evidence) for claim in contract.claims)
        turns.append(Turn(key, row['at'], contract.question, session, measure, claims, after))
    return tuple(turns)
