"""Own one native Hermes gateway for one benchmark turn on Linux."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class GatewayLifecycleError(RuntimeError):
    pass


def require_simulated_clock_ready():
    raise GatewayLifecycleError('Simulated native gateway clocks require verified shutdown marker and TLS handling')


def _live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise GatewayLifecycleError('Cannot verify gateway PID') from exc


def _start_time(pid: int) -> int:
    try:
        return int(Path(f'/proc/{pid}/stat').read_bytes().rsplit(b')', 1)[1].split()[19])
    except (OSError, IndexError, ValueError) as exc:
        raise GatewayLifecycleError('Cannot verify gateway process identity') from exc


def _read_record(path: Path):
    if path.is_symlink():
        raise GatewayLifecycleError('Gateway control paths must not be symlinks')
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or type(value.get('pid')) is not int or value['pid'] <= 1:
            raise ValueError('invalid PID record')
        return value
    except (OSError, ValueError) as exc:
        raise GatewayLifecycleError('Cannot verify gateway control record') from exc


def _assert_clear(home: Path):
    if home.parent.name == 'profiles':
        raise GatewayLifecycleError('Profile multiplexing is not supported for benchmark homes')
    for name in ('gateway.pid', 'gateway.lock', 'gateway_state.json'):
        record = _read_record(home / name)
        if record and _live(record['pid']):
            raise GatewayLifecycleError('A gateway control record names a live process; refusing to attach or stop it')
    for name in ('gateway.sock', '.gateway-planned-stop.json'):
        node = home / name
        if node.exists() or node.is_symlink():
            raise GatewayLifecycleError('Gateway control path remains after shutdown')


def _identity(home: Path, pid: int):
    record = _read_record(home / 'gateway.pid')
    if (not record or record.get('kind') != 'hermes-gateway' or record['pid'] != pid
            or record.get('hermes_home') != str(home) or record.get('start_time') != _start_time(pid)):
        raise GatewayLifecycleError('Gateway identity does not match the run-owned process')
    return record


def _ready(home: Path, pid: int) -> bool:
    record = _identity(home, pid)
    state = _read_record(home / 'gateway_state.json')
    return bool(state and state.get('pid') == pid and state.get('start_time') == record['start_time']
                and state.get('hermes_home') == str(home) and state.get('gateway_state') == 'running'
                and (home / 'gateway.sock').exists() and not (home / 'gateway.sock').is_symlink())


def _stop(home: Path, proc: subprocess.Popen, *, timeout: float):
    if proc.poll() is not None:
        raise GatewayLifecycleError('Run-owned gateway exited unexpectedly')
    record = _identity(home, proc.pid)
    marker = home / '.gateway-planned-stop.json'
    data = {'target_pid': proc.pid, 'target_start_time': record['start_time'],
            'stopper_pid': os.getpid(), 'written_at': datetime.now(timezone.utc).isoformat()}
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out)
        # Only this directly spawned, identity-checked process is signalled.
        proc.terminate()
        proc.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GatewayLifecycleError('Run-owned gateway did not stop cleanly; do not start another turn') from exc
    if proc.returncode != 0:
        raise GatewayLifecycleError('Gateway shutdown was not confirmed clean')
    _assert_clear(home)


@contextmanager
def turn_gateway(home: Path, python: Path, env: dict, *, cwd: Path, startup_timeout: float = 30, shutdown_timeout: float = 30):
    """Start, verify and gracefully stop a private native gateway; refuse uncertain ownership."""
    home = home.resolve()
    if not home.is_dir() or os.name != 'posix' or not Path('/proc/self/stat').exists():
        raise GatewayLifecycleError('A private existing home and Linux process identities are required')
    if env.get('HERMES_HOME') != str(home) or env.get('HERMES_TUI_GATEWAY_URL'):
        raise GatewayLifecycleError('Gateway environment does not identify the private home')
    if env.get('LD_PRELOAD') or any(k.startswith('FAKETIME_') or k == 'FAKETIME' for k in env):
        raise GatewayLifecycleError('Faketime gateway shutdown requires a verified simulated-clock marker')
    for key in ('HOME', 'XDG_STATE_HOME', 'HERMES_GATEWAY_LOCK_DIR'):
        value = env.get(key)
        if key == 'HOME' and not value:
            raise GatewayLifecycleError('Gateway requires a run-owned HOME')
        if value and not Path(value).resolve().is_relative_to(home.parent):
            raise GatewayLifecycleError(f'{key} is outside the run-owned directory')
    lock_path = home / '.pacbench-gateway.lock'
    if lock_path.is_symlink():
        raise GatewayLifecycleError('Gateway lifecycle lock must not be a symlink')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GatewayLifecycleError('Another benchmark turn owns this gateway home') from exc
        _assert_clear(home)
        proc = subprocess.Popen([str(python), '-m', 'hermes_cli.main', 'gateway', 'run', '--quiet', '--idle-exit'],
                                env=env, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = False
        try:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise GatewayLifecycleError('Run-owned gateway exited during startup')
                if (home / 'gateway.pid').exists():
                    if _ready(home, proc.pid):
                        ready = True
                        break
                time.sleep(.05)
            if not ready:
                raise GatewayLifecycleError('Run-owned gateway did not become ready')
            witness = {'version': 'per-turn-owned-gateway-v1', 'pid': proc.pid,
                       'start_time': _start_time(proc.pid), 'home': str(home), 'stopped': False}
            yield witness
        finally:
            if ready:
                _stop(home, proc, timeout=shutdown_timeout)
                witness['stopped'] = True
            elif proc.poll() is None:
                # The subprocess was launched by this scope and has not been reaped.
                proc.terminate()
                try:
                    proc.wait(timeout=shutdown_timeout)
                except subprocess.TimeoutExpired as exc:
                    raise GatewayLifecycleError('Gateway startup cleanup is uncertain') from exc
