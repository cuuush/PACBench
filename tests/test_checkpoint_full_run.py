"""Full r2 scope remains distinct from the already-qualified canary."""
import copy,hashlib,json,os
from pathlib import Path
import pytest
from pacbench import full_run_capability as cap
from pacbench.gateway_reuse import atomic
from pacbench.hermes_gateway import GatewayLifecycleError


def capability(tmp_path):
 projects={p:{r:f'pacbench-{hashlib.sha256(str(tmp_path/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver','judge']} for p in cap.PROVIDERS}
 binding=cap.create(tmp_path,projects,{p:'config' for p in cap.PROVIDERS});return binding,cap.load(binding)


def test_full_scope_exact_denominators_cross_day_and_no_canary_expansion(tmp_path,monkeypatch):
 from pacbench import canary_capability
 binding,c=capability(tmp_path)
 _,schedule,checks,plan=cap.frozen()
 assert len(checks)==42 and sum(len(c.claims) for _,c,_ in checks)==188
 assert sum(len(b['source_ids']) for b in schedule)==142
 assert [len(g['questions']) for g in plan['groups']]==[6,8,5,4,2,17]
 rows={r['id']:r for r,_,_ in checks}
 assert rows['lunch-snapshot']['session']==rows['lunch-refreshed']['session']!=rows['lunch-fresh']['session']
 assert len(canary_capability.frozen()[2])==3
 monkeypatch.setenv(cap.ENV,json.dumps(binding))
 with pytest.raises(GatewayLifecycleError):canary_capability.load(binding)
 for field in ['full_frozen','memcal','endpoint','model','scoring']:
  bad=copy.deepcopy(c);bad['specification'][field]='wrong';atomic(tmp_path/'capability.json',bad)
  with pytest.raises(GatewayLifecycleError):cap.load(cap.binding(tmp_path/'capability.json'))
 atomic(tmp_path/'capability.json',c)
 for other in ['PACBENCH_CHECKPOINT_CANARY','PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE']:
  with monkeypatch.context() as m:
   m.setenv(other,'not-the-current-scope')
   with pytest.raises(GatewayLifecycleError):cap.load(binding)


def test_full_native_serial_ingest_and_project_boundaries(tmp_path,monkeypatch):
 binding,c=capability(tmp_path);monkeypatch.setenv(cap.ENV,json.dumps(binding))
 group=c['specification']['groups'][-1]
 slot={'kind':'ingest','provider':'mem0','group_id':group['id'],'batch_ids':group['ready_batch_ids'],'batch_id':'2026-10-10'}
 atomic(tmp_path/'active-slot.json',slot)
 permit=cap.admission(c['projects']['mem0']['memory'],'luna-bedrock-global')
 for project in [c['projects']['mem0']['driver'],c['projects']['mem0']['judge'],c['projects']['reme']['memory'],'pacbench-judge','unknown-memory']:
  with pytest.raises(GatewayLifecycleError):cap.admission(project,'luna-bedrock-global')
 with pytest.raises(GatewayLifecycleError):cap.admission(c['projects']['mem0']['memory'],'deepseek-flex')
 monkeypatch.setattr('pacbench.openrouter_task.STATE',tmp_path)
 receipt=tmp_path/'receipt';receipt.write_text('{}')
 row={'id':'1','checkpoint_full_run_root':str(tmp_path),'checkpoint_full_run_slot':slot,'state':'settled','billing_receipt':{'relative_path':'receipt','sha256':cap.sha(receipt)}}
 cap.serial([row],permit)
 for edit in [{'state':'uncertain'},{'state':'reserved'},{'billing_receipt':None},{'error_label':'unknown'}]:
  with pytest.raises(GatewayLifecycleError):cap.serial([{**row,**edit}],permit)
 with pytest.raises(GatewayLifecycleError):cap.serial([row,row],permit)


