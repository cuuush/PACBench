"""Compile the current Pizza Week spec. Only public delivery objects cross into a provider."""
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .document_ingestion import PUBLIC_FIELDS, document_text
from .frozen import load_bundle
from .pizza_week_dataset import ROOT, load
from .prepare import read_spec
from .semantic_grade import Claim

SPEC = ROOT / 'specs/pizza_week_workflow_v2.json'
CONTEXT_JUDGE_PROTOCOL = 'semantic-judge-context-v4'
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


def compile_week(state, spec_path=SPEC):
    if state not in ('continuing-account', 'isolated-control'):
        raise ValueError('unknown account state mode')
    spec = read_spec(spec_path)
    if (spec['version'] != 'pizza-week-conversation-v2' or spec['judge'] != CONTEXT_JUDGE_PROTOCOL
            or spec['availability_basis'] != 'source-arrival-or-later-access'):
        raise ValueError('unsupported conversation spec or judge')
    root, _, checks, plan = load(ROOT / spec['dataset'])
    manifest = json.loads((root/'manifest.json').read_text())
    bundle = load_bundle(root/manifest['source_bundle'], manifest['source_bundle_hash'])
    messages = [r for life in bundle['lives'].values() for r in life['messages']]
    documents = nightly_documents(messages, spec['timezone'], spec['nightly_cutoff'])
    groups = {}
    for session, ids in spec['session_groups'].items():
        for key in ids:
            if key in groups:
                raise ValueError('turn assigned to two conversations')
            groups[key] = session
    controls = set(spec['isolated_controls'])
    turns = {}
    for row, contract, _ in checks:
        key = row['id']
        claims = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
        turns[key] = Turn(key, row['at'], row['prompt'], groups.get(key,key), 'initial', claims)
    if not controls <= turns.keys() or controls.intersection(groups):
        raise ValueError('isolated control cannot be in a primary session')
    originals = dict(turns)
    for item in spec['additional_turns']:
        key, parent = item['id'], item['after']
        if key in turns or parent not in turns or instant(item['at']) <= instant(turns[parent].at):
            raise ValueError('invalid or forward conversation dependency')
        measure, session = item['measure'], groups.get(key, key)
        if measure not in ('conversational-followup','user-correction','correction-uptake','correction-persistence'):
            raise ValueError('unknown conversational measure')
        if (session == turns[parent].session) == (measure == 'correction-persistence'):
            raise ValueError('dependent turns need a shared session; persistence requires a fresh one')
        claims = ()
        if measure == 'conversational-followup':
            subset = item['source_claims']
            reference = {c.id:c for c in originals[subset['question']].claims}
            claims = tuple(reference[i] for i in subset['ids'])
        elif measure in ('correction-uptake','correction-persistence'):
            c = item['user_claim']
            correction = turns[c['source_turn']]
            if correction.measure != 'user-correction' or not (
                parent == correction.id or turns[parent].after == correction.id
            ):
                raise ValueError('correction claim requires an earlier delivered correction')
            claims = (Claim(c['id'], c['statement'], ('turn/' + correction.id,)),)
        turns[key] = Turn(key,item['at'],item['prompt'],session,measure,claims,parent)
    if set(groups) - turns.keys() or len({instant(t.at) for t in turns.values()}) != len(turns):
        raise ValueError('unknown session member or simultaneous questions')
    if state == 'isolated-control':
        turns = {k:Turn(k,t.at,t.prompt,k,'isolated-control',t.claims) for k,t in originals.items()}
    else:
        turns = {k:t for k,t in turns.items() if k not in controls}
    ordered = sorted(turns.values(), key=lambda t: instant(t.at))
    available_at = {sid:instant(d.cutoff) for d in documents for sid in d.source_ids}
    previous = {}
    for t in ordered:
        for c in t.claims:
            for source in c.evidence:
                if source.startswith('turn/'):
                    if source[5:] not in turns or instant(turns[source[5:]].at) >= instant(t.at):
                        raise ValueError('future user correction')
                elif available_at[source] > instant(t.at):
                    raise ValueError('criterion cites an unavailable nightly export')
        if t.after and t.measure != 'correction-persistence' and previous.get(t.session) != t.after:
            raise ValueError('dependent follow-up must immediately follow parent within the chat')
        previous[t.session] = t.id
    return spec, documents, ordered, plan['frozen_inputs']
