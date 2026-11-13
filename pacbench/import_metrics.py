"""Durable real-clock phase boundaries for native import qualification."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json, os, time, uuid


class ImportMetrics:
    def __init__(self, output):
        self.path = Path(output) / 'import-events.jsonl'

    def emit(self, event, **values):
        row = {'event': event, 'utc': datetime.now(timezone.utc).isoformat(),
               'monotonic_s': time.monotonic(), **values}
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        return row

    @contextmanager
    def phase(self, name, **values):
        identifier = str(uuid.uuid4())
        start = self.emit('phase-start', phase=name, phase_id=identifier, **values)
        try:
            yield
        except BaseException as exc:
            self.emit('phase-end', phase=name, phase_id=identifier,
                      duration_s=time.monotonic()-start['monotonic_s'],
                      state='failed', error_class=type(exc).__name__, **values)
            raise
        else:
            self.emit('phase-end', phase=name, phase_id=identifier,
                      duration_s=time.monotonic()-start['monotonic_s'], state='complete', **values)
