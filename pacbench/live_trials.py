"""Admission switch for authorized native trials after runtime verification."""

LIVE_TRIALS = {
    'enabled': True,
    'reason': 'Isolated Hermes gateway/CLI, direct TLS, plugin APIs and persistent authorized OpenRouter accounting verified; every arm still requires native preflight and processing barriers.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])
