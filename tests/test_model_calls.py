"""Failed model attempts remain separately inspectable after a retry."""

import json
import io
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from pacbench.authoring import AuthoringError, ScenePlan, realize_scene
from pacbench.model_calls import ModelClient, ResponsesWriter, public_record


@pytest.mark.parametrize('status,kind',[(401,'upstream_error'),(403,'upstream_error'),(503,'upstream_auth_blocked')])
def test_auth_failure_is_terminal_without_retry_and_keeps_audit(tmp_path,status,kind):
    from pacbench.gateway_client import ModelAccessBlocked
    client=ModelClient(key='test',model='model',record_dir=tmp_path)
    error=urllib.error.HTTPError(client.url,status,'Blocked',{},
                                io.BytesIO(json.dumps({'error':{'type':kind}}).encode()))
    with mock.patch('pacbench.model_calls.urllib.request.urlopen',side_effect=error) as call, mock.patch(
            'pacbench.model_calls.time.sleep') as sleep:
        with pytest.raises(ModelAccessBlocked):client('Check the source')
    assert call.call_count==1
    sleep.assert_not_called()
    records=list(tmp_path.glob('*.json'))
    assert len(records)==1 and json.loads(records[0].read_text())['status']=='failed'


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
        return json.dumps({'facts': [], "supported": supported, "evidence": [1, 2],
                           "problem": "" if supported else "not agreed"})

    plan = ScenePlan(scene="Devon and I agree on pizza Friday",
                     at="2026-09-10T18:00:00")
    with mock.patch("pacbench.model_calls.urllib.request.urlopen", side_effect=urlopen):
        realized = realize_scene(plan, writer, verify)
    assert realized.record.attempts == 2
    assert realized.lines[-1] == ("Me", "Yes, let's do it.")
    assert requests[0]["store"] is False
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert requests[0]['reasoning']['effort'] == 'low'
    assert requests[1]["input"][1]["encrypted_content"] == "sealed-first"
    assert "not agreed" in requests[1]["input"][-1]["content"][0]["text"]
    saved = [json.loads(path.read_text()) for path in (tmp_path / "private").glob("*.json")]
    assert any(row["response"]["output"][0]["encrypted_content"] == "sealed-first"
               for row in saved)
    records = list(tmp_path.glob("*.json")) + list((tmp_path / "private").glob("*.json"))
    assert records and all(path.stat().st_mode & 0o077 == 0 for path in records)
    public = [path.read_text() for path in tmp_path.glob("*.json")]
    assert all("sealed-first" not in row and "sealed-second" not in row for row in public)
    # The existing public request still reuses its private record without a call.
    with mock.patch("pacbench.model_calls.urllib.request.urlopen") as call:
        replay = writer(requests[0]["input"][0]["content"][0]["text"])
        call.assert_not_called()
    assert replay.record_path == realized.record.generation_paths[0]
    with mock.patch("pacbench.model_calls.urllib.request.urlopen") as call:
        repaired = writer.continue_from(replay, requests[0]["input"][0]["content"][0]["text"],
                                       requests[1]["input"][-1]["content"][0]["text"])
        call.assert_not_called()
    assert repaired.record_path == realized.record.generation_paths[1]
    # A portable checkout must not send the redacted placeholder upstream.
    (tmp_path / "private" / Path(replay.record_path).name).unlink()
    with mock.patch("pacbench.model_calls.urllib.request.urlopen") as call:
        with pytest.raises(ValueError, match="requires private encrypted reasoning"):
            writer.continue_from(replay, "Initial prompt", "Try again")
        call.assert_not_called()


def test_explorer_projection_hides_replay_blob():
    raw = {"request": {"input": [{"encrypted_content": "prior-opaque"}]},
           "response": {"output": [{"encrypted_content": "new-opaque",
                                    "summary": [{"text": "A short summary."}]}]}}
    projected = public_record(raw)
    assert "prior-opaque" not in json.dumps(projected)
    assert "new-opaque" not in json.dumps(projected)
    assert projected["response"]["output"][0]["summary"][0]["text"] == "A short summary."


def test_two_rejected_drafts_stop_without_a_third_model_call(tmp_path):
    prompts = []

    def writer(prompt):
        prompts.append(prompt)
        return "Devon: Pizza Friday?\nMe: Maybe."

    def verifier(_):
        return json.dumps({'facts': [], "supported": False, "evidence": [1, 2],
                           "problem": "not agreed"})

    with pytest.raises(AuthoringError, match="failed after re-render"):
        realize_scene(ScenePlan(scene="Devon and I agree on pizza Friday",
                                at="2026-09-10T18:00:00"),
                      writer, verifier, record_dir=tmp_path)
    assert len(prompts) == 2
    assert "not agreed" in prompts[1]
    records = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(records) == 2
    assert all(record["passed"] is False for record in records)


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


