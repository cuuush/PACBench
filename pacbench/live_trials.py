"""Fail closed until the isolated Hermes gateway lifecycle is integrated."""

LIVE_TRIALS = {
    'enabled': False,
    'reason': 'Hermes v0.23.1 retains its startup environment in a persistent local gateway; per-turn clock, tool and provider settings require a verified run-owned gateway lifecycle.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])
