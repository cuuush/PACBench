"""Three-arm native transport diagnostic. No judge, follow-up or scored launcher."""
from pathlib import Path
import os,json,hashlib,shutil,subprocess,sqlite3,time
from dataclasses import asdict
from . import full_run_capability as cap
from .gateway_reuse import atomic,clone_observation,PersistentGateway
from .checkpoint_evaluator import CheckpointEvaluator,audit_transport,verify_checkpoint
from .checkpoint_clock import CheckpointClock
from .hermes_gateway import GatewayLifecycleError

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'vendor/venv-hermes-isolated/bin/python'
MEMCAL=Path('/workspace/memcal-review-wiki-retraction')
MEM0_PLUGIN=ROOT/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0/repeat-1/mem0/hermes-home/plugins/mem0'


def profile(root,provider,base,*,memcal=MEMCAL,portable=False):
 from . import harness
 root=Path(root);root.mkdir(exist_ok=True)
 plugin=memcal/'integrations/hermes/memcal' if provider=='memcal' else MEM0_PLUGIN if provider=='mem0' and not portable else None
 harness.provision_home(root/'hermes-home',provider_plugin_src=plugin,provider_name=provider,
  model='openai/gpt-6-luna',model_provider='openrouter',model_base_url=base)
 if provider=='reme':(root/'hermes-home/plugins/reme').symlink_to(ROOT/'vendor/reme/integrations/hermes_agent',target_is_directory=True)


def prepare_fixture(output, preparation, schedule, *, dataset=False):
 from .document_ingestion import prepare_sources
 from .imessage_membership import CONDITION
 bundle_path=preparation/('sources/bundle.json' if dataset else 'bundle.json')
 bundle=json.loads(bundle_path.read_text())
 messages=[m for life in bundle['lives'].values() for m in life['messages']]
 made=prepare_sources(output,messages,'America/New_York',native_fixtures=True,
                      membership_condition=CONDITION)
 for batch,original in zip(made,schedule,strict=True):
  document=output/batch['source_file']
  if (any(batch[k]!=original[k] for k in ('id','at','source_ids','source_file')) or
      cap.sha(document)!=original['document_sha256'] or
      (dataset and document.read_bytes()!=(preparation/original['source_file']).read_bytes())):
   raise GatewayLifecycleError('Native fixture differs from frozen source')
 return messages


def serial_rows(budget,root,prior_root=None):
 origins=set(cap.origins(cap.load()))
 with budget.locked(write=False) as state:
  rows=[r for r in state['calls'] if r.get('checkpoint_full_run_root') in origins]
  from .observational_receipts import enabled
  if enabled():return rows
  if any((r['state']!='settled' or r.get('error_label') or not r.get('billing_receipt')) and not cap.rejected_without_dispatch(r) for r in rows):
   raise GatewayLifecycleError('Canary accounting boundary: invalid or unsettled attempt')
  return rows


def close_observation_services(services):
 from .hermes_gateway import _live
 for stop,folder in services:
  result=stop();atomic(Path(folder)/'native-observation-shutdown.json',result)
  if (result.get('forced_kill') or result.get('returncode') not in (0,-15) or
      result.get('native_shutdown_complete') is not True or _live(result['pid'])):
   raise GatewayLifecycleError('Native observation service closure unverified; no next checkpoint')


def source_proof(prior,provider,group_id):
 """Resolve immutable source evidence through verified recovery ancestry."""
 path=Path(prior)/provider/('checkpoint-'+str(group_id))/'source-proof.json'
 if path.exists():return path
 # Prefix copies intentionally retain original receipt bindings.
 for ancestor in cap.origins(cap.load()):
  candidate=Path(ancestor)/provider/('checkpoint-'+str(group_id))/'source-proof.json'
  if candidate.exists():return candidate
 return path


