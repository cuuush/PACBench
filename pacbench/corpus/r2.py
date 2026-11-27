"""Compile the frozen Pizza Week corpus, questions and source layouts."""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
from functools import lru_cache, wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from pacbench.corpus.contract import ContractClaim, EvaluationContract, load_contract
from pacbench.corpus.prepare import read_spec, resolve_spec
from pacbench.corpus.sources import bundle_hash, load_bundle, messages_for
from pacbench.email_fixture import mail_address
from pacbench.evidence_store import file_sha as pizza_day_sha
from pacbench.imessage_fixture import identity
from pacbench.judging.grade import CONTEXT_JUDGE_PROTOCOL, Claim
from pacbench.judging.recovery import validate_policy
from pacbench.providers import BOTH
from pacbench.providers.memcal.source import member_digest, verify_contract
from pacbench.workflows.source_plan import daily_batches, document_text, ready_sources
from pacbench.workflows.spec import (
    Document,
    Turn,
    WorkflowPlan,
    instant,
    nightly_documents,
)

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
            cache_key = _input_stamp()
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
                        layout=layout, prepared=False)


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
    from pacbench.corpus.sources import parse_json_object

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
    from pacbench.providers.memcal.source import verify_contract

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


BUNDLE = '176a53acda1d8502886e6b7360d0fdee943e13d2c94f5d04e7b7028cefab4717'


DAY_CONTRACT = '30241cab0444520231e6151907f9796140b10b46320614c31f45745b6cf35dfc'


DAY_SPEC = '5160c6fbb73e0d12d6a1cec84c2ac54bc93f5ecaa6c2a1cd0a5cfce10ee85529'


DAY_DOCUMENT = '56d09bcb7c7ae2d39f9e2b0e55a0768c78745feddcfc02e34097a7ed879d9f98'


DAY = '2026-09-10'


DAY_AT = DAY + 'T18:50:00-04:00'


QUESTION_AT = '2026-09-11T09:00:00-04:00'


QUESTION = 'Hey, can you give me a quick recap on our pizza plan?'


DAY_BASIS = {'bundle_sha256': BUNDLE, 'contract_sha256': DAY_CONTRACT}


def pizza_day_inputs(preparation):
    from pacbench.workflows.source_plan import document_text
    if pizza_day_sha(preparation/'spec.json') != DAY_SPEC:
        raise ValueError('expected the published Pizza Day specification')
    messages = messages_for(load_bundle(preparation/'bundle.json', BUNDLE), 'pizza-day')
    contract = load_contract(preparation/'contract.json')
    check, = json.loads((preparation/'resolved-spec.json').read_text())['checks']
    source_ids = tuple(m['source_id'] for m in messages)
    if (contract.contract_hash != DAY_CONTRACT or contract.question != QUESTION or check['prompt'] != QUESTION
            or check['at'] != QUESTION_AT or check['id'] != 'pizza-plan' or len(messages) != len(set(source_ids))
            or len(messages) != 25 or {m['channel'] for m in messages} != {'imessage'}
            or [c.statement for c in contract.claims] != check['facts'] or len(contract.claims) != 5
            or any(not c.evidence or set(c.evidence)-set(source_ids) for c in contract.claims)):
        raise ValueError('frozen Pizza Day sources or criteria differ')
    doc = Document('messages.txt', DAY_AT, 'imessage', document_text(messages, DAY_AT), source_ids,
                   tuple(m['text'] for m in messages))
    if doc.sha256 != DAY_DOCUMENT:
        raise ValueError('published export differs')
    turn = Turn(check['id'], QUESTION_AT, QUESTION, 'pizza-recap', 'initial',
                tuple(Claim(c.id, c.statement, tuple(c.evidence)) for c in contract.claims))
    return messages, doc, turn


LINEUP_ADMISSION = 'pizza-day-native-document-v1'


