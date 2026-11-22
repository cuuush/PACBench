"""Real stock ReMe provider/client/queue + gateway; delayed synthetic HTTP job.

Saved live evidence supplies native extraction/tagging stage timings. This test
isolates native shutdown behavior; it does not claim synthetic storage validates
ReMe extraction or run a live model. Native shutdown_timeout stays at 30 seconds.
"""
import json,threading,time
from pathlib import Path
import pytest
from tests.test_gateway_reuse import LocalNativeFixture,native_home,PYTHON,ROOT
from pacbench.gateway_reuse import PersistentGateway,clone_observation,tree_manifest
from pacbench.checkpoint_clock import CheckpointClock
from pacbench.hermes_gateway import GatewayLifecycleError,_live


@pytest.mark.skipif(not PYTHON.exists() or not (ROOT/'vendor/reme').exists(), reason='requires provisioned Hermes and stock ReMe checkouts')
def test_stock_native_delayed_write_poisoned_owner_requires_closed_checkpoint_handoff(tmp_path):
 baseline=tmp_path/'baseline';baseline.mkdir();(baseline/'source.txt').write_text('Synthetic retained source.')
 manifest=tree_manifest(baseline);stores=[clone_observation(baseline,tmp_path/name) for name in ['first','delayed','recovered']]
 services=[LocalNativeFixture(Path(s['workspace'])) for s in stores]
 first,delayed,recovered=services;first.release.set();recovered.release.set();delayed.write_wait_s=60
 home,env=native_home(tmp_path,first.endpoint)
 # No shutdown/request timeout override: exercise the shipped default.
 assert json.loads((home/'reme/config.json').read_text())=={'mode':'http','endpoint':first.endpoint}
 clock=CheckpointClock(home,env['PACBENCH_NOW'],manifest['sha256']);cwd=tmp_path/'empty';cwd.mkdir()
 import os
 def obs(index):return {**stores[index],'endpoint':services[index].endpoint,'pid':os.getpid()}
 times={}
 def finish_realistically_delayed_http_write():
  assert delayed.entered.wait(20)
  times['write_entered']=time.monotonic()
  # Match the observed >30s native extraction+tagging sequence, without
  # modifying provider internals, native settings, clocks or shutdown code.
  time.sleep(32)
  times['write_released']=time.monotonic();delayed.release.set()
 releaser=threading.Thread(target=finish_realistically_delayed_http_write,daemon=True)
 pins={p.name:p.read_bytes() for p in (ROOT/'vendor/reme/integrations/hermes_agent').glob('*.py')}
 try:
  with PersistentGateway(home,PYTHON,env,cwd=cwd,checkpoint=manifest['sha256'],observation_clock=clock,cleanup_timeout=35) as owner:
   one,_=owner.run('FIRST-SYNTHETIC-QUESTION',at='2026-10-05T09:00:00-04:00',observation=obs(0),timeout=30)
   pid=owner.witness['pid'];releaser.start()
   with pytest.raises(GatewayLifecycleError,match='drain unverified'):
    owner.run('DELAYED-SYNTHETIC-QUESTION',at='2026-10-05T09:10:00-04:00',observation=obs(1),timeout=30)
   assert owner.witness['pid']==pid and owner.failed
   evidence=json.loads(next(tmp_path.glob('reuse-turns/*/native-lifecycle-failure.json')).read_text())
   first_failure=evidence['first_native_failure']
   assert 'reme-memory-writer' in first_failure['native_memory_threads']
   assert any(p['name']=='reme' and p.get('writer_alive') for row in first_failure['sessions'] for p in row['providers'])
   assert evidence['latest_validated_observation']['native_failures']
   failed=json.loads(next(tmp_path.glob('reuse-turns/*/failed.json')).read_text())
   import hashlib
   assert failed['lifecycle_evidence']['sha256']==hashlib.sha256(Path(failed['lifecycle_evidence']['path']).read_bytes()).hexdigest()
   evidence=json.loads(next(tmp_path.glob('reuse-turns/*/native-lifecycle-failure.json')).read_text())
   first_failure=evidence['first_native_failure']
   assert 'reme-memory-writer' in first_failure['native_memory_threads']
   provider=next(p for row in first_failure['sessions'] for p in row['providers'] if p['name']=='reme' and p.get('writer_alive'))
   assert provider['deferred_backend_close'] is True
   assert evidence['latest_validated_observation']['native_failures']
   failed=json.loads(next(tmp_path.glob('reuse-turns/*/failed.json')).read_text())
   import hashlib
   assert failed['lifecycle_evidence']['sha256']==hashlib.sha256(next(tmp_path.glob('reuse-turns/*/native-lifecycle-failure.json')).read_bytes()).hexdigest()
   releaser.join(3);assert not releaser.is_alive()
   observer=json.loads((home/'reuse-observer.json').read_text())
   assert 'ReMe shutdown timed out' in observer['native_failures']
   # Completion AFTER native shutdown's timeout must not turn failure into ready.
   assert not observer['native_memory_threads']
   with pytest.raises(GatewayLifecycleError,match='no replay'):
    owner.run('MUST-NOT-DISPATCH',at='2026-10-05T09:20:00-04:00',observation=obs(2),timeout=30)
   assert not recovered.requests
   assert len((Path(stores[1]['workspace'])/'native-writes.jsonl').read_text().splitlines())==1
   assert list(tmp_path.glob('reuse-turns/*/failed.json'))
  assert owner.witness['stopped'] and not _live(pid)
  assert tree_manifest(baseline)==manifest
  before=[(Path(s['workspace'])/'native-writes.jsonl').read_bytes() for s in stores[:2]]
  # Explicit handoff after old workers are joined, to an untouched source-only
  # observation. The old failed answer is never replayed or credited.
  with PersistentGateway(home,PYTHON,env,cwd=cwd,checkpoint=manifest['sha256'],observation_clock=clock,cleanup_timeout=5) as new:
   three,folder=new.run('RECOVERED-SYNTHETIC-QUESTION',at='2026-10-05T09:20:00-04:00',observation=obs(2),timeout=30)
   assert new.witness['pid']!=pid and three.session_id!=one.session_id
   ready=json.loads((folder/'answer-ready.json').read_text())
   assert all(s not in json.dumps(ready['answer_visible_records']) for s in ['FIRST-SYNTHETIC-QUESTION','DELAYED-SYNTHETIC-QUESTION'])
   assert ready['observation_clock']['state']=='pass'
  assert before==[(Path(s['workspace'])/'native-writes.jsonl').read_bytes() for s in stores[:2]]
  assert tree_manifest(baseline)==manifest
  assert pins=={p.name:p.read_bytes() for p in (ROOT/'vendor/reme/integrations/hermes_agent').glob('*.py')}
  (tmp_path/'conformance.json').write_text(json.dumps({'native_shutdown_default_seconds':30,'synthetic_http_delay_seconds':times['write_released']-times['write_entered'],
   'same_gateway_before_failure':True,'failed_turn_promoted':False,'checkpoint_handoff_after_join':True,
   'fresh_session_and_clock':True,'late_old_store_writes':False,'native_source_unchanged':True,'paid_calls':0}))
 finally:
  delayed.release.set()
  if releaser.is_alive():releaser.join(3)
  for service in services:service.close()


