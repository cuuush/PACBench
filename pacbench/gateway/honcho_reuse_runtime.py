"""Observe Honcho's native manager and workers after stock Hermes teardown."""
import threading
from . import memos_reuse_runtime


def main():
    previous = memos_reuse_runtime.provider_state

    def state(provider):
        if provider.name != 'honcho':
            return previous(provider)
        manager = provider._manager
        config = provider._config
        pending = None
        if manager is not None:
            with manager._cache_lock:
                sessions = [*manager._cache.values(), *manager._retry_sessions]
                pending = sum(not message.get('_synced') for session in sessions
                              for message in session.messages)
        workers = [t.name for t in threading.enumerate()
                   if t.name.startswith('honcho-') and t.is_alive()]
        return {'name':'honcho', 'endpoint':getattr(config, 'base_url', None),
                'workspace':getattr(config, 'workspace_id', None),
                'session_key':provider._session_key, 'workers':workers, 'pending':pending,
                'clean':pending == 0 and not workers}

    memos_reuse_runtime.provider_state = state
    memos_reuse_runtime.main()


if __name__ == '__main__':
    main()