def verify_completed(prior,provider,checks,plan,schedule,*,prefix=False):
 """Re-read frozen identities and native receipts before skipping valid work."""
 from .harness import TurnResult
 from .strict_memory import classify
 prior=Path(prior);evaluation=prior/provider/'evaluation';report=json.loads((evaluation/'evaluation.json').read_text())
 if (not prefix and report['state']!='diagnostic') or len(report['checks'])<len(checks):raise GatewayLifecycleError('Incomplete prior arm')
 for group in plan['groups']:
  if not any(q['id'] in {r['id'] for r,_,_ in checks} for q in group['questions']):continue
  verify_checkpoint(group,json.loads((source_proof(prior,provider,group['id'])).read_text()),schedule,provider)
 inherited={}
 for (row,contract,_),item in zip(checks,report['checks']):
  if item['state'] not in {'diagnostic','strict-ungraded'} or item.get('recovery',{}).get('state','complete')!='complete':raise GatewayLifecycleError('Prior prefix not complete')
  if item['id']!=row['id'] or item['contract_hash']!=contract.contract_hash:raise GatewayLifecycleError('Prior contract differs')
  folder=evaluation/'turns'/row['id'];turn=TurnResult(**json.loads((folder/'turn.json').read_text()));audit_transport(turn)
  if turn.prompt!=contract.question or turn.at!=row['at']:raise GatewayLifecycleError('Prior native prompt/time differs')
  key=row.get('session') or row['id'];decision=classify(provider,turn,inherited.get(key,'eligible'));inherited[key]=decision['state']
  if decision!=json.loads((folder/'strict-memory.json').read_text()):raise GatewayLifecycleError('Prior provenance changed')
  if decision['state']!='eligible':
   if item['state']!='strict-ungraded' or item['verdict'] is not None:raise GatewayLifecycleError('Prior raw/unknown answer scored')
  else:
   grade=json.loads((folder/'grade.json').read_text())
   if [x['id'] for x in grade['claims']]!=[x.id for x in contract.claims] or not (folder/'judgment.json').exists():raise GatewayLifecycleError('Prior grade coverage invalid')
 return {'state':'verified','questions':len(checks),'criteria':sum(len(c.claims) for _,c,_ in checks)}


def prepare_prefix(prior,provider,checks,plan,schedule,destination,evidence):
 """Copy audited complete records, never dispatch them or migrate live history."""
 from .native_checks import audit_checks,session_key
 from .semantic_grade import DATE_SCOPED_JUDGE_PROTOCOL,DIAGNOSTIC_VERSION
 old=Path(prior)/provider/'evaluation';report=json.loads((old/'evaluation.json').read_text());count=0
 for item in report['checks']:
  if item['state'] not in {'diagnostic','strict-ungraded'} or item.get('recovery',{}).get('state','complete')!='complete':break
  count+=1
 if not count:return 0
 before={session_key(r,'isolated') for r,_,_ in checks[:count]}
 after={session_key(r,'isolated') for r,_,_ in checks[count:]}
 if before & after:raise GatewayLifecycleError('Resume cut crosses required native session history')
 verify_completed(prior,provider,checks[:count],plan,schedule,prefix=True)
 destination=Path(destination);(destination/'turns').mkdir(parents=True)
 for row,_,_ in checks[:count]:shutil.copytree(old/'turns'/row['id'],destination/'turns'/row['id'])
 if (old.parent/'model-calls').exists():shutil.copytree(old.parent/'model-calls',destination.parent/'model-calls',dirs_exist_ok=True)
 atomic(destination/'evaluation.json',{**report,'state':'running','checks':report['checks'][:count]})
 errors=audit_checks(checks[:count],'isolated',destination,provenance_version='source-provenance-v2',
  evidence_by_id=evidence,diagnostic_version=DIAGNOSTIC_VERSION,judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL,prompt_protocol='native-question-v2')
 if errors:raise GatewayLifecycleError('Retained prefix audit failed: '+str(errors)[:600])
 atomic(destination/'resume-proof.json',{'protocol':'closed-checkpoint-independent-prefix-v1',
  'prior_evaluation':cap.binding(old/'evaluation.json'),'count':count,'pending_sessions_disjoint':True,
  'retained_turns':[cap.binding(destination/'turns'/r['id']/'turn.json') for r,_,_ in checks[:count]]})
 return count


