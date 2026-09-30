"""Failed model attempts remain separately inspectable after a retry."""

import json
from unittest import mock

import pytest

from pacbench.authoring import ScenePlan, realize_scene
from pacbench.model_calls import ModelClient, ResponsesWriter
from scripts.build_explorer_data import model_record_for_dashboard


def test_same_request_retry_preserves_each_failed_call(tmp_path):
    client = ModelClient(key="test", model="model", record_dir=tmp_path)
    with mock.patch("pacbench.model_calls.urllib.request.urlopen",
                    side_effect=OSError("offline")):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="audit record"):
                client("Write a scene")
    records = sorted(tmp_path.glob("*.json"))
    assert len(records) == 4
    assert all(json.loads(path.read_text())["status"] == "failed"
               for path in records)


def test_transient_disconnect_retries_same_request_and_keeps_both_attempts(tmp_path):
    stream = mock.MagicMock()
    stream.read.return_value = json.dumps({
        "choices": [{"message": {"content": "supported"}}],
        "usage": {"total_tokens": 12}}).encode()
    stream.__enter__.return_value = stream
    requests = []

    def urlopen(request, timeout):
        requests.append(request.data)
        if len(requests) == 1:
            raise ConnectionResetError("peer closed connection")
        return stream

    client = ModelClient(key="test", model="model", record_dir=tmp_path)
    with mock.patch("pacbench.model_calls.urllib.request.urlopen", side_effect=urlopen), \
         mock.patch("pacbench.model_calls.time.sleep"):
        output = client("Check pizza")
    assert output.text == "supported"
    assert requests[0] == requests[1]
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert sorted(r["status"] for r in records) == ["failed", "ok"]


def test_daily_model_requests_json_with_low_reasoning(tmp_path):
    stream = mock.MagicMock()
    stream.read.return_value = json.dumps({
        "choices": [{"message": {"content": '{"approved":true}'}}]}).encode()
    stream.__enter__.return_value = stream
    requests = []

    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return stream

    client = ModelClient(key="test", model="spark", record_dir=tmp_path,
                         reasoning_effort="low", json_mode=True)
    with mock.patch("pacbench.model_calls.urllib.request.urlopen", side_effect=urlopen):
        client("Review a daily contract")
    assert requests[0]["reasoning_effort"] == "low"
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_rejected_pizza_draft_replays_encrypted_reasoning(tmp_path):
    def response(dialogue, blob):
        payload = {"dialogue": dialogue, "writer_note": "The exchange starts casually."}
        raw = {"status": "completed", "model": "spark", "usage": {"total_tokens": 80},
               "output": [{"type": "reasoning", "summary": [],
                           "encrypted_content": blob},
                          {"type": "message", "role": "assistant", "content": [
                              {"type": "output_text", "text": json.dumps(payload)}]}]}
        stream = mock.MagicMock()
        stream.read.return_value = json.dumps(raw).encode()
        stream.__enter__.return_value = stream
        return stream

    requests = []
    outputs = iter([response("Devon: Pizza Friday?\nMe: Maybe.", "sealed-first"),
                    response("Devon: Pizza Friday?\nMe: Yes, let's do it.", "sealed-second")])

    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return next(outputs)

    writer = ResponsesWriter(key="test", model="spark", record_dir=tmp_path)
    verdicts = iter([False, True])

    def verify(_prompt):
        supported = next(verdicts)
        return json.dumps({"supported": supported, "evidence": [1, 2],
                           "problem": "" if supported else "not agreed"})

    plan = ScenePlan(scene="Devon and I agree on pizza Friday",
                     at="2026-09-10T18:00:00")
    with mock.patch("pacbench.model_calls.urllib.request.urlopen", side_effect=urlopen):
        realized = realize_scene(plan, writer, verify)
    assert realized.record.attempts == 2
    assert realized.lines[-1] == ("Me", "Yes, let's do it.")
    assert requests[0]["store"] is False
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert requests[1]["input"][1]["encrypted_content"] == "sealed-first"
    assert "not agreed" in requests[1]["input"][-1]["content"][0]["text"]
    saved = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert any(row["response"]["output"][0]["encrypted_content"] == "sealed-first"
               for row in saved)
    assert all(path.stat().st_mode & 0o077 == 0 for path in tmp_path.glob("*.json"))


def test_explorer_projection_hides_replay_blob():
    raw = {"request": {"input": [{"encrypted_content": "prior-opaque"}]},
           "response": {"output": [{"encrypted_content": "new-opaque",
                                    "summary": [{"text": "A short summary."}]}]}}
    projected = model_record_for_dashboard(raw)
    assert "prior-opaque" not in json.dumps(projected)
    assert "new-opaque" not in json.dumps(projected)
    assert projected["response"]["output"][0]["summary"][0]["text"] == "A short summary."


def test_incomplete_responses_writer_call_keeps_failed_audit(tmp_path):
    raw = {"status": "incomplete", "output": [],
           "usage": {"input_tokens": 10, "output_tokens": 192}}
    stream = mock.MagicMock()
    stream.read.return_value = json.dumps(raw).encode()
    stream.__enter__.return_value = stream
    writer = ResponsesWriter(key="test", model="spark", record_dir=tmp_path)
    with mock.patch("pacbench.model_calls.urllib.request.urlopen", return_value=stream):
        with pytest.raises(RuntimeError, match="audit record"):
            writer("Write pizza messages")
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert saved["response"] == raw
