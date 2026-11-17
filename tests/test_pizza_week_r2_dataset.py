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


def test_launcher_selects_dataset_without_runtime_or_archive(tmp_path):
    import subprocess
    import sys

    dataset = tmp_path / 'dataset'
    shutil.copytree(DATASET, dataset)
    command = [sys.executable, str(ROOT / 'scripts/run_three_arm_revised_week.py'),
               '--dataset', str(dataset), '--offline-preflight']
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report['state'] == 'dataset-preflight-pass'
    assert report['frozen_inputs'] == {'protocol': 'r2-input-file-bindings-v1',
                                       'sha256': FROZEN, 'messages': 142,
                                       'questions': 42, 'criteria': 188}
    assert report['native_source_batches'] == 7
    assert report['new_paid_calls'] == 0
    assert list(tmp_path.iterdir()) == [dataset]
    for flags in (['--execute'], ['--regenerate'], ['--resume-from', 'archive']):
        result = subprocess.run(command + flags, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 2
    source = next(dataset.glob('sources/*/messages.txt'))
    source.write_bytes(source.read_bytes() + b'changed')
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'Dataset checksum differs' in result.stderr


def test_full_run_capability_stays_bound_to_selected_dataset(tmp_path):
    import pytest
    from pacbench import full_run_capability as cap

    dataset = tmp_path / 'dataset'
    shutil.copytree(DATASET, dataset)
    root = tmp_path / 'run'
    root.mkdir()
    projects = {p: {role: f'pacbench-{sha256(str(root / p).encode()).hexdigest()[:12]}-{p}-{role}'
                    for role in ('memory', 'driver', 'judge')} for p in cap.PROVIDERS}
    receipt = cap.create(root, projects, dict.fromkeys(cap.PROVIDERS, 'test'), dataset=dataset)
    scope = cap.load(receipt)
    assert scope['dataset'] == str(dataset)
    assert scope['specification']['full_frozen'] == FROZEN
    assert sum(len(group['questions']) for group in scope['specification']['groups']) == 42
    (dataset / 'manifest.json').unlink()
    with pytest.raises(FileNotFoundError):
        cap.load(receipt)
