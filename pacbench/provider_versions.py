"""Source identity of the isolated current-provider cohort, never old run labels."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

MEMCAL_REVISION = 'e67b8d912484b5b4317dafcff4d60fc975194d8e'
MEMCAL_VERSION = '0.8.11'
MEMCAL_REVIEW_CONDITION = 'wiki-supersession-review-da58057-v1'
MEMCAL_REVIEW_REVISION = 'da58057eb00f79082dfcf8f59baa264267c83ca1'
MEMCAL_CONTEXT_CONDITION = 'wiki-context-review-dac7214-v1'
MEMCAL_CONTEXT_REVISION = 'dac7214006b84ec364ecbcd9472daca7db588a25'
MEMCAL_OPERATIONS_CONDITION = 'wiki-preference-operations-review-c19d216-v1'
MEMCAL_OPERATIONS_REVISION = 'c19d216a7e5dd3913f69ab79c24d2d339bacef16'


def memcal_pin():
    condition=os.environ.get('PACBENCH_MEMCAL_CONDITION','stable-0.8.11')
    if condition=='stable-0.8.11':
        return condition,MEMCAL_REVISION,MEMCAL_VERSION
    if condition==MEMCAL_REVIEW_CONDITION:
        return condition,MEMCAL_REVIEW_REVISION,'0.8.12'
    if condition==MEMCAL_CONTEXT_CONDITION:
        return condition,MEMCAL_CONTEXT_REVISION,'0.8.12'
    if condition==MEMCAL_OPERATIONS_CONDITION:
        return condition,MEMCAL_OPERATIONS_REVISION,'0.8.12'
    raise RuntimeError('Unknown Memcal source condition')


def memcal_source(repo: Path) -> Path:
    override = os.environ.get('PACBENCH_MEMCAL_SRC', '').strip()
    return Path(override).expanduser().resolve() if override else repo.parent / 'memcal-stable-20261113'


def verify_memcal_source(repo: Path) -> dict:
    condition,revision,version=memcal_pin()
    root = memcal_source(repo)
    top = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], text=True).strip()
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if Path(top).resolve() != root.resolve() or actual != revision:
        raise RuntimeError('Memcal source differs from the selected stable release')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip():
        raise RuntimeError('Selected Memcal release has working tree changes')
    if not (root / 'integrations/hermes/memcal/__init__.py').is_file():
        raise RuntimeError('Selected Memcal native Hermes integration is missing')
    return {'revision': actual, 'version': version, 'source': str(root), 'working_tree_changes': False,
            'condition':condition}


def verify_memos_package(repo: Path) -> tuple[Path, dict]:
    """Verify the official archive and every shipped file before loading native JS.

    Installation may add node_modules and a dependency lock; it cannot rewrite
    files supplied in the verified release. No vendor source patches are allowed.
    """
    pin = json.loads((repo / 'vendor/MEMOS_LOCAL_PIN.json').read_text())
    root = repo / 'vendor/memos-local-plugin'
    archive = root / 'release.tgz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin['sha256']:
        raise RuntimeError('MemOS official release archive digest mismatch')
    package = root / 'package'
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream.getmembers():
            path = Path(member.name)
            if (not path.parts or path.parts[0] != 'package' or '..' in path.parts
                    or member.issym() or member.islnk()):
                raise RuntimeError('Unexpected MemOS archive path or link')
            if not member.isfile():
                continue
            destination = root / path
            if destination.is_symlink() or not destination.is_file():
                raise RuntimeError('MemOS release file missing or replaced by link')
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(stream.extractfile(member).read()).digest():
                raise RuntimeError('MemOS release file differs from the verified native package')
    manifest = json.loads((package / 'package.json').read_text())
    if (manifest['name'], manifest['version']) != (pin['name'], pin['version']):
        raise RuntimeError('MemOS native package identity mismatch')
    return package, pin
