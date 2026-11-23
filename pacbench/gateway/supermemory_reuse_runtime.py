"""Observe native Supermemory worker completion without replacing its writer."""
import threading
from . import memos_reuse_runtime


def main():
    import os
    from pacbench import supermemory_explicit_store as explicit

    if os.environ.get(explicit.ENV) == explicit.PROTOCOL:
        explicit.observe_gateway()
    previous=memos_reuse_runtime.provider_state

    def state(provider):
        if provider.name!='supermemory':
            return previous(provider)
        active=[thread.name for thread in threading.enumerate()
                if thread.name.startswith('supermemory-') and thread.is_alive()]
        return {'name':'supermemory','namespace':provider._container_tag,'endpoint':provider._base_url,
                'workers':active,'clean':not active and provider._write_thread is None}

    memos_reuse_runtime.provider_state=state
    memos_reuse_runtime.main()


if __name__=='__main__': main()
