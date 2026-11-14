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
