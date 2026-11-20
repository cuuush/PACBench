"""Read the verified, prompt-free Pizza Week r2 dataset for the scored runner."""
import hashlib
import json
from pathlib import Path

from .document_ingestion import daily_batches, document_text, ready_sources
from .followups import validate_policy
from .frozen import load_bundle
from .observation_adapters import checkpoint_plan
from .semantic_contract import ContractClaim, EvaluationContract

ROOT = Path(__file__).resolve().parents[1]
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
    from .prepare import resolve_spec

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
    return root, schedule, checks, plan
