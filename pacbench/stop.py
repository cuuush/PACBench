"""Cooperative operator stop at safe boundaries between native work units."""
import os
from pathlib import Path


class OperatorStop(Exception):
    """An operator requested a stop; this is not a provider failure."""


def check_stop(path=None):
    paths = [path, os.environ.get('PACBENCH_STOP_FILE')]
    if any(value and Path(value).exists() for value in paths):
        raise OperatorStop('operator stop requested')