@pytest.mark.skipif(not PYTHON.exists() or not (ROOT/'vendor/reme').exists(), reason='requires provisioned Hermes and stock ReMe checkouts')
def test_preclose_quiescence_keeps_stock_write_and_gateway_warm(tmp_path):
 from pacbench.gateway.preclose import ENV,CONDITION
 from pacbench.checkpoint_evaluator import audit_transport
 import os
 base=tmp_path/'baseline';base.mkdir();(base/'source.txt').write_text('Synthetic original checkpoint.')
 signature=tree_manifest(base);clones=[clone_observation(base,tmp_path/name) for name in ['long','next']]
 first=LocalNativeFixture(Path(clones[0]['workspace']));second=LocalNativeFixture(Path(clones[1]['workspace']))
 first.write_wait_s=60;second.release.set()
 home,env=native_home(tmp_path,first.endpoint);env[ENV]=CONDITION
 clock=CheckpointClock(home,env['PACBENCH_NOW'],signature['sha256']);cwd=tmp_path/'empty';cwd.mkdir()
 def release():
  assert first.entered.wait(20);time.sleep(32);first.release.set()
 thread=threading.Thread(target=release,daemon=True);thread.start()
 try:
  with PersistentGateway(home,PYTHON,env,cwd=cwd,checkpoint=signature['sha256'],observation_clock=clock,cleanup_timeout=30) as owner:
   one,folder=owner.run('NATURAL-DELAYED-WRITE',at='2026-10-05T09:00:00-04:00',observation={**clones[0],'endpoint':first.endpoint,'pid':os.getpid()},timeout=30)
   pid=owner.witness['pid'];audit_transport(one)
   pre=json.loads((folder/'preclose.json').read_text());cleanup=json.loads((folder/'cleanup.json').read_text())
   assert pre['state']=='quiescent' and pre['preclose_wait_s']>=31
   assert pre['native_shutdown_settings_changed'] is False and pre['observation']['sessions'][-1]['shutdown_returned'] is False
   assert cleanup['native_observation']['native_failures']==[] and not cleanup['native_observation']['native_memory_threads']
   first_done=(Path(clones[0]['workspace'])/'native-writes.jsonl').read_bytes()
   two,_=owner.run('BLANK-NEXT-QUESTION',at='2026-10-05T09:10:00-04:00',observation={**clones[1],'endpoint':second.endpoint,'pid':os.getpid()},timeout=30)
   audit_transport(two)
   assert owner.witness['pid']==pid and two.session_id!=one.session_id
   assert first_done==(Path(clones[0]['workspace'])/'native-writes.jsonl').read_bytes()
   for path,body in first.requests:
    if path.endswith('/chat/completions') and 'BLANK-NEXT-QUESTION' in json.dumps(body):assert 'NATURAL-DELAYED-WRITE' not in json.dumps(body)
   assert tree_manifest(base)==signature
   assert 'shutdown_timeout' not in json.loads((home/'reme/config.json').read_text())
   # A changed pre-close receipt cannot be promoted to qualified capture.
   old=(folder/'preclose.json').read_bytes();(folder/'preclose.json').write_text('{}')
   try:
    with pytest.raises(GatewayLifecycleError):audit_transport(one)
   finally:(folder/'preclose.json').write_bytes(old)
  assert owner.witness['stopped'] and not _live(pid)
 finally:
  first.release.set();thread.join(3);first.close();second.close()


