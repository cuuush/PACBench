"""Compile the frozen Pizza Week corpus, questions and source layouts."""
from collections import Counter, defaultdict
from dataclasses import replace
from functools import lru_cache, wraps
import hashlib
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from ..email_fixture import mail_address
from ..imessage_fixture import identity
from ..document_ingestion import daily_batches, document_text, ready_sources
from ..followups import validate_policy
from pacbench.corpus.sources import load_bundle
from ..observation_adapters import checkpoint_plan
from pacbench.corpus.contract import ContractClaim, EvaluationContract
from ..providers.memcal.contract import member_digest
from ..semantic_grade import Claim, CONTEXT_JUDGE_PROTOCOL
from ..workflows.spec import Document, Turn, WorkflowPlan, instant, nightly_documents
from ..workflows.tracks import BOTH

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = 'a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'
# Trust the reviewed manifest, including schedule, export hashes and spec pin.
MANIFEST = '038349eaa06d9a1a78bf5ebb598935524a3e666431460ccddcc4a941084fcba9'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def binding(files):
    return sha(json.dumps(files, sort_keys=True).encode())


def checked_file(root, name, expected):
    path = root / name
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError(f'Invalid dataset file path: {name}')
    content = path.read_bytes()
    if sha(content) != expected:
        raise ValueError(f'Dataset checksum differs: {name}')
    return content


def load(dataset):
    root, schedule, checks, plan, _ = _load_dataset(dataset)
    return root, schedule, checks, plan


def _load_dataset(dataset):
    from pacbench.corpus.prepare import resolve_spec

    root = Path(dataset).resolve(strict=True)
    manifest = json.loads(checked_file(root, 'manifest.json', MANIFEST))
    files = manifest['file_sha256']
    original = manifest['original_contract']
    if (binding(files) != manifest['dataset_sha256'] or
            binding(original['input_file_sha256']) != ORIGINAL or
            original['sha256'] != ORIGINAL):
        raise ValueError('Dataset input identity differs')
    payload = {name: checked_file(root, name, expected) for name, expected in files.items()}
    spec_path = manifest['authored_spec']['path']
    spec = json.loads(checked_file(ROOT, spec_path, manifest['authored_spec']['sha256']))
    _, _, resolved = resolve_spec(spec, (ROOT / spec_path).with_suffix(''))
    bundle = load_bundle(root / manifest['source_bundle'], manifest['source_bundle_hash'])
    messages = [m for life in bundle['lives'].values() for m in life['messages']]
    schedule = manifest['source_batches']
    zone = manifest['timezone']
    for (day, batch), entry in zip(daily_batches(messages, zone), schedule, strict=True):
        if (entry['id'] != day or entry['source_ids'] != [m['source_id'] for m in batch] or
                sha(payload[entry['source_file']]) != entry['document_sha256'] or
                document_text(batch, entry['at']).encode() != payload[entry['source_file']]):
            raise ValueError('Dataset source order or native document differs')
    by_id = {row['id']: row for row in resolved['checks']}
    checks = []
    for name in manifest['evaluation_questions']:
        item = json.loads(payload[name])
        row = {**by_id[item['id']], 'prompt': item['question']}
        if any(row.get(k) != item.get(k) for k in ('at', 'session', 'expect')):
            raise ValueError(f'Dataset question timing, session or intent differs: {item["id"]}')
        claims = tuple(ContractClaim(c['id'], c['statement'], tuple(c['evidence']))
                       for c in item['claims'])
        # Two broad questions gained authored criteria in the reviewed r2 freeze.
        if row['facts'] or row['id'] in {'assistant-weekend-before', 'assistant-bored'}:
            row['facts'] = [claim.statement for claim in claims]
        visible = {sid for batch in ready_sources(schedule, 'mem0', row['at'], zone)
                   for sid in batch['source_ids']}
        if any(not set(claim.evidence) <= visible for claim in claims):
            raise ValueError(f'Dataset criterion cites unavailable source: {item["id"]}')
        contract = EvaluationContract(claims, item['question'], item['original_evidence_hash'],
                                      item['original_contract_hash'])
        checks.append((row, contract, validate_policy(item['followup_policy'], contract)))
    rows = [row for row, _, _ in checks]
    plan = checkpoint_plan({**resolved, 'checks': rows}, schedule, 'mem0',
                           contracts={row['id']: contract for row, contract, _ in checks})
    counts = (len(messages), plan['questions'], plan['criteria'])
    if counts != (142, 42, 188) or len({m['source_id'] for m in messages}) != 142:
        raise ValueError('Dataset message, question or criterion count differs')
    plan['frozen_inputs'] = {'protocol': 'r2-input-file-bindings-v1', 'sha256': ORIGINAL,
                             'messages': 142, 'questions': 42, 'criteria': 188}
    return root, schedule, checks, plan, messages


SPEC = ROOT / 'specs/pizza_week_workflow_v2.json'
CANARY_TURNS = 4


