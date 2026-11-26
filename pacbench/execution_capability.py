"""Mutually exclusive native checkpoint execution permissions."""
import os
from . import canary_capability,full_run_capability,remaining_r2_capability
from pacbench.workflows import capability as workflow_capability
from .hermes_gateway import GatewayLifecycleError
CanaryBusy=canary_capability.CanaryBusy

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
