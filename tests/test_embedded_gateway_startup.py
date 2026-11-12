"""Private-state startup boundaries; all state is temporary test data."""
import json
import os
import subprocess
import sys
import time

from pacbench.gateway.ledger import Ledger


def run_cli(state, *flags):
    return subprocess.run([sys.executable,'-m','pacbench.gateway','--state',str(state),*flags],
                          env={**os.environ,'META':'fixture-key'},capture_output=True,text=True,timeout=10)


def test_startup_without_state_never_creates_replacement(tmp_path):
    state=tmp_path/'missing'
    result=run_cli(state)
    assert result.returncode and not state.exists()
    state.mkdir()
    result=run_cli(state)
    assert result.returncode and not (state/'ledger.sqlite3').exists() and not (state/'client-token').exists()
    result=run_cli(state,'--initialize-new-state')
    assert result.returncode and not (state/'ledger.sqlite3').exists()


def test_explicit_new_install_then_existing_state_restart(tmp_path):
    state=tmp_path/'new'
    proc=subprocess.Popen([sys.executable,'-m','pacbench.gateway','--state',str(state),
                           '--port','0','--initialize-new-state'],env={**os.environ,'META':'fixture-key'},
                          stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline and not (state/'address.json').exists():
            if proc.poll() is not None: break
            time.sleep(.02)
        assert (state/'address.json').is_file(), proc.stderr.read() if proc.poll() is not None else 'server did not start'
        assert json.loads((state/'address.json').read_text())['base'].startswith('http://127.0.0.1:')
        assert (state/'client-token').read_text().strip()
        assert Ledger(state/'ledger.sqlite3',existing_only=True).health()['service']=='pacbench-budget-gateway'
    finally:
        proc.terminate();proc.wait(timeout=5)
    token=(state/'client-token').read_bytes()
    assert run_cli(state,'--initialize-new-state').returncode
    assert (state/'client-token').read_bytes()==token
