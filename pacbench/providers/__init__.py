"""Native provider selection, Hermes plugin resolution and trial conditions."""
from contextlib import contextmanager
from importlib import import_module
import os


ACTIVE_PROVIDERS = ('memcal', 'mem0', 'hindsight', 'supermemory', 'honcho',
                    'openviking', 'reme', 'cognee', 'memos')
RETIRED_PROVIDERS = {
    'byterover': 'ByteRover was removed from the active benchmark at the user\'s direction; historical evidence is retained.',
}


def require_not_retired(provider):
    if provider in RETIRED_PROVIDERS:
        raise ValueError(RETIRED_PROVIDERS[provider])


LIVE_TRIALS = {
    'enabled': True,
    'reason': 'Isolated Hermes gateway/CLI, direct TLS, plugin APIs and persistent authorized OpenRouter accounting verified; every arm still requires native preflight and processing barriers.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])


def provider_module(name, submodule=None):
    memory = import_module("plugins.memory")
    resolver = getattr(memory, "import_provider_module", None)
    if resolver is not None:
        return resolver(name, submodule)
    suffix = f".{submodule}" if submodule else ""
    return import_module(f"plugins.memory.{name}{suffix}")


@contextmanager
def run_environment(settings):
    previous = {key:os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
