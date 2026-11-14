"""Own one native Hermes gateway for one benchmark turn on Linux."""
from __future__ import annotations

import fcntl
import hashlib
import math
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class GatewayLifecycleError(RuntimeError):
    pass


def require_simulated_clock_ready(root: Path):
    """Reject legacy or unverified process clocks before any provider clock step."""
    from .provider_clock import LIBRARY, LIBRARY_SHA256
    from provider_adapters.honcho_clock import load
    try:
        load(root)  # Current protocol only; historical receipts remain readable but cannot resume.
        if hashlib.sha256(LIBRARY.read_bytes()).hexdigest() != LIBRARY_SHA256:
            raise ValueError('clock library hash mismatch')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise GatewayLifecycleError('Simulated native gateway clock is not verified current protocol') from exc


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
    for name in ('gateway.sock', 'gateway.sock.path', '.gateway-planned-stop.json'):
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
                and state.get('hermes_home') == str(home) and state.get('gateway_state') == 'running')
    # Native discovery authenticates either the direct socket or its private
    # short-path pointer. Long run homes exceed Unix sun_path limits.


def _published_ready(home,pid):
    """Native metadata files can exist briefly before json.dump finishes."""
    try:return _ready(home,pid)
    except GatewayLifecycleError as exc:
        if isinstance(exc.__cause__,json.JSONDecodeError):return False
        raise


