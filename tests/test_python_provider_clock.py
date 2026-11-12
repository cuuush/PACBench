"""Clock changes must not change TLS, monotonic deadlines or spending dates."""
from datetime import datetime, timedelta, timezone
import http.server
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import threading
import time

import pytest

from pacbench.provider_clock import ProviderClock
from pacbench.python_provider_clock import offset_ns, shared_time_ns, package_metadata


@pytest.mark.parametrize('value',['nan\n','inf\n','1e9\n','-1.0\n','../file','', '1.000000001\n'])
def test_bad_offset_never_becomes_real_time_fallback(value):
    with pytest.raises(ValueError):offset_ns(value)


def test_sample_retries_if_offset_changes_during_suspension():
    values=iter(['-1.000000\n','-2.000000\n','-2.000000\n','-2.000000\n'])
    clock=iter([9_000_000_000,10_000_000_000])
    class Offset:
        def read_text(self,**kwargs):return next(values)
    assert shared_time_ns(Offset(),lambda:next(clock))==8_000_000_000


CHILD = '''
import ctypes,json,sys,time
from datetime import datetime,timezone
from pacbench.direct_accounting import now
libc=ctypes.CDLL(None);libc.time.restype=ctypes.c_long
for line in sys.stdin:
 print(json.dumps({'python_at':datetime.now(timezone.utc).isoformat(),
   'epoch':time.time(),'monotonic':time.monotonic(),'c_epoch':libc.time(None),
   'accounting_at':now()}),flush=True)
'''


def clock_runtime():
    try:package_metadata()
    except (ImportError,RuntimeError):pytest.skip('Verified optional Python clock runtime is not installed')


def test_clock_steps_are_immediate_and_accounting_and_os_stay_real(tmp_path):
    clock_runtime()
    clock=ProviderClock(tmp_path/'clock',datetime(2026,9,10,tzinfo=timezone.utc))
    env=clock.python_environment(os.environ.copy())
    env['PACBENCH_DIRECT_CLOCK']=f'{time.time()},{time.monotonic()}'
    child=subprocess.Popen([sys.executable,'-u','-c',CHILD],env=env,stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def sample():
        child.stdin.write('read\n');child.stdin.flush();line=child.stdout.readline()
        assert line,child.stderr.read()
        return json.loads(line)
    try:
        first=sample();clock.advance(datetime(2026,9,11,tzinfo=timezone.utc));second=sample()
        assert first['python_at'].startswith('2026-09-10') and second['python_at'].startswith('2026-09-11')
        assert second['epoch']-first['epoch']>86000
        assert 0<=second['monotonic']-first['monotonic']<5
        assert abs(second['c_epoch']-time.time())<2
        assert abs((datetime.fromisoformat(second['accounting_at'])-datetime.now(timezone.utc)).total_seconds())<2
        adoption=json.loads((clock.directory/'python-adoptions'/f'{child.pid}.json').read_text())
        assert adoption['tls_clock']==adoption['os_clock']=='real'
        os.kill(child.pid,signal.SIGSTOP)
        def stopped():return Path(f'/proc/{child.pid}/stat').read_text().split(') ')[1][0] in 'Tt'
        deadline=time.monotonic()+3
        while not stopped():
            assert time.monotonic()<deadline;time.sleep(.005)
        frozen=clock.now();time.sleep(.15)
        clock.resume_suspended(frozen,stopped)
        os.kill(child.pid,signal.SIGCONT)
        third=sample()
        assert 0<=(datetime.fromisoformat(third['python_at'])-frozen).total_seconds()<.1
        assert third['monotonic']-second['monotonic']>=.15
    finally:
        if child.poll() is None:
            os.kill(child.pid,signal.SIGCONT);child.terminate()
        child.communicate(timeout=5)


def test_invalid_offset_terminates_clocked_child(tmp_path):
    clock_runtime()
    clock=ProviderClock(tmp_path/'clock',datetime(2026,9,10,tzinfo=timezone.utc))
    clock.path.write_text('nan\n')
    result=subprocess.run([sys.executable,'-c','raise AssertionError("Application must not start")'],
        env=clock.python_environment(os.environ.copy()),capture_output=True,text=True,timeout=10)
    assert result.returncode==78 and 'no inference permitted' in result.stderr


def test_python_clock_validates_real_tls_dates_and_hostname(tmp_path):
    clock_runtime()
    cert=tmp_path/'cert.pem';key=tmp_path/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
        '-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],
        check=True,capture_output=True)
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'ok')
        def log_message(self,*args):pass
    server=http.server.HTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    clock=ProviderClock(tmp_path/'clock',datetime(2026,9,10,tzinfo=timezone.utc))
    script='''
import ssl,sys,json,urllib.request,urllib.error
from datetime import datetime,timezone
ctx=ssl.create_default_context(cafile=sys.argv[1])
with urllib.request.urlopen(sys.argv[2],context=ctx,timeout=5) as response:body=response.read().decode()
try:urllib.request.urlopen(sys.argv[3],context=ctx,timeout=5)
except urllib.error.URLError as error:
 assert isinstance(error.reason,ssl.SSLCertVerificationError)
else:raise AssertionError('Wrong hostname accepted')
print(json.dumps({'body':body,'at':datetime.now(timezone.utc).isoformat(),
 'verify_mode':int(ctx.verify_mode),'check_hostname':ctx.check_hostname}))
'''
    try:
        result=subprocess.run([sys.executable,'-c',script,str(cert),f'https://localhost:{server.server_port}/',
            f'https://127.0.0.1:{server.server_port}/'],env=clock.python_environment(os.environ.copy()),
            capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
        value=json.loads(result.stdout)
        assert value=={'body':'ok','at':value['at'],'verify_mode':2,'check_hostname':True}
        assert value['at'].startswith('2026-09-10')
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
