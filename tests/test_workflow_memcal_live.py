import pytest

from pacbench.corpus.r2 import compile_week

def test_request_source_verifier_uses_registered_connection(tmp_path):
    from pacbench.workflows.source_plan import SourcePlan
    _, docs, turns, _ = compile_week('continuing-account')
    with SourcePlan(tmp_path, docs, turns) as plan:
        with SourcePlan.from_connection(plan.db, plan.digest) as view:
            assert next(view.iter_turns()) == next(plan.iter_turns())
        with pytest.raises(ValueError, match='differs from the frozen scope'):
            SourcePlan.from_connection(plan.db, 'other')
        assert plan.turn_count == 49

def test_appended_wire_trace_rejects_replacement_and_keeps_only_current_turn(tmp_path):
    from pacbench.workflows.native import appended_rows, trace_position
    path = tmp_path/'wire.jsonl'
    path.write_bytes(b'{"previous":true}\n')
    cursor = trace_position(path)
    with path.open('ab') as stream:
        stream.write(b'{"current":true}\n')
    assert list(appended_rows(path, cursor)) == [{'current':True}]
    path.write_bytes(b'{"previous":null}\n{"current":true}\n')
    with pytest.raises(ValueError, match='boundary changed'):
        list(appended_rows(path, cursor))
