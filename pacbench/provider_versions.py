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


def memcal_source(repo: Path) -> Path:
    override = os.environ.get('PACBENCH_MEMCAL_SRC', '').strip()
    return Path(override).expanduser().resolve() if override else repo.parent / 'memcal-stable-20261113'


def verify_memcal_source(repo: Path) -> dict:
    root = memcal_source(repo)
    top = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], text=True).strip()
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if Path(top).resolve() != root.resolve() or actual != MEMCAL_REVISION:
        raise RuntimeError('Memcal source differs from the selected stable release')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip():
        raise RuntimeError('Selected Memcal release has working tree changes')
    if not (root / 'integrations/hermes/memcal/__init__.py').is_file():
        raise RuntimeError('Selected Memcal native Hermes integration is missing')
    return {'revision': actual, 'version': MEMCAL_VERSION, 'source': str(root), 'working_tree_changes': False}


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
