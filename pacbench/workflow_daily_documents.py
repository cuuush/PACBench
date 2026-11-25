"""Seven chronological source documents for new Pizza Week runs."""
from collections import Counter, defaultdict
from functools import lru_cache
from hashlib import sha256
import json
import uuid
from zoneinfo import ZoneInfo

from .canary_capability import sha
from .email_fixture import mail_address
from .frozen import load_bundle
from .imessage_fixture import identity
from .pizza_week_dataset import MANIFEST, ROOT, checked_file
from .workflow_memcal_contract import member_digest, verify_contract
from .workflow_spec import Document, SPEC, compile_week, instant

DAILY_SPEC = ROOT / 'specs/pizza_week_daily_documents_v2.json'
DAILY_CHANNEL = 'daily-messages'
LAYOUT = 'pizza-week-seven-daily-documents-v2'
STORAGE_METADATA = ('SQLite ROWID and Apple control records', 'IMAP UID and UIDVALIDITY',
                    'transport cursors and collection clocks', 'provider-assigned document and revision IDs')


def message_metadata(row):
    """Project the fields supplied by the frozen synthetic chat.db and IMAP inputs."""
    structured = row['structured']
    meta = {key: row[key] for key in ('channel', 'sender', 'written_at', 'arrived_at', 'from_owner')}
    if row['channel'] == 'email':
        headers = structured.get('email_headers', {})
        assigned = '<' + str(uuid.uuid5(uuid.NAMESPACE_URL, row['source_id'])) + '@mail.example.test>'
        meta.update(message_id=headers.get('Message-ID', assigned), sender_address=mail_address(row['sender']),
                    subject=structured['email_subject'], folder='Sent' if row['from_owner'] else 'INBOX')
        meta.update({name: headers[name] for name in ('To', 'Cc', 'References', 'In-Reply-To') if name in headers})
    else:
        roster = structured['participants']
        conversation = ('chat' + uuid.uuid5(uuid.NAMESPACE_URL, 'pacbench-source-thread:' + row['thread']).hex
                        if len(roster) > 2 else identity(next(name for name in roster if name != 'Me'))['handle'])
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pacbench-source-message:' + row['source_id'])).upper()
        meta.update(message_id=guid, sender_address=identity(row['sender'])['handle'],
                    conversation_id='id:' + conversation, participants=roster)
        if structured.get('chat_name'):
            meta['chat_name'] = structured['chat_name']
    return meta


def message_block(row):
    return json.dumps(message_metadata(row), ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n' + row['text']


def daily_documents(originals, messages, timezone, *, mail_history=None):
    zone = ZoneInfo(timezone)
    records = {row['source_id']: row for row in messages}
    expected = [(doc.cutoff, doc.channel, ident, text) for doc in originals
                for ident, text in zip(doc.source_ids, doc.bodies, strict=True)]
    identities = {item[2] for item in expected}
    if len(identities) != len(expected) or identities != records.keys() or len(records) != len(messages):
        raise ValueError('Frozen source identities are missing, repeated, or contain added records')
    groups = defaultdict(list)
    for cutoff, channel, ident, text in expected:
        row = records[ident]
        creation = instant(row['written_at']).astimezone(zone)
        release = instant(cutoff).astimezone(zone)
        if creation.date() != release.date():
            raise ValueError('Source creation day differs from the frozen release day; prior-day backfill is forbidden')
        arrived = instant(row['arrived_at'])
        available = instant(row.get('available_at', row['arrived_at']))
        if not creation <= arrived <= available <= release:
            raise ValueError('Source creation, arrival or access falls outside the frozen cutoff')
        if (row['channel'], row['text']) != (channel, text):
            raise ValueError('Daily export differs from the original source record')
        if mail_history is not None and ident == mail_history['source_id']:
            old = records[mail_history['quoted_prior_source_id']]
            raw = text.encode()
            body = raw[:mail_history['retained_authored_bytes']]
            if (channel != 'email' or old['channel'] != 'email' or instant(old['written_at']).astimezone(zone).date() >= creation.date()
                    or sha256(raw).hexdigest() != mail_history['raw_body_sha256']
                    or sha256(body).hexdigest() != mail_history['projected_body_sha256']
                    or old['text'].encode() not in raw[len(body):] or old['text'].encode() in body):
                raise ValueError('Frozen MemCal email body projection differs from its verified prior-day quote exclusion')
            row = {**row, 'text': body.decode()}
        groups[cutoff].append(row)
    docs = []
    for cutoff, rows in sorted(groups.items(), key=lambda item: instant(item[0])):
        rows.sort(key=lambda row: (instant(row['written_at']), row['source_id']))
        doc_text = '\n\n'.join(message_block(row) for row in rows) + '\n'
        day = instant(cutoff).astimezone(zone).date().isoformat()
        docs.append(Document(f'{day}-messages.txt', cutoff, DAILY_CHANNEL, doc_text,
                             tuple(row['source_id'] for row in rows), tuple(row['text'] for row in rows)))
    return docs


@lru_cache(maxsize=1)
def compile_daily_week():
    spec, original, turns, basis = compile_week('continuing-account')
    layout = json.loads(DAILY_SPEC.read_text())
    if (layout['version'] != LAYOUT or layout['frozen_workflow_sha256'] != sha(SPEC)
            or layout['frozen_source_binding'] != basis['sha256']):
        raise ValueError('Daily source layout differs from the frozen Pizza Week workflow')
    contract = verify_contract(original, turns, basis)
    root = ROOT / spec['dataset']
    manifest = json.loads(checked_file(root, 'manifest.json', MANIFEST))
    bundle = load_bundle(root / manifest['source_bundle'], manifest['source_bundle_hash'])
    messages = [row for life in bundle['lives'].values() for row in life['messages']]
    mail_history = layout['verified_mail_history_exclusion']
    docs = daily_documents(original, messages, spec['timezone'], mail_history=mail_history)
    if (len(docs) != 7 or [doc.cutoff for doc in docs] != contract['nightly_cutoffs']
            or sum(len(doc.source_ids) for doc in docs) != contract['records']):
        raise ValueError('Pizza Week requires one complete document per frozen creation day')
    index = [dict(name=doc.filename, cutoff=doc.cutoff, sha256=doc.sha256, source_ids=list(doc.source_ids)) for doc in docs]
    index_sha = member_digest(index)
    if layout['normalized_document_index_sha256'] != index_sha:
        raise ValueError('Daily document bytes or order differ from the frozen layout manifest')
    return spec, docs, turns, basis, {
        **contract, 'source_layout': LAYOUT, 'layout_spec_sha256': sha(DAILY_SPEC),
        'normalized_index_sha256': index_sha, 'documents': len(docs), 'document_index': index,
        'source_records_by_channel': dict(Counter(row['channel'] for row in messages)),
        'excluded_storage_metadata': list(STORAGE_METADATA),
        'memcal_ingestion_revision': layout['memcal_ingestion_revision'],
        'verified_mail_history_exclusion': mail_history,
    }
