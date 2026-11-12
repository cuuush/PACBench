"""Synthetic redirect endpoints must never receive authenticated requests."""
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pacbench.gateway.ledger import Ledger, MODEL, RESERVATION
from pacbench.gateway.server import Server
from pacbench.gateway.openrouter_reconcile import fetch_generation, retry_due


@contextmanager
def run_server(handler):
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield server
    finally: server.shutdown();server.server_close();thread.join(timeout=5)


def test_upstream_redirect_cannot_forward_key_or_prompt(tmp_path):
    received=[]
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self): received.append(self.headers);self.send_response(200);self.end_headers()
        def do_GET(self): received.append(self.headers);self.send_response(200);self.end_headers()
        def log_message(self,*args): pass
    with run_server(Receiver) as destination:
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307);self.send_header('Location',f'http://127.0.0.1:{destination.server_port}/stolen');self.end_headers()
            def log_message(self,*args): pass
        with run_server(Redirect) as origin:
            ledger=Ledger(tmp_path/'ledger.db')
            server=Server(('127.0.0.1',0),ledger,'local-key','upstream-key',upstream=f'http://127.0.0.1:{origin.server_port}')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/p/test/v1/responses',
                    data=json.dumps({'model':MODEL,'input':'private prompt'}).encode(),headers={'Authorization':'Bearer local-key'})
                try: urllib.request.urlopen(req)
                except urllib.error.HTTPError as exc: assert exc.code==307
            finally:server.shutdown();server.server_close();thread.join(timeout=5)
    assert not received
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['state']=='unknown' and row['accounted']==RESERVATION
    assert 'private' not in json.dumps(row) and 'upstream-key' not in json.dumps(row)


def test_generation_redirect_cannot_forward_key_and_retries(tmp_path,monkeypatch):
    from pacbench.gateway import openrouter_reconcile
    received=[]
    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self): received.append(self.headers);self.send_response(200);self.end_headers()
        def log_message(self,*args): pass
    with run_server(Receiver) as destination:
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{destination.server_port}/stolen');self.end_headers()
            def log_message(self,*args): pass
        with run_server(Redirect) as origin:
            monkeypatch.setattr(openrouter_reconcile,'_URL',f'http://127.0.0.1:{origin.server_port}/generation')
            ledger=Ledger(tmp_path/'ledger.db');rid=ledger.reserve('p','responses');ledger.finish(rid)
            with ledger.connect() as db:db.execute("UPDATE calls SET upstream_origin='openrouter',generation_id='gen-abc' WHERE id=?",(rid,))
            assert retry_due(ledger,lambda generation_id:fetch_generation(generation_id,'private-key'))=={rid:'lookup_failed'}
    assert not received
    with ledger.connect() as db: row=dict(db.execute('SELECT * FROM calls').fetchone())
    assert row['accounted']==RESERVATION and row['actual_cost_nano'] is None
    assert row['reconcile_status']=='lookup_failed' and 'private-key' not in json.dumps(row)