@pytest.mark.skipif(not PYTHON.exists() or not (ROOT/'vendor/reme').exists(), reason='requires provisioned Hermes and stock ReMe checkouts')
def test_preclose_rejects_stale_empty_before_enqueue_and_failed_work():
 import copy
 from pacbench.gateway.preclose import qualified
 good={'observed_monotonic_ns':20,'application_clock':{'nonce':'n'},'native_failures':[],'native_release_threads':[],
  'sessions':[{'session_id':'s','shutdown_returned':False,'pending_futures':0,
   'submitted_tasks':[{'kind':'write','nonce':'n','state':'returned'}],
   'sync_dispatches':[{'nonce':'n','prompt_sha256':'p','state':'returned'}],
   'providers':[{'name':'reme','unfinished_writes':0,'write_enabled':True,'shutdown_started':False,'backend_attached':True,'deferred_backend_close':False}]}]}
 check=lambda v:qualified(v,session='s',nonce='n',after_ns=10,prompt_hash='p')
 assert check(good)
 for edit in ['stale','unsubmitted','pending','callback-delayed','writing','failed','closed','disabled','nonce']:
  bad=copy.deepcopy(good);r=bad['sessions'][0]
  if edit=='stale':bad['observed_monotonic_ns']=5
  elif edit=='unsubmitted':r['submitted_tasks']=[]
  elif edit=='pending':r['pending_futures']=1
  elif edit=='callback-delayed':r['submitted_tasks'][0]['state']='submitted'
  elif edit=='writing':r['providers'][0]['unfinished_writes']=1
  elif edit=='failed':bad['native_failures']=['ReMe recording failed']
  elif edit=='closed':r['providers'][0]['shutdown_started']=True
  elif edit=='disabled':r['providers'][0]['write_enabled']=False
  elif edit=='nonce':bad['application_clock']['nonce']='other'
  assert not check(bad),edit


@pytest.mark.skipif(not PYTHON.exists() or not (ROOT/'vendor/reme').exists(), reason='requires provisioned Hermes and stock ReMe checkouts')
def test_observation_close_requires_native_exit_and_no_live_process(tmp_path,monkeypatch):
 from pacbench.checkpoint_canary import close_observation_services
 monkeypatch.setattr('pacbench.hermes_gateway._live',lambda pid:False)
 good={'pid':42,'forced_kill':False,'returncode':0,'native_shutdown_complete':True}
 close_observation_services([(lambda:good,tmp_path)])
 for update in [{'forced_kill':True},{'returncode':9},{'native_shutdown_complete':False}]:
  with pytest.raises(GatewayLifecycleError):close_observation_services([(lambda:{**good,**update},tmp_path)])
 monkeypatch.setattr('pacbench.hermes_gateway._live',lambda pid:True)
 with pytest.raises(GatewayLifecycleError):close_observation_services([(lambda:good,tmp_path)])


@pytest.mark.skipif(not PYTHON.exists() or not (ROOT/'vendor/reme').exists(), reason='requires provisioned Hermes and stock ReMe checkouts')
def test_preclose_selects_current_manager_after_native_session_reopen():
 from pacbench.gateway.preclose import qualified
 from pathlib import Path
 import json,hashlib,copy
 folder=Path(__file__).resolve().parents[1]/'runs/revised-r2-observational-live-20261117/remaining-native-recovery-v3/reme/checkpoint-4/reuse-turns/2655d7c511664e769d1ce1c1a8ee3d00'
 if not folder.exists():
  import pytest;pytest.skip('Private retained native witness unavailable')
 proof=json.loads((folder/'preclose.json').read_text());turn=json.loads((folder/'answer-ready.json').read_text())['native_turn']
 args=dict(session=turn['session_id'],nonce=proof['nonce'],after_ns=proof['terminal_verified_before_ns'],prompt_hash=hashlib.sha256(turn['prompt'].encode()).hexdigest())
 sample=proof['observation'];assert qualified(sample,**args)
 current=next(r for r in sample['sessions'] if any(t.get('nonce')==proof['nonce'] for t in r.get('sync_dispatches',[])))
 for kind in ['pending','ambiguous','old_not_clean','wrong_nonce']:
  bad=copy.deepcopy(sample)
  target=next(r for r in bad['sessions'] if r['manager_id']==current['manager_id'])
  if kind=='pending':target['providers'][0]['unfinished_writes']=1
  if kind=='ambiguous':bad['sessions'].append(copy.deepcopy(target))
  if kind=='old_not_clean':next(r for r in bad['sessions'] if r['manager_id']!=target['manager_id'])['clean']=False
  if kind=='wrong_nonce':target['sync_dispatches'][0]['nonce']='other'
  assert not qualified(bad,**args)
