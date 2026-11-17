import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from pacbench.provider_clock import LIBRARY, ProviderClock, nightly_time


def test_nightly_dst():
    assert nightly_time(date(2026, 3, 7)).isoformat() == '2026-03-08T07:00:00+00:00'
    assert nightly_time(date(2026, 10, 31)).isoformat() == '2026-11-01T08:00:00+00:00'


def test_relative_clock_children(tmp_path):
    if not LIBRARY.exists():
        pytest.skip('Install pinned libfaketime runtime to test process clocks')
    target = datetime.now(timezone.utc) - timedelta(days=60)
    clock = ProviderClock(tmp_path / 'clock', target)
    program = '''import asyncio,json,sys,time
async def main():
 for line in sys.stdin:
  before=time.monotonic(); time.sleep(.01); await asyncio.sleep(.08)
  print(json.dumps([time.time(),time.monotonic()-before]),flush=True)
asyncio.run(main())'''
    children = [subprocess.Popen([sys.executable, '-u', '-c', program],
        env=clock.environment(os.environ), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True) for _ in range(2)]
    def sample():
        for p in children:
            p.stdin.write('sample\n'); p.stdin.flush()
        return [json.loads(p.stdout.readline()) for p in children]
    try:
        real_before = time.time()
        first = sample()
        assert abs(first[0][0] - target.timestamp()) < 3
        assert abs(first[0][0] - first[1][0]) < 1
        assert all(.06 < a[1] < 2 for a in first)
        clock.advance(datetime.now(timezone.utc) - timedelta(days=60) + timedelta(minutes=30))
        second = sample()
        assert all(1799 < b[0]-a[0] < 1802 for a,b in zip(first,second))
        clock.advance(datetime.now(timezone.utc) - timedelta(days=59) + timedelta(minutes=30))
        third = sample()
        assert all(86399 < c[0]-b[0] < 86402 for b,c in zip(second,third))
        assert all(.06 < a[1] < 2 for a in third)
        assert 0 < time.time()-real_before < 5
        with pytest.raises(ValueError):
            clock.advance(target)
    finally:
        for p in children:
            p.terminate(); p.wait(timeout=5)


def test_suspended_clock_excludes_only_verified_overhead(tmp_path):
    target = datetime.now(timezone.utc)-timedelta(days=10)
    clock = ProviderClock(tmp_path/'clock',target)
    frozen = clock.now()
    before = clock.path.read_bytes()
    with pytest.raises(RuntimeError,match='fully suspended'):
        clock.resume_suspended(frozen,lambda:False)
    assert clock.path.read_bytes() == before
    time.sleep(.08)
    clock.resume_suspended(frozen,lambda:True)
    assert abs((clock.now()-frozen).total_seconds()) < .05
    clock.advance(target+timedelta(days=1))
    assert abs((clock.now()-target).total_seconds()-86400) < .1


def test_suspended_clock_rechecks_under_controller_lock(tmp_path):
    clock = ProviderClock(tmp_path/'clock',datetime.now(timezone.utc))
    before = clock.path.read_bytes()
    checks = iter([True,False])
    with pytest.raises(RuntimeError,match='resumed'):
        clock.resume_suspended(clock.now(),lambda:next(checks))
    assert clock.path.read_bytes() == before


def test_clock_steps_refuse_a_live_gateway_lifecycle(tmp_path):
    import fcntl
    home=tmp_path/'hermes-home';home.mkdir()
    clock=ProviderClock(tmp_path/'provider-clock',datetime.fromisoformat('2026-09-11T09:00:00-04:00'))
    before=clock.path.read_bytes()
    with (home/'.pacbench-gateway.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with pytest.raises(RuntimeError,match='lifecycle'):
            clock.advance(datetime.fromisoformat('2026-09-12T09:00:00-04:00'))
        with pytest.raises(RuntimeError,match='lifecycle'):
            clock.resume_suspended(clock.now(),lambda:True)
    assert clock.path.read_bytes()==before
