"""Isolated, advancing wall clocks for native provider processes.

The controller itself uses the real host clock. Callers must establish native
quiescence before each advance, and keep the gateway outside this environment.
"""
from __future__ import annotations

import os
import math
import fcntl
import tempfile
import time
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LEGACY_LIBRARY = Path(__file__).resolve().parents[1] / 'vendor/libfaketime/runtime/libfaketime.so.1'
LEGACY_LIBRARY_SHA256 = '45c4822a7516e9c41ec1b5498b0f08665ba4877330e9e9573c1213d16d6629a0'
LIBRARY = Path(__file__).resolve().parents[1] / 'vendor/libfaketime/runtime-v4/libfaketime.so.1'
LIBRARY_SHA256 = 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa'


def nightly_time(collection_day: date, timezone_name: str = 'America/New_York') -> datetime:
    """The 03:00 local processing time after a collection day, in UTC."""
    return datetime.combine(collection_day + timedelta(days=1), daytime(3),
                            ZoneInfo(timezone_name)).astimezone(timezone.utc)


class ProviderClock:
    """One shared libfaketime relative offset, advancing at real-time speed."""

    def __init__(self, directory: Path, target: datetime):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o755)
        self.directory.chmod(0o755)
        self.path = self.directory / 'offset'
        self._offset = None
        self.advance(target)

    def advance(self, target: datetime) -> float:
        if target.tzinfo is None or target.utcoffset() is None:
            raise ValueError('Clock target must include a timezone')
        with (self.directory / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            offset = target.timestamp() - time.time()
            previous = float(self.path.read_text()) if self.path.exists() else None
            if previous is not None and offset < previous:
                raise ValueError('Provider clock cannot move backward')
            fd, name = tempfile.mkstemp(prefix='.offset-', dir=self.directory)
            try:
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    stream.write(f'{offset:+.6f}\n')
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        self._offset = offset
        return offset

    @classmethod
    def attach(cls, directory: Path):
        """Attach a real-time harness process to an existing isolated clock."""
        clock = cls.__new__(cls)
        clock.directory = Path(directory).resolve()
        clock.path = clock.directory / 'offset'
        clock._offset = float(clock.path.read_text())
        if not math.isfinite(clock._offset):
            raise ValueError('Invalid provider clock offset')
        return clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(time.time() + float(self.path.read_text()), timezone.utc)

    def resume_suspended(self, at: datetime, verify_suspended):
        """Exclude harness overhead only while every native clock user is stopped.

        This changes the offset without rewinding any *observed* provider time.
        Normal advances still reject backward changes. The caller supplies and
        records independent process/container suspension verification.
        """
        if at.tzinfo is None or not verify_suspended():
            raise RuntimeError('Clock compensation requires a fully suspended native runtime')
        with (self.directory/'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not verify_suspended():
                raise RuntimeError('Native runtime resumed during clock compensation')
            fd, name = tempfile.mkstemp(prefix='.offset-',dir=self.directory)
            try:
                with os.fdopen(fd,'w',encoding='ascii') as stream:
                    os.fchmod(stream.fileno(),0o644)
                    stream.write(f'{at.timestamp()-time.time():+.6f}\n')
                os.replace(name,self.path)
            finally:
                if os.path.exists(name):os.unlink(name)

    def environment(self, base: dict[str, str], *, library: str | Path = LIBRARY,
                    timestamp_file: str | Path | None = None) -> dict[str, str]:
        env = {k: v for k, v in base.items()
               if k != 'FAKETIME' and not k.startswith('FAKETIME_') and k != 'DONT_FAKE_MONOTONIC'}
        if env.get('LD_PRELOAD'):
            raise ValueError('Cannot combine provider clock with inherited LD_PRELOAD')
        env.update(LD_PRELOAD=str(library), FAKETIME_TIMESTAMP_FILE=str(timestamp_file or self.path),
                   FAKETIME_NO_CACHE='1', FAKETIME_DONT_FAKE_MONOTONIC='1')
        return env
