import hashlib
import json
from pathlib import Path
import shutil

import pytest

from pacbench.pizza_day_recovery import review_packet, dialogue_import
from pacbench.frozen import messages_for, load_bundle

ARCHIVE = Path('/workspace/pacbench-restore-20261112/pacbench-transfer-20261112/pizza-day-archive/runs/pizza-clean-context-mem0-20261007/preparation')


@pytest.fixture
def archive(tmp_path):
    if not ARCHIVE.is_dir():
        pytest.skip('historical Pizza Day archive not available')
    target = tmp_path / 'preparation'
    shutil.copytree(ARCHIVE, target)
    return target


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_packet_preserves_archive_and_blocks_execution(archive):
    before = snapshot(archive)
    packet = review_packet(archive)
    summary = json.loads((archive / 'summary.json').read_text())
    original = messages_for(load_bundle(archive / 'bundle.json', summary['bundle_hash']), 'pizza-day')
    assert snapshot(archive) == before
    assert packet['state'] == 'blocked'
    assert packet['historical_audit']['state'] == 'fail'
    assert packet['source_messages'] == original
    assert len(original) == 25
    assert len(packet['proposed_claims']) == 5
    from pacbench.corpus_review import VERSION
    assert packet['fresh_review_inputs']['corpus_realism']['review_version'] == VERSION
    assert original[0]['text'] in packet['fresh_review_inputs']['corpus_realism']['prompt']
    assert packet['compiled_spec']['checks'][0]['prompt'] in packet['fresh_review_inputs']['combined_claim_and_question']['prompt']
    assert not (archive / 'week.json').exists()


def test_packet_rejects_modified_bundle(archive):
    path = archive / 'bundle.json'
    bundle = json.loads(path.read_text())
    messages_for(bundle, 'pizza-day')[0]['text'] = 'tampered'
    path.write_text(json.dumps(bundle))
    with pytest.raises(RuntimeError, match='hash mismatch'):
        review_packet(archive)


def test_dialogue_import_preserves_text_and_refuses_changed_scene_rows(archive):
    before = snapshot(archive)
    packet = review_packet(archive)
    spec, imported = dialogue_import(archive)
    assert snapshot(archive) == before
    assert [(s,t) for row in imported['scenes'].values() for s,t in row] == [
        (m['sender'],m['text']) for m in packet['source_messages']]
    assert imported['source_bundle_hash'] == packet['source_bundle_hash']
    assert all(s['participants'] and s['message_range'][0] == s['message_range'][1] for s in spec['scenes'])
    path = archive/'scenes.json'
    data = json.loads(path.read_text())
    data['scenes'][0]['lines'][0][1] = 'modified source'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='differs from the hash-verified source'):
        dialogue_import(archive)
