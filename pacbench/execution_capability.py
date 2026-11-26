"""Mutually exclusive native checkpoint execution permissions."""
from functools import wraps
import os
from threading import Lock
from . import canary_capability,full_run_capability,remaining_r2_capability
from pacbench.workflows import capability as workflow_capability
from .hermes_gateway import GatewayLifecycleError
CanaryBusy=canary_capability.CanaryBusy

# Capability lookup and native clients both read the process environment.
# Deployed parallel arms have separate processes. Overlapping launchers inside
# one interpreter would observe and restore each other's settings.
_launcher_lock = Lock()


def scoped_launcher(*keys):
    """Restore only the process variables owned by this launcher, including on startup failure."""
    def decorate(run):
        @wraps(run)
        def scoped(root, env, **kwargs):
            with _launcher_lock:
                previous = {key: os.environ.get(key) for key in keys}
                try:
                    return run(root, dict(env), **kwargs)
                finally:
                    for key, value in previous.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
        return scoped
    return decorate


def selected():
 modes=[m for m in (canary_capability,full_run_capability,remaining_r2_capability,workflow_capability) if os.environ.get(m.ENV)]
 if len(modes)>1:raise GatewayLifecycleError('Conflicting checkpoint capabilities')
 return modes[0] if modes else None

def admission(project,profile,body=None):
 m=selected()
 return m.admission(project,profile,body) if m else None

def serial(calls,permit):
 m=selected()
 if m and m is not workflow_capability:m.serial(calls,permit)

def fields(permit):
 m=selected()
 if not permit or m is None:return {}
 prefix='workflow' if m is workflow_capability else 'checkpoint_remaining' if m is remaining_r2_capability else 'checkpoint_full_run' if m is full_run_capability else 'checkpoint_canary'
 return {prefix+'_root':permit['root'],prefix+'_capability':permit['capability'],prefix+'_slot':permit['slot']}
