"""The upgraded Hermes prompt keeps its session start and advances native date."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def test_native_date_refresh_preserves_other_bytes_and_persists(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'vendor/hermes-isolated/agent/turn_context.py'
    tree = ast.parse(source.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_pacbench_refresh_date')
    namespace = {'os': os}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    import agent.system_prompt
    monkeypatch.setattr(agent.system_prompt, '_timestamp_line', lambda agent:
        "Conversation started: Wednesday, September 09, 2026\nToday's date (as of the last context rebuild): Friday, September 11, 2026")
    monkeypatch.setenv('PACBENCH_NOW', '2026-09-11T09:00:00-04:00')
    db = Mock(); agent = SimpleNamespace(_session_db=db, session_id='s', _cached_system_prompt_static='prefix')
    original = 'prefix\nConversation started: Wednesday, September 09, 2026\ntrailer'
    updated = namespace['_pacbench_refresh_date'](agent, original)
    assert updated.replace("\nToday's date (as of the last context rebuild): Friday, September 11, 2026", '') == original
    db.update_system_prompt.assert_called_once_with('s', updated)
    assert agent._cached_system_prompt_static == 'prefix'


def test_session_start_offset_is_stable_across_dst(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import agent.system_prompt as prompt
    import hermes_time

    monkeypatch.setenv('PACBENCH_SESSION_STARTED_AT', '2026-09-09T09:00:00-04:00')
    monkeypatch.setenv('PACBENCH_NOW', '2026-09-09T09:00:00-04:00')
    monkeypatch.setattr(hermes_time, 'get_timezone', lambda: ZoneInfo('America/New_York'))
    monkeypatch.setattr(hermes_time, 'now', lambda: datetime.fromisoformat(os.environ['PACBENCH_NOW']))
    agent = SimpleNamespace(pass_session_id=False, model='', provider='', platform='')
    september = prompt._timestamp_line(agent)
    monkeypatch.setenv('PACBENCH_NOW', '2026-12-12T09:00:00-05:00')
    december = prompt._timestamp_line(agent)
    start_before = september.splitlines()[0]
    start_after = december.splitlines()[0]
    assert start_before == start_after
    assert 'September 09, 2026' in start_after and 'UTC-04:00' in start_after
    assert 'UTC-05:00' not in start_after
    assert "Today's date (as of the last context rebuild): Saturday, December 12, 2026" in december
