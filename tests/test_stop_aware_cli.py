import subprocess,sys,threading,time
import pytest
from pacbench.stop import communicate_until_stop,OperatorStop


def test_admission_stop_reaps_stuck_native_cli_promptly(tmp_path):
    marker=tmp_path/'STOP';proc=subprocess.Popen([sys.executable,'-c','import time;print("partial",flush=True);time.sleep(60)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    timer=threading.Timer(.1,marker.touch);timer.start();started=time.monotonic()
    try:
        with pytest.raises(OperatorStop):communicate_until_stop(proc,timeout=30,env={'PACBENCH_STOP_FILE':str(marker)})
        assert proc.poll() is not None and time.monotonic()-started<3
    finally:timer.cancel();proc.kill() if proc.poll() is None else None


def test_normal_cli_output_unchanged_with_stop_monitor(tmp_path):
    proc=subprocess.Popen([sys.executable,'-c','print("complete")'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    out,err=communicate_until_stop(proc,timeout=5,env={'PACBENCH_STOP_FILE':str(tmp_path/'STOP')})
    assert out=='complete\n' and err=='' and proc.returncode==0


def test_deadline_reaps_child_and_preserves_partial_output(tmp_path):
    proc=subprocess.Popen([sys.executable,'-c','import time;print("partial",flush=True);time.sleep(60)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        communicate_until_stop(proc,timeout=.2,env={'PACBENCH_STOP_FILE':str(tmp_path/'STOP')})
    assert proc.poll() is not None and 'partial' in caught.value.stdout
