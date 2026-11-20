"""Observe the stock Cognee plugin's workers and native HTTP binding."""
import sys

from . import memos_reuse_runtime


def main():
    previous = memos_reuse_runtime.provider_state
    close_modes = {}

    def state(provider):
        if provider.name != 'cognee':
            return previous(provider)
        session = provider._session_cognee_id
        if provider._close_handed_off and provider._watcher_state_path:
            watcher = sys.modules[type(provider).__module__].exit_watcher
            current = watcher.read_state(provider._watcher_state_path)
            if current and current.get('session_id') == session and isinstance(current.get('improve'), bool):
                close_modes[session] = current['improve']
        workers = (provider._prefetch_thread, provider._sync_thread, provider._pre_compress_thread)
        return {'name': 'cognee', 'endpoint': provider._backend.url, 'dataset': provider._dataset,
                'native_session': session, 'close_requested_improve': close_modes.get(session),
                'clean': not any(thread and thread.is_alive() for thread in workers)}

    memos_reuse_runtime.provider_state = state
    memos_reuse_runtime.main()


if __name__ == '__main__':
    main()