def test_full_r2_production_cross_day_three_native_arms(tmp_path,monkeypatch):
 from tests.offline_native_wire import NativeWire
 from tests.test_memcal_native_diagnostic import empty_review
 from tests.test_reme_native_checkpoints import write_response
 from tests.test_evaluation import correct_judge
 from pacbench.checkpoint_full_run import run
 from pacbench.gateway_client import client_settings
 def respond(body):
  raw=json.dumps(body['messages']);prompt=body['messages'][0].get('content','')
  if 'Claims (hidden from the assistant): ' in prompt:
   v=json.loads(correct_judge(prompt));claims=json.loads(prompt.split('Claims (hidden from the assistant): ')[1])
   v.update(interaction='answer',benchmark_error=None,evidence_diagnostic={'claims':[{'id':c['id'],'status':'not_found','reason':'Synthetic test','citations':[]} for c in claims],'extra_assertions':[]})
   return json.dumps(v)
  if 'Target note path:' in raw:return write_response(body)
  if body.get('stream'):return 'Synthetic native answer.'
  if 'BUNDLE ID' in raw or 'merges' in raw:return empty_review(body)
  if 'memory' in raw.lower():return json.dumps({'memory':[{'text':'Synthetic derived fact '+hashlib.sha256(raw.encode()).hexdigest()[:12]}]})
  return json.dumps({'reviewed':[],'diffs':[],'merges':[],'actions':[]})
 wire=NativeWire(respond)
 env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
 env.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='0',PACBENCH_OFFLINE_CONFORMANCE='1',PACBENCH_OPENROUTER_PROXY_URL=wire.endpoint,
  PACBENCH_OPENROUTER_PROXY_TOKEN='offline-not-a-secret',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
  PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 for k,v in env.items():monkeypatch.setenv(k,v)
 try:
  root=tmp_path/'full';result=run(root,env,settings=client_settings,offline_slice={'lunch-snapshot','lunch-refreshed','lunch-fresh'})
  for p in cap.PROVIDERS:
   evaluation=json.loads((root/p/'evaluation/evaluation.json').read_text())
   assert evaluation['state']=='diagnostic',evaluation
   turns={q:json.loads((root/p/'evaluation/turns'/q/'turn.json').read_text()) for q in ['lunch-snapshot','lunch-refreshed','lunch-fresh']}
   assert turns['lunch-snapshot']['session_id']==turns['lunch-refreshed']['session_id']!=turns['lunch-fresh']['session_id']
   assert turns['lunch-snapshot']['hermes_gateway']['prior_user_count']==0
   assert turns['lunch-refreshed']['hermes_gateway']['prior_user_count']==1
   assert turns['lunch-fresh']['hermes_gateway']['prior_user_count']==0
   assert turns['lunch-refreshed']['hermes_gateway']['pid']==turns['lunch-fresh']['hermes_gateway']['pid']
   assert (root/p/'evaluation/session-seals/checkpoint-1.json').exists()
 finally:wire.close()

from tests.test_single_admission_owner import owner,body,response

def test_funded_full_run_preserves_named_cap_and_settled_receipts(owner,monkeypatch,tmp_path):
 from decimal import Decimal
 budget=owner.budget
 permit={'root':str(tmp_path/'full'),'slot':{'kind':'ingest','provider':'mem0','batch_id':'2026-10-05'},'capability':{},'projects':['pacbench-test-mem0-memory']}
 monkeypatch.setenv(cap.ENV,'offline-full-test')
 monkeypatch.setattr(cap,'admission',lambda *a:permit)
 monkeypatch.setattr('pacbench.openrouter_task.STATE',budget.path.parent)
 project=permit['projects'][0]
 r=budget.reserve(project,body());assert budget.snapshot()[0]['checkpoint_full_run_root']==permit['root']
 status,headers,data=response();budget.settle(r['id'],status,data,headers=headers,body_complete=True)
 assert budget.snapshot()[0]['state']=='settled'
 monkeypatch.setenv('PACBENCH_FUNDED_RUN_ID','existing')
 monkeypatch.setattr('pacbench.funded_run_budget.credit_snapshot',lambda:{'remaining_usd':'20','request_started':'2026-11-16T00:00:00+00:00','observed_at':'2026-11-16T00:00:01+00:00'})
 with budget.locked() as state:
  state['funded_runs']={'existing':{'id':'existing','status':'active','limit_usd':'20','baseline_ledger_rows':0,'prepaid_safety':{'auto_recharge':'off','evidence':'Synthetic confirmation'}}}
  state['calls'].append({'id':'historic','project':'old','funded_run_id':'existing','state':'uncertain','accounted':'19.99999'})
 before=budget.snapshot()
 with pytest.raises(RuntimeError,match='spending cap'):budget.reserve(project,body('no room'))
 assert budget.snapshot()==before


