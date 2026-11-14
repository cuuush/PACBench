"""Cooperative operator stop at safe boundaries between native work units."""
import os
from pathlib import Path


class OperatorStop(Exception):
    """An operator requested a stop; this is not a provider failure."""


def check_stop(path=None):
    paths = [path, os.environ.get('PACBENCH_STOP_FILE')]
    if any(value and Path(value).exists() for value in paths):
        raise OperatorStop('operator stop requested')


def communicate_until_stop(proc, *, timeout, env):
    """Reap a native CLI promptly after admission/operator stop, keeping timeouts bounded."""
    import subprocess,time
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
