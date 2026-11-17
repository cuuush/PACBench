"""Exact versioned three-arm scored r2 admission; no general scoring permission."""
from pathlib import Path
import hashlib,json,os
from .hermes_gateway import GatewayLifecycleError

from .canary_capability import TOOLS,check_tools,CanaryBusy,FROZEN,PROVIDERS
ENV='PACBENCH_CHECKPOINT_FULL_RUN'
PROTOCOL='r2-three-arm-scored-checkpoint-v1'
MAX_CALLS=2500

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def binding(path):return {'path':str(Path(path).resolve()),'sha256':sha(path)}
def read(b):
 p=Path(b['path'])
 if p.is_symlink() or sha(p)!=b['sha256']:raise GatewayLifecycleError('Full-run bound evidence changed')
 return json.loads(p.read_text())

def frozen(dataset=None):
 if dataset is not None:
  from .pizza_week_dataset import load
  return load(dataset)
 from .canary_capability import frozen as source
 return source(full=True)

def specification(dataset=None):
 _,schedule,_,plan=frozen(dataset) if dataset is not None else frozen()
 return {'full_frozen':FROZEN,'groups':plan['groups'],'schedule':schedule,
  'model':'openai/gpt-6-luna','profile':'luna-bedrock-global','endpoint':'amazon-bedrock/global',
  'scoring':'semantic-judge-date-scope-v2','strict_memory':'strict-memory-only-v1',
  'session_transfer':'native-cold-session-checkpoint-v1',
  'luna_compatibility':'temperature-omitted-default-v1',
  'memcal':'c19d216a7e5dd3913f69ab79c24d2d339bacef16','max_calls':MAX_CALLS}