def test_live_full_run_cannot_hide_a_shortened_cohort(tmp_path,monkeypatch):
 from pacbench.checkpoint_evaluator import launch
 b,c=capability(tmp_path);monkeypatch.setenv(cap.ENV,json.dumps(b))
 _,schedule,checks,plan=cap.frozen();plan['questions']=1;plan['criteria']=len(checks[0][1].claims);plan['groups']=plan['groups'][:1];plan['groups'][0]['questions']=plan['groups'][0]['questions'][:1]
 with pytest.raises(GatewayLifecycleError,match='full frozen'):
  launch(checks=checks[:1],plan=plan,schedule=schedule,runtime_factory=lambda g:pytest.fail('ingestion'),root=tmp_path/'evaluation',judge=lambda p:pytest.fail('judge'),env={'PACBENCH_DIRECT':'1',cap.ENV:json.dumps(b)},question_dir=tmp_path/'questions',budget_snapshot=lambda:{})


def test_prefix_cannot_drop_required_cross_day_session(tmp_path):
 from pacbench.checkpoint_full_run import prepare_prefix
 _,schedule,checks,plan=cap.frozen()
 count=next(i+1 for i,c in enumerate(checks) if c[0]['id']=='lunch-snapshot')
 folder=tmp_path/'old/mem0/evaluation';folder.mkdir(parents=True)
 atomic(folder/'evaluation.json',{'checks':[{'state':'diagnostic'} for _ in range(count)]})
 with pytest.raises(GatewayLifecycleError,match='session history'):
  prepare_prefix(tmp_path/'old','mem0',checks,plan,schedule,tmp_path/'new',lambda r:{})


def test_recovery_ancestry_rechecks_all_bound_roots(tmp_path):
 first=tmp_path/'first';first.mkdir();b,c=capability(first)
 atomic(first/'report.json',{'frozen_inputs':{'sha256':cap.FROZEN}})
 second=tmp_path/'second';second.mkdir()
 projects={p:{r:f'pacbench-{hashlib.sha256(str(second/p).encode()).hexdigest()[:12]}-{p}-{r}' for r in ['memory','driver','judge']} for p in cap.PROVIDERS}
 b2=cap.create(second,projects,c['configs'],prior_root=first);c2=cap.read(b2)
 assert cap.origins(c2)==[str(second),str(first)]
 atomic(first/'report.json',{'frozen_inputs':{'sha256':'changed'}})
 with pytest.raises(GatewayLifecycleError,match='evidence changed'):cap.origins(c2)


def test_predispatch_release_is_not_a_settled_zero_and_unknowns_still_block(owner,monkeypatch,tmp_path):
 import hashlib
 budget=owner.budget
 attempt=budget.reserve('pacbench-test-mem0-memory',body())
 error={'error':{'code':404,'message':'No endpoints found that can handle the requested parameters. To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection','metadata':{'failed_routing_step':'Filter by Parameters','routing_funnel':[{'step':'Filter by Allowed Providers','endpoint_count':1}]}}}
 raw=json.dumps(error).encode();budget.settle(attempt['id'],404,raw,body_complete=True)
 budget.release_routing_rejection(attempt['id'],evidence_sha256=hashlib.sha256(raw).hexdigest())
 # Full-run continuation accepts only the independently audited no-dispatch event.
 row=json.loads((tmp_path/'ledger.json').read_text())['calls'][0]
 monkeypatch.setattr('pacbench.openrouter_task.STATE',tmp_path)
 assert cap.rejected_without_dispatch(row)
 assert not cap.rejected_without_dispatch({**row,'state':'uncertain'})
 for edit in [{'accounted':'1'},{'upstream_request_id':'gen-unknown'},{'no_dispatch_release':{}}]:
  with pytest.raises(GatewayLifecycleError):cap.rejected_without_dispatch({**row,**edit})


