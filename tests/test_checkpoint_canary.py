"""Exact canary-only funding gates and the unmodified native launch path."""
import copy,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from pacbench import canary_capability as cap
from pacbench.gateway_reuse import atomic
from pacbench.hermes_gateway import GatewayLifecycleError


def capability(tmp_path):
 projects={p:{r:f'pacbench-{hashlib.sha256(str(tmp_path/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver']} for p in cap.PROVIDERS}
 b=cap.create(tmp_path,projects,{p:'config' for p in cap.PROVIDERS});c=cap.load(b)
 g=c['specification']['groups'][0];s={'kind':'ingest','provider':'mem0','group_id':g['id'],'batch_ids':g['ready_batch_ids'],'batch_id':g['ready_batch_ids'][-1]}
 atomic(tmp_path/'active-slot.json',s)
 return b,c,s


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_exact_frozen_slice_and_contract_tampering(tmp_path,monkeypatch):
 b,c,s=capability(tmp_path)
 assert [len(b['source_ids']) for b in c['specification']['schedule']]==[39,23]
 assert [q['id'] for g in c['specification']['groups'] for q in g['questions']]==list(cap.IDS)
 monkeypatch.setenv(cap.ENV,json.dumps(b))
 assert cap.admission(c['projects']['mem0']['memory'],'luna-bedrock-global')['slot']==s
 for project,profile in [('pacbench-judge','luna-bedrock-global'),(c['projects']['mem0']['driver'],'luna-bedrock-global'),(c['projects']['memcal']['memory'],'luna-bedrock-global'),(c['projects']['mem0']['memory'],'deepseek-flex')]:
  with pytest.raises(GatewayLifecycleError):cap.admission(project,profile)
 for field,value in [('full_frozen','bad'),('memcal','bad'),('model','wrong'),('endpoint','wrong')]:
  bad=copy.deepcopy(c);bad['specification'][field]=value;atomic(tmp_path/'capability.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.load(cap.binding(tmp_path/'capability.json'))
 atomic(tmp_path/'capability.json',c)
 for bad in [dict(s,batch_ids=s['batch_ids']+['2026-10-06']),dict(s,group_id='bad'),dict(s,kind='scored')]:
  atomic(tmp_path/'active-slot.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.admission(c['projects']['mem0']['memory'],'luna-bedrock-global')


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_serial_requires_unique_settled_authoritative_receipts_and_ingest_prefix(tmp_path,monkeypatch):
 b,c,s=capability(tmp_path);monkeypatch.setattr('pacbench.openrouter_task.STATE',tmp_path)
 receipt=tmp_path/'receipt.json';atomic(receipt,{'usage':{'cost':'.001'}})
 row={'id':'one','state':'settled','checkpoint_canary_root':str(tmp_path),'checkpoint_canary_slot':s,
      'billing_receipt':{'relative_path':'receipt.json','sha256':cap.sha(receipt)}}
 permit={'root':str(tmp_path),'slot':s}
 cap.serial([row],permit)
 for change in [{'state':'reserved'},{'state':'uncertain'},{'billing_receipt':None},{'error_label':'transport_unknown'}]:
  with pytest.raises(GatewayLifecycleError):cap.serial([{**row,**change}],permit)
 # Answer cannot use a fabricated readiness claim or absent native attempt.
 permit['slot']={**s,'kind':'answer','ingest_receipt_ids':['one']}
 cap.serial([row],permit)
 for ids in [[],['missing'],['one','one']]:
  with pytest.raises(GatewayLifecycleError):cap.serial([row],{**permit,'slot':{**permit['slot'],'ingest_receipt_ids':ids}})
 receipt.write_text('{}')
 with pytest.raises(GatewayLifecycleError):cap.serial([row],permit)


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_question_rejects_wrong_clock_session_prompt_observation_before_proof(tmp_path):
 b,c,s=capability(tmp_path);q=c['specification']['groups'][0]['questions'][0]
 s.pop('batch_id');s.update(kind='answer',question_id=q['id'],home=str(tmp_path/'home'),checkpoint='ck',observation={'workspace':'one'},proof={},ingest_receipt_ids=[])
 atomic(tmp_path/'active-slot.json',s)
 owner=SimpleNamespace(provider='mem0',home=tmp_path/'home',checkpoint='ck')
 for prompt,at,observation,session in [(q['prompt'],'2026-10-06T09:01:00-04:00',s['observation'],None),('changed',q['at'],s['observation'],None),(q['prompt'],q['at'],s['observation'],'prior-session')]:
  with pytest.raises(GatewayLifecycleError):cap.question(b,owner,prompt,at,observation,session)


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_native_production_canary_all_three_offline(tmp_path,monkeypatch):
 from tests.offline_native_wire import NativeWire
 from tests.test_memcal_native_diagnostic import empty_review
 from tests.test_reme_native_checkpoints import write_response
 from pacbench.checkpoint_canary import run
 from pacbench.gateway_client import client_settings
 def respond(body):
  raw=json.dumps(body['messages'])
  if 'Target note path:' in raw:return write_response(body)
  if body.get('stream'):return 'Synthetic canary response.'
  if 'BUNDLE ID' in raw or 'merges' in raw:return empty_review(body)
  if 'memory' in raw.lower():return json.dumps({'memory':[{'text':'Offline native memory '+hashlib.sha256(raw.encode()).hexdigest()[:12]}]})
  return json.dumps({'reviewed':[],'diffs':[],'merges':[],'actions':[]})
 wire=NativeWire(respond)
 env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
 env.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='0',PACBENCH_OPENROUTER_PROXY_URL=wire.endpoint,
  PACBENCH_OPENROUTER_PROXY_TOKEN='offline-not-a-secret',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',
  PACBENCH_ROLE_ROUTES='luna-all-roles-v1',PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 for k,v in env.items():monkeypatch.setenv(k,v)
 try:
  report=run(tmp_path/'canary',env,settings=client_settings)
  assert {p:r['state'] for p,r in report['providers'].items()}=={p:'native-transport-complete-not-scored' for p in cap.PROVIDERS},report
  for p,r in report['providers'].items():
   assert [t['id'] for t in r['turns']]==list(cap.IDS)
   assert len({t['session_id'] for t in r['turns']})==3
   turns=[json.loads(Path(t['receipt']).read_text()) for t in r['turns']]
   assert turns[0]['hermes_gateway']['pid']==turns[1]['hermes_gateway']['pid']
   assert turns[0]['hermes_gateway']['pid']!=turns[2]['hermes_gateway']['pid']
   assert all(t['hermes_gateway']['prior_user_count']==0 for t in turns)
   for t in turns:
    home=Path(json.loads((Path(t['hermes_gateway']['receipt'])/'answer-ready.json').read_text())['observation_clock']['descriptor']['home'])
    logs=[json.loads(line) for line in (home.parent/'reuse-context.jsonl.tools.jsonl').read_text().splitlines()]
    assert logs and all(sorted(r['offered_tools'])==sorted(cap.TOOLS[p]) for r in logs)
  # Interrupt after the first valid ReMe answer. Discard the second proof as
  # not yet durably committed; recovery must rebuild from Monday's CLOSED
  # snapshot, not the later canonical workspace, and reuse the valid answer.
  original=tmp_path/'canary';old=json.loads((original/'report.json').read_text())
  old['providers']['reme']['turns']=old['providers']['reme']['turns'][:1]
  old['providers']['reme']['state']='blocked';atomic(original/'report.json',old)
  second=original/'reme/checkpoint-2/source-proof.json';second.rename(second.with_suffix('.not-committed.json'))
  recovered=run(original,env,settings=client_settings,resume=True)
  assert all(r['state']=='native-transport-complete-not-scored' and len(r['turns'])==3 for r in recovered['providers'].values()),recovered
  assert recovered['providers']['reme']['turns'][0]['reused_valid_native_turn'] is True
  assert cap.sha(original/'report.json')==recovered['recovery']['prior_report']['sha256']
  for provider in ['memcal','mem0']:
   assert not list((original/'tool-wiring-recovery-v2'/provider).glob('canonical/ingestion/*.json'))
  assert list((original/'tool-wiring-recovery-v2/reme/canonical/ingestion').glob('*.json'))[0].name=='2026-10-06.json'
  (tmp_path/'requests.private.json').write_text(json.dumps(wire.requests))
 finally:wire.close()

from tests.test_single_admission_owner import owner,body,response


def test_original_caps_apply_and_native_parallel_calls_queue_without_replay(owner,monkeypatch,tmp_path):
 import threading,time
 from concurrent.futures import ThreadPoolExecutor
 b=owner.budget;root=tmp_path/'canary-scope';root.mkdir()
 permit={'root':str(root),'slot':{'kind':'ingest','provider':'mem0','batch_id':'2026-10-05'},'capability':{}}
 monkeypatch.setenv(cap.ENV,'test-synthetic-scope')
 monkeypatch.setattr(cap,'admission',lambda project,profile,body=None:permit)
 monkeypatch.setattr('pacbench.openrouter_task.STATE',b.path.parent)
 project='pacbench-synthetic-mem0-memory'
 first=b.reserve(project,body('first'));began=threading.Event()
 def next_call():began.set();return b.reserve(project,body('second'))
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(next_call);assert began.wait(2);time.sleep(.15)
  assert not future.done() and len(b.snapshot())==1
  status,headers,data=response();b.settle(first['id'],status,data,headers=headers,body_complete=True)
  second=future.result(timeout=3)
 assert len(b.snapshot())==2 and first['id']!=second['id']
 b.settle(second['id'],200,b'{}',body_complete=True)
 with pytest.raises(GatewayLifecycleError):b.reserve(project,body('third'))
 assert len(b.snapshot())==2


def test_native_canary_preserves_named_guard_and_unknown_exposure(owner,monkeypatch,tmp_path):
 b=owner.budget;permit={'root':str(tmp_path),'slot':{'kind':'ingest'},'capability':{}}
 monkeypatch.setenv(cap.ENV,'test-synthetic-scope');monkeypatch.setattr(cap,'admission',lambda p,r,body=None:permit)
 monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','existing')
 monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:{'remaining_usd':'20','request_started':'2026-11-16T00:00:00+00:00','observed_at':'2026-11-16T00:00:01+00:00'})
 with b.locked() as state:
  state['funded_runs']={'existing':{'id':'existing','status':'active','limit_usd':'20','baseline_ledger_rows':0,
    'prepaid_safety':{'auto_recharge':'off','evidence':'Offline owner confirmation'}}}
  state['calls']=[{'id':'history','project':'historic-project','funded_run_id':'existing','state':'uncertain','accounted':'19.99999'}]
 original=b.snapshot()
 with pytest.raises(RuntimeError,match='spending cap'):b.reserve('pacbench-synthetic-mem0-memory',body())
 assert b.snapshot()==original


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_cross_scope_and_unavailable_embedding_endpoint_fail_closed(tmp_path,monkeypatch):
 import socket
 from provider_adapters.embedding_health import check
 b,c,s=capability(tmp_path)
 for name in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE']:
  with monkeypatch.context() as m:
   m.setenv(name,'other-scope')
   with pytest.raises(GatewayLifecycleError):cap.load(b)
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 with pytest.raises(RuntimeError,match='preflight failed'):check(f'http://127.0.0.1:{port}/v1',timeout=1)


def test_exact_memcal_condition_pin_cleanliness_and_copied_plugin(tmp_path,monkeypatch):
 import shutil,subprocess
 from pacbench.observation_adapters import native_source
 from pacbench.checkpoint_canary import MEMCAL,PYTHON,ROOT
 if not MEMCAL.exists():pytest.skip('requires historical MemCal c19d216 source checkout')
 home=tmp_path/'hermes-home';(home/'plugins').mkdir(parents=True)
 shutil.copytree(MEMCAL/'integrations/hermes/memcal',home/'plugins/memcal')
 env={'MEMCAL_SRC':str(MEMCAL),'PACBENCH_MEMCAL_REUSE_REVISION':cap.specification()['memcal'],
  'PACBENCH_MEMCAL_CONDITION':'wiki-preference-operations-review-c19d216-v1'}
 assert native_source('memcal',home,ROOT,env,PYTHON)=={'memcal':cap.specification()['memcal']}
 for change in [{'PACBENCH_MEMCAL_REUSE_REVISION':'unreviewed'},{'PACBENCH_MEMCAL_CONDITION':'old'},
                {'PACBENCH_MEMCAL_REUSE_REVISION':'dac7214006b84ec364ecbcd9472daca7db588a25'}]:
  with pytest.raises(ValueError):native_source('memcal',home,ROOT,{**env,**change},PYTHON)
 (home/'plugins/memcal/unlisted.py').write_text('')
 with pytest.raises(ValueError,match='file set'):native_source('memcal',home,ROOT,env,PYTHON)
 (home/'plugins/memcal/unlisted.py').unlink()
 original=subprocess.check_output
 def dirty(args,**kwargs):return ' M memcal/dream/apply.py\n' if args[-2:]==['status','--porcelain'] else original(args,**kwargs)
 monkeypatch.setattr(subprocess,'check_output',dirty)
 with pytest.raises(ValueError,match='source differs'):native_source('memcal',home,ROOT,env,PYTHON)


def test_driver_cannot_spend_with_absent_invented_or_partial_tools():
 for p,names in cap.TOOLS.items():
  request={'tools':[{'function':{'name':n}} for n in names]}
  cap.check_tools(p,request)
  if names:
   for wrong in [{}, {'tools':request['tools'][:-1]}]:
    with pytest.raises(GatewayLifecycleError):cap.check_tools(p,wrong)
  with pytest.raises(GatewayLifecycleError):cap.check_tools(p,{'tools':request['tools']+[{'function':{'name':'terminal'}}]})


def test_outgoing_driver_tools_cannot_be_empty_invented_or_source_filtered():
 for provider in cap.PROVIDERS:
  request={'tools':[{'type':'function','function':{'name':name}} for name in cap.TOOLS[provider]]}
  cap.check_tools(provider,request)
  if cap.TOOLS[provider]:
   with pytest.raises(GatewayLifecycleError):cap.check_tools(provider,{})
   with pytest.raises(GatewayLifecycleError):cap.check_tools(provider,{'tools':request['tools'][:-1]})
  with pytest.raises(GatewayLifecycleError):cap.check_tools(provider,{'tools':request['tools']+[{'function':{'name':'file_read'}}]})


@pytest.mark.usefixtures('historical_checkpoint_archive')
def test_only_reme_preclose_recovery_uses_actual_production_path(tmp_path,monkeypatch):
 from tests.offline_native_wire import NativeWire
 from tests.test_reme_native_checkpoints import write_response
 from pacbench.gateway_client import client_settings
 from pacbench.checkpoint_canary import run
 from pacbench.gateway.preclose import ENV,CONDITION
 def respond(body):
  if 'Target note path:' in json.dumps(body['messages']):return write_response(body)
  return 'Synthetic native canary response.'
 wire=NativeWire(respond)
 env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','ALL_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
 env.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='0',PACBENCH_OPENROUTER_PROXY_URL=wire.endpoint,
  PACBENCH_OPENROUTER_PROXY_TOKEN='offline-not-a-secret',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',
  PACBENCH_ROLE_ROUTES='luna-all-roles-v1',PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 for k,v in env.items():monkeypatch.setenv(k,v)
 try:
  root=tmp_path/'canary';first=run(root,env,settings=client_settings,only_reme=True)
  assert set(first['providers'])=={'reme'} and len(first['providers']['reme']['turns'])==3
  first['providers']['reme']['turns']=first['providers']['reme']['turns'][:1];atomic(root/'report.json',first)
  p=root/'reme/checkpoint-2/source-proof.json';p.rename(p.with_suffix('.not-committed.json'))
  env[ENV]=CONDITION;monkeypatch.setenv(ENV,CONDITION)
  second=run(root,env,settings=client_settings,resume=True,only_reme=True,preclose_recovery=True)
  assert set(second['providers'])=={'reme'}
  arm=second['providers']['reme'];assert arm['state']=='native-transport-complete-not-scored' and len(arm['turns'])==3,second
  assert arm['turns'][0]['reused_valid_native_turn'] is True
  for t in arm['turns'][1:]:
   turn=json.loads(Path(t['receipt']).read_text());assert turn['hermes_gateway']['close_condition']==CONDITION
   assert turn['hermes_gateway']['native_drain']=='complete'
  assert not (root/'reme-preclose-recovery-v3/memcal').exists() and not (root/'reme-preclose-recovery-v3/mem0').exists()
  (tmp_path/'production-recovery-pass.json').write_text(json.dumps({'state':'pass','providers':['reme'],'reused_valid_turns':1,'new_turns':2,'source_prefixes':[39,62],'paid_calls':0}))
 finally:wire.close()
