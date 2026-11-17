"""Run-owned Linux cleanup with identity checks and explicit coverage limits.

Never delete stores, volumes or financial reservations. Process evidence omits
raw command lines and environment values. Remote requests cannot be unsent.
"""
from __future__ import annotations
import json, os, signal, subprocess, time
from pathlib import Path

OWNER = 'PACBENCH_IMPORT_OWNER'
PATH_KEYS = {'HOME', 'HERMES_HOME', 'MEMCAL_HOME', 'MEM0_DIR', 'COGNEE_SYSTEM_DIR',
             'COGNEE_DATA_ROOT_DIRECTORY', 'REME_WORKSPACE', 'OV_CONFIG_FILE'}
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']


def identity(pid):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': fields[19], 'state': fields[0]}
    except (OSError, ValueError, IndexError):
        return None


def alive(row):
    current = identity(row['pid'])
    return bool(current and current['start_ticks'] == row['start_ticks'] and current['state'] not in {'Z', 'X'})


def ancestors():
    result = set(); pid = os.getpid()
    while pid > 1 and pid not in result:
        result.add(pid); row = identity(pid)
        if not row: break
        pid = row['ppid']
    return result


def beneath(value, root):
    if not value.startswith('/'): return False
    try: return Path(value).resolve().is_relative_to(root)
    except (OSError, ValueError): return False


def scan(root, owner, known=()):
    root = Path(root).resolve(); excluded = ancestors(); rows = {}; previous = {r['pid']: r for r in known}
    errors = 0
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        pid = int(entry.name)
        if pid in excluded: continue
        row = identity(pid)
        if not row or row['state'] in {'Z', 'X'}: continue
        reasons = []
        old = previous.get(pid)
        if old and old['start_ticks'] == row['start_ticks']: reasons.append('previously-observed-owner')
        try:
            if beneath(os.readlink(entry / 'cwd'), root): reasons.append('run-cwd')
            args = (entry / 'cmdline').read_bytes().split(b'\0')
            if any(beneath(a.decode(errors='replace').split('=', 1)[-1], root) for a in args if a):
                reasons.append('run-path-argument')
            env = dict(item.split(b'=', 1) for item in (entry / 'environ').read_bytes().split(b'\0') if b'=' in item)
            if env.get(OWNER.encode()) == owner.encode(): reasons.append('unique-owner-token')
            for key in PATH_KEYS:
                if beneath(env.get(key.encode(), b'').decode(errors='replace'), root):
                    reasons.append('run-environment-path:' + key)
        except (OSError, ValueError):
            errors += 1
        rows[pid] = {**row, 'ownership': reasons}
    # Resolve descendants transitively, then retain their exact identities for
    # later scans after a daemon reparents or starts a new session.
    changed = True
    while changed:
        changed = False
        for row in rows.values():
            if not row['ownership'] and rows.get(row['ppid'], {}).get('ownership'):
                row['ownership'] = ['observed-owned-descendant']; changed = True
    return [r for r in rows.values() if r['ownership']], errors


def send(row, sig):
    """Use pidfd when available; start ticks reject stale or reused PIDs."""
    fd = None
    try:
        if hasattr(os, 'pidfd_open'): fd = os.pidfd_open(row['pid'])
        if not alive(row): return False
        if fd is not None and hasattr(signal, 'pidfd_send_signal'):
            signal.pidfd_send_signal(fd, sig)
        else:
            os.kill(row['pid'], sig)
        return True
    except ProcessLookupError:
        return False
    finally:
        if fd is not None: os.close(fd)


