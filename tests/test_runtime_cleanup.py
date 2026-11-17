import json
import shutil

import pytest

from pacbench import runtime_cleanup as cleanup


@pytest.fixture
def stores(tmp_path):
    reference = tmp_path / 'ref/hermes-home/tools'
    reference.mkdir(parents=True)
    facts = {'schema': 1, 'packages': {name: {'entry': f'{name}-test'} for name in ['python', 'uv']}}
    (reference / 'facts.json').write_text(json.dumps(facts))
    for row in facts['packages'].values():
        tree = reference / row['entry']
        tree.mkdir()
        (tree / 'content').write_bytes(b'verified')
        (tree / 'relative-link').symlink_to('content')
    target = tmp_path / 'other/hermes-home/tools'
    shutil.copytree(reference, target, symlinks=True)
    lock = tmp_path / 'execution.lock'
    lock.touch()
    return reference, target, lock


def test_preview_remove_restore_preserves_run_data(stores):
    reference, target, lock = stores
    protected = target.parent / 'state.db'
    protected.write_bytes(b'leave unchanged')
    (target / 'unlisted').write_bytes(b'leave unchanged')
    preview = cleanup.plan(reference, target)
    assert len(preview['packages']) == 2 and (target / 'python-test/content').exists()
    cleanup.remove(reference, target, lock)
    assert not (target / 'python-test').exists()
    assert (target / cleanup.MARKER).exists()
    assert protected.read_bytes() == (target / 'unlisted').read_bytes() == b'leave unchanged'
    cleanup.restore(reference, target, lock)
    assert cleanup.plan(reference, target) == preview


def test_skip_different_bytes_and_active_store(stores, monkeypatch):
    reference, target, lock = stores
    changed = target / 'uv-test/content'
    changed.write_bytes(b'changed')
    with pytest.raises(ValueError, match='differ'):
        cleanup.remove(reference, target, lock)
    assert (target / 'python-test').exists() and changed.read_bytes() == b'changed'
    changed.write_bytes(b'verified')
    monkeypatch.setattr(cleanup, 'scan', lambda *args: ([{'pid': 73}], 0))
    with pytest.raises(ValueError, match='owned process'):
        cleanup.remove(reference, target, lock)
    assert not (target / cleanup.MARKER).exists()


def test_package_selection_can_preserve_a_nonmatching_runtime(stores):
    reference, target, lock = stores
    changed = target / 'python-test/content'
    changed.write_bytes(b'unique runtime')
    cleanup.remove(reference, target, lock, package_names={'uv'})
    assert changed.read_bytes() == b'unique runtime' and not (target / 'uv-test').exists()
    cleanup.restore(reference, target, lock)
    assert (target / 'uv-test/content').read_bytes() == b'verified'