def create(root,projects,configs,*,prior_root=None,dataset=None):
 from .gateway_reuse import atomic
 root=Path(root).resolve()
 expected={p:{r:'pacbench-'+hashlib.sha256(str((root/p).resolve()).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver','judge']} for p in PROVIDERS}
 if projects!=expected or set(configs)!=set(PROVIDERS):raise GatewayLifecycleError('Full-run accounting/config bindings differ')
 recovery={}
 if prior_root:
  prior_root=Path(prior_root).resolve()
  recovery={'root':str(prior_root),'report':binding(prior_root/'report.json'),'capability':binding(prior_root/'capability.json')}
 path=root/'capability.json';atomic(path,{'protocol':PROTOCOL,'root':str(root),'specification':specification(dataset) if dataset is not None else specification(),
  **({'dataset':str(Path(dataset).resolve())} if dataset is not None else {}),
  'projects':projects,'configs':configs,'slot_path':str(root/'active-slot.json'),'recovery':recovery})
 return binding(path)

def load(b=None):
 if any(os.environ.get(k) for k in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE','PACBENCH_CHECKPOINT_CANARY']):
  raise GatewayLifecycleError('Full-run cannot share render or other diagnostic capability')
 if b is None:
  value=os.environ.get(ENV)
  if not value:raise GatewayLifecycleError('Full-run capability absent')
  b=json.loads(value)
 c=read(b)
 dataset=c.get('dataset')
 required={'protocol','root','specification','projects','configs','slot_path','recovery'}
 if dataset is not None:required.add('dataset')
 if set(c)!=required or c['protocol']!=PROTOCOL or c['specification']!=(specification(dataset) if dataset is not None else specification()):raise GatewayLifecycleError('Full-run scope/contract/route differs')
 root=Path(c['root']).resolve()
 if Path(b['path']).resolve()!=root/'capability.json' or c['slot_path']!=str(root/'active-slot.json'):raise GatewayLifecycleError('Full-run paths differ')
 expected={p:{r:'pacbench-'+hashlib.sha256(str((root/p).resolve()).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver','judge']} for p in PROVIDERS}
 if c['projects']!=expected or set(c['configs'])!=set(PROVIDERS):raise GatewayLifecycleError('Full-run projects/configs differ')
 if c['recovery']:
  prior=read(c['recovery']['capability']);report=read(c['recovery']['report'])
  if (prior['root']!=c['recovery']['root'] or prior['protocol']!=PROTOCOL or prior['specification']['full_frozen']!=FROZEN or prior['configs']!=c['configs'] or report['frozen_inputs']['sha256']!=FROZEN):raise GatewayLifecycleError('Recovery source/condition differs')
 return c

def origins(c):
 """Verify the immutable recovery chain before including ancestor receipts."""
 result=[]
 while True:
  if c['root'] in result or len(result)>=8:raise GatewayLifecycleError('Invalid recovery ancestry')
  result.append(c['root'])
  if not c.get('recovery'):return result
  parent=read(c['recovery']['capability']);report=read(c['recovery']['report'])
  same=parent['specification']==c['specification']
  # One retained original capability predates the explicit compatibility label.
  # Its exact immutable config/source/contract is retained; all NEW requests
  # still require the explicit omission condition in admission().
  if not same and c['recovery']['capability']['sha256']=='33f63210fe01652c061c36b4ec133727578e4b2a351854f42a87f4e8ac4f9a2d':
   same={**parent['specification'],'luna_compatibility':'temperature-omitted-default-v1'}==c['specification']
  if (parent['root']!=c['recovery']['root'] or parent['protocol']!=PROTOCOL or
      not same or parent['configs']!=c['configs'] or
      report['frozen_inputs']['sha256']!=FROZEN):raise GatewayLifecycleError('Recovery ancestry condition differs')
  c=parent

def slot(c):
 p=Path(c['slot_path'])
 if p.is_symlink():raise GatewayLifecycleError('Full-run slot cannot be a link')
 s=json.loads(p.read_text())
 if s.get('provider') not in PROVIDERS or s.get('kind') not in {'ingest','checkpoint','answer','judge','retained_judge'}:raise GatewayLifecycleError('Full-run active slot absent')
 expected={'kind','provider','group_id','batch_ids'}
 expected|={'batch_id'} if s['kind']=='ingest' else {'home','checkpoint','proof','ingest_receipt_ids'}
 if s['kind'] in {'answer','judge','retained_judge'}:expected|={'question_id','observation','question_directory'}
 if s['kind'] in {'judge','retained_judge'}:expected|={'prompt_sha256','turn','strict'}
 if 'lifecycle_condition' in s:
  from .gateway.preclose import CONDITION
  if s['provider']!='reme' or s['lifecycle_condition']!=CONDITION:raise GatewayLifecycleError('Unknown canary lifecycle condition')
  expected.add('lifecycle_condition')
 if set(s)!=expected:raise GatewayLifecycleError('Full-run slot fields differ')
 groups=c['specification']['groups'];g=next((g for g in groups if g['id']==s.get('group_id')),None)
 if not g or s.get('batch_ids')!=g['ready_batch_ids'] or (s['kind']=='ingest' and s['batch_id'] not in g['ready_batch_ids']):raise GatewayLifecycleError('Full-run source prefix differs')
 return s,g

def ready(c,s,g):
 from .checkpoint_evaluator import verify_checkpoint
 proof=read(s['proof'])
 verify_checkpoint(g,proof,c['specification']['schedule'],s['provider'])
 if proof['manifest']['sha256']!=s['checkpoint']:raise GatewayLifecycleError('Full-run snapshot differs')
 if s['provider']=='memcal':
  for b in proof['native_closure']['evidence']:
   if read(b)['pins'].get('memcal')!=c['specification']['memcal']:raise GatewayLifecycleError('Full-run MemCal SHA differs')
 return proof

def owner(b,env,home,checkpoint,provider,clock):
 c=load(b);s,g=slot(c)
 if s['kind']!='checkpoint' or s['provider']!=provider or s['checkpoint']!=checkpoint or clock is None:
  raise GatewayLifecycleError('Full-run owner requires native checkpoint and clock')
 if str(Path(home).resolve())!=s['home'] or sha(Path(home)/'config.yaml')!=c['configs'][provider]:raise GatewayLifecycleError('Full-run stock configuration differs')
 if env.get('PACBENCH_TOOL_ALLOWLIST')!=','.join(sorted(TOOLS[provider])):raise GatewayLifecycleError('Full-run provider tool allowlist differs')
 if (env.get('PACBENCH_OFFLINE_CONFORMANCE') and env.get('PACBENCH_DIRECT')=='1') or env.get('PACBENCH_OPENROUTER_PROFILE')!='luna-bedrock-global' or env.get('PACBENCH_ROLE_ROUTES')!='luna-all-roles-v1':raise GatewayLifecycleError('Full-run funded route/condition differs')
 import yaml
 config=yaml.safe_load((Path(home)/'config.yaml').read_text())
 if (set(config)!={'model','memory','agent','provider_routing'} or
     config['model'].get('default')!='openai/gpt-6-luna' or config['model'].get('provider')!='openrouter' or
     config['provider_routing']!={'only':['amazon-bedrock/global']} or
     config['memory']!={'memory_enabled':False,'user_profile_enabled':False,'provider':provider}):
  raise GatewayLifecycleError('Full-run stock model/provider configuration differs')
 from .gateway_client import validate_route
 validate_route(config['model']['base_url'])
 if env.get('PACBENCH_DIRECT')=='1' and env.get('PACBENCH_LUNA_COMPATIBILITY')!=c['specification']['luna_compatibility']:raise GatewayLifecycleError('Reviewed Luna compatibility condition absent')
 if env.get('PACBENCH_NATIVE_PRECLOSE')!=s.get('lifecycle_condition'):raise GatewayLifecycleError('Full-run close condition binding differs')
 ready(c,s,g)
 return c

def question(b,owner,prompt,at,observation,session_id):
 c=load(b);s,g=slot(c)
 q=next((q for q in g['questions'] if q['id']==s.get('question_id')),None)
 if s['kind'] not in {'answer','judge'} or not q or at!=q['at']:
  raise GatewayLifecycleError('Full run requires frozen question/clock')
 if prompt!=q['prompt']:
  from .followups import load_combined_plan
  plan=read(binding(Path(s['question_directory'])/'followups/combined/question-plan.json'))
  # The production author/reviewer seals the plan before native dispatch.
  from .semantic_contract import load_contract
  from .followups import load_questions,combined_binding
  _,_,checks,_=frozen(c['dataset']) if 'dataset' in c else frozen()
  _,contract,policy=next(check for check in checks if check[0]['id']==q['id'])
  from .semantic_grade import Grade,GradedClaim
  from .followups import natural_eligibility
  raw=json.loads((Path(s['question_directory'])/'grade.json').read_text())
  grade=Grade(checkpoint=q['id'],slots=[GradedClaim(**x) for x in raw['claims']],correct=raw['correct'],missed=raw['missed'],misrepresented=raw['misrepresented'],recovery=raw['recovery'])
  eligible,_=natural_eligibility(grade)
  if plan.get('claim_ids')!=eligible:raise GatewayLifecycleError('Recovery is not the frozen eligible omission set')
  selected=[cl for cl in contract.claims if cl.id in eligible]
  initial=json.loads((Path(s['question_directory'])/'turn.json').read_text())
  load_combined_plan(Path(s['question_directory'])/'followups/combined/question-plan.json',
   {**combined_binding(contract,policy,selected),'known_context':[initial['answer']]})
  if not session_id or plan['question']!=prompt or initial['session_id']!=session_id:
   raise GatewayLifecycleError('Full-run recovery differs from reviewed native session')
 elif session_id:
  if q['id']!='lunch-refreshed' or not owner.session_context.get(session_id,{}).get('authored_pending'):
   raise GatewayLifecycleError('Unexpected inherited full-run session')
 if (s['provider']!=owner.provider or s['home']!=str(owner.home) or s['checkpoint']!=owner.checkpoint or
     sha(owner.home/'config.yaml')!=c['configs'][owner.provider] or s.get('observation')!=observation):
  raise GatewayLifecycleError('Full-run observation isolation differs')
 ready(c,s,g)
 return c

def admission(project,profile,body=None):
 if not os.environ.get(ENV):return None
 c=load();s,g=slot(c);provider=s['provider']
 allowed=[c['projects'][provider]['memory']]
 if s['kind']=='answer':allowed.append(c['projects'][provider]['driver'])
 if s['kind'] in {'judge','retained_judge'}:allowed=[c['projects'][provider]['judge']]
 if project not in allowed or profile!='luna-bedrock-global':raise GatewayLifecycleError('Full-run accounting role/model route differs')
 if s['kind']!='ingest':ready(c,s,g)
 if project==c['projects'][provider]['driver']:
  if body is None:raise GatewayLifecycleError('Full-run outgoing driver schema proof absent')
  check_tools(provider,body)
 if s['kind'] in {'judge','retained_judge'}:
  if os.environ.get('PACBENCH_LUNA_COMPATIBILITY')!='temperature-omitted-default-v1':raise GatewayLifecycleError('Judge compatibility condition absent')
  from .strict_memory import classify
  from .harness import TurnResult
  from .checkpoint_evaluator import audit_transport
  turn=TurnResult(**read(s['turn']));audit_transport(turn)
  q=next((q for q in g['questions'] if q['id']==s.get('question_id')),None)
  captured=json.loads((Path(turn.hermes_gateway['receipt'])/'answer-ready.json').read_text())
  if not q or captured['checkpoint']!=s['checkpoint'] or (turn.prompt!=q['prompt'] and not (Path(s['question_directory'])/'followups/combined/question-plan.json').exists()):
   raise GatewayLifecycleError('Judge answer does not bind the current source/contract')
  decision=read(s['strict'])
  if s['kind']=='retained_judge':
   from .observational_receipts import enabled
   expected=[Path(root)/provider/'evaluation/turns'/q['id']/'turn.json' for root in origins(c)]
   computed=classify(provider,turn)
   if not enabled() or Path(s['turn']['path']) not in expected or (computed['state']=='excluded' and decision.get('state')!='excluded'):
    raise GatewayLifecycleError('Retained practical judge requires original captured answer/provenance')
  elif decision.get('state')!='eligible' or classify(provider,turn)['state']!='eligible':
   raise GatewayLifecycleError('Raw or unknown answer cannot dispatch a judge')
  messages=(body or {}).get('messages',[])
  if len(messages)!=1 or messages[0].get('role')!='user' or hashlib.sha256(messages[0]['content'].encode()).hexdigest()!=s['prompt_sha256'] or (body or {}).get('tools'):
   raise GatewayLifecycleError('Judge prompt is not bound to current captured eligible answer')
 if s['kind']=='answer':
  folder=Path(s['home']).parent/'reuse-clock'
  pointer=json.loads((folder/'active.json').read_text())
  d=read({'path':str(folder/'questions'/(pointer['nonce']+'.json')),'sha256':pointer['sha256']})
  b=json.loads((folder/'bindings'/(d['nonce']+'.json')).read_text())
  from .hermes_gateway import _identity
  _identity(Path(s['home']),d['gateway_pid'])
  if b.get('question_sha256')!=d['question_sha256'] or not b.get('session_id'):raise GatewayLifecycleError('Full-run native request binding absent')
  q=next((q for q in g['questions'] if q['id']==s.get('question_id')),None)
  from datetime import datetime
  initial=hashlib.sha256(q['prompt'].encode()).hexdigest() if q else None
  authored=q and q['id']=='lunch-refreshed' and d.get('continuation_reason')=='authored-checkpoint'
  recovery=d.get('continuation_reason')=='omission-recovery'
  if (not q or d.get('kind') not in {'fresh','continuation'} or d['home']!=s['home'] or d['checkpoint']!=s['checkpoint'] or
      (d['kind']=='fresh' and (d['at']!=q['at'] or d['question_sha256']!=initial)) or
      (d['kind']=='continuation' and not (authored or recovery))):
   raise GatewayLifecycleError('Full-run request clock/session proof differs')
  if recovery:
   p=read(binding(Path(s['question_directory'])/'followups/combined/question-plan.json'))
   if hashlib.sha256(p['question'].encode()).hexdigest()!=d['question_sha256'] or not datetime.fromisoformat(q['at'])<=datetime.fromisoformat(d['at'])<datetime.fromisoformat(q['horizon']):
    raise GatewayLifecycleError('Full-run recovery clock/prompt differs')
  if authored and (d['at']!=q['at'] or d['question_sha256']!=initial):raise GatewayLifecycleError('Authored continuation changed')
 return {'origins':origins(c),'capability':json.loads(os.environ[ENV]),'root':c['root'],'slot':s,'projects':[x for p in c['projects'].values() for x in p.values()]}

def serial(calls,permit):
 if permit is None:return
 own=[r for r in calls if r.get('checkpoint_full_run_root') in permit.get('origins',[permit['root']])]
 from .observational_receipts import enabled,MODE
 if enabled():
  active=[r for r in own if r.get('accounting_mode')==MODE and not r.get('finished')]
  if active:raise CanaryBusy('Current observational sender still dispatching')
  return
 if permit['slot']['kind']!='ingest':
  ids=permit['slot'].get('ingest_receipt_ids',[])
  matched=[r for r in own if r['id'] in ids and r.get('checkpoint_full_run_slot',{}).get('kind')=='ingest']
  if not ids or len(matched)!=len(ids):raise GatewayLifecycleError('Full-run native checkpoint reservations/receipts absent')
  if {r['checkpoint_full_run_slot']['batch_id'] for r in matched}!=set(permit['slot']['batch_ids']):raise GatewayLifecycleError('Full-run receipt/source prefix differs')
 if len({r['id'] for r in own})!=len(own):raise GatewayLifecycleError('Full-run duplicate attempt identity')
 if len(own)>=MAX_CALLS:raise GatewayLifecycleError('Full-run attempt ceiling reached')
 for r in own:
  if rejected_without_dispatch(r):continue
  if r.get('state')=='reserved' and not r.get('error_label'):continue
  if r.get('state')!='settled' or r.get('error_label') or not r.get('billing_receipt'):
   raise GatewayLifecycleError('Full-run unsettled/invalid receipt; global serial admission stopped')
  b=r['billing_receipt'];from .openrouter_task import STATE
  if sha(STATE/b['relative_path'])!=b['sha256']:raise GatewayLifecycleError('Full-run settlement receipt changed')

 if any(r.get('state')=='reserved' for r in own):raise CanaryBusy('Full-run serial native request still in flight')


def rejected_without_dispatch(row):
 """Validate a separately audited native pre-dispatch release, never zero usage."""
 if row.get('state')!='rejected_no_dispatch':return False
 event=row.get('no_dispatch_release') or {};proof=row.get('private_error_body') or {}
 if (row.get('accounted')!='0' or event.get('reservation_id')!=row['id'] or
     event.get('classification')!='rejected_no_dispatch' or not proof.get('body_complete') or
     event.get('evidence_sha256')!=proof.get('sha256') or row.get('upstream_request_id')):
  raise GatewayLifecycleError('Routing release proof invalid')
 from .openrouter_task import STATE
 if sha(STATE/proof['relative_path'])!=proof['sha256']:raise GatewayLifecycleError('Routing rejection receipt changed')
 return True
