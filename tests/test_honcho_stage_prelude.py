from datetime import datetime
from types import SimpleNamespace


def test_inside_startup_prelude_does_not_create_a_second_scripted_deadline(tmp_path,monkeypatch):
    from provider_adapters import honcho_clock as c
    clock=SimpleNamespace(now=lambda:datetime.fromisoformat('2026-10-06T08:59:10-04:00'))
    monkeypatch.setattr(c,'load',lambda root:(clock,{}))
    def forbidden(*a,**kw):raise AssertionError('An ephemeral now value must not become a deadline')
    monkeypatch.setattr(c,'before_turn',forbidden)
    monkeypatch.setattr(c,'python_environment',lambda clock,env:{**env,'clock':'native'})
    assert c.stage_turn(tmp_path,'2026-10-06T09:00:00-04:00',{})=={'clock':'native'}


def setup_wait(monkeypatch,tmp_path,after):
    from provider_adapters import honcho_clock as c
    state={'now':datetime.fromisoformat('2026-10-06T09:01:38-04:00'),'steps':[]}
    clock=SimpleNamespace(now=lambda:state['now'])
    monkeypatch.setattr(c,'enabled',lambda _:True)
    monkeypatch.setattr(c,'load',lambda _:(clock,{}))
    monkeypatch.setattr(c,'python_environment',lambda *a:{'unchanged':'clock'})
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    monkeypatch.setattr(c,'snapshot',lambda _:{'pending':0})
    def busy(root,target,**kw):
        state['steps'].append((target,kw));state['now']=datetime.fromisoformat(after);return False
    monkeypatch.setattr(c,'step_if_idle',busy);monkeypatch.setattr(c.time,'sleep',lambda _:None)
    return c,state


def test_crossing_soft_prelude_does_not_reject_an_upcoming_actual_question(tmp_path,monkeypatch):
    c,state=setup_wait(monkeypatch,tmp_path,'2026-10-06T09:09:06-04:00')
    assert c.stage_turn(tmp_path,'2026-10-06T09:10:00-04:00',{})=={'unchanged':'clock'}
    assert len(state['steps'])==1 and state['steps'][0][0].isoformat()=='2026-10-06T09:09:00-04:00'
    assert state['now'].isoformat()=='2026-10-06T09:09:06-04:00'


def test_crossing_actual_question_still_fails_closed(tmp_path,monkeypatch):
    import pytest
    c,_=setup_wait(monkeypatch,tmp_path,'2026-10-06T09:10:00-04:00')
    with pytest.raises(RuntimeError,match='future-time exposure'):c.stage_turn(tmp_path,'2026-10-06T09:10:00-04:00',{})


def test_regular_hard_deadline_is_unchanged(tmp_path,monkeypatch):
    import pytest
    c,_=setup_wait(monkeypatch,tmp_path,'2026-10-06T09:09:06-04:00')
    with pytest.raises(RuntimeError,match='future-time exposure'):c.before_turn(tmp_path,'2026-10-06T09:09:00-04:00',{})


def test_prelude_does_not_bypass_stop_or_spending_boundary(tmp_path,monkeypatch):
    import pytest
    from pacbench.process_cleanup import OperatorStop
    c,_=setup_wait(monkeypatch,tmp_path,'2026-10-06T09:09:06-04:00')
    def stop(*a,**kw):raise OperatorStop()
    monkeypatch.setattr('pacbench.process_cleanup.check_stop',stop)
    with pytest.raises(OperatorStop):c.stage_turn(tmp_path,'2026-10-06T09:10:00-04:00',{})
    monkeypatch.setattr('pacbench.process_cleanup.check_stop',lambda *a,**kw:None)
    def denied():raise RuntimeError('budget stopped')
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',denied)
    with pytest.raises(RuntimeError,match='budget stopped'):c.stage_turn(tmp_path,'2026-10-06T09:10:00-04:00',{})
