from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from pacbench.provider_clock import ProviderClock, LIBRARY_SHA256
from provider_adapters import honcho_clock as clock


def test_attached_controllers_cannot_rewind_each_other(tmp_path):
    at = datetime.now(timezone.utc) - timedelta(days=30)
    first = ProviderClock(tmp_path / 'clock', at)
    second = ProviderClock.attach(first.directory)
    second.advance(at + timedelta(days=1))
    assert abs((first.now() - at).total_seconds() - 86400) < 2
    with pytest.raises(ValueError, match='backward'):
        first.advance(at + timedelta(hours=1))


def idle_snapshot(**changes):
    return {'claims':0,'eligible':0,'embeddings':0,'busy_connections':0,
            'errors':[],'messages':[],'pending':0, **changes}


@pytest.mark.parametrize('field', ['claims','eligible','embeddings','busy_connections'])
def test_clock_never_steps_during_native_work(monkeypatch, tmp_path, field):
    provider = ProviderClock(tmp_path / 'clock', datetime.now(timezone.utc) - timedelta(days=1))
    monkeypatch.setattr(clock, 'load', lambda root:(provider,{}))
    monkeypatch.setattr(clock, 'snapshot', lambda *a:idle_snapshot(**{field:1}))
    @contextmanager
    def pause(root): yield
    monkeypatch.setattr(clock, 'paused_apps', pause)
    before = provider.path.read_bytes()
    assert clock.step_if_idle(tmp_path, provider.now()+timedelta(hours=1)) is False
    assert provider.path.read_bytes() == before


def test_unenrolled_messages_do_not_age(monkeypatch, tmp_path):
    provider = ProviderClock(tmp_path / 'clock', datetime.now(timezone.utc) - timedelta(days=1))
    monkeypatch.setattr(clock, 'load', lambda root:(provider,{}))
    monkeypatch.setattr(clock, 'snapshot', lambda *a:idle_snapshot())
    @contextmanager
    def pause(root): yield
    monkeypatch.setattr(clock, 'paused_apps', pause)
    before = provider.path.read_bytes()
    assert not clock.step_if_idle(tmp_path, provider.now()+timedelta(hours=1), ids=['not-enrolled'])
    assert provider.path.read_bytes() == before


def test_background_errors_preserved_without_blocking_unrelated_state(monkeypatch, tmp_path):
    provider = ProviderClock(tmp_path / 'clock', datetime.now(timezone.utc) - timedelta(days=1))
    monkeypatch.setattr(clock, 'load', lambda root:(provider,{}))
    monkeypatch.setattr(clock, 'snapshot', lambda *a:idle_snapshot(
        errors=[{'id':1}],db_now=provider.now().isoformat()))
    monkeypatch.setattr('provider_adapters.honcho_local.queue_errors',
                        lambda *a,**kw:[{'id':1,'error_sha256':'a'*64}])
    resumed = []
    @contextmanager
    def pause(root):
        try: yield
        finally: resumed.append(True)
    monkeypatch.setattr(clock, 'paused_apps', pause)
    assert clock.step_if_idle(tmp_path, provider.now()+timedelta(hours=1))
    assert resumed == [True]
    step=json.loads((tmp_path/'provider-clock-steps.jsonl').read_text())
    assert step['native_errors_before'][0]['id']==1


def test_docker_mount_tracks_atomic_offset_replacements(tmp_path):
    provider = ProviderClock(tmp_path / 'clock', datetime.now(timezone.utc))
    args = clock.docker_clock_args(provider)
    assert f'type=bind,src={provider.directory},dst=/pacbench-clock,readonly' in args
    assert '999:999' in args
    assert 'FAKETIME_DONT_FAKE_MONOTONIC=1' in args
    assert 'DERIVER_FLUSH_ENABLED=true' not in args


