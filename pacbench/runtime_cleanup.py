"""Preview, remove, and restore verified duplicate Hermes tool packages."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4

from .gateway_reuse import atomic
from .import_lifecycle import scan

MARKER = 'runtime-cleanup.json'
PACKAGE_NAMES = {'python', 'uv'}


def catalog(store):
    if store.resolve() != store.absolute() or store.name != 'tools' or store.parent.name != 'hermes-home':
        raise ValueError('Expected a real run-local hermes-home/tools directory')
    facts = store / 'facts.json'
    if facts.is_symlink():
        raise ValueError('Hermes tool facts must be a regular file')
    data = json.loads(facts.read_text())
    if data['schema'] != 1 or not PACKAGE_NAMES <= data['packages'].keys():
        raise ValueError('Unknown Hermes runtime facts')
    rows = {}
    for name in sorted(PACKAGE_NAMES):
        package = data['packages'][name]
        entry = package['entry']
        if not re.fullmatch(rf'{name}-[a-zA-Z0-9.+_-]+', entry):
            raise ValueError('Unexpected tool package name')
        rows[name] = package
    return rows


def fingerprint(directory):
    """Bind bytes, modes, names and link targets; never follow a link."""
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('Tool package is absent or symlinked')
    digest = hashlib.sha256()
    releasable = 0
    for path in [directory, *sorted(directory.rglob('*'))]:
        metadata = path.lstat()
        kind = stat.S_IFMT(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            with path.open('rb') as stream:
                data = hashlib.file_digest(stream, 'sha256').hexdigest()
            if metadata.st_nlink == 1:
                releasable += metadata.st_blocks * 512
        elif stat.S_ISLNK(metadata.st_mode):
            data = os.readlink(path)
            if Path(data).is_absolute() or not path.resolve().is_relative_to(directory):
                raise ValueError('Tool package has an out-of-tree link')
        elif stat.S_ISDIR(metadata.st_mode):
            data = None
        else:
            raise ValueError('Tool package has a non-file entry')
        digest.update(json.dumps([path.relative_to(directory).as_posix(), kind,
                                  stat.S_IMODE(metadata.st_mode), data]).encode() + b'\n')
    return digest.hexdigest(), releasable


def plan(reference, target, known_reference=None, *, package_names=PACKAGE_NAMES):
    if reference.resolve() == target.resolve() or target.is_relative_to(reference) or reference.is_relative_to(target):
        raise ValueError('Reference and target must be independent tool stores')
    packages = catalog(target)
    if catalog(reference) != packages:
        raise ValueError('Pinned tool facts differ')
    if (target / MARKER).exists():
        raise ValueError('A cleanup receipt exists; restore or finish that cleanup first')
    selected = [packages[name] for name in sorted(package_names)]
    known_reference = known_reference or {row['entry']: fingerprint(reference / row['entry'])[0]
                                          for row in selected}
    rows = []
    for row in selected:
        entry = row['entry']
        checksum, size = fingerprint(target / entry)
        if checksum != known_reference[entry]:
            raise ValueError(f'Tool bytes or modes differ for {entry}')
        rows.append({'entry': entry, 'sha256': checksum, 'reclaimable_bytes_estimate': size})
    return {'protocol': 'hermes-runtime-cleanup-v1', 'reference': str(reference),
            'tool_store': str(target), 'packages': rows}


def lease(stack, path, *, shared=False):
    if path.is_symlink():
        raise ValueError('Refusing a symlinked lock')
    stream = stack.enter_context(path.open('r' if path.exists() else 'x'))
    fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)


def assert_idle(store):
    workers, _ = scan(store.parent.parent, 'hermes-runtime-cleanup')
    if workers:
        raise ValueError('The run still has an owned process')
    # Also cover drivers whose executable is inside the tools directory but
    # which have neither a run directory nor a run path in their argv.
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            executable = (process / 'exe').resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if executable.is_relative_to(store):
            raise ValueError('A process is executing this toolchain')


def remove(reference, target, execution_lock, *, package_names=PACKAGE_NAMES):
    catalog(reference)
    catalog(target)
    with ExitStack() as stack:
        lease(stack, execution_lock)
        lease(stack, reference / '.install.lock', shared=True)
        lease(stack, target / '.install.lock')
        assert_idle(target)
        record = plan(reference, target, package_names=package_names)
        for row in record['packages']:
            row['retired_entry'] = f".retired-{uuid4().hex}"
        # A package disappears from its normal location atomically, so an
        # interrupted removal can leave a retired copy but never a partial
        # package at the normal location.
        atomic(target / MARKER, record)
        for row in record['packages']:
            retired = target / row['retired_entry']
            (target / row['entry']).rename(retired)
            shutil.rmtree(retired)
        return record


def restore(reference, target, execution_lock):
    if reference.resolve() == target.resolve():
        raise ValueError('Reference must be independent of the restored store')
    catalog(reference)
    catalog(target)
    with ExitStack() as stack:
        lease(stack, execution_lock)
        lease(stack, reference / '.install.lock', shared=True)
        lease(stack, target / '.install.lock')
        assert_idle(target)
        marker = target / MARKER
        if marker.is_symlink():
            raise ValueError('Refusing a symlinked cleanup record')
        record = json.loads(marker.read_text())
        packages = catalog(target)
        entries = [r['entry'] for r in record['packages']]
        if (record.get('protocol') != 'hermes-runtime-cleanup-v1' or record.get('tool_store') != str(target)
                or catalog(reference) != packages or not entries or len(set(entries)) != len(entries)
                or not set(entries) <= {p['entry'] for p in packages.values()}):
            raise ValueError('Restore receipt or pinned facts differ')
        for row in record['packages']:
            entry = row['entry']
            retired_entry = row.get('retired_entry', '')
            if not re.fullmatch(r'\.retired-[0-9a-f]{32}', retired_entry):
                raise ValueError('Invalid retired package path')
            source = reference / entry
            if fingerprint(source)[0] != row['sha256']:
                raise ValueError(f'Reference no longer matches {entry}')
            destination = target / entry
            if destination.is_symlink():
                raise ValueError('Refusing a symlinked tool restore destination')
            if destination.exists():
                if fingerprint(destination)[0] != row['sha256']:
                    raise ValueError(f'Existing tool package differs: {destination}; preserve and inspect it')
            else:
                staging = target / f'.restore-{uuid4().hex}'
                try:
                    shutil.copytree(source, staging, symlinks=True)
                    if fingerprint(staging)[0] != row['sha256']:
                        raise ValueError('Restored copy failed verification')
                    staging.rename(destination)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            retired = target / retired_entry
            if retired.is_symlink():
                raise ValueError('Refusing a symlinked retired package')
            if retired.exists():
                shutil.rmtree(retired)
        marker.unlink()
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'remove', 'restore'])
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--execution-lock', type=Path)
    parser.add_argument('--package', choices=sorted(PACKAGE_NAMES), action='append',
                        help='Select a package to verify and reclaim. Default verifies both.')
    parser.add_argument('targets', type=Path, nargs='+')
    args = parser.parse_args()
    if args.action != 'plan' and (not args.execution_lock or not args.execution_lock.is_file()):
        parser.error('Pass the existing benchmark execution lock for removal or restore')
    reference = args.reference.absolute()
    recorded = catalog(reference)
    selected = set(args.package) if args.package else PACKAGE_NAMES
    known = {r['entry']: fingerprint(reference / r['entry'])[0]
             for name, r in recorded.items() if name in selected} if args.action == 'plan' else None
    successful = 0
    estimated = 0
    for target in args.targets:
        target = target.absolute()
        try:
            if args.action == 'plan':
                record = plan(reference, target, known, package_names=selected)
            elif args.action == 'remove':
                record = remove(reference, target, args.execution_lock, package_names=selected)
            else:
                record = restore(reference, target, args.execution_lock)
        except (OSError, ValueError, KeyError) as error:
            print(json.dumps({'tool_store': str(target), 'status': 'skipped', 'reason': str(error)}))
            continue
        successful += 1
        estimated += sum(p['reclaimable_bytes_estimate'] for p in record['packages'])
        print(json.dumps({'status': args.action, **record}))
    print(json.dumps({'successful_tool_stores': successful, 'total_reclaimable_bytes_estimate': estimated}))
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