def checkpoint_plan(resolved,schedule,provider,*,contracts=None):
    """Keep every exact question time; restart at a changed ingestion boundary."""
    from pacbench.corpus.contract import EvaluationContract
    from pacbench.workflows.source_plan import ready_sources
    if provider not in {'memcal','mem0','reme'}:raise ValueError('Unknown staged provider')
    questions=sorted(resolved['checks'],key=lambda q:datetime.fromisoformat(q['at']))
    if len({q['id'] for q in questions})!=len(questions):raise ValueError('Duplicate question identity')
    if contracts is not None and set(contracts)!={q['id'] for q in questions}:raise ValueError('Frozen contract coverage differs')
    groups=[];last_key=None
    for index,q in enumerate(questions):
        at=datetime.fromisoformat(q['at'])
        if at.tzinfo is None:raise ValueError('Question clock needs timezone')
        batches=ready_sources(schedule,provider,q['at'],resolved['timezone'])
        key=(tuple(b['id'] for b in batches),at.astimezone(ZoneInfo(resolved['timezone'])).date().isoformat())
        if key!=last_key:
            groups.append({'id':len(groups)+1,'ready_batch_ids':list(key[0]),'local_date':key[1],
                'restart_before_ingest_or_clock_boundary':True,'questions':[]});last_key=key
        contract=contracts[q['id']] if contracts is not None else None
        if isinstance(contract, EvaluationContract):
            contract = {'question': contract.question, 'contract_hash': contract.contract_hash,
                        'claims': contract.claims}
        elif contract is not None:
            body={k:v for k,v in contract.items() if k!='contract_hash'}
            if (hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=contract['contract_hash'] or
                contract['question_at']!=q['at']):raise ValueError('Frozen contract hash or question time differs')
        prompt=contract['question'] if contract else q['prompt']
        groups[-1]['questions'].append({'id':q['id'],'at':q['at'],'prompt':prompt,
            'horizon':questions[index+1]['at'] if index+1<len(questions) else None,
            'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'authored_prompt_sha256':hashlib.sha256(q['prompt'].encode()).hexdigest(),
            'prompt_from':'frozen evaluated contract' if contract else 'authored spec; frozen contract not supplied',
            'contract_hash':contract['contract_hash'] if contract else None,
            'criteria_count':len(contract['claims']) if contract else None,'authored_fact_count':len(q['facts'])})
    return {'protocol':'chronological-observation-checkpoints-v1','provider':provider,'groups':groups,
        'questions':len(questions),'criteria':sum(q['criteria_count'] for g in groups for q in g['questions']) if contracts is not None else None,
        'authored_facts':sum(len(q['facts']) for q in questions),
        'times_collapsed':False,'automatic_replay':False,'live_qualified':False,
        'required_gates':['checkpoint native readiness and quiescence','native isolated observation binding',
            'new request-bound clock admission and application-clock audit','prepaid headroom','selected MemCal repair gate when applicable']}


FIXTURES = ROOT / 'tests/fixtures'


EXCEPTION = FIXTURES / 'spec_first_rsvp_scope_v1.json'


def authored_questions():
    path = ROOT / 'specs/pizza_week_assistant.json'
    _, _, resolved = resolve_spec(read_spec(path), path.with_suffix(''))
    return resolved['checks']


def scope_pair(authored, turn, dataset, track):
    a = dict(id=authored['id'], at=authored['at'], question=authored['prompt'], facts=authored['facts'])
    if 'expect' in authored:
        a['expect'] = authored['expect']
    s = dict(dataset=dataset, track=track, id=turn.id, at=turn.at, question=turn.prompt,
             claims=[asdict(c) for c in turn.claims])
    return a, s


def historical_rsvp_scopes():
    track = 'continuing-account'
    workflow, _, turns, _ = compile_week(track)
    authored = next(q for q in authored_questions() if q['id'] == 'rsvp-before')
    turn = next(t for t in turns if t.id == authored['id'])
    return scope_pair(authored, turn, workflow['dataset'], track)


def audit_scope(authored, scored, exception=None):
    facts = authored['facts']
    statements = [claim['description'] for claim in scored['claims']]
    differing = [field for field in ('id', 'at', 'question') if authored[field] != scored[field]]
    if facts != statements:
        differing.append('criteria')
    if not differing and exception is None:
        return dict(status='aligned', differences=[])
    # Bind both complete projections, including ordered claims and evidence.
    drift_sha = bundle_hash({'authored': authored, 'scored': scored})
    if (not differing or not exception or not exception.get('reason', '').strip()
            or exception.get('revision') not in ('rsvp-existing-scope-v1', 'pizza-week-existing-scope-v1')
            or exception.get('question_id') != authored['id'] or exception.get('scope_sha256') != drift_sha):
        detail = ', '.join(differing) or 'obsolete exception'
        raise ValueError(f'unrecognized scope drift on {authored["id"]}: {detail} ({drift_sha})')
    return dict(status='existing-scope exception', differences=differing, reason=exception['reason'],
                authored_question=authored['question'], scored_question=scored['question'],
                authored_facts=len(facts), scored_claims=len(statements),
                authored_only=[s for s in facts if s not in statements],
                scored_only=[s for s in statements if s not in facts])


