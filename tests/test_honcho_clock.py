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
        {'observer':{'peer_id':'hermes'},'messages':[{'id':'n1'}]}], {})
    assert result['ready'] is False
    assert result['message_witnesses'][0]['has_error'] is True
    assert result['processing_status'][-1]['native_queue_errors'][0]['error_class'] == 'ValueError'


def test_scripted_checkpoint_cannot_use_already_later_provider_time(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    provider=ProviderClock(tmp_path/'clock',now+timedelta(minutes=30))
    monkeypatch.setattr(clock,'enabled',lambda r:True)
    monkeypatch.setattr(clock,'load',lambda r:(provider,{}))
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    with pytest.raises(RuntimeError,match='future-time exposure'):
        clock.before_turn(tmp_path,(now+timedelta(minutes=10)).isoformat(),{})


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
    assert env['FAKETIME_DONT_FAKE_MONOTONIC']=='1'
    with pytest.raises(RuntimeError,match='checkpoint'):
        clock.before_recovery(tmp_path,(now-timedelta(minutes=1)).isoformat(),{
            'PACBENCH_CLOCK_NEXT_CHECKPOINT':(now-timedelta(seconds=1)).isoformat()})


def test_offline_clock_audit_rejects_missing_or_unsafe_evidence(tmp_path):
    from pacbench.followups import save
    target=datetime.now(timezone.utc)
    save(tmp_path/'provider-clock.json',{'version':clock.VERSION,'library_sha256':LIBRARY_SHA256,
        'wall_speed':1,'monotonic':'real','gateway_clock':'real',
        'source_timestamps':'unchanged','native_batching':'unchanged'})
    save(tmp_path/'provider-clock-startup.json',{'version':clock.VERSION,'library_sha256':LIBRARY_SHA256,
        'provider_at':target.isoformat(),'api_date':target.isoformat(),'db_at':target.isoformat(),
        'real_at':target.isoformat(),'monotonic':'real'})
    assert clock.audit_evidence(tmp_path,clock.VERSION)
    later=target+timedelta(minutes=30)
    step={'version':clock.VERSION,'from':target.isoformat(),'to':later.isoformat(),
        'target':later.isoformat(),'real_at':target.isoformat(),'db_after':later.isoformat(),
        'pre_step':idle_snapshot(),'horizon':later.isoformat()}
    path=tmp_path/'provider-clock-steps.jsonl'; path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,clock.VERSION)==[]
    step['pre_step']['claims']=1;path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,clock.VERSION)
    step['pre_step']['claims']=0;step['horizon']=target.isoformat()
    path.write_text(json.dumps(step)+'\n')
    assert clock.audit_evidence(tmp_path,clock.VERSION)