def test_hold_prevents_any_native_setup(tmp_path, monkeypatch):
    from provider_adapters.honcho_local import start, OperatorConditionHold
    root = tmp_path/'repeat-1/honcho'; root.mkdir(parents=True)
    (tmp_path/'provider-conditions').mkdir()
    (tmp_path/'summary.json').write_text(json.dumps({'bundle_hash':'a'*64}))
    (tmp_path/'provider-conditions/honcho.json').write_text(json.dumps({
        'version':'provider-condition-v1','provider':'honcho','mode':'hold',
        'bundle_hash':'a'*64,'reason':'user requested simulated time'}))
    monkeypatch.setattr('provider_adapters.honcho_local.validate_route',
                        lambda *a:pytest.fail('Native setup began despite hold'))
    with pytest.raises(OperatorConditionHold): start(root, {}, 'model', 'url', 'key')
    assert json.loads((root/'operator-condition.json').read_text())['native_setup_started'] is False


def test_source_readiness_needs_each_actual_processed_message():
    from pacbench.document_audit import audit_honcho_receipt
    import hashlib
    source={'source_id':'m1','channel':'imessage','thread':'chat','sender':'Ben',
            'from_owner':False,'written_at':'2026-09-15T12:00:00-04:00','text':'Lunch tomorrow?'}
    session='source-'+hashlib.sha256(json.dumps(('imessage','chat')).encode()).hexdigest()[:24]
    result={'clock_protocol':clock.VERSION,'result':[{'session_id':session,
        'observer':{'peer_id':'hermes','observe_others':True},'messages':[{
            'id':'native-message','session_id':session,'peer_id':'Ben','content':source['text'],
            'created_at':source['written_at'],'metadata':{k:v for k,v in source.items() if k!='text'}}]}],
        'processing_status':[idle_snapshot()], 'message_witnesses':[
            {'id':'native-message','queue_id':1,'processed':True,'has_error':False}]}
    assert audit_honcho_receipt([source], result) == []
    result['source_error_scope']='submitted-source-sessions-v1'
    for failed_session in (session,None,'unrelated-hermes-session'):
        result['processing_status'][0]['errors']=[{'id':9,'task_type':'summary','session_name':failed_session}]
        assert bool(audit_honcho_receipt([source],result)) is (failed_session!='unrelated-hermes-session')
    result['processing_status'][0]['errors']=[]
    result.pop('source_error_scope')
    result['message_witnesses'][0]['processed'] = False
    assert audit_honcho_receipt([source], result)
    result['message_witnesses'] = []
    assert audit_honcho_receipt([source], result)


def test_failed_native_work_keeps_enrollment_and_error_witnesses(monkeypatch, tmp_path):
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary', lambda:{})
    state = idle_snapshot(errors=[{'id': 1}], messages=[
        {'id':'n1','queue_id':1,'processed':True,'has_error':True}])
    monkeypatch.setattr(clock, 'snapshot', lambda *a:state)
    monkeypatch.setattr('provider_adapters.honcho_local.queue_errors', lambda *a,**kw:[
        {'id':1,'error_class':'ValueError','error_sha256':'a'*64}])
    result = clock.wait_for_batch(tmp_path, [
        {'session_id':'source-session','observer':{'peer_id':'hermes'},'messages':[{'id':'n1'}]}], {})
    assert result['ready'] is False
    assert result['message_witnesses'][0]['has_error'] is True
    assert result['processing_status'][-1]['native_queue_errors'][0]['error_class'] == 'ValueError'


@pytest.mark.parametrize('session',['source-session',None,'unrelated-hermes-session'])
def test_source_readiness_checks_summary_errors_and_keeps_unrelated_work_separate(monkeypatch,tmp_path,session):
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    state=idle_snapshot(errors=[{'id':9,'task_type':'summary','session_name':session}],
        messages=[{'id':'n1','queue_id':1,'processed':True,'has_error':False}])
    monkeypatch.setattr(clock,'snapshot',lambda *a:state)
    monkeypatch.setattr('provider_adapters.honcho_local.queue_errors',lambda *a,**kw:[{
        'id':9,'task_type':'summary','session_name':session,'error_class':'EmptySummaryError','error_sha256':'a'*64}])
    receipt=clock.wait_for_batch(tmp_path,[{'session_id':'source-session',
        'observer':{'peer_id':'hermes'},'messages':[{'id':'n1'}]}],{})
    assert receipt['ready'] is (session=='unrelated-hermes-session')
    assert receipt['source_error_scope']=='submitted-source-sessions-v1'
    if not receipt['ready']:
        assert receipt['processing_status'][-1]['native_queue_errors'][0]['error_class']=='EmptySummaryError'


