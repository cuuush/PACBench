import json
import pytest
from pacbench.workflow_metrics import summarize


def test_torn_utf8_tail_keeps_prior_stop(tmp_path):
    path = tmp_path/'workflow.jsonl'
    stop = {'phase':'stopped','elapsed_s':5,'error':'NativeFailure','automatic_replay':False}
    prefix = (json.dumps(stop)+'\n').encode()
    tail = b'{"phase":"\xe2'
    path.write_bytes(prefix + tail)
    report = summarize(path)
    assert report['status'] == 'stopped'
    assert report['terminal_event'] == stop
    assert report['incomplete_tail']['offset'] == len(prefix)
    assert path.read_bytes() == prefix + tail
    path.write_bytes(prefix + tail + b'\n' + prefix)
    with pytest.raises(ValueError):
        summarize(path)


def test_document_close_crash_preserves_grade_queue_and_denominator(tmp_path):
    from tests.test_workflow_realism import condition
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import run_current_week
    from pacbench.workflow_progress import WorkflowProgress
    from pacbench.workflow_spec import compile_week
    _, docs, turns, _ = compile_week('continuing-account')
    route = condition(p='reme')

    class Interrupted(RecordingAccount):
        def end_chat(self, session_id):
            raise RuntimeError('ambiguous close')

    dest = tmp_path/'workflow'
    with pytest.raises(RuntimeError, match='ambiguous close'):
        run_current_week(route, Interrupted(route, docs), dest)
    key = json.loads((dest/'run.json').read_text())['run_key']
    with WorkflowProgress(dest, key) as progress:
        turn, surface, activity = progress.get_verified(turns[0].id)
        assert surface['answer'] and activity['driver_receipts']
        assert progress.claim_grade() == (turn, surface)
        assert sum(row['planned_criteria'] for row in progress.summary().values()) == 192
        assert not progress.complete
    with pytest.raises(ValueError, match='resume'):
        run_current_week(route, Interrupted(route, docs), dest, resume=True)
