"""Shared simulated Python wall time; OS/TLS and monotonic clocks stay real.

This is version-pinned clock instrumentation, not a documented time-machine
extension API. Its C dispatch reads Traveller.time_ns on every call. PostgreSQL
uses the same atomic offset file through libfaketime, in its own process only.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import time

VERSION = 'python-offset-clock-v2'
PACKAGE_VERSION = '3.6.0'
PACKAGE_PY_SHA256 = '95e63b5db9bb3a83189083700281483bc138d163e558e5ae0023fcdfe177c6dd'
PACKAGE_C_SHA256 = {
    'cpython-314':'461407458c6e1f0f349c7d6be0c72633a52a79782f53df0e1d21c518cde4ce2b',
    'cpython-312':'f3d64a7000cd6e114aa8de5bc9038b44860f2616313770e3aa5f87aff2f103b6',
    'cpython-313':'17fd4db930fca0dba6081546c404eb92c1ff62ec60f665cd43ae9a61841eef8e',
}
_controller = None


def package_metadata():
    import time_machine
    import _time_machine
    python_hash = hashlib.sha256(Path(time_machine.__file__).read_bytes()).hexdigest()
    c_hash = hashlib.sha256(Path(_time_machine.__file__).read_bytes()).hexdigest()
    version = importlib.metadata.version('time-machine')
    if (version != PACKAGE_VERSION or python_hash != PACKAGE_PY_SHA256 or
            c_hash != PACKAGE_C_SHA256.get(sys.implementation.cache_tag)):
        raise RuntimeError('Python provider clock runtime differs from verified wheels')
    return {'package':'time-machine','version':version,'python_sha256':python_hash,
        'extension_sha256':c_hash,'abi':sys.implementation.cache_tag,
        'bootstrap_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def offset_ns(raw):
    if not re.fullmatch(r'[+-]?[0-9]+\.[0-9]{6}\n?', raw):
        raise ValueError('Invalid provider clock offset format')
    try:
        value = Decimal(raw.strip()) * 1_000_000_000
    except InvalidOperation:
        raise ValueError('Invalid provider clock offset') from None
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError('Invalid provider clock offset precision')
    return int(value)


def clock_value_ns(raw, stamp, *, allow_ceiling=False):
    offset, separator, ceiling = raw.removesuffix('\n').partition(' ceiling=')
    if not separator:
        return stamp + offset_ns(raw)
    if not allow_ceiling or not re.fullmatch(r'[1-9][0-9]{18}', ceiling):
        raise ValueError('Invalid provider clock ceiling')
    return min(stamp + offset_ns(offset), int(ceiling))


def shared_time_ns(path, real_ns, *, allow_ceiling=False):
    """Re-read after sampling real time to handle a suspended clock step."""
    while True:
        first = path.read_text(encoding='ascii')
        stamp = real_ns()
        second = path.read_text(encoding='ascii')
        if first == second:
            return clock_value_ns(first, stamp, allow_ceiling=allow_ceiling)


def physical_cognee_watcher():
    """The pinned detached provider watcher is outside the Hermes question clock."""
    if os.environ.get('PACBENCH_COGNEE_PHYSICAL_EXIT_WATCHER')!='1' or sys.argv[1:2]!=['--state']:
        return False
    home = os.environ.get('HERMES_HOME')
    if not home or not Path(sys.argv[0]).is_absolute():
        return False
    watcher = Path(home)/'plugins/cognee/cognee_integration_hermes/exit_watcher.py'
    return watcher.is_file() and Path(sys.argv[0]).resolve()==watcher.resolve()


def install():
    global _controller
    name = os.environ.get('PACBENCH_PYTHON_CLOCK_FILE')
    if not name:
        return
    if physical_cognee_watcher():
        return
    if _controller is not None:
        return
    if (os.environ.get('PACBENCH_PYTHON_CLOCK_PROTOCOL') != VERSION or
            os.environ.get('LD_PRELOAD') or any(k.startswith('FAKETIME') for k in os.environ)):
        raise RuntimeError('Python and OS provider clocks must not be combined')
    import time_machine
    metadata = package_metadata()
    if time_machine.escape_hatch.is_travelling():
        raise RuntimeError('Unexpected nested Python provider clock')
    path = Path(name)
    if not path.is_absolute():
        raise RuntimeError('Provider clock file must be absolute')
    allow_ceiling = (os.environ.get('PACBENCH_REUSE_CLOCK_CEILING')=='bounded-observation-v1'
                     and path.parent.name=='reuse-clock'
                     and os.environ.get('PACBENCH_REUSE_CLOCK_STATE')==str(path.parent/'active.json'))
    clock_value_ns(path.read_text(encoding='ascii'), 0, allow_ceiling=allow_ceiling)
    _controller = time_machine.travel(0, tick=False)
    traveller = _controller.start()
    def read_time():
        try:
            if time_machine.traveller_stack != [traveller]:
                raise RuntimeError('Provider clock state was replaced')
            return shared_time_ns(path, time_machine.escape_hatch.time.time_ns, allow_ceiling=allow_ceiling)
        except BaseException:
            os.write(2,b'PACBench Python clock failed; no inference permitted\n')
            os._exit(78)
    traveller.time_ns = read_time
    # Each exec records adoption without exposing environment values or keys.
    # Write atomically; the controller binds native service PIDs to these rows.
    adopted = {**metadata,'protocol':VERSION,'pid':os.getpid(),'python':sys.executable,
        'process_start_ticks':int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),
        'turn_nonce':os.environ.get('PACBENCH_PYTHON_CLOCK_NONCE',''),
        'clock_file':str(path),'python_epoch_ns':time.time_ns(),
        'real_epoch_ns':time_machine.escape_hatch.time.time_ns(),
        'monotonic_ns':time.monotonic_ns(),'os_clock':'real','tls_clock':'real',
        'role':os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE','setup'),
        'home':os.environ.get('HERMES_HOME',''),'cwd':os.getcwd()}
    directory = path.parent/'python-adoptions'
    directory.mkdir(exist_ok=True)
    target = directory/(str(os.getpid())+'.json')
    temporary = directory/(str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(adopted,sort_keys=True)+'\n')
    os.replace(temporary,target)
