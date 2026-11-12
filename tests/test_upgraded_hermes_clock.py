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
