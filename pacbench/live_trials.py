"""Keep paid trials stopped pending restored state and native provider canaries."""

LIVE_TRIALS = {
    'enabled': False,
    'reason': 'Offline Hermes, clock and TLS integration passed; restore the original budget ledger and verify native provider readiness before enabling paid trials.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])
