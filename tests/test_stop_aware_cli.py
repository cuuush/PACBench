import subprocess,sys,threading,time
from types import SimpleNamespace

import pytest
from pacbench.process_cleanup import communicate_until_stop, OperatorStop
from pacbench.workflows import native as workflow_native


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


@pytest.mark.parametrize('stop_during_turn',[False,True])
def test_warm_hermes_monitors_stop_before_accepting_a_turn(tmp_path,monkeypatch,stop_during_turn):
    marker=tmp_path/'STOP'
    env=dict(PACBENCH_WORKFLOW_CAPTURE='pizza-week-conversation-v2',PACBENCH_STOP_FILE=str(marker),
             PACBENCH_CONTEXT_TRACE=str(tmp_path/'context'),PACBENCH_API_INPUT_TRACE=str(tmp_path/'inputs'))
    owner=SimpleNamespace(home=tmp_path,cwd=tmp_path,python=sys.executable,env=env,witness={'pid':100})
    clock=SimpleNamespace(prepare=lambda *args,**kwargs: {'nonce':'clock-nonce'})
    monkeypatch.setattr(workflow_native,'_identity',lambda *_: None)
    frontend=workflow_native.WarmHermes(owner,clock,lambda:None,watch_stop=True)
    monkeypatch.setattr(frontend,'read_return',lambda prompt,at,session_id,raw,**kwargs: raw)
    actual_popen=subprocess.Popen
    children=[]

    def cli(args,**kwargs):
        assert args[1:4]==['-m','hermes_cli.main','chat']
        program='print("complete")'
        if stop_during_turn:
            program='import os,pathlib,time; pathlib.Path(os.environ["PACBENCH_STOP_FILE"]).touch(); time.sleep(5)'
        child=actual_popen([sys.executable,'-c',program],**kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(workflow_native.subprocess,'Popen',cli)
    saved=[]
    if stop_during_turn:
        with pytest.raises(OperatorStop):
            frontend.ask('Question',at='2026-10-12',session_id=None,on_return=saved.append)
        assert not saved
    else:
        result=frontend.ask('Question',at='2026-10-12',session_id=None,on_return=saved.append)
        assert saved==[result] and result['stdout']=='complete\n' and result['returncode']==0
    assert len(children)==1 and children[0].poll() is not None