def test_scripted_checkpoint_cannot_use_already_later_provider_time(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    provider=ProviderClock(tmp_path/'clock',now+timedelta(minutes=30))
    monkeypatch.setattr(clock,'enabled',lambda r:True)
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    with pytest.raises(RuntimeError,match='future-time exposure'):
        clock.before_turn(tmp_path,(now+timedelta(minutes=10)).isoformat(),{})


def test_clock_processes_due_native_batch_before_later_scripted_question(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    provider=ProviderClock(tmp_path/'clock',now)
    due=now+timedelta(minutes=30);target=now+timedelta(hours=1)
    monkeypatch.setattr(clock,'enabled',lambda r:True)
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    advances=[]
    def snapshot(root):
        return idle_snapshot(pending=1 if not advances else 0,next_eligible_at=due.isoformat())
    def step(root,at,**kwargs):
        assert kwargs['horizon']==target
        advances.append(at);provider.advance(at);return True
    monkeypatch.setattr(clock,'snapshot',snapshot)
    monkeypatch.setattr(clock,'step_if_idle',step)
    result=clock.before_turn(tmp_path,target.isoformat(),{})
    assert advances==[due+timedelta(milliseconds=10),target]
    assert result['PACBENCH_PYTHON_CLOCK_FILE']==str(provider.path)
    assert 'LD_PRELOAD' not in result


def test_source_age_step_cannot_cross_question_horizon(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    provider=ProviderClock(tmp_path/'clock',now)
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    monkeypatch.setattr(clock,'snapshot',lambda *a:idle_snapshot())
    @contextmanager
    def pause(r):yield
    monkeypatch.setattr(clock,'paused_apps',pause)
    before=provider.path.read_bytes()
    with pytest.raises(RuntimeError,match='checkpoint'):
        clock.step_if_idle(tmp_path,now+timedelta(minutes=30),horizon=now+timedelta(minutes=10))
    assert provider.path.read_bytes()==before


def test_recovery_uses_current_time_without_crossing_next_check(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    provider=ProviderClock(tmp_path/'clock',now)
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    at, env=clock.before_recovery(tmp_path,(now-timedelta(minutes=1)).isoformat(),{
        'PACBENCH_CLOCK_NEXT_CHECKPOINT':(now+timedelta(minutes=1)).isoformat()})
    assert datetime.fromisoformat(at)>=now
    assert env['PACBENCH_PYTHON_CLOCK_FILE']==str(provider.path)
    assert 'LD_PRELOAD' not in env
    with pytest.raises(RuntimeError,match='checkpoint'):
        clock.before_recovery(tmp_path,(now-timedelta(minutes=1)).isoformat(),{
            'PACBENCH_CLOCK_NEXT_CHECKPOINT':(now-timedelta(seconds=1)).isoformat()})


def test_offline_clock_audit_rejects_missing_or_unsafe_evidence(tmp_path):
    # Preserve the frozen v3 audit format; v4 additionally requires Python adoption.
    from pacbench.followups import save
    target=datetime.now(timezone.utc)
    save(tmp_path/'provider-clock.json',{'version':'honcho-wall-clock-v3','library_sha256':clock.LEGACY_LIBRARY_SHA256,
        'wall_speed':1,'monotonic':'real','gateway_clock':'real',
        'source_timestamps':'unchanged','native_batching':'unchanged'})
    save(tmp_path/'provider-clock-startup.json',{'version':'honcho-wall-clock-v3','library_sha256':clock.LEGACY_LIBRARY_SHA256,
        'provider_at':target.isoformat(),'api_date':target.isoformat(),'db_at':target.isoformat(),
        'real_at':target.isoformat(),'monotonic':'real'})
    assert clock.audit_evidence(tmp_path,'honcho-wall-clock-v3')
    later=target+timedelta(minutes=30)
    step={'version':'honcho-wall-clock-v3','from':target.isoformat(),'to':later.isoformat(),
        'target':later.isoformat(),'real_at':target.isoformat(),'db_after':later.isoformat(),
        'pre_step':idle_snapshot(),'horizon':later.isoformat()}
    path=tmp_path/'provider-clock-steps.jsonl'; path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,'honcho-wall-clock-v3')==[]
    step['pre_step']['claims']=1;path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,'honcho-wall-clock-v3')
    step['pre_step']['claims']=0;step['horizon']=target.isoformat()
    path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,'honcho-wall-clock-v3')


def test_checkpoint_order_uses_instants_across_dst_fallback():
    from pacbench.native_pilot import next_checkpoint
    rows=[{'at':v} for v in ('2026-11-01T01:30:00-04:00',
        '2026-11-01T01:15:00-05:00','2026-11-01T02:00:00-05:00')]
    assert next_checkpoint(rows,'2026-11-01T01:45:00-04:00') == '2026-11-01T01:15:00-05:00'


@pytest.mark.parametrize('busy', ['claims','eligible','embeddings','busy_connections','model','accounting-lock'])
def test_judging_never_suspends_observed_native_work(monkeypatch,tmp_path,busy):
    provider=ProviderClock(tmp_path/'clock',datetime.now(timezone.utc))
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    monkeypatch.setattr(clock,'snapshot',lambda r:idle_snapshot(db_now=provider.now().isoformat(),
        **({busy:1} if busy not in {'model','accounting-lock'} else {})))
    def cost_snapshot(roles, *, nonblocking=False):
        assert nonblocking
        return {'state':'unavailable' if busy=='accounting-lock' else 'captured','calls':[
            {'role':'memory_provider','finished':None}] if busy=='model' else []}
    monkeypatch.setattr('pacbench.usage.snapshot',cost_snapshot)
    events=[]
    @contextmanager
    def paused(r):
        events.append('pause')
        try:yield
        finally:events.append('resume')
    monkeypatch.setattr(clock,'paused_apps',paused)
    monkeypatch.setattr(clock,'_docker',lambda *a:pytest.fail('Busy runtime was suspended'))
    before=provider.path.read_bytes()
    with clock.benchmark_overhead(tmp_path,{}):
        assert events==['pause','resume']
    assert provider.path.read_bytes()==before


@pytest.mark.parametrize('abort',[False,True])
def test_suspension_owner_death_stops_native_work(monkeypatch,tmp_path,abort):
    from types import SimpleNamespace
    marker=tmp_path/'marker';marker.write_text(json.dumps({'abort':abort}))
    monkeypatch.setattr(clock,'_process_started',lambda pid:'old-start' if abort else None)
    monkeypatch.setattr(clock,'load',lambda r:(None,{'container':'owned-db'}))
    events=[]
    monkeypatch.setattr(clock,'_docker',lambda cfg,*a:(events.append(a) or SimpleNamespace(returncode=0)))
    monkeypatch.setattr('pacbench.process_cleanup.cleanup_run_processes',lambda r:events.append('cleanup'))
    clock.watch_overhead(tmp_path,marker,999999,'old-start')
    assert events==[('unpause','owned-db'),'cleanup',('stop','--time','5','owned-db')]
    assert not marker.exists()
    assert json.loads((tmp_path/'provider-clock-interrupted.json').read_text())['scored'] is False


@pytest.mark.parametrize('error,inspection,allowed',[
    ('OCI runtime pause failed: freezer not supported',{'Running':True,'Paused':False},True),
    ('permission denied',{'Running':True,'Paused':False},False),
    ('freezer not supported',{'Running':False,'Paused':False},False),
    ('freezer not supported',{'Running':True,'Paused':True},False),
    ('freezer not supported',{},False),
])
def test_unsupported_freezer_runs_native_time_only_when_running_unpaused_is_verified(monkeypatch,tmp_path,error,inspection,allowed):
    from types import SimpleNamespace
    import time
    provider=ProviderClock(tmp_path/'clock',datetime.now(timezone.utc))
    monkeypatch.setattr(clock,'load',lambda r:(provider,{'container':'owned-db'}))
    monkeypatch.setattr(clock,'snapshot',lambda r:idle_snapshot(db_now=provider.now().isoformat()))
    monkeypatch.setattr('pacbench.usage.snapshot',lambda r,**kw:{'state':'captured','calls':[]})
    events=[]
    @contextmanager
    def paused(root):
        events.append('apps-paused')
        try:yield
        finally:events.append('apps-resumed')
    monkeypatch.setattr(clock,'paused_apps',paused)
    def docker(config,*args):
        events.append(args[0])
        if args[0]=='pause':return SimpleNamespace(returncode=1,stderr=error,stdout='')
        if args[0]=='inspect':return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps(inspection))
        pytest.fail('Fallback must never unpause, restart or edit the database')
    monkeypatch.setattr(clock,'_docker',docker)
    monkeypatch.setattr(provider,'resume_suspended',lambda *a:pytest.fail('A running database must never be rewound'))
    offset=provider.path.read_bytes()
    if not allowed:
        with pytest.raises(RuntimeError,match='Cannot suspend'):
            with clock.benchmark_overhead(tmp_path,{}):pytest.fail('Unsafe pause failure reached judge')
        assert events[-1]=='apps-resumed'
        assert not (tmp_path/'provider-clock-running-overhead.jsonl').exists()
    else:
        with clock.benchmark_overhead(tmp_path,{}):
            assert events[-1]=='apps-resumed'
            time.sleep(.02)
        row=json.loads((tmp_path/'provider-clock-running-overhead.jsonl').read_text())
        assert row['state']=='observed' and row['native_clock_running'] is True
        assert row['database_paused'] is False and row['offset_adjustment'] is False
        assert row['offset_before_sha256']==row['offset_after_sha256']
        assert row['pause_failure']['inspection']=={'Running':True,'Paused':False}
        assert datetime.fromisoformat(row['provider_finished_at'])>datetime.fromisoformat(row['provider_started_at'])
    assert provider.path.read_bytes()==offset
