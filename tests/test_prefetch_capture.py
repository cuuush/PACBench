"""Provider evidence must belong to the current native turn, even on failure."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from pacbench.harness import capture_prefetch
from pacbench import harness


def row(session='current', query='current question', text='retained context'):
    return (json.dumps(dict(session_id=session, query=query, prefetch=text))+'\n').encode()


@pytest.mark.parametrize('new', [b'', row(session='other'), row(query='other question'),
                              row()+row(), b'{unfinished', row(text=None)])
def test_stale_unrelated_or_ambiguous_context_is_unknown(tmp_path, new):
    path=tmp_path/'context.jsonl'
    before=row(session='previous', query='previous question', text='stale facts')
    path.write_bytes(before+new)
    turn=SimpleNamespace(session_id='current', prompt='current question', prefetch_context=None)
    capture_prefetch(turn,path,before)
    assert turn.prefetch_context is None


@pytest.mark.parametrize('text', ['', 'native injected context'])
@pytest.mark.parametrize('returncode', [0, -1])
def test_current_context_survives_success_or_timeout(tmp_path,text,returncode):
    path=tmp_path/'context.jsonl'
    before=row(session='previous')
    path.write_bytes(before+row(text=text))
    turn=SimpleNamespace(session_id='current', prompt='current question',
                         returncode=returncode, prefetch_context=None)
    capture_prefetch(turn,path,before)
    assert turn.prefetch_context == text


def test_rotated_trace_does_not_prove_current_context(tmp_path):
    path=tmp_path/'context.jsonl'
    path.write_bytes(row())
    turn=SimpleNamespace(session_id='current', prompt='current question', prefetch_context=None)
    capture_prefetch(turn,path,row(session='previous'))
    assert turn.prefetch_context is None


@pytest.mark.parametrize('timeout', [False, True])
def test_run_turn_captures_native_hook_on_both_exit_paths(tmp_path, monkeypatch, timeout):
    home=tmp_path/'home'
    home.mkdir()
    path=tmp_path/'context.jsonl'
    path.write_bytes(row(session='stale'))
    monkeypatch.setattr('pacbench.context_isolation.workspace_for',lambda _: tmp_path)
    monkeypatch.setattr('pacbench.context_isolation.actual_context',lambda *a: {'state':'pass'})
    monkeypatch.setattr(harness,'provider_trace_snapshot',lambda _: {})
    def child(cmd, **kwargs):
        with path.open('ab') as f:
            f.write(row(query=cmd[3]))
        out=json.dumps({'type':'result','session_id':'current','text':'answer'})+'\n'
        if timeout:
            raise subprocess.TimeoutExpired(cmd,1,output=out.encode())
        return subprocess.CompletedProcess(cmd,0,out,'')
    monkeypatch.setattr(harness.subprocess,'run',child)
    turn=harness.run_turn('current question',at='2026-09-11T09:00:00-04:00',
                          home=home,allowlist='')
    assert turn.returncode == (-1 if timeout else 0)
    assert turn.prefetch_context == 'retained context'
