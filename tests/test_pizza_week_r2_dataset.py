"""Verify the portable synthetic r2 dataset independently of prior runs."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil

from pacbench.document_ingestion import daily_batches, document_text, ready_sources
from pacbench.followups import digest
from pacbench.frozen import load_bundle


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'datasets/pizza-week/r2'
FROZEN = 'a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


def binding_hash(files):
    return sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def test_export_checksums_and_original_input_identity(tmp_path):
    root = tmp_path / 'portable-copy'
    shutil.copytree(DATASET, root)
    manifest = read(root / 'manifest.json')
    files = manifest['file_sha256']
    assert set(files) == {
        path.relative_to(root).as_posix()
        for path in root.rglob('*') if path.is_file() and path.name not in {'README.md', 'manifest.json'}
    }
    for name, expected in files.items():
        path = root / name
        assert path.resolve().is_relative_to(root) and not path.is_symlink()
        assert checksum(path) == expected
        path.read_text(encoding='utf-8')
    assert binding_hash(files) == manifest['dataset_sha256']
    original = manifest['original_contract']
    assert (original['messages'], original['questions'], original['criteria']) == (142, 42, 188)
    assert len(original['input_file_sha256']) == 87
    assert binding_hash(original['input_file_sha256']) == original['sha256'] == FROZEN
    assert files[manifest['source_bundle']] == original['input_file_sha256']['bundle.json']
    spec = manifest['authored_spec']
    assert checksum(ROOT / spec['path']) == spec['sha256']
    assert spec['revision'] == '4e0a248d181e92f5dc4aebfe1f3fe5cacd0de287'


def test_provider_sources_and_evaluation_use_the_frozen_timeline():
    manifest = read(DATASET / 'manifest.json')
    zone = manifest['timezone']
    bundle = load_bundle(DATASET / manifest['source_bundle'], manifest['source_bundle_hash'])
    messages = [m for life in bundle['lives'].values() for m in life['messages']]
    assert Counter(m['channel'] for m in messages) == {'imessage': 120, 'email': 22}
    assert len({m['source_id'] for m in messages}) == 142
    schedule = manifest['source_batches']
    assert len(schedule) == 7
    for (day, batch), entry in zip(daily_batches(messages, zone), schedule, strict=True):
        assert entry['id'] == day
        assert entry['source_ids'] == [m['source_id'] for m in batch]
        source = DATASET / entry['source_file']
        assert source.read_text(encoding='utf-8') == document_text(batch, entry['at'])
        assert checksum(source) == entry['document_sha256']

    questions = [read(DATASET / name) for name in manifest['evaluation_questions']]
    assert len(questions) == len({q['id'] for q in questions}) == 42
    assert sum(len(q['claims']) for q in questions) == 188
    assert [q['at'] for q in questions] == sorted(q['at'] for q in questions)
    for question in questions:
        assert set(question) <= {'id', 'at', 'session', 'expect', 'question', 'claims',
                                 'original_contract_hash', 'original_evidence_hash', 'followup_policy'}
        assert datetime.fromisoformat(question['at']).utcoffset() is not None
        visible = {sid for source in ready_sources(schedule, 'mem0', question['at'], zone) for sid in source['source_ids']}
        for claim in question['claims']:
            assert set(claim) == {'id', 'statement', 'evidence'}
            assert set(claim['evidence']) <= visible
        policy = question['followup_policy']
        assert policy['contract_hash'] == question['original_contract_hash']
        assert policy['plan_hash'] == digest({k: v for k, v in policy.items() if k != 'plan_hash'})
        assert policy['claim_ids'] == [claim['id'] for claim in question['claims']]
    assert [q['id'] for q in questions if q['session'] == 'lunch-continuity'] == ['lunch-snapshot', 'lunch-refreshed']