@pytest.mark.parametrize('completed',[1,2])
def test_full_production_mem0_interrupted_recovery_reuses_only_valid_answer(tmp_path,monkeypatch,completed):
 from tests.offline_native_wire import NativeWire
 from tests.test_memcal_native_diagnostic import empty_review
 from tests.test_reme_native_checkpoints import write_response
 from tests.test_evaluation import correct_judge
 from pacbench.checkpoint_full_run import run
 from pacbench.gateway_client import client_settings
 def respond(body):
  raw=json.dumps(body['messages']);prompt=body['messages'][0].get('content','')
  if 'Claims (hidden from the assistant): ' in prompt:
   v=json.loads(correct_judge(prompt));claims=json.loads(prompt.split('Claims (hidden from the assistant): ')[1])
   v.update(interaction='answer',benchmark_error=None,evidence_diagnostic={'claims':[{'id':c['id'],'status':'not_found','reason':'Synthetic test','citations':[]} for c in claims],'extra_assertions':[]})
   return json.dumps(v)
  if 'Target note path:' in raw:return write_response(body)
  if body.get('stream'):return 'Synthetic native answer.'
  if 'BUNDLE ID' in raw or 'merges' in raw:return empty_review(body)
  if 'memory' in raw.lower():return json.dumps({'memory':[{'text':'Synthetic derived fact '+hashlib.sha256(raw.encode()).hexdigest()[:12]}]})
  return json.dumps({'reviewed':[],'diffs':[],'merges':[],'actions':[]})
 wire=NativeWire(respond)
 env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
 env.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='0',PACBENCH_OFFLINE_CONFORMANCE='1',PACBENCH_OPENROUTER_PROXY_URL=wire.endpoint,
  PACBENCH_OPENROUTER_PROXY_TOKEN='offline-not-a-secret',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
  PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 for k,v in env.items():monkeypatch.setenv(k,v)
 try:
  settings=lambda project:(wire.endpoint+'/p/pacbench-offline-native/v1','offline-not-a-secret')
  first=tmp_path/'first';slice={'rsvp-before','cancellation-before','rsvp-after'}
  result=run(first,env,settings=settings,offline_slice=slice,offline_provider='mem0',offline_stop_after=completed)
  old=first/'mem0/evaluation/turns/rsvp-before/turn.json';old_turn=json.loads(old.read_text())
  assert result['providers']['mem0']['state']=='interrupted'
  assert not (first/'mem0/checkpoint-2').exists()
  second=tmp_path/'second';recovered=run(second,env,settings=settings,offline_slice=slice,offline_provider='mem0',resume_from=first)
  e=json.loads((second/'mem0/evaluation/evaluation.json').read_text())
  assert e['state']=='diagnostic',e
  assert json.loads((second/'mem0/evaluation/turns/rsvp-before/turn.json').read_text())==old_turn
  assert not list((second/'mem0/canonical/ingestion').glob('2026-10-05.json'))
  assert list((second/'mem0/canonical/ingestion').glob('2026-10-06.json'))
  # Two newly answered questions, never a second sender for the retained answer.
  ready=list((second/'mem0').rglob('answer-ready.json'));assert len(ready)==3-completed
  assert not (second/'memcal/evaluation').exists() and not (second/'reme/evaluation').exists()
 finally:wire.close()


def test_recovered_prefix_resolves_sources_from_verified_ancestry(tmp_path,monkeypatch):
 from pacbench.checkpoint_full_run import source_proof
 prior=tmp_path/'prior';ancestor=tmp_path/'ancestor'
 old=ancestor/'mem0/checkpoint-1/source-proof.json';old.parent.mkdir(parents=True);old.write_text('{}')
 monkeypatch.setattr(cap,'load',lambda:'verified')
 def origins(value):
  assert value=='verified';return [str(prior),str(ancestor)]
 monkeypatch.setattr(cap,'origins',origins)
 assert source_proof(prior,'mem0',1)==old
 assert not source_proof(prior,'mem0',2).exists()
 def broken():raise GatewayLifecycleError('Recovery source changed')
 monkeypatch.setattr(cap,'load',broken)
 with pytest.raises(GatewayLifecycleError):source_proof(prior,'mem0',1)
