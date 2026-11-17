"""Loopback-only clock/TLS integration: synthetic credentials and fixture ledgers."""
import concurrent.futures
import datetime as dt
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
pytest.importorskip("cryptography")
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pacbench.gateway.ledger import Ledger, MODEL
from pacbench.gateway.server import Server

LIB = Path(os.environ.get('PACBENCH_FAKETIME_LIB', '/workspace/scratch/tls-clock-proxy/runtime/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1'))
CLIENT = r'''import http.client,json,sys,time
c=http.client.HTTPConnection('127.0.0.1',int(sys.argv[1]),timeout=8)
c.request('POST','/p/test/v1/chat/completions',json.dumps({'model':'muse-spark-1.3-contributor','stream':True}),{'Authorization':'Bearer local-test','Content-Type':'application/json'})
r=c.getresponse(); first=r.readline(); first_at=time.monotonic(); rest=r.read(); print(json.dumps({'status':r.status,'first':first.decode(),'rest':rest.decode(),'first_at':first_at,'end':time.monotonic(),'clock':time.strftime('%Y-%m-%d',time.gmtime())}))'''

def make_cert(tmp, host, issuer=None, expired=False):
    now=dt.datetime.now(dt.timezone.utc); key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,host)])
    ca=issuer is None
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(issuer[0].subject if issuer else subject)
      .public_key(key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(now-dt.timedelta(days=10 if expired else 1)).not_valid_after(now+dt.timedelta(days=-2 if expired else 2))
      .add_extension(x509.BasicConstraints(ca=ca,path_length=None),critical=True))
    cert=cert.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
    cert=cert.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer[1] if issuer else key).public_key()),critical=False)
    cert=cert.add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=not ca,data_encipherment=False,key_agreement=False,key_cert_sign=ca,crl_sign=ca,encipher_only=False,decipher_only=False),critical=True)
    if not ca: cert=cert.add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),critical=False)
    cert=cert.sign(issuer[1] if issuer else key,hashes.SHA256())
    prefix=host+('-expired' if expired else '')+('-'+str(id(issuer)) if issuer else '')
    cert_path=tmp/(prefix+'.crt'); key_path=tmp/(prefix+'.key')
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    return cert,key,cert_path,key_path

class Upstream(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        with self.server.guard:
            self.server.active+=1; self.server.maximum=max(self.server.maximum,self.server.active)
        try:
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"delta":"first"}\n\n'); self.wfile.flush()
            time.sleep(1)
            self.wfile.write(b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\ndata: [DONE]\n\n'); self.wfile.flush()
        finally:
            with self.server.guard: self.server.active-=1

def run_server(server):
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); return thread

def close(server,thread):
    server.shutdown(); server.server_close(); thread.join(timeout=5)

def fake_client(port):
    env=os.environ.copy(); env.update(LD_PRELOAD=str(LIB),FAKETIME='2020-01-01 12:00:00',FAKETIME_DONT_FAKE_MONOTONIC='1')
    p=subprocess.run([sys.executable,'-c',CLIENT,str(port)],env=env,capture_output=True,text=True,timeout=10,check=True)
    return json.loads(p.stdout)

def post(port,path='/p/test/v1/chat/completions',key='local-test'):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    c.request('POST',path,json.dumps({'model':MODEL}),{'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    r=c.getresponse(); status=r.status; r.read(); c.close(); return status

@pytest.mark.skipif(not LIB.is_file(),reason='requires hash-verified pinned libfaketime fixture')
def test_real_gateway_fake_client_tls_streaming_and_controls(tmp_path,monkeypatch):
    ca=make_cert(tmp_path,'test-ca'); other=make_cert(tmp_path,'other-ca')
    good=make_cert(tmp_path,'localhost',ca); expired=make_cert(tmp_path,'localhost',ca,True); untrusted=make_cert(tmp_path,'localhost',other)
    monkeypatch.setenv('SSL_CERT_FILE',str(ca[2]))
    for variable in ('NO_PROXY','no_proxy'):
        monkeypatch.setenv(variable, ','.join(filter(None,(os.environ.get(variable,''),'localhost','127.0.0.1'))))
    def start_target(pair):
        s=ThreadingHTTPServer(('127.0.0.1',0),Upstream); s.guard=threading.Lock(); s.active=s.maximum=0
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(pair[2],pair[3]); s.socket=ctx.wrap_socket(s.socket,server_side=True)
        return s,run_server(s)
    target,tt=start_target(good)
    # This database is a test fixture in pytest's temporary directory, never private gateway state.
    gateway=Server(('127.0.0.1',0),Ledger(tmp_path/'fixture-ledger.db'),'local-test','synthetic-upstream',upstream=f'https://localhost:{target.server_port}/v1')
    gt=run_server(gateway)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: results=list(pool.map(lambda _:fake_client(gateway.server_port),range(3)))
        assert all(r['status']==200 and r['clock']=='2020-01-01' and r['first']=='data: {"delta":"first"}\n' and 'data: [DONE]' in r['rest'] for r in results), results
        assert all(r['end']-r['first_at']>.5 for r in results)
        assert target.maximum >= 2
        assert post(gateway.server_port,key='wrong')==401
        assert post(gateway.server_port,path='/p/test/v1/arbitrary-destination')==404
        for pair,host in ((good,'127.0.0.1'),(expired,'localhost'),(untrusted,'localhost')):
            s,t=start_target(pair)
            try:
                gateway.upstream=f'https://{host}:{s.server_port}/v1'
                assert post(gateway.server_port)==502
            finally: close(s,t)
    finally: close(gateway,gt); close(target,tt)
