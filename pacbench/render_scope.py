"""Opt-in logical source rendering: no provider or scored capabilities."""
import os

SCOPE='source-render-v1'
PROJECTS={'pacbench-authoring','pacbench-scene-verifier'}

def active():
    scope=os.environ.get('PACBENCH_EXECUTION_SCOPE')
    if scope not in (None,SCOPE):raise ValueError('Unknown execution scope')
    return scope==SCOPE

def validate(config_scope,project,profile):
    if config_scope not in (None,'import-and-connectivity-probes','scored-week-roles'):
        raise RuntimeError('Unknown ledger request scope')
    if not active():return False
    if (profile!='luna-bedrock-global' or project not in PROJECTS or
            config_scope!='scored-week-roles' or not os.environ.get('PACBENCH_FUNDED_RUN_ID')):
        raise RuntimeError('Render scope requires prepaid Luna authoring or scene-verifier capability')
    return True
