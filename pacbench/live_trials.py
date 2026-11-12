"""Fail closed while the upgraded runtime and provider integrations are validated."""

LIVE_TRIALS = {
    'enabled': False,
    'reason': 'The Hermes upgrade has not completed independent offline integration and native provider readiness validation.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])