def run(root,env,*,settings,budget=None,offline_slice=None,resume_from=None,offline_provider=None,offline_stop_after=None,dataset=None):
 from . import harness
 from .native_checkpoint_sources import Mem0SourceCheckpoints,MemcalSourceCheckpoints,ReMeSourceCheckpoints
 from .checkpoint_evaluator import launch
 from .model_calls import ModelClient
 from .gateway_client import SpendingBlocked
 from .semantic_grade import DATE_SCOPED_JUDGE_PROTOCOL,DIAGNOSTIC_VERSION
 from .evaluation import judge_evidence
 from .strict_memory import classify
 from .harness import TurnResult
 from .imessage_membership import CONDITION
 if (offline_slice or offline_provider or offline_stop_after) and (env.get('PACBENCH_DIRECT')!='0' or env.get('PACBENCH_OFFLINE_CONFORMANCE')!='1'):
  raise GatewayLifecycleError('A reduced slice is offline conformance only')
 root=Path(root).resolve();root.mkdir(exist_ok=False);work=root
 prior=Path(resume_from).resolve() if resume_from else None
 prior_report=json.loads((prior/'report.json').read_text()) if prior else {}
 os.environ['PACBENCH_STOP_FILE']=str(root/'STOP')
 os.environ['PACBENCH_DIRECT_STOP_ON_429']=str(root/'STOP')
 os.environ['PACBENCH_IMESSAGE_MEMBERSHIP_CONDITION']=CONDITION
 env['PACBENCH_IMESSAGE_MEMBERSHIP_CONDITION']=CONDITION
 preparation,schedule,checks,full=cap.frozen(dataset) if dataset is not None else cap.frozen()
 canonical={};configs={};projects={}
 native_fixture=root/'native-source-fixture'
 messages=prepare_fixture(native_fixture,preparation,schedule,dataset=dataset is not None)
 if dataset is not None:
  from .evaluation import contract_evidence,source_evidence
  def evidence(row):
   return source_evidence(contract_evidence(messages,[q for q,_,_ in checks],row,'isolated'),DIAGNOSTIC_VERSION)
 else:
  resolved=json.loads((preparation/'resolved-spec.json').read_text())
  def evidence(row):
   return judge_evidence(preparation,messages,resolved,row,'isolated',diagnostic_version=DIAGNOSTIC_VERSION)
 specification=cap.specification(dataset) if dataset is not None else cap.specification()
 source_ids={sid for b in schedule for sid in b['source_ids']}
 from .provider_versions import memcal_source
 memcal=memcal_source(ROOT) if dataset is not None else MEMCAL
 for provider in cap.PROVIDERS:
  arm=root/provider
  arm.mkdir()
  c=arm/'canonical'
  c.mkdir()
  runid=hashlib.sha256(str(arm).encode()).hexdigest()[:12]
  projects[provider]={role:f'pacbench-{runid}-{provider}-{role}' for role in ['memory','driver','judge']}
  base,driverkey=settings(projects[provider]['driver']);mbase,memorykey=settings(projects[provider]['memory'])
  if dataset is None:profile(c,provider,base)
  else:profile(c,provider,base,memcal=memcal,portable=True)
  (c/'fallback-home').mkdir()
  if provider=='reme' and prior and (prior/provider/'canonical/hermes-home/reme').exists():shutil.copytree(prior/provider/'canonical/hermes-home/reme',c/'hermes-home/reme')
  child={**env,'HOME':str(c/'fallback-home'),'MEMCAL_SRC':str(memcal),'PACBENCH_MEMCAL_REUSE_REVISION':specification['memcal'],
   'PACBENCH_NO_BUILTIN_MEMORY':'1','PACBENCH_NO_SESSION_SEARCH':'1','PACBENCH_PROVIDER_ONLY':'1','HERMES_ENABLE_PROJECT_PLUGINS':'0',
   'OPENROUTER_API_KEY':driverkey,'OPENROUTER_API_BASE':base,'OPENROUTER_BASE_URL':base,'OPENAI_API_KEY':memorykey,
   'PACBENCH_USAGE_ROLES':json.dumps({projects[provider]['driver']:'hermes',projects[provider]['memory']:'memory_provider'}),
   'PACBENCH_TOOL_ALLOWLIST':','.join(sorted(cap.TOOLS[provider])),'PACBENCH_SOURCE_ARGUMENT_RULES':'{}',
   'TZ':'America/New_York','MEM0_TELEMETRY':'false','PACBENCH_STOP_FILE':str(root/'STOP'),'PACBENCH_DIRECT_STOP_ON_429':str(root/'STOP')}
  if provider!='reme':child.pop('PACBENCH_NATIVE_PRECLOSE',None)
  else:
   from .gateway.preclose import ENV,CONDITION
   child[ENV]=CONDITION
  if provider=='memcal':
   from .native_pilot import memcal_model_settings
   child.update(memcal_model_settings('openai/gpt-6-luna',mbase,memorykey));child['MEMCAL_FALLBACK_PROVIDER']=''
  elif provider=='mem0':
   if dataset is not None:
    from .catalog_plugins import install_catalog_provider
    install=install_catalog_provider('mem0',c/'hermes-home',c/'fallback-home',harness.ISOLATED_BIN,harness.ISOLATED_TREE)
    atomic(c/'catalog-plugin.json',install)
   if prior or dataset is None:
    cfg=json.loads(((prior/'mem0/canonical/hermes-home/mem0.json') if prior else MEM0_PLUGIN.parents[1]/'mem0.json').read_text())
   else:
    # Same native OSS configuration as the reviewed arm, with fresh local state.
    cfg={'mode':'oss','sync_max_chars':6000,'oss':{
     'llm':{'provider':'openai','config':{'openrouter_base_url':'https://openrouter.ai/api/v1','is_reasoning_model':True}},
     'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':'http://127.0.0.1:11435','embedding_dims':768}},
     'vector_store':{'provider':'qdrant','config':{'path':str(c/'qdrant')}}}}
   if not prior:cfg['user_id']='r2-scored-'+runid
   cfg['oss']['llm']['config'].update(api_key=memorykey,model='openai/gpt-6-luna',openai_base_url=mbase)
   (c/'hermes-home/mem0.json').write_text(json.dumps(cfg))
  if True:
   from .native_pilot import probe
   native=probe(c,provider,child)
   if sorted(native['tools'])!=sorted(cap.TOOLS[provider]):raise GatewayLifecycleError('Native canary tool probe differs')
   atomic(work/(provider+'-native-tools.json'),native)
  canonical[provider]=(c,child,mbase,memorykey)
  configs[provider]=cap.sha(c/'hermes-home/config.yaml')
 permission=cap.create(root,projects,configs,prior_root=prior,**({'dataset':dataset} if dataset is not None else {}))
 env[cap.ENV]=json.dumps(permission)
 os.environ[cap.ENV]=json.dumps(permission)
 for c,child,_,_ in canonical.values():child[cap.ENV]=json.dumps(permission)
 report={'protocol':cap.PROTOCOL,'frozen_inputs':full['frozen_inputs'],'providers':{},'conditions':specification,
  'recovery':{'prior_report':cap.binding(prior/'report.json')} if prior else None,
  'pacbench_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
 report['dispatch_tracking']=env.get('PACBENCH_DISPATCH_TRACKING')
 atomic(root/'report.json',report)
 def publish(value):atomic(root/'active-slot.json',value)
 for provider in ((offline_provider,) if offline_provider else cap.PROVIDERS):
  arm=root/provider
  old_arm=prior
  inherited=prior_report.get('providers',{}).get(provider,{})
  if prior and inherited.get('state')=='reused-complete':
   old_arm=Path(inherited['evaluation']).parents[2]
   if str(old_arm) not in cap.origins(cap.load()):raise GatewayLifecycleError('Prior completed arm outside verified ancestry')
  old_evaluation=old_arm/provider/'evaluation/evaluation.json' if old_arm else None
  if old_evaluation and old_evaluation.exists():
   old=json.loads(old_evaluation.read_text())
   if old['state']=='diagnostic' and len(old['checks'])==(len(offline_slice) if offline_slice else 42):
    verify_completed(old_arm,provider,[c for c in checks if not offline_slice or c[0]['id'] in offline_slice],{**full,'groups':[{**g,'questions':[q for q in g['questions'] if not offline_slice or q['id'] in offline_slice]} for g in full['groups'] if any(not offline_slice or q['id'] in offline_slice for q in g['questions'])]},schedule)
    report['providers'][provider]={'state':'reused-complete','evaluation':str(old_evaluation),'prior_report':cap.binding(prior/'report.json')}
    atomic(root/'report.json',report);continue
  c,child,base,key=canonical[provider]
  document_root=native_fixture if dataset is not None else preparation
  source=(MemcalSourceCheckpoints(native_fixture,c,schedule,child,PYTHON,timeout_s=120) if provider=='memcal' else
   Mem0SourceCheckpoints(document_root,c,schedule,child,PYTHON,timeout_s=120) if provider=='mem0' else
   ReMeSourceCheckpoints(document_root,c,schedule,child,ROOT/'vendor/venv-reme/bin/python',model='openai/gpt-6-luna',base=base,key=key))
  plan=json.loads(json.dumps({**full,'provider':provider}))
  if offline_slice:
   checks_for_arm=[c for c in checks if c[0]['id'] in offline_slice]
   plan['groups']=[{**g,'questions':[q for q in g['questions'] if q['id'] in offline_slice]} for g in plan['groups'] if any(q['id'] in offline_slice for q in g['questions'])]
   plan.update(questions=len(checks_for_arm),criteria=sum(len(c.claims) for _,c,_ in checks_for_arm))
  else:checks_for_arm=checks
  runtimes=[];reused={};reused_followups={};resume_count=0
  if prior and (prior/provider/'question-plans').exists():shutil.copytree(prior/provider/'question-plans',arm/'question-plans')
  if prior and (prior/provider/'evaluation/evaluation.json').exists():
   resume_count=prepare_prefix(prior,provider,checks_for_arm,plan,schedule,arm/'evaluation',evidence)
   if not resume_count:
    turn=prior/provider/'evaluation/turns/rsvp-before/turn.json'
    if turn.exists():reused={'rsvp-before':str(turn)}
  if prior:
   # Reuse sealed author/reviewer plans; never pay to rewrite an existing follow-up.
   for ancestor in reversed(cap.origins(cap.load())[1:]):
    plans=Path(ancestor)/provider/'question-plans'
    if plans.exists():shutil.copytree(plans,arm/'question-plans',dirs_exist_ok=True)
   # Prefer completed turns; recover only genuinely missing post-report records.
   from .closed_answer_recovery import recover
   pending={r['id']:(r,contract) for r,contract,_ in checks_for_arm[resume_count:]}
   for ancestor in cap.origins(cap.load())[1:]:
    for qid,(row,contract) in pending.items():
     candidate=Path(ancestor)/provider/'evaluation/turns'/qid/'turn.json'
     if qid not in reused and candidate.exists():
      turn=TurnResult(**json.loads(candidate.read_text()));audit_transport(turn)
      if turn.prompt!=contract.question or turn.at!=row['at']:raise GatewayLifecycleError('Recovered answer contract differs')
      reused[qid]=str(candidate)
    for ready_path in (Path(ancestor)/provider).glob('checkpoint-*/reuse-turns/*/answer-ready.json'):
     captured=json.loads(ready_path.read_text())['native_turn']
     matched=[qid for qid,(row,contract) in pending.items() if captured['at']==row['at'] and captured['prompt']==contract.question]
     if not matched or matched[0] in reused:continue
     if len(matched)!=1:raise GatewayLifecycleError('Ambiguous closed answer recovery')
     qid=matched[0];completed=ready_path.parent/'completed-turn.json'
     if completed.exists():
      candidate=TurnResult(**json.loads(completed.read_text()));audit_transport(candidate);reused[qid]=str(completed)
     else:reused[qid]=recover(ready_path.parent,arm/(qid+'-recovered-turn.json'))
  if prior:
   from .closed_answer_recovery import recover_preclose
   for ancestor in cap.origins(cap.load())[1:]:
    for failed in (Path(ancestor)/provider).glob('checkpoint-*/reuse-turns/*/preclose.json'):
     p=json.loads(failed.read_text())
     if p.get('state')!='blocked':continue
     ready=json.loads((failed.parent/'answer-ready.json').read_text());t=ready['native_turn']
     matches=[qid for qid in reused if json.loads(Path(reused[qid]).read_text())['session_id']==t['session_id'] and json.loads(Path(reused[qid]).read_text())['prompt']!=t['prompt']]
     if len(matches)==1 and matches[0] not in reused_followups:
      reused_followups[matches[0]]=recover_preclose(failed.parent,arm/(matches[0]+'-followup-requalified'))
  class Runtime:
   from .checkpoint_sessions import PROTOCOL as native_session_checkpoint
   def __init__(self,group):
    nonlocal source
    self.group=group;self.root=arm/('checkpoint-'+str(group['id']));self.root.mkdir();self.entered=False;self.services=[];runtimes.append(self)
    self.home=self.root/'hermes-home';shutil.copytree(c/'hermes-home',self.home,symlinks=True)
    self.env=harness.turn_env(group['questions'][0]['at'],self.home,child['PACBENCH_TOOL_ALLOWLIST'],{**child,'HOME':str(self.root/'fallback-home')});Path(self.env['HOME']).mkdir()
    self.cwd=Path(os.environ.get('TMPDIR','/var/tmp'))/('pacbench-r2-'+hashlib.sha256(str(arm).encode()).hexdigest()[:16])
    self.cwd.mkdir(exist_ok=True)
    if list(self.cwd.iterdir()):raise GatewayLifecycleError('Native retained-session workspace is not empty')
    def before_batch(batch):publish({'kind':'ingest','provider':provider,'group_id':group['id'],'batch_ids':group['ready_batch_ids'],'batch_id':batch['id']})
    source.before_batch=before_batch
    start=time.monotonic()
    saved=source_proof(prior,provider,group['id']) if prior else None
    if prior and not saved.exists():
     # A completed checkpoint may have no pending questions. Seed from its
     # closed source store before importing only later required batches.
     candidates=[g for g in plan['groups'] if g['id']<group['id'] and source_proof(prior,provider,g['id']).exists()]
     if candidates:
      previous=candidates[-1];seed=json.loads(source_proof(prior,provider,previous['id']).read_text())
      verify_checkpoint(previous,seed,schedule,provider)
      if not set(seed['ingested_batch_ids'])<=set(group['ready_batch_ids']):raise GatewayLifecycleError('Recovery source store contains future data')
      shutil.rmtree(source.store,ignore_errors=False) if source.store.exists() else None;clone_observation(seed['baseline'],source.store)
      source.ingested=list(seed['ingested_batch_ids']);source.receipts=dict(seed['ingestion_receipts'])
      if provider=='mem0':source.clock_proofs=list(cap.read(seed['native_closure']['evidence'][0])['source_and_billing_clock_proofs'])
    if saved and saved.exists():
     self.proof=json.loads(saved.read_text());verify_checkpoint(group,self.proof,schedule,provider)
     if len(self.proof['ingested_batch_ids'])>len(source.ingested):
      shutil.rmtree(source.store,ignore_errors=False) if source.store.exists() else None;clone_observation(self.proof['baseline'],source.store)
      source.ingested=list(self.proof['ingested_batch_ids']);source.receipts=dict(self.proof['ingestion_receipts'])
      if provider=='mem0':
       native=cap.read(self.proof['native_closure']['evidence'][0]);source.clock_proofs=list(native['source_and_billing_clock_proofs'])
    else:self.proof=source.materialize(group,self.root/'baseline')
    atomic(self.root/'ingest-timing.json',{'wall_s':time.monotonic()-start,'ready_batch_ids':group['ready_batch_ids']})
    if provider=='reme':shutil.copytree((source.root if source else c)/'hermes-home/reme',self.home/'reme',dirs_exist_ok=True)
    verify_checkpoint(group,self.proof,schedule,provider)
    p=self.root/'source-proof.json';atomic(p,self.proof);self.proof_binding=cap.binding(p)
    from .closed_answer_recovery import prepare_sessions
    self.recovered_sessions=prepare_sessions([reused[q['id']] for q in group['questions'] if q['id'] in reused and q['id'] not in reused_followups],self.home,self.proof['manifest']['sha256'])
    if self.recovered_sessions:
     from .context_isolation import outside_project
     previous_cwds={record['turn'].context_isolation['native_cwd'] for record in self.recovered_sessions.values()}
     if len(previous_cwds)!=1:raise GatewayLifecycleError('Recovered native sessions have different isolated workspaces')
     self.cwd=Path(next(iter(previous_cwds)))
     if not self.cwd.is_dir() or not outside_project(self.cwd) or list(self.cwd.iterdir()):raise GatewayLifecycleError('Original isolated native workspace is unavailable or changed')
    rows=serial_rows(budget,root,prior) if budget else []
    self.receipt_ids=[r['id'] for r in rows if r.get('checkpoint_full_run_slot',{}).get('kind')=='ingest' and r['checkpoint_full_run_slot']['provider']==provider and r['checkpoint_full_run_slot']['batch_id'] in group['ready_batch_ids']]
    self.slot={'kind':'checkpoint','provider':provider,'group_id':group['id'],'batch_ids':group['ready_batch_ids'],
     'home':str(self.home),'checkpoint':self.proof['manifest']['sha256'],'proof':self.proof_binding,'ingest_receipt_ids':self.receipt_ids}
    if provider=='reme':self.slot['lifecycle_condition']=self.env['PACBENCH_NATIVE_PRECLOSE']
    publish(self.slot)
   def __enter__(self):
    clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
    self.owner=PersistentGateway(self.home,PYTHON,self.env,cwd=self.cwd,checkpoint=self.proof['manifest']['sha256'],
     observation_clock=clock,provider=provider,full_run=permission,cleanup_timeout=30)
    self.owner.__enter__();self.entered=True;return self
   def observation(self,q):
    if provider=='reme':
     from provider_adapters.reme_local import start
     service=self.root/(q['id']+'-service');(service/'hermes-home/plugins').mkdir(parents=True);(service/'hermes-home/reme').mkdir()
     observation=clone_observation(self.proof['baseline'],service/'reme-workspace')
     stop=start(service,self.env,'openai/gpt-6-luna',base,key);self.services.append((stop,service))
     setup=json.loads((service/'native-server-setup.json').read_text());observation.update(endpoint=setup['endpoint'],pid=setup['pid'])
    else:observation=clone_observation(self.proof['baseline'],self.root/(q['id']+'-observation'))
    self.active={**self.slot,'kind':'answer','question_id':q['id'],'observation':observation,'question_directory':str(arm/'evaluation/turns'/q['id'])}
    publish(self.active)
    return observation
   def reuse_observation(self,q,turn):
    ready=json.loads((Path(turn.hermes_gateway['receipt'])/'answer-ready.json').read_text())
    if ready['checkpoint']!=self.proof['manifest']['sha256']:raise GatewayLifecycleError('Reused answer source prefix changed')
    if turn.session_id in self.recovered_sessions:
     from .closed_answer_recovery import admit_session
     admit_session(self.owner,self.recovered_sessions[turn.session_id],q)
    self.active={**self.slot,'kind':'answer','question_id':q['id'],'observation':ready['observation'],'question_directory':str(arm/'evaluation/turns'/q['id'])}
    publish(self.active)
   def reuse_followup(self,q,prompt,at,session_id):
    if q['id'] not in reused_followups:return None
    path=Path(reused_followups[q['id']]);turn=TurnResult(**json.loads(path.read_text()))
    if at!=q['at'] or turn.session_id!=session_id or turn.prompt!=prompt:raise GatewayLifecycleError('Retained follow-up binding differs')
    audit_transport(turn);reused_followups.pop(q['id']);return turn
   def __exit__(self,*a):
    try:
     if self.entered:self.owner.__exit__(*a);self.entered=False
    finally:
     services,self.services=self.services,[]
     close_observation_services(services)
  provider_started=time.monotonic()
  entry={'state':'running'};report['providers'][provider]=entry;atomic(root/'report.json',report)
  jbase,jkey=settings(projects[provider]['judge'])
  client=ModelClient(key=jkey,model='openai/gpt-6-luna',url=jbase+'/chat/completions',record_dir=arm/'model-calls',json_mode=True,timeout_s=180,max_attempts=1)
  def boundary():
   if offline_stop_after and (arm/'evaluation/evaluation.json').exists():
    progress=json.loads((arm/'evaluation/evaluation.json').read_text())
    if progress['state']=='running' and sum(x['state'] in {'diagnostic','strict-ungraded'} for x in progress['checks'])>=offline_stop_after:
     from .stop import OperatorStop
     raise OperatorStop('Deliberate offline interruption')
   if budget:
    try:
     rows=serial_rows(budget,root,prior)
     if (root/'STOP').exists():raise SpendingBlocked('Existing admission stop; preserve completed checkpoints')
     from decimal import Decimal
     projects=[]
     for project in {r['project'] for r in rows}:
      own=[r for r in rows if r['project']==project and r['state']=='settled']
      projects.append({'project':project,'calls':len(own),'settled_upper_bound_usd':float(sum((Decimal(r['settled_charge_usd']) for r in own),Decimal(0))),'held_usd':0,'input_tokens':sum(r.get('input_tokens') or 0 for r in own),'output_tokens':sum(r.get('output_tokens') or 0 for r in own)})
     return {'projects':projects}
    except SpendingBlocked:raise
    except Exception as e:raise SpendingBlocked('Unsettled full-run attempt; no further paid dispatch') from e
   return {'projects':[]}
  def judge(prompt):
   runtime=runtimes[-1];slot=runtime.active;directory=Path(slot['question_directory'])
   turn_path=directory/'turn.json'
   if (directory/'followups/combined/turn.json').exists():turn_path=directory/'followups/combined/turn.json'
   turn=TurnResult(**json.loads(turn_path.read_text()));audit_transport(turn)
   if classify(provider,turn)['state']!='eligible':raise GatewayLifecycleError('Raw or unknown answer cannot reach model judge')
   strict=directory/'strict-memory.json'
   publish({**slot,'kind':'judge','prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'turn':cap.binding(turn_path),'strict':cap.binding(strict)})
   begin=time.monotonic()
   try:
    boundary()
    if prior:
     for ancestor in cap.origins(cap.load())[1:]:
      saved=Path(ancestor)/provider/'evaluation/turns'/slot['question_id']/'judgment.json'
      if saved.exists():
       cached=json.loads(saved.read_text())
       if cached.get('prompt')==prompt:
        atomic(arm/(slot['question_id']+'-reused-judge.json'),{'judgment':cap.binding(saved),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
        return cached['response']
    result=client(prompt);boundary();return result
   finally:
    with (arm/'judge-timing.jsonl').open('a') as f:f.write(json.dumps({'question_id':slot['question_id'],'wall_s':time.monotonic()-begin,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})+'\n')
    publish(slot)
  try:
   result=launch(checks=checks_for_arm,plan=plan,schedule=schedule,runtime_factory=Runtime,root=arm/'evaluation',judge=judge,
    env={**child,cap.ENV:json.dumps(permission)},reused_turns=reused,resume_prefix=resume_count,question_dir=arm/'question-plans',budget_snapshot=boundary,
    evidence_by_id=evidence,
    provenance_version='source-provenance-v2',diagnostic_version=DIAGNOSTIC_VERSION,judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL,turn_timeout_s=120)
   entry.update(state=result['state'],coverage=result.get('counts'),evaluation=str(arm/'evaluation/evaluation.json'))
   boundary()
   if result['state'] in {'budget_blocked','access_blocked','interrupted'}:break
  except SpendingBlocked as e:
   entry.update(state='budget_blocked',error_type=type(e).__name__,reason=str(e)[:300]);break
  except BaseException as e:
   entry.update(state='blocked',error_type=type(e).__name__,reason=str(e)[:300])
   atomic(root/'report.json',report)
   try:boundary()
   except SpendingBlocked as stop:
    entry.update(state='budget_blocked',error_type=type(stop).__name__,reason=str(stop)[:300]);break
  finally:
   for runtime in runtimes:runtime.__exit__(None,None,None)
   entry['end_to_end_wall_s']=time.monotonic()-provider_started
   atomic(root/'report.json',report)
 report['state']='terminal-with-per-arm-coverage';atomic(root/'report.json',report)
 return report
