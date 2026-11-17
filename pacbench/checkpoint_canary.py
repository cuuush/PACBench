"""Three-arm native transport diagnostic. No judge, follow-up or scored launcher."""
from pathlib import Path
import os,json,hashlib,shutil,subprocess,sqlite3,time
from dataclasses import asdict
from . import canary_capability as cap
from .gateway_reuse import atomic,clone_observation,PersistentGateway
from .checkpoint_evaluator import CheckpointEvaluator,audit_transport,verify_checkpoint
from .checkpoint_clock import CheckpointClock
from .hermes_gateway import GatewayLifecycleError

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'vendor/venv-hermes-isolated/bin/python'
MEMCAL=Path('/workspace/memcal-review-wiki-retraction')
MEM0_PLUGIN=ROOT/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0/repeat-1/mem0/hermes-home/plugins/mem0'


def profile(root,provider,base):
 from . import harness
 root=Path(root);root.mkdir(exist_ok=True)
 plugin=MEMCAL/'integrations/hermes/memcal' if provider=='memcal' else MEM0_PLUGIN if provider=='mem0' else None
 harness.provision_home(root/'hermes-home',provider_plugin_src=plugin,provider_name=provider,
  model='openai/gpt-6-luna',model_provider='openrouter',model_base_url=base)
 if provider=='reme':(root/'hermes-home/plugins/reme').symlink_to(ROOT/'vendor/reme/integrations/hermes_agent',target_is_directory=True)


def serial_rows(budget,root):
 with budget.locked(write=False) as state:
  rows=[r for r in state['calls'] if r.get('checkpoint_canary_root')==str(root)]
  if any(r['state']!='settled' or r.get('error_label') or not r.get('billing_receipt') for r in rows):
   raise GatewayLifecycleError('Canary accounting boundary: invalid or unsettled attempt')
  return rows


def close_observation_services(services):
 from .hermes_gateway import _live
 for stop,folder in services:
  result=stop();atomic(Path(folder)/'native-observation-shutdown.json',result)
  if (result.get('forced_kill') or result.get('returncode') not in (0,-15) or
      result.get('native_shutdown_complete') is not True or _live(result['pid'])):
   raise GatewayLifecycleError('Native observation service closure unverified; no next checkpoint')


