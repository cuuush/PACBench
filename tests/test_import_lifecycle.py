import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

import pytest

from pacbench import import_lifecycle as lc
from pacbench.import_metrics import ImportMetrics
from pacbench.measured_import import supervise, observations


def test_path_ownership_rejects_similarly_prefixed_sibling(tmp_path):
    root=tmp_path/'arm'
    assert lc.beneath(str(root/'store'),root)
    assert not lc.beneath(str(tmp_path/'arm-other/store'),root)
    assert not lc.beneath('arbitrary text '+str(root),root)


def test_identity_mismatch_never_signals(monkeypatch):
    monkeypatch.setattr(lc,'identity',lambda pid:{'pid':pid,'start_ticks':'new','state':'S'})
    monkeypatch.setattr(lc.os,'pidfd_open',lambda pid:42)
    monkeypatch.setattr(lc.os,'close',lambda fd:None)
    monkeypatch.setattr(lc.signal,'pidfd_send_signal',lambda *a:pytest.fail('reused PID was signaled'))
    assert lc.send({'pid':123,'start_ticks':'old'},signal.SIGTERM) is False


def test_timeout_stops_detached_term_resistant_vendor_service_preserves_store(tmp_path):
    component=tmp_path/'arm';sibling=tmp_path/'arm-sibling';sibling.mkdir()
    sentinel=sibling/'ledger.private.json';sentinel.write_text('preserve financial hold SECRET-SENTINEL')
    bystander=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],cwd=sibling)
    code="""
import os, pathlib, subprocess, sys, time
root=pathlib.Path(sys.argv[1]);root.mkdir();(root/'store').write_text('source facts')
child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],start_new_session=True,cwd='/var/tmp')
(root/'daemon-pid').write_text(str(child.pid))
time.sleep(60)
"""
    try:
        result=supervise([sys.executable,'-c',code,str(component)],component,stop=threading.Event(),
            stop_file=tmp_path/'STOP',timeout_s=.8,grace_s=.1,container_cleanup=False)
        assert result['state']=='timeout' and result['launch_to_cleanup_wall_s']<12
        assert result['cleanup']['state']=='verified-inventory-stopped'
        assert any(x['signal']=='SIGKILL' for x in result['cleanup']['signals'])
        assert not result['cleanup']['remaining_processes']
        assert (component/'store').read_text()=='source facts'
        assert bystander.poll() is None and sentinel.read_text().endswith('SECRET-SENTINEL')
        assert 'SECRET-SENTINEL' not in json.dumps(result)
        assert not lc.alive({'pid':int((component/'daemon-pid').read_text()),'start_ticks':'unknown'})
    finally:
        bystander.terminate();bystander.wait(timeout=5)


def test_provision_failure_still_cleans_started_background_process(tmp_path):
    root=tmp_path/'arm'
    code="""
import pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]);root.mkdir()
subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],cwd='/var/tmp',start_new_session=True)
raise RuntimeError('provision failed after daemon start')
"""
    result=supervise([sys.executable,'-c',code,str(root)],root,stop=threading.Event(),
        stop_file=tmp_path/'STOP',timeout_s=5,grace_s=.1,container_cleanup=False)
    assert result['state']=='incomplete' and result['returncode']!=0
    assert result['cleanup']['state']=='verified-inventory-stopped'
    assert result['cleanup']['observed_processes']


def test_global_stop_cancels_blocked_child(tmp_path):
    root=tmp_path/'arm';global_stop=tmp_path/'globalSTOP';global_stop.touch()
    result=supervise([sys.executable,'-c','import time;time.sleep(60)'],root,stop=threading.Event(),
        stop_file=tmp_path/'armSTOP',cancel_file=global_stop,timeout_s=60,grace_s=.1,container_cleanup=False)
    assert result['state']=='cancelled' and result['cleanup']['state']=='verified-inventory-stopped'


