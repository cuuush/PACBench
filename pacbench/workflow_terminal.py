"""Keep the first execution error while recording independent terminal stages."""
from contextlib import contextmanager


class RunFailures:
    def __init__(self, result):
        self.result = result
        self.primary = None

    @contextmanager
    def record(self, stage):
        try:
            yield
        except BaseException as exc:
            failure_type = type(exc).__name__
            if self.primary is None:
                self.primary = exc
                self.result.update(failure_type=failure_type, failure_stage=stage)
            else:
                self.result.setdefault('secondary_failures', []).append(dict(stage=stage, failure_type=failure_type))
                self.primary.add_note(f'Additional failure during {stage}: {failure_type}')