def run(root,env,*,settings,budget=None,resume=False,only_reme=False,preclose_recovery=False):
 """Same production path for local synthetic completions and guarded live calls."""
 from . import harness
 from .document_ingestion import prepare_sources
 from .native_checkpoint_sources import Mem0SourceCheckpoints,MemcalSourceCheckpoints,ReMeSourceCheckpoints
 from .strict_memory import classify
 root=Path(root).resolve()
 if not resume:root.mkdir(exist_ok=False)
 if preclose_recovery and (not resume or not only_reme):raise GatewayLifecycleError('Pre-close recovery is the isolated ReMe slice only')
 if only_reme and not resume and budget is not None:raise GatewayLifecycleError('Funded ReMe continuation must preserve prior valid work')
 work=root/('reme-preclose-recovery-v3' if preclose_recovery else 'tool-wiring-recovery-v2') if resume else root
 if resume:work.mkdir(exist_ok=False)
 preparation,schedule,checks,full=cap.frozen();canonical={};configs={};projects={}
 native_fixture=root/'native-source-fixture'
 bundle=json.loads((preparation/'bundle.json').read_text());messages=[m for v in bundle['lives'].values() for m in v['messages']]
 source_ids={sid for b in schedule[:2] for sid in b['source_ids']};messages=[m for m in messages if m['source_id'] in source_ids]
 made=prepare_sources(native_fixture,messages,'America/New_York',native_fixtures=True) if not resume else [b for b in schedule[:2]]
 for b in made:
  original=next(s for s in schedule if s['id']==b['id'])
  if cap.sha(native_fixture/b['source_file'])!=original['document_sha256'] or b['source_ids']!=original['source_ids']:
   raise GatewayLifecycleError('Canary native fixture differs from frozen source')
 for provider in cap.PROVIDERS:
  arm=root/provider
  if not resume:arm.mkdir()
  c=arm/'canonical'
  if not resume:c.mkdir()
  runid=hashlib.sha256(str(arm).encode()).hexdigest()[:12]
  projects[provider]={role:f'pacbench-{runid}-{provider}-{role}' for role in ['memory','driver']}
  base,driverkey=settings(projects[provider]['driver']);mbase,memorykey=settings(projects[provider]['memory'])
  if not resume:profile(c,provider,base);(c/'fallback-home').mkdir()
  child={**env,'HOME':str(c/'fallback-home'),'MEMCAL_SRC':str(MEMCAL),'PACBENCH_MEMCAL_REUSE_REVISION':cap.specification()['memcal'],
   'PACBENCH_NO_BUILTIN_MEMORY':'1','PACBENCH_NO_SESSION_SEARCH':'1','PACBENCH_PROVIDER_ONLY':'1','HERMES_ENABLE_PROJECT_PLUGINS':'0',
   'OPENROUTER_API_KEY':driverkey,'OPENROUTER_API_BASE':base,'OPENROUTER_BASE_URL':base,'OPENAI_API_KEY':memorykey,
   'PACBENCH_USAGE_ROLES':json.dumps({projects[provider]['driver']:'hermes',projects[provider]['memory']:'memory_provider'}),
   'PACBENCH_TOOL_ALLOWLIST':','.join(sorted(cap.TOOLS[provider])),'PACBENCH_SOURCE_ARGUMENT_RULES':'{}',
   'TZ':'America/New_York','MEM0_TELEMETRY':'false','PACBENCH_STOP_FILE':str(root/'STOP'),'PACBENCH_DIRECT_STOP_ON_429':str(root/'STOP')}
  if provider!='reme':child.pop('PACBENCH_NATIVE_PRECLOSE',None)
  if provider=='memcal':
   from .native_pilot import memcal_model_settings
   child.update(memcal_model_settings('openai/gpt-6-luna',mbase,memorykey));child['MEMCAL_FALLBACK_PROVIDER']=''
  elif provider=='mem0' and not resume:
   cfg=json.loads((MEM0_PLUGIN.parents[1]/'mem0.json').read_text())
   cfg['user_id']='canary-'+runid;cfg['oss']['llm']['config'].update(api_key=memorykey,model='openai/gpt-6-luna',openai_base_url=mbase)
   (c/'hermes-home/mem0.json').write_text(json.dumps(cfg))
  if not only_reme or provider=='reme':
   from .native_pilot import probe
   native=probe(c,provider,child)
   if sorted(native['tools'])!=sorted(cap.TOOLS[provider]):raise GatewayLifecycleError('Native canary tool probe differs')
   atomic(work/(provider+'-native-tools.json'),native)
  canonical[provider]=(c,child,mbase,memorykey)
  configs[provider]=cap.sha(c/'hermes-home/config.yaml')
 permission=cap.binding(root/'capability.json') if resume else cap.create(root,projects,configs)
 if resume:
  previous=cap.load(permission)
  if previous['projects']!=projects or previous['configs']!=configs:raise GatewayLifecycleError('Canary recovery capability/configuration changed')
 env[cap.ENV]=json.dumps(permission)
 os.environ[cap.ENV]=json.dumps(permission)
 for c,child,_,_ in canonical.values():child[cap.ENV]=json.dumps(permission)
 report={'protocol':cap.PROTOCOL,'condition':'canary-diagnostics-not-Pizza-Week-scores','capability':permission,'providers':{},'scored_questions':0,'pacbench_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
 if resume:report['recovery']={'prior_report':cap.binding(root/'report.json'),'reason':('Premature explicit viewer close raced a productive native writer; pre-close quiescence now precedes unchanged native shutdown' if preclose_recovery else 'Empty explicit tool allowlist invalidated MemCal/Mem0 answer condition; original ReMe zero-tool integration unchanged'),'imports_reused_without_replay':True}
 atomic(work/'report.json',report)
 def publish(value):atomic(root/'active-slot.json',value)
 for provider in (('reme',) if only_reme else cap.PROVIDERS):
  arm=work/provider
  if resume:arm.mkdir()
  c,child,base,key=canonical[provider]
  source=None if resume else (MemcalSourceCheckpoints(native_fixture,c,schedule,child,PYTHON,timeout_s=120) if provider=='memcal' else
   Mem0SourceCheckpoints(preparation,c,schedule,child,PYTHON,timeout_s=120) if provider=='mem0' else
   ReMeSourceCheckpoints(preparation,c,schedule,child,ROOT/'vendor/venv-reme/bin/python',model='openai/gpt-6-luna',base=base,key=key))
  plan=json.loads(json.dumps({**full,'provider':provider}))
  for g in plan['groups']:
   for q in g['questions']:q['logical_session']=q['id']
  runtimes=[]
  class Runtime:
   def __init__(self,group):
    nonlocal source
    self.group=group;self.root=arm/('checkpoint-'+str(group['id']));self.root.mkdir();self.entered=False;self.services=[];runtimes.append(self)
    self.home=self.root/'hermes-home';shutil.copytree(c/'hermes-home',self.home,symlinks=True)
    self.env=harness.turn_env(group['questions'][0]['at'],self.home,child['PACBENCH_TOOL_ALLOWLIST'],{**child,'HOME':str(self.root/'fallback-home')});Path(self.env['HOME']).mkdir()
    self.cwd=Path(os.environ.get('TMPDIR','/var/tmp'))/('pacbench-canary-'+hashlib.sha256(str(self.root).encode()).hexdigest()[:16]);self.cwd.mkdir(exist_ok=False)
    publish({'kind':'ingest','provider':provider,'group_id':group['id'],'batch_ids':group['ready_batch_ids'],'batch_id':group['ready_batch_ids'][-1]})
    prior=root/provider/('checkpoint-'+str(group['id']))/'source-proof.json'
    if resume and prior.exists():
     self.proof=json.loads(prior.read_text())
     verify_checkpoint(group,self.proof,schedule,provider)
    else:
     if source is None:
      if provider!='reme':raise GatewayLifecycleError('Missing reusable source proof; no automatic re-ingestion')
      first=root/provider/'checkpoint-1/source-proof.json';seed=json.loads(first.read_text())
      verify_checkpoint(plan['groups'][0],seed,schedule,provider)
      rebuilt=arm/'canonical';rebuilt.mkdir();shutil.copytree(c/'hermes-home',rebuilt/'hermes-home',symlinks=True)
      source=ReMeSourceCheckpoints(preparation,rebuilt,schedule,child,ROOT/'vendor/venv-reme/bin/python',model='openai/gpt-6-luna',base=base,key=key)
      clone_observation(seed['baseline'],source.store)
      source.ingested=list(seed['ingested_batch_ids']);source.receipts=dict(seed['ingestion_receipts'])
     self.proof=source.materialize(group,self.root/'baseline')
    if provider=='reme':shutil.copytree((source.root if source else c)/'hermes-home/reme',self.home/'reme',dirs_exist_ok=True)
    verify_checkpoint(group,self.proof,schedule,provider)
    p=self.root/'source-proof.json';atomic(p,self.proof);self.proof_binding=cap.binding(p)
    rows=serial_rows(budget,root) if budget else []
    self.receipt_ids=[r['id'] for r in rows if r.get('checkpoint_canary_slot',{}).get('kind')=='ingest' and r['checkpoint_canary_slot']['provider']==provider and r['checkpoint_canary_slot']['batch_id'] in group['ready_batch_ids']]
    self.slot={'kind':'checkpoint','provider':provider,'group_id':group['id'],'batch_ids':group['ready_batch_ids'],
     'home':str(self.home),'checkpoint':self.proof['manifest']['sha256'],'proof':self.proof_binding,'ingest_receipt_ids':self.receipt_ids}
    if preclose_recovery:self.slot['lifecycle_condition']=self.env['PACBENCH_NATIVE_PRECLOSE']
    publish(self.slot)
   def __enter__(self):
    clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
    self.owner=PersistentGateway(self.home,PYTHON,self.env,cwd=self.cwd,checkpoint=self.proof['manifest']['sha256'],
     observation_clock=clock,provider=provider,canary=permission,cleanup_timeout=30)
    self.owner.__enter__();self.entered=True;return self
   def observation(self,q):
    if provider=='reme':
     from provider_adapters.reme_local import start
     service=self.root/(q['id']+'-service');(service/'hermes-home/plugins').mkdir(parents=True);(service/'hermes-home/reme').mkdir()
     observation=clone_observation(self.proof['baseline'],service/'reme-workspace')
     stop=start(service,self.env,'openai/gpt-6-luna',base,key);self.services.append((stop,service))
     setup=json.loads((service/'native-server-setup.json').read_text());observation.update(endpoint=setup['endpoint'],pid=setup['pid'])
    else:observation=clone_observation(self.proof['baseline'],self.root/(q['id']+'-observation'))
    publish({**self.slot,'kind':'answer','question_id':q['id'],'observation':observation})
    return observation
   def __exit__(self,*a):
    try:
     if self.entered:self.owner.__exit__(*a);self.entered=False
    finally:
     services,self.services=self.services,[]
     close_observation_services(services)
  entry={'state':'running','turns':[]};report['providers'][provider]=entry;atomic(work/'report.json',report)
  transport=CheckpointEvaluator(plan,schedule,Runtime,arm/'transport')
  try:
   for row,contract,_policy in checks:
    folder=arm/'turns'/row['id'];transport.before_check(row,folder)
    prior_report=json.loads((root/'report.json').read_text()) if resume else {}
    valid=next((t for t in prior_report.get('providers',{}).get(provider,{}).get('turns',[]) if provider=='reme' and t['id']==row['id']),None)
    if valid:
     from .harness import TurnResult
     # The only unchanged zero-tool shipped integration. Audit original native
     # capture; skip dispatch entirely, never inject its answer into a session.
     old=json.loads(Path(valid['receipt']).read_text());audit_transport(TurnResult(**old))
     entry['turns'].append({**valid,'reused_valid_native_turn':True});atomic(work/'report.json',report);continue
    turn=transport(contract.question,at=row['at'],timeout_s=120)
    audit_transport(turn);atomic(folder/'turn.json',asdict(turn));decision=classify(provider,turn)
    if budget:serial_rows(budget,root)
    entry['turns'].append({'id':row['id'],'at':row['at'],'answer':turn.answer,'session_id':turn.session_id,
     'tool_calls':len(turn.tool_calls),'tool_results':len(turn.tool_results),'lifecycle_timing':turn.lifecycle_timing,
     'strict_memory':decision,'retention_credit':'ungraded canary; raw/unknown excluded',
     'sanity':sanity(row['id'],turn.answer),'receipt':str(folder/'turn.json')})
    atomic(work/'report.json',report)
   entry['state']='native-transport-complete-not-scored'
  except BaseException as e:
   entry.update(state='blocked',error_type=type(e).__name__,reason=str(e)[:300]);atomic(work/'report.json',report)
   if budget:
    try:serial_rows(budget,root)
    except GatewayLifecycleError:raise
  finally:
   transport.close()
   for runtime in runtimes:runtime.__exit__(None,None,None)
  atomic(work/'report.json',report)
 report['state']='canary-finished-with-independent-arm-outcomes';atomic(work/'report.json',report)
 return report


def sanity(identifier,answer):
 """Cheap observations, not semantic grades. Never promote raw/unknown retention."""
 lower=answer.lower()
 if identifier=='rsvp-before':return {'maya_mentioned':'maya' in lower,'leo_mentioned':'leo' in lower,'tess_mentioned':'tess' in lower}
 if identifier=='cancellation-before':return {'uncertainty_word_observed':any(s in lower for s in ['unclear','which','unknown','cannot','not enough','can’t'])}
 return {'noon_mentioned':any(s in lower for s in ['noon','12:00','12 pm']),'marios_mentioned':'mario' in lower,'pine_mentioned':'pine' in lower}