PRIMARY = 'continuing-account'


CONTROL = 'isolated-control'


AUDIT_REVISION = 'pizza-week-authoring-audit-v1'


REGISTRY = FIXTURES / 'pizza_week_authoring_audit_v1.json'


DATASET = ROOT / 'datasets/pizza-week/r2'


WORKFLOW_MANIFEST = 'c15fd1b408738d448d3724ec668de93e1f0051e5a8c47c61b15e145d86f74496'


def compare_questions(questions, workflow, turns, control_turns, registry, rsvp):
    """Compare original authored rows, before the r2 loader substitutes frozen facts."""
    if registry['revision'] != AUDIT_REVISION:
        raise ValueError('unknown authoring audit revision')
    exceptions = dict(registry['fact_list_exceptions'])
    if rsvp['question_id'] in exceptions:
        raise ValueError('duplicate RSVP exception')
    exceptions[rsvp['question_id']] = rsvp
    prose = dict(registry['expect_contracts'])
    authored = {q['id']: q for q in questions}
    controls = {t.id: t for t in control_turns}
    primary = {t.id: t for t in turns}
    extra = {q['id']: q for q in workflow['additional_turns']}
    isolated_only = set(workflow['isolated_controls'])
    expected_primary = (authored.keys() - isolated_only) | extra.keys()
    if (len(authored) != len(questions) or len(primary) != len(turns) or len(controls) != len(control_turns)
            or len(extra) != len(workflow['additional_turns']) or extra.keys() & authored.keys()
            or authored.keys() != controls.keys() or not isolated_only <= controls.keys()
            or primary.keys() != expected_primary):
        raise ValueError('turn inventory differs between authored, primary and isolated schedules')

    rows = []
    for t in [*turns, *(t for t in control_turns if t.id in isolated_only)]:
        track = CONTROL if t.id in isolated_only else PRIMARY
        row = dict(id=t.id, track=track, at=t.at, measure=t.measure, scored_claims=len(t.claims))
        if t.id in extra:
            declaration = extra[t.id]
            row.update(basis='workflow', after=declaration['after'])
            if 'source_claims' in declaration:
                row['source_claims'] = declaration['source_claims']
            if 'user_claim' in declaration:
                row['source_turn'] = declaration['user_claim']['source_turn']
        else:
            a, s = scope_pair(authored[t.id], t, workflow['dataset'], track)
            if a['facts']:
                exception = exceptions.pop(t.id, None)
                result = audit_scope(a, s, exception)
                row.update(basis='explicit-facts', authored_facts=len(a['facts']), differences=result['differences'])
                if exception:
                    row['exception'] = exception
            else:
                entry = prose.pop(t.id, None)
                if (not a.get('expect') or not entry or not entry.get('reason', '').strip()
                        or bundle_hash({'authored': a, 'scored': s}) != entry['scope_sha256']):
                    raise ValueError(f'unregistered prose expectation or changed r2 contract: {t.id}')
                row.update(basis='prose-expectation', historical_scope=entry['reason'])
        rows.append(row)
    if exceptions or prose:
        raise ValueError(f'unused scope entries: {sorted(exceptions.keys() | prose.keys())}')
    return rows


class RefundStatus(StrEnum):
    ISSUED = 'issued'
    PENDING = 'pending'


@dataclass(frozen=True)
class ReportedRefund:
    booking: str
    cancellation_day: str
    reported_status: RefundStatus
    source: str


# Reviewed sidecar for one public r2 claim. The speaker reports issuance.
# Funds arriving in the user's account would be a different claim.
REFUND = ReportedRefund('pottery', 'Wednesday October 7', RefundStatus.ISSUED,
                        'message-2bdf115e8eb602900aba9851')