@pytest.mark.parametrize('declared', ['honcho-wall-clock-v3', 'honcho-wall-clock-v5'])
def test_clock_audit_keeps_versioned_hash_and_v3_overhead_semantics(tmp_path, declared):
    from pacbench.followups import save
    from pacbench.provider_clock import LEGACY_LIBRARY_SHA256
    now = datetime.now(timezone.utc)
    later = now + timedelta(minutes=10)
    expected_hash = LIBRARY_SHA256 if declared == 'honcho-wall-clock-v5' else LEGACY_LIBRARY_SHA256
    config = {'version': declared, 'library_sha256': expected_hash, 'wall_speed': 1,
        'monotonic': 'real', 'gateway_clock': 'real', 'source_timestamps': 'unchanged', 'native_batching': 'unchanged'}
    startup = {'version': declared, 'library_sha256': expected_hash,
        'provider_at': now.isoformat(), 'api_date': now.isoformat(), 'db_at': now.isoformat(),
        'real_at': now.isoformat(), 'monotonic': 'real'}
    if declared==clock.VERSION:
        config.update(python_clock={'os_clock':'real','tls_clock':'real'},processes={})
        startup['python_adoptions']={}
    save(tmp_path/'provider-clock.json', config)
    save(tmp_path/'provider-clock-startup.json', startup)
    overhead = {'kind': 'benchmark-overhead', 'version': declared, 'provider_at': now.isoformat(),
        'real_started_at': now.isoformat(), 'real_at': later.isoformat(), 'database_paused': True,
        'db_after': now.isoformat(), 'pre_step': idle_snapshot()}
    (tmp_path/'provider-clock-steps.jsonl').write_text(json.dumps(overhead)+'\n')
    assert clock.audit_evidence(tmp_path, declared) == []
    config['library_sha256'] = LEGACY_LIBRARY_SHA256 if declared == 'honcho-wall-clock-v5' else LIBRARY_SHA256
    startup['library_sha256'] = config['library_sha256']
    save(tmp_path/'provider-clock.json', config)
    save(tmp_path/'provider-clock-startup.json', startup)
    assert clock.audit_evidence(tmp_path, declared)
