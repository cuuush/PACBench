"""Failed model attempts remain separately inspectable after a retry."""

import json
from unittest import mock

import pytest

from pacbench.model_calls import ModelClient


def test_same_request_retry_preserves_each_failed_call(tmp_path):
    client = ModelClient(key="test", model="model", record_dir=tmp_path)
    with mock.patch("pacbench.model_calls.urllib.request.urlopen",
                    side_effect=OSError("offline")):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="audit record"):
                client("Write a scene")
    records = sorted(tmp_path.glob("*.json"))
    assert len(records) == 2
    assert all(json.loads(path.read_text())["status"] == "failed"
               for path in records)
