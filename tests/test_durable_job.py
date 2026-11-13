import json,os,sys,time
import pytest
from pacbench.durable_job import launch


def test_detached_child_owns_new_session_and_duplicate_launch_is_rejected(tmp_path):
    output=tmp_path/'cohort';sentinel=tmp_path/'sid'
    code="import os,pathlib;pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getsid(0)))"
    result=launch([sys.executable,'-c',code,str(sentinel)],output)
    deadline=time.monotonic()+5
    while not sentinel.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert int(sentinel.read_text())==result['pid']
    assert (tmp_path/'cohort.launch.private.json').stat().st_mode & 0o777 == 0o600
    assert (tmp_path/'cohort.controller.private.log').stat().st_mode & 0o777 == 0o600
    assert not output.exists()
    with pytest.raises(FileExistsError):launch([sys.executable,'-c','pass'],output)


def test_failed_launch_keeps_auditable_receipt_and_is_not_automatically_replayed(tmp_path):
    output=tmp_path/'cohort'
    with pytest.raises(FileNotFoundError):launch(['/nonexistent-pacbench-program'],output)
    assert json.loads((tmp_path/'cohort.launch.private.json').read_text())['state']=='launch-failed'
    with pytest.raises(FileExistsError):launch(['/nonexistent-pacbench-program'],output)