def test_container_inventory_requires_exact_resolved_mount(tmp_path):
    root=tmp_path/'arm'
    def runner(cmd,**kw):
        if 'ps' in cmd:return subprocess.CompletedProcess(cmd,0,'a b','')
        return subprocess.CompletedProcess(cmd,0,json.dumps([
            {'Id':'a','Mounts':[{'Type':'bind','Source':str(root/'honcho-db')}], 'Config':{'Labels':{'pacbench.import-root':str(root)}}},
            {'Id':'b','Mounts':[{'Type':'bind','Source':str(tmp_path/'arm-sibling/honcho-db')}]}]),'')
    rows,error=lc.owned_containers(root,runner=runner)
    assert not error and [r['id'] for r in rows]==['a'] and rows[0]['run_label_matches']


@pytest.mark.parametrize('label,receipt_id',[('wrong','a'),('correct','different')])
def test_forged_container_ownership_is_not_accepted(tmp_path,label,receipt_id):
    root=tmp_path/'arm';root.mkdir();db=root/'honcho-db'
    (root/'native-container-ownership.json').write_text(json.dumps({'run_root':str(root),
        'data_bind_mount':str(db),'container_id':receipt_id,'label_root':str(root)}))
    def runner(cmd,**kw):
        if 'ps' in cmd:return subprocess.CompletedProcess(cmd,0,'a','')
        return subprocess.CompletedProcess(cmd,0,json.dumps([{'Id':'a',
            'Mounts':[{'Type':'bind','Source':str(db)}],
            'Config':{'Labels':{'pacbench.import-root':str(root) if label=='correct' else 'wrong'}}}]),'')
    rows,error=lc.owned_containers(root,runner=runner)
    assert not rows and error=='container-ownership-unverified'


@pytest.mark.parametrize('malformed,returncode',[(False,3),(True,0)])
def test_nonzero_exit_or_interrupted_json_cannot_report_success(tmp_path,malformed,returncode):
    root=tmp_path/'arm'
    code="""
import json,pathlib,sys,time
root=pathlib.Path(sys.argv[1]);root.mkdir()
summary={'state':'processing-complete','mode':'native-import-validation','benchmark_questions_sent':0,'judge_calls':0}
(root/'summary.json').write_text('{' if sys.argv[2]=='True' else json.dumps(summary))
(root/'import-events.jsonl').write_text(json.dumps({'event':'native-all-batches-ready','utc':'2026-11-13T12:00:00+00:00','monotonic_s':time.monotonic()})+'\\n'+('{' if sys.argv[2]=='True' else ''))
sys.exit(int(sys.argv[3]))
"""
    result=supervise([sys.executable,'-c',code,str(root),str(malformed),str(returncode)],root,
        stop=threading.Event(),stop_file=tmp_path/'STOP',timeout_s=5,container_cleanup=False)
    assert result['state']=='incomplete'
    assert result['cleanup']['state']=='verified-inventory-stopped'
    assert (tmp_path/'arm.ownership.private.json').exists()
    if malformed:assert result['parse_errors']


def test_phase_events_preserve_failed_and_unfinished_phases(tmp_path):
    metrics=ImportMetrics(tmp_path)
    with pytest.raises(ValueError):
        with metrics.phase('provision'):raise ValueError('SECRET-SENTINEL')
    metrics.emit('phase-start',phase='batch',phase_id='crashed')
    events=[json.loads(l) for l in metrics.path.read_text().splitlines()]
    assert events[1]['state']=='failed' and events[1]['duration_s']>=0
    assert 'SECRET-SENTINEL' not in metrics.path.read_text()


def test_metrics_report_uncertain_cost_and_missing_tokens_explicitly(tmp_path):
    root=tmp_path/'mem0';root.mkdir();metrics=ImportMetrics(root)
    with metrics.phase('native-batch-import-through-readiness',batch='one'):pass
    import hashlib
    project='pacbench-'+hashlib.sha256(str((root/'repeat-1/mem0').resolve()).encode()).hexdigest()[:12]+'-mem0-memory'
    call={'id':'unknown','project':project,'started':'2026-11-13T12:00:00+00:00',
          'state':'reserved','accounted':'0.175','request_sha256':'x'}
    result=observations(root,[call])
    assert result['request_count_observed']==1 and result['requests_missing_token_counts']==1
    assert result['unresolved_bound_usd']=='0.175' and result['settled_reported_cost_usd']=='0'
    assert result['requests'][0]['duration_s'] is None and result['requests'][0]['reported_cost_usd'] is None
