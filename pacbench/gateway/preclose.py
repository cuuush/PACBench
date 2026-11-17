"""Read-only native write quiescence before a supported viewer close.

This does not change ReMe's request/shutdown settings or declare an open backend
closed. The original full native shutdown fence remains mandatory afterward.
"""
import json,os,subprocess,time
from pathlib import Path

CONDITION='native-write-quiescence-before-close-v1'
ENV='PACBENCH_NATIVE_PRECLOSE'
TIMEOUT=60


def qualified(sample,*,session,nonce,after_ns,prompt_hash):
 if sample.get('application_clock',{}).get('nonce')!=nonce:return False
 if sample.get('observed_monotonic_ns',0)<=after_ns or sample.get('native_failures') or sample.get('native_release_threads') or sample.get('native_children'):
  return False
 rows=sample.get('sessions',[]);target=[r for r in rows if r.get('session_id')==session and
  any(t.get('nonce')==nonce and t.get('prompt_sha256')==prompt_hash for t in r.get('sync_dispatches',[]))]
 if len(target)!=1 or any(not r.get('clean') for r in rows if r not in target):return False
 row=target[0]
 if row.get('shutdown_returned') or row.get('pending_futures')!=0:return False
 tasks=row.get('submitted_tasks',[])
 writes=[t for t in tasks if t.get('kind')=='write' and t.get('nonce')==nonce]
 if not writes or any(t.get('state')!='returned' for t in tasks if t.get('nonce')==nonce):return False
 dispatch=[t for t in row.get('sync_dispatches',[]) if t.get('nonce')==nonce and t.get('prompt_sha256')==prompt_hash]
 if len(dispatch)!=1 or dispatch[0].get('state')!='returned':return False
 providers=row.get('providers',[]);reme=[p for p in providers if p.get('name')=='reme']
 return (len(reme)==1 and reme[0].get('unfinished_writes')==0 and reme[0].get('write_enabled') is True
  and reme[0].get('shutdown_started') is False and reme[0].get('backend_attached') is True
  and not reme[0].get('deferred_backend_close') and all(p.get('name') in {'reme','builtin'} for p in providers))


LEASE='''
import asyncio,sys,json,os
from pathlib import Path
from hermes_cli.gateway_client import connect_gateway
from pacbench.gateway_reuse import atomic
async def main():
 async with connect_gateway() as client:
  sub=await client.rpc('session.resume',session_id=sys.argv[1])
  atomic(Path(sys.argv[2]),{'session_id':sys.argv[1],'pid':os.getpid(),'subscription_id':sub['subscription_id']})
  command=await asyncio.to_thread(sys.stdin.readline)
  if command.strip()!='close':raise RuntimeError('Missing explicit close command')
  await client.rpc('session.detach',session_id=sys.argv[1],subscription_id=sub['subscription_id'],end_reason='cli_close')
asyncio.run(main())
'''


def drain_then_close(owner,turn,folder,clock_evidence):
 import hashlib
 from pacbench.gateway_reuse import atomic
 from pacbench.hermes_gateway import _identity,GatewayLifecycleError
 nonce=(clock_evidence or {}).get('descriptor',{}).get('nonce')
 if not nonce:raise GatewayLifecycleError('Pre-close requires verified request-bound terminal capture')
 # Subscription prevents stock orphan retirement during the bounded wait.
 # The helper sends no message and invokes the original close exactly once.
 ready=folder/'viewer-lease.json';log=folder/'viewer-lease.stderr'
 with log.open('w') as errors:
  process=subprocess.Popen([str(owner.python),'-c',LEASE,turn.session_id,str(ready)],env=owner.env,cwd=owner.cwd,
   stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors,text=True)
  started=time.monotonic();after_ns=time.monotonic_ns();last=None;passed=False
  try:
   while not ready.exists():
    if process.poll() is not None or time.monotonic()-started>5:raise GatewayLifecycleError('Native pre-close viewer lease unavailable')
    time.sleep(.02)
   lease=json.loads(ready.read_text())
   if lease.get('session_id')!=turn.session_id or lease.get('pid')!=process.pid:raise GatewayLifecycleError('Native viewer lease identity differs')
   after_ns=time.monotonic_ns()
   while True:
    _identity(owner.home,owner.witness['pid'])
    last=json.loads((owner.home/'reuse-observer.json').read_text())
    if any(last.get(k)!=v for k,v in {'protocol':'fixed-checkpoint-gateway-reuse-v1','pid':owner.witness['pid'],'start_time':owner.witness['start_time'],
       'observer_sha256':owner.pins['reuse_observer_sha256']}.items()):raise GatewayLifecycleError('Pre-close observer identity differs')
    if last.get('native_failures') or last.get('application_clock',{}).get('nonce')!=nonce:
     raise GatewayLifecycleError('Native pre-close failure or changed clock')
    if qualified(last,session=turn.session_id,nonce=nonce,after_ns=after_ns,prompt_hash=hashlib.sha256(turn.prompt.encode()).hexdigest()):
     passed=True;break
    if process.poll() is not None or time.monotonic()-started>TIMEOUT:raise GatewayLifecycleError('Native pre-close quiescence unverified')
    time.sleep(.02)
  finally:
   proof={'condition':CONDITION,'state':'quiescent' if passed else 'blocked','session_id':turn.session_id,
    'nonce':nonce,'terminal_verified_before_ns':after_ns,'preclose_wait_s':time.monotonic()-started,
    'wait_bound_s':TIMEOUT,'observation':last,'viewer_lease':str(ready),'native_shutdown_settings_changed':False}
   atomic(folder/'preclose.json',proof)
   if process.poll() is None:
    try:
     process.stdin.write('close\n');process.stdin.flush();process.stdin.close();process.wait(timeout=owner.cleanup_timeout)
    except BaseException:
     process.terminate();process.wait(timeout=5)
     if passed:raise GatewayLifecycleError('Native close under viewer lease did not return') from None
   if passed and process.returncode!=0:raise GatewayLifecycleError('Native viewer close failed')
 return proof
