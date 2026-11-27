"""Real HTTP keepalives must not extend a harness judge's total deadline."""
import http.server
import json
from pathlib import Path
import threading
import time
import urllib.request
from unittest.mock import patch

import pytest
from pacbench import model_calls


def test_real_whitespace_keepalives_stop_at_wall_deadline_and_capture_incomplete_cost(monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.end_headers()
            for _ in range(80):
                try:self.wfile.write(b' ');self.wfile.flush()
                except OSError:break
                time.sleep(.02)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(model_calls,'MODEL_TIMEOUT_S',.2)
    started=time.monotonic()
    try:
        raw=urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/',timeout=1)
        with raw as response:
            with pytest.raises((RuntimeError,TimeoutError)):
                model_calls.read_bounded_response(response,started)
        assert time.monotonic()-started<1
    finally:server.shutdown();server.server_close();thread.join()


def test_proxy_network_timeout_is_not_retried(tmp_path,monkeypatch):
    monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter')
    monkeypatch.setattr('pacbench.gateway_client.validate_route',lambda *a:None)
    client=model_calls.ModelClient(key='inert',model='model',record_dir=tmp_path)
    with patch('pacbench.model_calls.urllib.request.urlopen',side_effect=TimeoutError('uncertain')) as opened:
        with pytest.raises(RuntimeError,match='audit record'):client('Judge this answer')
    assert opened.call_count==1
    assert len(list(tmp_path.glob('*.json')))==1
