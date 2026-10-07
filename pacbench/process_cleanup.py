"""Stop leftover processes belonging to a completed, run-owned directory."""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from .followups import save


def cleanup_run_processes(root: Path, grace_s: float = 3) -> list[dict]:
    """Linux fallback after native shutdown hooks; never inspect process credentials.

    Some native CLIs leave grandchildren after a query timeout. Match the private
    run directory, not an executable name that could belong to unrelated work.
    """
    root = root.resolve()
    records = []
    proc = Path('/proc')
    if not proc.exists():
        save(root / 'post-run-process-cleanup.json', {'state': 'unsupported-platform'})
        return records
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cwd = Path(os.readlink(entry / 'cwd'))
            if not cwd.is_relative_to(root):
                continue
            item = {'pid': int(entry.name), 'cwd': str(cwd),
                    'executable': os.readlink(entry / 'exe')}
            os.kill(item['pid'], signal.SIGTERM)
            records.append(item)
        except (OSError, ValueError):
            continue
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not any((proc / str(r['pid']) / 'cwd').exists() for r in records):
            break
        time.sleep(.05)
    for item in records:
        try:
            cwd = Path(os.readlink(proc / str(item['pid']) / 'cwd'))
            if cwd.is_relative_to(root):
                os.kill(item['pid'], signal.SIGKILL)
                item['signal'] = 'SIGKILL after shutdown grace'
        except OSError:
            pass
        item.setdefault('signal', 'SIGTERM')
    save(root / 'post-run-process-cleanup.json', records)
    return records
