"""Explicit route selection must not silently change existing preparations."""

import json

import pytest

from pacbench import prepare as preparation


def test_rejects_unknown_route_before_creating_output(tmp_path):
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='Unknown preparation inference route'):
        preparation.prepare({}, output, inference_route='unexpected')
    assert not output.exists()


def test_resume_refuses_inference_route_change_without_altering_files(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'resolved-spec.json').write_text('{}')
    original = {'inference_route': 'muse-gateway', 'state': 'incomplete'}
    (output / 'experiment-status.json').write_text(json.dumps(original))
    with pytest.raises(ValueError, match='different inference route'):
        preparation.prepare({}, output, resume=True, inference_route='codex-subscription')
    assert json.loads((output / 'experiment-status.json').read_text()) == original
