"""Native completion belongs to each captured side, session and exact task."""
import hashlib
import json
from pathlib import Path
import pytest
from pacbench import terminal_processing as terminal


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


def turn(root,relative='turns/check/turn.json',session='native-session',answer='Seven.',at='2026-09-11T09:00:00-04:00'):
    write(root/relative,{'prompt':'When?','answer':answer,'session_id':session,'at':at})


def archive(root,session='native-session',task='task-one',status='completed',ids=('u','a')):
    directory=root/'openviking-data/viking/default/user/default/sessions'/session/'history/archive_001'
    directory.mkdir(parents=True,exist_ok=True)
    rows=[{'id':mid,'role':role,'parts':[{'type':'text','text':text}],'created_at':stamp}
          for mid,role,text,stamp in [(ids[0],'user','When?',1),(ids[1],'assistant','Seven.',2)]]
    raw=b''.join((json.dumps(r)+'\n').encode() for r in rows)
    (directory/'messages.jsonl').write_bytes(raw)
    write(directory/'.meta.json',{'phase1':{'status':'ready','messages_sha256':hashlib.sha256(raw).hexdigest(),
        'queue_message':{'task_id':task,'session_id':session},'archived_message_ids':list(ids)}})
    value={'task_id':task,'task_type':'session_commit','resource_id':session,'status':status}
    write(root/'openviking-data/viking/default/_system/tasks/default'/(task+'.json'),value)
    return value


def test_completed_both_sides_bind_to_exact_task_and_audit_survives_copy(tmp_path):
    turn(tmp_path);task=archive(tmp_path)
    scope=terminal.scope_for(tmp_path)
    snap=terminal.capture_snapshot(tmp_path,scope,lambda _:task)
    assert snap['state']=='complete' and snap['captures'][0]['captured_ids']==['u','a']
    task['status']='running'
    assert terminal.capture_snapshot(tmp_path,scope,lambda _:task)['state']=='pending'
    task['status']='cancelled'
    assert terminal.capture_snapshot(tmp_path,scope,lambda _:task)['state']=='native_failed'


def test_recovery_is_an_executed_turn_and_missing_session_is_not_discarded(tmp_path):
    turn(tmp_path);turn(tmp_path,'turns/check/followups/combined/turn.json',answer='At seven.')
    scope=terminal.scope_for(tmp_path)
    assert len(scope['turns'])==2 and scope['turns'][0]['file']=='turns/check/turn.json'
    turn(tmp_path,'turns/failed/turn.json',session=None)
    with pytest.raises(ValueError,match='session identity'):terminal.scope_for(tmp_path)


def test_completed_earlier_archive_does_not_cover_new_recovery(tmp_path):
    turn(tmp_path);turn(tmp_path,'turns/check/followups/combined/turn.json',answer='At seven.')
    task=archive(tmp_path)
    snap=terminal.capture_snapshot(tmp_path,terminal.scope_for(tmp_path),lambda _:task)
    assert snap['state']=='pending' and snap['captures'][0]['processed']
    assert not snap['captures'][1]['both_sides_captured']


def test_same_message_ids_in_other_session_do_not_prove_processing(tmp_path):
    turn(tmp_path,session='a-session');turn(tmp_path,'turns/second/turn.json',session='b-session')
    tasks={'task-a':archive(tmp_path,'a-session','task-a'),
           'task-b':archive(tmp_path,'b-session','task-b','running')}
    snap=terminal.capture_snapshot(tmp_path,terminal.scope_for(tmp_path),tasks.__getitem__)
    assert snap['state']=='pending'
    assert [r['processed'] for r in snap['captures']]==[True,False]
    assert snap==terminal.capture_snapshot(tmp_path,terminal.scope_for(tmp_path),tasks.__getitem__)


def test_task_reused_across_sessions_is_rejected(tmp_path):
    turn(tmp_path,session='a-session');turn(tmp_path,'turns/second/turn.json',session='b-session')
    archive(tmp_path,'a-session');archive(tmp_path,'b-session')
    with pytest.raises(ValueError,match='inconsistent archive'):
        terminal.capture_snapshot(tmp_path,terminal.scope_for(tmp_path),lambda _:None)


ENV={'OPENVIKING_ENDPOINT':'http://127.0.0.1:12345','OPENVIKING_ACCOUNT':'default','OPENVIKING_USER':'default'}


def test_scope_failure_is_persisted_as_unknown(tmp_path):
    turn(tmp_path,session=None)
    result=terminal.observe_before_stop(tmp_path,ENV)
    assert result['state']=='observation_unknown' and result['error_type']=='ValueError'
    assert (tmp_path/'terminal-processing/status.json').exists()


def test_deadline_cannot_accept_late_completed_snapshot(tmp_path,monkeypatch):
    turn(tmp_path);now=[0]
    monkeypatch.setattr(terminal.time,'monotonic',lambda:now[0])
    def late(*a,**kw):
        now[0]=11
        return {'state':'complete'}
    monkeypatch.setattr(terminal,'capture_snapshot',late)
    result=terminal.observe_before_stop(tmp_path,ENV,timeout_s=10)
    assert result['state']=='deadline_pending'


def test_boundary_stop_prevents_status_queries(tmp_path,monkeypatch):
    from pacbench.stop import OperatorStop
    turn(tmp_path)
    monkeypatch.setattr(terminal.urllib.request,'urlopen',lambda *a,**k:pytest.fail('query after stop'))
    def stop():raise OperatorStop('stop')
    result=terminal.observe_before_stop(tmp_path,ENV,boundary=stop)
    assert result['state']=='stopped_by_boundary' and result['error_type']=='OperatorStop'


def test_native_scope_and_archive_hash_tampering_cannot_audit(tmp_path,monkeypatch):
    turn(tmp_path);task=archive(tmp_path)
    scope=terminal.scope_for(tmp_path)
    snapshot=terminal.capture_snapshot(tmp_path,scope,lambda _:task)
    write(tmp_path/'terminal-processing/scope.json',scope)
    write(tmp_path/'terminal-processing/status.json',{'protocol':terminal.VERSION,'scope_hash':terminal.digest(scope),
        'state':'complete','clock_advanced':False,'processing_forced':False,'observations':[snapshot],
        'namespace':{'account':'default','user':'default'}})
    assert terminal.audit_terminal(tmp_path)==[]
    turn(tmp_path,answer='Eight.')
    assert terminal.audit_terminal(tmp_path)
