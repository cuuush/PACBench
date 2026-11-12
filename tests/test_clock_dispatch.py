"""Actual submission/admission times, including delayed writers, are bounded."""
from datetime import datetime,timezone
import pytest
from pacbench import clock_dispatch as clock

AT='2026-09-11T09:00:00-04:00'
ENV={'PACBENCH_NOW':AT,'PACBENCH_CLOCK_NEXT_CHECKPOINT':'2026-09-11T09:01:00-04:00'}

@pytest.mark.parametrize('seconds,allowed',[(-.001,False),(0,True),(5,True),(5.001,False),(60,False)])
def test_actual_submission_window(seconds,allowed):
    value=datetime.fromtimestamp(datetime.fromisoformat(AT).timestamp()+seconds,timezone.utc).isoformat()
    if allowed:assert clock.window({'provider_at':value},ENV)
    else:
        with pytest.raises(RuntimeError):clock.window({'provider_at':value},ENV)


def test_recovery_uses_actual_time_but_never_next_checkpoint_or_next_local_day():
    env={**ENV,'PACBENCH_CLOCK_RECOVERY':'1'}
    assert clock.window({'provider_at':'2026-09-11T09:00:59-04:00'},env)
    with pytest.raises(RuntimeError,match='next scripted'):
        clock.window({'provider_at':ENV['PACBENCH_CLOCK_NEXT_CHECKPOINT']},env)
    del env['PACBENCH_CLOCK_NEXT_CHECKPOINT']
    with pytest.raises(RuntimeError,match='local date'):
        clock.window({'provider_at':'2026-09-12T00:00:00-04:00'},env)


def test_transaction_guard_resamples_after_writer_delay_without_external_effects(monkeypatch):
    monkeypatch.setattr(clock,'active',lambda:True)
    monkeypatch.setattr(clock,'sample',lambda *a,**kw:{'provider_at':'2026-09-11T09:00:06-04:00'})
    monkeypatch.setenv('PACBENCH_NOW',AT)
    monkeypatch.delenv('PACBENCH_CLOCK_RECOVERY',raising=False)
    monkeypatch.setattr(clock,'emit',lambda *a:pytest.fail('Transaction guard must not write witness files'))
    called=[]
    guard=clock.admission_guard(lambda conn:called.append(conn),'s','r',{'text':'unchanged'})
    with pytest.raises(RuntimeError,match='five-second'):guard('connection')
    assert called==['connection']
    clock._pending.clear()


def test_late_committed_acceptance_is_retained_and_does_not_undo_native_work(monkeypatch):
    monkeypatch.setattr(clock,'active',lambda:True)
    monkeypatch.setattr(clock,'sample',lambda *a,**kw:{'provider_at':'2026-09-11T09:00:06-04:00',**kw})
    monkeypatch.setenv('PACBENCH_NOW',AT);monkeypatch.delenv('PACBENCH_CLOCK_RECOVERY',raising=False)
    rows=[];monkeypatch.setattr(clock,'emit',rows.append)
    clock.admitted({'target_session_id':'s','request_id':'r','payload':{'text':'q'},'admission_id':'accepted'})
    assert rows[-1]['admission_id']=='accepted' and rows[-1]['in_window'] is False


def test_native_admission_rechecks_after_waiting_for_its_writer(tmp_path,monkeypatch):
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch,admit_session_input,RuntimeStoreError
    db=SessionDB(tmp_path/'state.db');db.create_session('session','cli')
    epoch=begin_runtime_epoch(db,instance_id='offline-test')
    monkeypatch.setenv('PACBENCH_PYTHON_CLOCK_FILE','offline-no-clock-install')
    monkeypatch.setenv('PACBENCH_NOW',AT)
    monkeypatch.delenv('PACBENCH_CLOCK_RECOVERY',raising=False)
    now={'value':'2026-09-11T09:00:00-04:00'}
    monkeypatch.setattr(clock,'sample',lambda *a,**kw:{'provider_at':now['value']})
    monkeypatch.setattr(clock,'emit',lambda *a:pytest.fail('No transaction-side filesystem effects'))
    original=db._execute_write
    def delayed_write(callback):
        # The previous launch/receive sample was valid; admission now waited.
        now['value']='2026-09-11T09:00:06-04:00'
        return original(callback)
    monkeypatch.setattr(db,'_execute_write',delayed_write)
    try:
        with pytest.raises(RuntimeStoreError,match='pacbench_clock_window_missed'):
            admit_session_input(db,epoch=epoch,principal_id='local',session_id='session',
                request_id='request',payload={'text':'unaltered question'})
        with db._read_ctx() as conn:
            assert conn.execute('SELECT COUNT(*) FROM session_admissions').fetchone()[0]==0
    finally:
        clock._pending.clear();db.close()
