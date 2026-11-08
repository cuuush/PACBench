"""List and structurally validate author specs without writing files or calling models."""
from __future__ import annotations

import argparse
from pathlib import Path

from .prepare import read_spec, resolve_spec


def inspect_spec(path, *, week_start=None):
    spec = read_spec(path)
    if ('days' in spec or 'weeks' in spec) and not (week_start or spec.get('week_start')):
        raise ValueError('Pin week_start in the spec or supply --week-start; validation never uses today')
    scenes, _, resolved = resolve_spec(spec, Path(path).with_suffix(''), week_start=week_start)
    return {
        'id': resolved['id'],
        'source_days': len({s.at[:10] for s in scenes}),
        'scenes': len(scenes),
        'questions': len(resolved['checks']) or 1,
        'noise': resolved['noise'],
        'start': resolved.get('week_start', min(s.at[:10] for s in scenes)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path, default=[Path('specs')],
                        help='Spec JSON files or directories; defaults to specs/')
    parser.add_argument('--week-start', help='Explicit first Monday for unpinned specs (YYYY-MM-DD)')
    args = parser.parse_args(argv)
    paths = set()
    errors = []
    for path in args.paths:
        if path.is_dir():
            found = list(path.glob('*.json'))
            if not found:
                errors.append(f'{path}: no JSON specs found')
            paths.update(found)
        else:
            paths.add(path)
    print('Spec | Source days | Scenes | Questions | Noise | Start')
    seen = {}
    for path in sorted(paths):
        try:
            item = inspect_spec(path, week_start=args.week_start)
            if item['id'] in seen:
                raise ValueError(f'Duplicate spec id {item["id"]!r}, also in {seen[item["id"]]}')
            seen[item['id']] = path
            print(f'{path} | {item["source_days"]} | {item["scenes"]} | '
                  f'{item["questions"]} | {item["noise"]} | {item["start"]}')
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            errors.append(f'{path}: {type(exc).__name__}: {exc}')
    for error in errors:
        print(f'ERROR {error}')
    print(f'{len(seen)} valid specs; {len(errors)} errors. No model calls or generated files.')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
