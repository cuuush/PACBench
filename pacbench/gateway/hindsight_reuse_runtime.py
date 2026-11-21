"""Observe native Hindsight worker teardown without replacing its lifecycle."""
import sys

from . import memos_reuse_runtime


def main():
    previous=memos_reuse_runtime.provider_state

    def state(provider):
        if provider.name!='hindsight':
            return previous(provider)
        workers=(provider._writer_thread,provider._prefetch_thread)
        sentinel=sys.modules[type(provider).__module__]._WRITER_SENTINEL
        with provider._retain_queue.mutex:
            pending=sum(job is not sentinel for job in provider._retain_queue.queue)
            unfinished=provider._retain_queue.unfinished_tasks
        active=[worker.name for worker in workers if worker and worker.is_alive()]
        return {'name':'hindsight','bank':provider._bank_id,'endpoint':provider._api_url,
                'local_pending_writes':pending,'local_unfinished_writes':unfinished,'active_workers':active,
                'shutdown_started':provider._shutting_down.is_set(),'client_attached':provider._client is not None,
                # Local shutdown, queue and worker evidence is followed by a
                # bank-scoped server operation drain before the next question.
                'clean':provider._shutting_down.is_set() and pending==0 and not active}

    memos_reuse_runtime.provider_state=state
    memos_reuse_runtime.main()


if __name__=='__main__': main()