def _control_ready(home: Path, pid: int, python: Path, env: dict, timeout: float) -> bool:
    """Require the same native identify check the CLI uses, in the same clock."""
    code = ("import sys; from pathlib import Path; "
            "from hermes_cli.gateway_runtime_discovery import query_identify; "
            "from hermes_cli.gateway_runtime import _endpoint; "
            "h=Path(sys.argv[1]); p=query_identify(h,timeout=2.0); "
            "sys.exit(0 if p.get('pid')==int(sys.argv[2]) and "
            "_endpoint(p,h,control_home=h).state=='ready' else 1)")
    try:
        if env.get('PACBENCH_PYTHON_CLOCK_FILE'):
            from .gateway_python_clock import probe
            result,_=probe(home,python,env,'gateway-control-probe',code,timeout=timeout,args=(home,pid))
            return result.returncode==0
        result = subprocess.run([str(python), '-I', '-c', code, str(home), str(pid)],
                                env=env, cwd=str(home), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _gateway_marker_time(home: Path, python: Path, env: dict) -> datetime:
    """Read wall time in a no-network child with the gateway's exact clock environment."""
    if env.get('PACBENCH_PYTHON_CLOCK_FILE'):
        from .gateway_python_clock import marker_time
        return marker_time(home,python,env)
    faketime_keys = {key for key in env if key.startswith('FAKETIME_') or key == 'FAKETIME'}
    if not env.get('LD_PRELOAD') and not faketime_keys:
        return datetime.now(timezone.utc)
    from .provider_clock import LIBRARY, LIBRARY_SHA256
    expected = {'FAKETIME_TIMESTAMP_FILE', 'FAKETIME_NO_CACHE', 'FAKETIME_DONT_FAKE_MONOTONIC'}
    try:
        library = Path(env['LD_PRELOAD']).resolve(strict=True)
        offset_file = Path(env['FAKETIME_TIMESTAMP_FILE']).resolve(strict=True)
        if (library != LIBRARY.resolve(strict=True) or hashlib.sha256(library.read_bytes()).hexdigest() != LIBRARY_SHA256
                or faketime_keys != expected or env.get('FAKETIME_NO_CACHE') != '1'
                or env.get('FAKETIME_DONT_FAKE_MONOTONIC') != '1'
                or offset_file != (home.parent / 'provider-clock' / 'offset').resolve(strict=True)):
            raise ValueError('unsupported process clock')
        offset = float(offset_file.read_text())
        if not math.isfinite(offset):
            raise ValueError('invalid offset')
        result = subprocess.run([str(python), '-I', '-S', '-c', 'import time;print(time.time())'],
                                env=env, cwd=str(home), capture_output=True, text=True, timeout=5, check=True)
        value = float(result.stdout.strip())
        if not math.isfinite(value) or abs(value - (time.time() + offset)) > 5:
            raise ValueError('gateway clock did not adopt the expected offset')
        return datetime.fromtimestamp(value, timezone.utc)
    except (KeyError, OSError, ValueError, OverflowError, subprocess.SubprocessError) as exc:
        raise GatewayLifecycleError('Cannot verify the run-owned gateway process clock') from exc


def _stop(home: Path, proc: subprocess.Popen, *, python: Path, env: dict, timeout: float):
    if proc.poll() is not None:
        raise GatewayLifecycleError('Run-owned gateway exited unexpectedly')
    record = _identity(home, proc.pid)
    marker = home / '.gateway-planned-stop.json'
    data = {'target_pid': proc.pid, 'target_start_time': record['start_time'],
            'stopper_pid': os.getpid(), 'written_at': _gateway_marker_time(home, python, env).isoformat()}
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
def turn_gateway(home: Path, python: Path, env: dict, *, cwd: Path, startup_timeout: float = 120, shutdown_timeout: float = 45):
    """Start, verify and gracefully stop a private native gateway; refuse uncertain ownership."""
    home = home.resolve()
    if not home.is_dir() or os.name != 'posix' or not Path('/proc/self/stat').exists():
        raise GatewayLifecycleError('A private existing home and Linux process identities are required')
    if env.get('HERMES_HOME') != str(home) or env.get('HERMES_TUI_GATEWAY_URL'):
        raise GatewayLifecycleError('Gateway environment does not identify the private home')
    if env.get('PACBENCH_PYTHON_CLOCK_FILE'):
        from .gateway_python_clock import environment
        env['PACBENCH_PYTHON_CLOCK_NONCE']=uuid.uuid4().hex
        offset=Path(env['PACBENCH_PYTHON_CLOCK_FILE']).read_bytes()
        env['PACBENCH_CLOCK_OFFSET_SHA256']=hashlib.sha256(offset).hexdigest()
        env['PACBENCH_PYTHON_CLOCK_ROLE']='hermes-cli'
        gateway_env=environment(home,python,env,'hermes-gateway')
    else:gateway_env=env
    # Fail before spawning for unknown preload or clock settings.
    _gateway_marker_time(home, python, env)
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
        proc = subprocess.Popen([str(python), '-m', 'hermes_cli.main', 'gateway', 'run', '--quiet'],
                                env=gateway_env, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        witness = {'version': 'per-turn-owned-gateway-v1', 'pid': proc.pid,
                   'start_time': _start_time(proc.pid), 'home': str(home), 'stopped': False,
                   'real_started_at':datetime.now(timezone.utc).isoformat()}
        ready = False
        try:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise GatewayLifecycleError('Run-owned gateway exited during startup')
                if (home / 'gateway.pid').exists():
                    if _published_ready(home, proc.pid) and _control_ready(
                            home, proc.pid, python, env, timeout=min(8., max(.1, deadline-time.monotonic()))):
                        _identity(home, proc.pid)
                        ready = True
                        break
                time.sleep(.05)
            if not ready:
                raise GatewayLifecycleError('Run-owned gateway did not become ready')
            if env.get('PACBENCH_PYTHON_CLOCK_FILE'):
                from .gateway_python_clock import adoption
                witness['turn_nonce']=env['PACBENCH_PYTHON_CLOCK_NONCE']
                witness['clock_offset']=offset.decode('ascii')
                witness['offset_sha256']=env['PACBENCH_CLOCK_OFFSET_SHA256']
                witness['gateway_adoption']=adoption(home,python,gateway_env,proc.pid,
                    witness['start_time'],'hermes-gateway',cwd=cwd)
            yield witness
        finally:
            try:
                owned=False
                if proc.poll() is None:
                    try:_identity(home,proc.pid);owned=True
                    except GatewayLifecycleError:pass
                if ready or owned:
                    _stop(home, proc, python=python, env=gateway_env, timeout=shutdown_timeout)
                    witness['stopped'] = True
                elif proc.poll() is None:
                    # This directly spawned startup never published a native identity.
                    proc.terminate()
                    try:proc.wait(timeout=shutdown_timeout)
                    except subprocess.TimeoutExpired as exc:
                        raise GatewayLifecycleError('Gateway startup cleanup is uncertain') from exc
            finally:
                witness['real_finished_at']=datetime.now(timezone.utc).isoformat()
                witness['returncode']=proc.poll()
                if env.get('PACBENCH_PYTHON_CLOCK_FILE'):
                    witness['offset_unchanged']=Path(env['PACBENCH_PYTHON_CLOCK_FILE']).read_bytes()==offset
                directory=home.parent/'gateway-lifecycles';directory.mkdir(exist_ok=True)
                receipt=directory/(env.get('PACBENCH_PYTHON_CLOCK_NONCE') or uuid.uuid4().hex)
                receipt.with_suffix('.json').write_text(json.dumps(witness,sort_keys=True)+'\n')