def owned_containers(root, *, runner=subprocess.run):
    """Inspect bind mounts, never expose Docker's credential-bearing config."""
    root = Path(root).resolve()
    listing = runner([*DOCKER, 'ps', '-q', '--no-trunc'], capture_output=True, text=True, timeout=10)
    if listing.returncode: return [], 'docker-list-failed'
    ids = listing.stdout.split()
    if not ids: return [], None
    response = runner([*DOCKER, 'inspect', *ids], capture_output=True, text=True, timeout=10)
    if response.returncode: return [], 'docker-inspect-failed'
    rows = []
    ownership_error = None
    receipts = []
    for path in root.rglob('native-container-ownership.json'):
        try:
            doc = json.loads(path.read_text())
            if Path(doc['run_root']).resolve() == path.parent.resolve() and beneath(doc['data_bind_mount'], root):
                receipts.append(doc)
        except (OSError, ValueError, KeyError):
            ownership_error = 'invalid-container-ownership-receipt'
    for item in json.loads(response.stdout):
        mounts = [m['Source'] for m in item.get('Mounts', []) if m.get('Type') == 'bind' and beneath(m.get('Source', ''), root)]
        label = item.get('Config', {}).get('Labels', {}).get('pacbench.import-root')
        if mounts:
            expected_db = [m for m in mounts if Path(m).name == 'honcho-db']
            receipt_matches = [r for r in receipts if r['container_id'] == item['Id'] and
                               r['data_bind_mount'] in expected_db and r.get('label_root') == label]
            # Labels and exact expected data mounts recover the small gap
            # between Docker creation and writing its identity receipt.
            if label != str(root) or len(expected_db) != 1 or receipts and not receipt_matches:
                ownership_error = 'container-ownership-unverified'; continue
            rows.append({'id': item['Id'], 'owned_bind_mounts': mounts,
                         'run_label_matches': True, 'receipt_matches': bool(receipt_matches)})
    return rows, ownership_error


def cleanup(root, owner, known=(), *, grace_s=3, container_cleanup=True):
    root = Path(root).resolve(); started = time.monotonic(); all_rows = {r['pid']: r for r in known}
    signaled = []; failures = []; scan_errors = 0
    # Repeated scans catch children spawned by native teardown handlers.
    quiet = 0; deadline = started + grace_s + 5
    while time.monotonic() < deadline:
        rows, errors = scan(root, owner, all_rows.values()); scan_errors += errors
        all_rows.update({r['pid']: r for r in rows})
        if not rows:
            quiet += 1
            if quiet >= 3: break
        else: quiet = 0
        sig = signal.SIGTERM if time.monotonic()-started < grace_s else signal.SIGKILL
        for row in rows:
            try:
                if send(row, sig): signaled.append({'pid': row['pid'], 'start_ticks': row['start_ticks'], 'signal': sig.name})
            except OSError as exc: failures.append({'pid': row['pid'], 'error_class': type(exc).__name__})
        time.sleep(.15)
    remaining, errors = scan(root, owner, all_rows.values()); scan_errors += errors
    containers = []; docker_error = None
    if container_cleanup:
        try:
            containers, docker_error = owned_containers(root)
            for item in containers:
                # Reinspect by full identity immediately before stopping.
                current, err = owned_containers(root)
                if err or not any(r['id'] == item['id'] and r['owned_bind_mounts'] == item['owned_bind_mounts'] for r in current):
                    item['state'] = 'ownership-changed'; continue
                proc = subprocess.run([*DOCKER, 'stop', '--time', '5', item['id']], capture_output=True, text=True, timeout=15)
                item['state'] = 'stop-requested' if proc.returncode == 0 else 'stop-failed'
            survivors, err = owned_containers(root)
            if err: docker_error = err
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            docker_error = type(exc).__name__; survivors = []
    else: survivors = []
    return {'state': 'verified-inventory-stopped' if not remaining and not survivors and not failures and not docker_error else 'cleanup-unverified',
            'elapsed_s': time.monotonic()-started, 'observed_processes': list(all_rows.values()),
            'signals': signaled, 'remaining_processes': remaining, 'signal_errors': failures,
            'proc_read_errors_all_system_processes': scan_errors, 'containers': containers,
            'remaining_containers': survivors, 'container_error': docker_error,
            'stores_deleted': False, 'ledger_modified': False,
            'coverage': 'Verified run bindings and observed descendants; no cgroup containment. A process that drops every binding between scans may be unobservable. Remote API outcomes remain uncertain until authoritative usage arrives.'}
