"""Active trial selection, separate from historical evidence support."""

ACTIVE_PROVIDERS = ('memcal', 'mem0', 'hindsight', 'supermemory', 'honcho',
                    'openviking', 'reme', 'cognee', 'memos')
RETIRED_PROVIDERS = {
    'byterover': 'ByteRover was removed from the active benchmark at the user\'s direction; historical evidence is retained.',
}


def require_not_retired(provider):
    if provider in RETIRED_PROVIDERS:
        raise ValueError(RETIRED_PROVIDERS[provider])
