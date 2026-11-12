import io
import json
from unittest.mock import patch

import pytest

from provider_adapters.embedding_health import check


def response(vector):
    return io.BytesIO(json.dumps({"data": [{"embedding": vector}]}).encode())


def test_local_embedding_preflight_checks_actual_dimensions():
    with patch("provider_adapters.embedding_health.urlopen", return_value=response([0.1] * 768)) as call:
        result = check()
    assert result["state"] == "pass"
    assert result["paid_inference"] is False
    request = call.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:11435/v1/embeddings"
    assert "Authorization" not in request.headers


@pytest.mark.parametrize("vector", [[], [0.1], [float("nan")] * 768, [True] * 768])
def test_local_embedding_preflight_rejects_invalid_vectors(vector):
    with patch("provider_adapters.embedding_health.urlopen", return_value=response(vector)):
        with pytest.raises(RuntimeError, match="no provider model initialization"):
            check()


def test_local_embedding_preflight_preserves_private_errors():
    with patch("provider_adapters.embedding_health.urlopen", side_effect=OSError("private detail")):
        with pytest.raises(RuntimeError) as error:
            check()
    assert "private detail" not in str(error.value)


def test_local_embedding_preflight_rejects_remote_endpoint_before_request():
    with patch("provider_adapters.embedding_health.urlopen") as call:
        with pytest.raises(ValueError, match="loopback"):
            check("https://embedding.example/v1")
    call.assert_not_called()


def test_native_ollama_preflight_uses_full_configured_endpoint_and_shape():
    data=io.BytesIO(json.dumps({'embeddings':[[0.1]*768]}).encode())
    with patch('provider_adapters.embedding_health.urlopen',return_value=data) as call:
        result=check('http://127.0.0.1:11435/api/embed',interface='ollama')
    req=call.call_args.args[0]
    assert req.full_url=='http://127.0.0.1:11435/api/embed'
    assert json.loads(req.data)['truncate'] is False
    assert result['interface']=='ollama'