import pytest as _pytest
@_pytest.fixture(autouse=True)
def fake_gateway_identity(tmp_path, monkeypatch):
    from pacbench import gateway_client
    state = tmp_path / 'gateway'
    state.mkdir()
    (state / 'client-token').write_text('test' if __name__.endswith('test_model_calls') else 'TEST-GATEWAY')
    (state / 'address.json').write_text(json.dumps({'base':'http://127.0.0.1:8099'}))
    monkeypatch.setattr(gateway_client, 'STATE', state)


def test_gateway_budget_refusal_is_terminal_without_transport_retry(tmp_path):
    import io
    import urllib.error
    refusal=urllib.error.HTTPError('http://gateway',429,'Too Many Requests',{},
        io.BytesIO(b'{"error":{"type":"budget_refused","message":"daily limit"}}'))
    client=ModelClient(key='test',model='spark',record_dir=tmp_path)
    with mock.patch('pacbench.model_calls.urllib.request.urlopen',side_effect=refusal) as call, \
         mock.patch('pacbench.model_calls.time.sleep') as sleep:
        with pytest.raises(RuntimeError,match='HTTP 429'):client('Write a scene')
    assert call.call_count==1 and not sleep.called
    assert len(list(tmp_path.glob('*.json')))==1


@pytest.mark.parametrize('body', [b'         \n\n', b'{"unexpected":"private upstream detail"}',
                                 b'[{"value":1}]', b'42', b'null'])
def test_direct_invalid_reply_keeps_private_bytes_and_safe_headers_without_retry(tmp_path,monkeypatch,body):
    import hashlib
    import stat
    from email.message import Message
    from pacbench import model_calls
    monkeypatch.setenv('PACBENCH_DIRECT','1')
    monkeypatch.setattr('pacbench.gateway_client.validate_route',lambda *a:None)
    class Response:
        status=200
        def __init__(self):
            self.stream=io.BytesIO(body);self.headers=Message()
            for key,value in [('Content-Type','application/json'),('X-Request-ID','correlation-only'),
                ('X-Generation-ID','gen-observed-header'),('Authorization','SECRET_AUTH'),
                ('Set-Cookie','SECRET_COOKIE')]:self.headers[key]=value
        def read1(self,*args):return self.stream.read(*args)
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close()
    client=ModelClient(key='inert',model='model',record_dir=tmp_path)
    with mock.patch('pacbench.model_calls.urllib.request.urlopen',return_value=Response()) as opened:
        with pytest.raises(RuntimeError,match='audit record'):client('Judge the unchanged answer')
    assert opened.call_count==1
    records=list(tmp_path.glob('*.json'));assert len(records)==1
    value=json.loads(records[0].read_text());assert value['status']=='failed' and value['cost_usd'] is None
    metadata=value['direct_response']
    assert metadata['http_status']==200 and metadata['body_sha256']==hashlib.sha256(body).hexdigest()
    assert metadata['body_bytes']==len(body)
    assert metadata['headers']=={'content-type':'application/json','x-request-id':'correlation-only','x-generation-id':'gen-observed-header'}
    assert 'SECRET_AUTH' not in records[0].read_text() and 'SECRET_COOKIE' not in records[0].read_text()
    raw=tmp_path/value['failed_response_body']['relative_path']
    assert raw.read_bytes()==body and stat.S_IMODE(raw.stat().st_mode)==0o600
    assert value['failed_response_body']['sha256']==metadata['body_sha256']
    assert not list(tmp_path.glob('*.bin'))


def test_direct_success_records_correlation_but_never_copies_raw_body(tmp_path,monkeypatch):
    from email.message import Message
    monkeypatch.setenv('PACBENCH_DIRECT','1')
    monkeypatch.setattr('pacbench.gateway_client.validate_route',lambda *a:None)
    body=json.dumps({'id':'gen-response','choices':[{'message':{'content':'complete'}}],
                     'usage':{'prompt_tokens':2,'completion_tokens':1,'cost':0.001}}).encode()
    class Response:
        status=200
        headers=Message()
        def __init__(self):self.stream=io.BytesIO(body)
        def read1(self,*args):return self.stream.read(*args)
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close()
    with mock.patch('pacbench.model_calls.urllib.request.urlopen',return_value=Response()):
        output=ModelClient(key='inert',model='model',record_dir=tmp_path)('Judge the answer')
    value=json.loads(Path(output.record_path).read_text())
    assert output.text=='complete' and value['response']['id']=='gen-response'
    assert value['direct_response']['body_bytes']==len(body)
    assert 'failed_response_body' not in value and not (tmp_path/'private').exists()