def refund_controls(turn, documents):
    fact = REFUND
    if turn.id != 'refund' or len(turn.claims) != 1:
        raise ValueError('refund example needs the historical single-claim question')
    claim, = turn.claims
    statement = f'The {fact.booking} refund is reported as {fact.reported_status}.'
    if (claim.id, claim.description, claim.evidence) != ('fact-1', statement, (fact.source,)):
        raise ValueError('typed refund differs from the frozen claim')
    body = f'Your refund for the {fact.booking} class cancelled {fact.cancellation_day} has been {fact.reported_status}.'
    records = [(d.cutoff, text) for d in documents for source, text in zip(d.source_ids, d.bodies, strict=True)
               if source == fact.source]
    if len(records) != 1 or instant(records[0][0]) > instant(turn.at) or records[0][1] != body:
        raise ValueError('typed refund needs its exact cited report available at the cutoff')
    cases, key = [], {}
    phrasing = {RefundStatus.ISSUED: ' They confirm they have issued your refund.',
                None: '', RefundStatus.PENDING: ' They say your refund is still pending and has yet to be issued.'}
    other_status, = set(RefundStatus) - {fact.reported_status}
    for status, verdict in ((fact.reported_status, 'correct'), (None, 'missed'), (other_status, 'misrepresented')):
        answer = f'The {fact.booking} class was cancelled.' + phrasing[status]
        ident = bundle_hash({'question_id': turn.id, 'answer': answer})[:16]
        cases.append(dict(id=ident, at=turn.at, question=turn.prompt, answer=answer,
                          claims=[(claim.id, claim.description, claim.evidence)]))
        key[ident] = [verdict]
    return sorted(cases, key=lambda c: c['id']), {'verdicts': key}


def audit_week():
    archive = json.loads(checked_file(DATASET, 'workflow-v2.json', WORKFLOW_MANIFEST))
    pinned_workflow = json.loads(checked_file(ROOT, archive['spec'], archive['spec_sha256']))
    workflow, docs, turns, source = compile_week(PRIMARY)
    if workflow != pinned_workflow:
        raise ValueError('compiled workflow declarations differ from the archived spec')
    _, _, isolated, _ = compile_week(CONTROL)
    rows = compare_questions(authored_questions(), workflow, turns, isolated, read_spec(REGISTRY), read_spec(EXCEPTION))
    # The archive serialization binds IDs, wording, evidence order, clocks, sessions
    # and parentage even where authored and compiled prose change together.
    frozen = verify_contract(docs, turns, source)
    if frozen['contract_sha256'] != archive['schedules'][PRIMARY]['sha256']:
        raise ValueError('primary schedule differs from the frozen r2 contract')
    if member_digest([asdict(t) for t in isolated]) != archive['schedules'][CONTROL]['sha256']:
        raise ValueError('isolated-control schedule differs from the frozen r2 contract')
    primary_rows = [r for r in rows if r['track'] == PRIMARY]
    primary_by_basis = Counter(r['basis'] for r in primary_rows)
    summary = dict(authored_fact_lists=sum(r['basis'] == 'explicit-facts' for r in rows),
                   primary_turns_by_basis=dict(primary_by_basis),
                   primary_claims_by_basis={k: sum(r['scored_claims'] for r in primary_rows if r['basis'] == k)
                                            for k in primary_by_basis},
                   isolated_only=[r['id'] for r in rows if r['track'] == CONTROL],
                   scope_exceptions=[r['id'] for r in rows if 'exception' in r])
    refund = next(t for t in turns if t.id == 'refund')
    cases, key = refund_controls(refund, docs)
    report = dict(revision=AUDIT_REVISION, dataset=workflow['dataset'], summary=summary, frozen_primary=frozen, turns=rows,
                  comparison='Text comparisons check wording and order. Semantic entailment requires evidence.',
                  typed_example=dict(turn=refund.id, claim=refund.claims[0].id, as_of=refund.at,
                                     reported_status=REFUND.reported_status, control_count=len(cases)))
    return report, cases, key


def audit_r2_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emit', choices=('report', 'cases', 'key'), default='report')
    args = parser.parse_args(argv)
    report, cases, key = audit_week()
    print(json.dumps({'report': report, 'cases': cases, 'key': key}[args.emit], indent=2, ensure_ascii=False))
