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