def _copy_schedule(value):
    if isinstance(value, dict):
        return {key: _copy_schedule(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_schedule(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_schedule(item) for item in value)
    return value  # The remaining schedule values are frozen documents, turns, or scalars.


def _input_stamp():
    # Unbound schedule readers may share completed work only while all frozen
    # inputs retain the same file identities and change times.
    dataset = ROOT / json.loads(SPEC.read_bytes())['dataset']
    manifest_path = dataset / 'manifest.json'
    manifest = json.loads(checked_file(dataset, 'manifest.json', MANIFEST))
    paths = (SPEC, DAILY_SPEC, manifest_path, ROOT / manifest['authored_spec']['path'],
             *(dataset / name for name in manifest['file_sha256']))
    stamps = []
    for path in paths:
        if path != path.resolve():
            raise ValueError(f'Invalid dataset file path: {path}')
        info = path.stat()
        stamps.append((str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return tuple(stamps)


def cache_bound_schedule(*, maxsize):
    """Reuse a bound run schedule or the same verified on-disk inputs."""
    def decorate(compile_schedule):
        @lru_cache(maxsize=maxsize)
        def by_scope(scope_binding, args, kwargs):
            return compile_schedule(*args, **dict(kwargs))

        @wraps(compile_schedule)
        def selected(*args, **kwargs):
            from ..workflows.capability import ENV

            scope_binding = os.environ.get(ENV)
            cache_key = scope_binding if scope_binding else _input_stamp()
            return _copy_schedule(by_scope(cache_key, args, tuple(kwargs.items())))

        return selected

    return decorate


def compile_pizza_week(state='continuing-account', *, canary=False, seven_daily_documents=False):
    if canary and (state != 'continuing-account' or seven_daily_documents):
        raise ValueError('Pizza Week canary requires the continuing per-thread schedule')
    plan = _compile_pizza_week(state, seven_daily_documents)
    if not canary:
        return plan
    turns = plan.turns[:CANARY_TURNS]
    documents = tuple(doc for doc in plan.documents if instant(doc.cutoff) <= instant(turns[-1].at))
    return replace(plan, documents=documents, turns=turns, canary=True)


def _compile_pizza_week(state, seven_daily_documents):
    layout = None
    if seven_daily_documents:
        if state != 'continuing-account':
            raise ValueError('seven daily files require the complete continuing-account document condition')
        spec, documents, turns, basis, layout, digest = _compile_daily_week()
    else:
        spec, documents, turns, basis, _, digest = _compile_week(state, SPEC)
    documents = tuple(documents)
    return WorkflowPlan(spec, digest, basis, state, documents, tuple(turns), documents, BOTH,
                        layout=layout, authorize=authorize)


def authorize(plan, condition, account, directory, judge, resume):
    if plan.layout is not None:
        if condition.track != 'nightly-document':
            raise ValueError('seven daily files require the complete continuing-account document condition')
        if account.evidence_kind != 'recording-fake':
            if (account.evidence_kind != 'native' or condition.provider != 'supermemory'
                    or resume or judge is not None):
                raise ValueError('native seven daily files require a fresh qualified stock Supermemory Week without grading')
            from ..providers.supermemory.week import verify_daily_execution

            verify_daily_execution(condition, account, directory, list(plan.documents), plan.source_basis, plan.layout)
    if condition.provider == 'memcal' and condition.track == 'first-party-connector' and plan.state == 'continuing-account':
        from pacbench.providers.memcal.contract import verify_contract

        full = compile_pizza_week() if plan.canary else plan
        verify_contract(list(full.documents), list(full.turns), full.source_basis)


def verify_legacy_combined_schedule(condition, documents, turns):
    """Retain direct callers of the original raw-list daily workflow."""
    if ({doc.channel for doc in documents} != {DAILY_CHANNEL} or condition.track != 'nightly-document'
            or condition.state != 'continuing-account' or set(condition.channels) != set(BOTH)):
        raise ValueError('source corpus does not match declared channel scope')
    plan = compile_pizza_week(seven_daily_documents=True)
    if tuple(documents) != plan.documents or tuple(turns) != plan.turns:
        raise ValueError('seven daily files must match the frozen source and question schedule')


def compile_week(state, spec_path=SPEC):
    return _compile_week(state, spec_path)[:4]


def _compile_week(state, spec_path):
    from ..corpus.sources import parse_json_object

    if state not in ('continuing-account', 'isolated-control'):
        raise ValueError('unknown account state mode')
    raw = Path(spec_path).read_bytes()
    spec = parse_json_object(raw)
    if (spec['version'] != 'pizza-week-conversation-v2' or spec['judge'] != CONTEXT_JUDGE_PROTOCOL
            or spec['availability_basis'] != 'source-arrival-or-later-access'):
        raise ValueError('unsupported conversation spec or judge')
    _, _, checks, plan, messages = _load_dataset(ROOT / spec['dataset'])
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
    return spec, documents, ordered, plan['frozen_inputs'], messages, sha(raw)


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
                    or hashlib.sha256(raw).hexdigest() != mail_history['raw_body_sha256']
                    or hashlib.sha256(body).hexdigest() != mail_history['projected_body_sha256']
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


def compile_daily_week():
    return _compile_daily_week()[:5]


@cache_bound_schedule(maxsize=4)
def daily_week_for_scope():
    return compile_daily_week()


def _compile_daily_week():
    from ..providers.memcal.contract import verify_contract

    spec, original, turns, basis, messages, spec_sha = _compile_week('continuing-account', SPEC)
    layout_bytes = DAILY_SPEC.read_bytes()
    layout = json.loads(layout_bytes)
    if (layout['version'] != LAYOUT or layout['frozen_workflow_sha256'] != spec_sha
            or layout['frozen_source_binding'] != basis['sha256']):
        raise ValueError('Daily source layout differs from the frozen Pizza Week workflow')
    contract = verify_contract(original, turns, basis)
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
        **contract, 'source_layout': LAYOUT, 'layout_spec_sha256': sha(layout_bytes),
        'normalized_index_sha256': index_sha, 'documents': len(docs), 'document_index': index,
        'source_records_by_channel': dict(Counter(row['channel'] for row in messages)),
        'excluded_storage_metadata': list(STORAGE_METADATA),
        'memcal_ingestion_revision': layout['memcal_ingestion_revision'],
        'verified_mail_history_exclusion': mail_history,
    }, spec_sha
