"""Stop leftover processes belonging to a completed, run-owned directory."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pacbench.evidence_store import save


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


class OperatorStop(Exception):
    """An operator requested a stop; this is not a provider failure."""


def check_stop(path=None):
    paths = [path, os.environ.get('PACBENCH_STOP_FILE')]
    if any(value and Path(value).exists() for value in paths):
        raise OperatorStop('operator stop requested')


def communicate_until_stop(proc, *, timeout, env):
    """Reap a native CLI promptly after admission/operator stop, keeping timeouts bounded."""
    import subprocess
    import time
    marker=env.get('PACBENCH_STOP_FILE')
    deadline=time.monotonic()+timeout
    while True:
        if marker and Path(marker).exists():
            proc.terminate()
            try:proc.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill();proc.communicate()
            raise OperatorStop('operator/admission stop while awaiting native CLI')
        remaining=deadline-time.monotonic()
        if remaining<=0:
            proc.kill();out,err=proc.communicate()
            raise subprocess.TimeoutExpired(proc.args,timeout,output=out,stderr=err)
        try:return proc.communicate(timeout=min(.2,remaining))
        except subprocess.TimeoutExpired:pass


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


def cleanup_owned(root, owner, known=(), *, grace_s=3, container_cleanup=True):
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


REPO = Path(__file__).resolve().parents[1]


def now(): return datetime.now(timezone.utc).isoformat()


def write(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600); json.dump(value, stream, indent=2); stream.write('\n')
    temp.replace(path)


def supervise(command, component, *, stop, stop_file, timeout_s, grace_s=5, container_cleanup=True, cancel_file=None):
    from pacbench.workflows import recording as workflows_recording
    component = Path(component).resolve(); owner = uuid.uuid4().hex
    env = {**os.environ, OWNER: owner, 'PACBENCH_IMPORT_ROOT': str(component),
           'PACBENCH_STOP_FILE': str(stop_file), 'TMPDIR': '/var/tmp',
           'PACBENCH_MEMORY_API_INPUT_TRACE_ROOT':str(component/'repeat-1'/component.name/'memory-api-inputs'),
           'DERIVER_MODEL_CONFIG__THINKING_EFFORT': 'none'}
    if cancel_file is not None:
        env['PACBENCH_STOP_FILE'] = str(stop_file)
    started_utc = now(); started = time.monotonic(); known = []; reason = None
    ownership_path=component.with_name(component.name+'.ownership.private.json')
    log = component.with_name(component.name + '.execution.private.log')
    with log.open('xb') as stream:
        log.chmod(0o600)
        child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=stream, start_new_session=True, cwd=REPO)
        row = identity(child.pid)
        if row: known.append({**row, 'ownership': ['controller-created-child']})
        write(ownership_path,{'state':'running','root':str(component),'owner':owner,'processes':known,'started':started_utc})
        last_inventory=time.monotonic()
        try:
            while child.poll() is None:
                rows, _ = scan(component, owner, known)
                known = list({(r['pid'],r['start_ticks']):r for r in [*known,*rows]}.values())
                if time.monotonic()-last_inventory>=2:
                    write(ownership_path,{'state':'running','root':str(component),'owner':owner,'processes':known,'started':started_utc})
                    last_inventory=time.monotonic()
                if stop.is_set() or stop_file.exists() or cancel_file is not None and cancel_file.exists():
                    reason = 'cancelled'; break
                if time.monotonic()-started >= timeout_s: reason = 'timeout'; break
                time.sleep(.25)
            if reason:
                stop_file.touch(exist_ok=True)
                deadline = time.monotonic()+grace_s
                while child.poll() is None and time.monotonic()<deadline:
                    rows, _ = scan(component, owner, known); known += rows
                    time.sleep(.1)
        except BaseException:
            reason = 'controller-exception'; stop_file.touch(exist_ok=True)
            raise
        finally:
            cleaned = cleanup_owned(component, owner, known, grace_s=2, container_cleanup=container_cleanup)
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: cleaned['direct_child_wait'] = 'still-running'
            write(ownership_path,{'state':'terminal','root':str(component),'owner':owner,'processes':known,
                'started':started_utc,'finished':now(),'cleanup':cleaned,'stop_reason':reason})
    finished = now()
    parse_errors=[]
    try: summary = json.loads((component/'summary.json').read_text()) if (component/'summary.json').exists() else {}
    except (OSError,ValueError): summary={};parse_errors.append('summary-unreadable-or-interrupted')
    events,event_errors=workflows_recording.read_events(component/workflows_recording.EVENT_LOG);parse_errors+=event_errors
    ready = next((e for e in events if e['event']=='native-all-batches-ready'), None)
    first_batch=next((e for e in events if e.get('event')=='phase-start' and e.get('phase')=='native-batch-import-through-readiness'),None)
    complete=(not reason and child.returncode==0 and summary.get('state')=='processing-complete'
        and summary.get('mode')=='native-import-validation' and summary.get('benchmark_questions_sent')==0
        and summary.get('judge_calls')==0 and not parse_errors and ready
        and cleaned['state']=='verified-inventory-stopped')
    return {'provider': component.name, 'started': started_utc, 'finished': finished,
            'launch_to_cleanup_wall_s': time.monotonic()-started,
            'launch_to_native_ready_s': ready['monotonic_s']-started if ready else None,
            'first_batch_to_native_ready_s': ready['monotonic_s']-first_batch['monotonic_s'] if ready and first_batch else None,
            'returncode': child.returncode, 'stop_reason': reason,
            'state': 'processing-complete' if complete else reason or 'incomplete',
            'parse_errors':parse_errors,
            'cleanup': cleaned, 'benchmark_questions_sent': summary.get('benchmark_questions_sent'),
            'judge_calls': summary.get('judge_calls'), 'stores_preserved': True,
            'condition': 'Fresh stores, concurrent functional imports; includes initialization and native background processing. Readiness excludes subsequent shutdown. Not a controlled hardware latency ranking.'}
