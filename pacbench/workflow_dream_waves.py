"""Observe stock Dream's documented second-look wave without changing any request."""
from contextlib import contextmanager

from .canary_capability import digest

_observer = None


class DreamWaves:
    def __init__(self):
        self.kind = None
        self.expected = 0
        self.finished = set()
        self.extra = None
        self.second_look = False

    def observe(self, event, data):
        if event == 'propose_wave':
            kind = data['kind']
            if self.kind is not None and len(self.finished) != self.expected:
                raise ValueError('Dream opened a wave before its native predecessors completed')
            if kind == 'second-look':
                if self.second_look or self.kind not in ('main','split') or not data['entities']:
                    raise ValueError('Unexpected stock Dream second-look wave')
                self.second_look = True
                self.extra = {'kind':kind,'entities_sha256':digest(data['entities'])}
            else:
                self.extra = None
                if kind == 'main':
                    self.second_look = False
            self.kind = kind
            self.expected = data['requests']
            self.finished = set()
        elif event == 'propose_request':
            index = data['index']
            if not 1 <= index <= self.expected or index in self.finished:
                raise ValueError('Unexpected Dream work-unit completion')
            self.finished.add(index)
            if len(self.finished) == self.expected:
                self.extra = None
        elif event == 'stage' and data['stage'] != 'propose':
            self.extra = None


@contextmanager
def observed_dream():
    global _observer
    if _observer is not None:
        raise ValueError('Only the run-owned Dream may publish native waves')
    _observer = DreamWaves()
    try:
        yield _observer.observe
    finally:
        _observer = None


def native_dream_slot(slot):
    if slot['kind']=='ingest' and _observer is not None and _observer.extra is not None:
        return {**slot,'native_work':_observer.extra}
    return slot
