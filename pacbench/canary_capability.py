"""Exact, bounded r2 transport qualification; never a scored-run permission."""
from pathlib import Path
import hashlib,json,os
from .hermes_gateway import GatewayLifecycleError

ENV='PACBENCH_CHECKPOINT_CANARY'
PROTOCOL='r2-three-arm-checkpoint-canary-v1'
FROZEN='a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'
IDS=('rsvp-before','cancellation-before','rsvp-after')
PROVIDERS=('memcal','mem0','reme')
MAX_CALLS=90
TOOLS={'memcal':['memcal_open','memcal_search_wiki','memcal_open_source','memcal_conversation','memcal_list_days','memcal_list_month','memcal_search_archive','memcal_activity','memcal_refresh','memcal_add','memcal_update','memcal_schedule','memcal_move_once','memcal_merge','memcal_drop','memcal_todo','memcal_answer','memcal_note','memcal_alias','memcal_reviewed'],
 'mem0':['mem0_search','mem0_add','mem0_update','mem0_delete'],'reme':[]}

def check_tools(provider,request):
 names=[t.get('function',{}).get('name') for t in request.get('tools',[])]
 if sorted(names)!=sorted(TOOLS[provider]):raise GatewayLifecycleError('Canary driver lacks the exact shipped provider tool catalog')


class CanaryBusy(GatewayLifecycleError):
 """An identified native request is in flight; not permission to replay it."""
 pass

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def binding(path):return {'path':str(Path(path).resolve()),'sha256':sha(path)}
def read(b):
 p=Path(b['path'])
 if p.is_symlink() or sha(p)!=b['sha256']:raise GatewayLifecycleError('Canary bound evidence changed')
 return json.loads(p.read_text())

def frozen(*,full=False):
 from .checkpoint_evaluator import freeze_inputs
 repo=Path(__file__).resolve().parents[1]
 preparation=repo/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0'
 expected=json.loads((repo/'runs/pizza-week-evaluator-reuse-offline-20261115/r2-input-preflight.json').read_text())['files']
 schedule=[]
 for p in sorted((preparation/'sources').glob('*/source.json')):
  r=json.loads(p.read_text());schedule.append({'id':p.parent.name,'at':r['at'],'source_ids':r['source_ids'],
   'source_file':str((p.parent/'messages.txt').relative_to(preparation)),'document_sha256':r['document_sha256']})
 checks,plan=freeze_inputs(preparation,schedule,'mem0',expected)
 if plan['frozen_inputs']['sha256']!=FROZEN:raise GatewayLifecycleError('Canary full frozen identity differs')
 all_checks=checks
 checks=[c for c in checks if c[0]['id'] in IDS]
 groups=[{**g,'questions':[q for q in g['questions'] if q['id'] in IDS]} for g in plan['groups'] if any(q['id'] in IDS for q in g['questions'])]
 if tuple(r['id'] for r,_,_ in checks)!=IDS or [g['ready_batch_ids'] for g in groups]!=[['2026-10-05'],['2026-10-05','2026-10-06']]:raise GatewayLifecycleError('Canary slice differs')
 # Bind exact source-ID order/transport, not only membership of the full corpus.
 bundle=json.loads((preparation/'bundle.json').read_text());messages=[m for life in bundle['lives'].values() for m in life['messages']]
 from .document_ingestion import daily_batches,document_text
 batches=dict(daily_batches(messages,'America/New_York'))
 for b in schedule:
  native=json.loads((preparation/'sources'/b['id']/'messages.json').read_text())
  if native!=batches[b['id']] or [m['source_id'] for m in native]!=b['source_ids'] or hashlib.sha256(document_text(native,b['at']).encode()).hexdigest()!=b['document_sha256']:
   raise GatewayLifecycleError('Canary source order or transport differs')
 if full:return preparation,schedule,all_checks,plan
 return preparation,schedule,checks,{**plan,'groups':groups,'questions':3,'criteria':sum(len(c.claims) for _,c,_ in checks)}

def specification():
 _,schedule,_,plan=frozen()
 return {'full_frozen':FROZEN,'groups':plan['groups'],'schedule':schedule[:2],
  'model':'openai/gpt-6-luna','profile':'luna-bedrock-global','endpoint':'amazon-bedrock/global',
  'memcal':'c19d216a7e5dd3913f69ab79c24d2d339bacef16','max_calls':MAX_CALLS}

def create(root,projects,configs):
 from .gateway_reuse import atomic
 root=Path(root).resolve()
 expected={p:{r:'pacbench-'+hashlib.sha256(str((root/p).resolve()).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver']} for p in PROVIDERS}
 if projects!=expected or set(configs)!=set(PROVIDERS):raise GatewayLifecycleError('Canary accounting/config bindings differ')
 path=root/'capability.json';atomic(path,{'protocol':PROTOCOL,'root':str(root),'specification':specification(),
  'projects':projects,'configs':configs,'slot_path':str(root/'active-slot.json')})
 return binding(path)

def load(b=None):
 if any(os.environ.get(k) for k in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE','PACBENCH_CHECKPOINT_FULL_RUN']):
  raise GatewayLifecycleError('Canary cannot share render or other diagnostic capability')
 if b is None:
  value=os.environ.get(ENV)
  if not value:raise GatewayLifecycleError('Canary capability absent')
  b=json.loads(value)
 c=read(b)
 if set(c)!={'protocol','root','specification','projects','configs','slot_path'} or c['protocol']!=PROTOCOL or c['specification']!=specification():raise GatewayLifecycleError('Canary scope/contract/route differs')
 root=Path(c['root']).resolve()
 if Path(b['path']).resolve()!=root/'capability.json' or c['slot_path']!=str(root/'active-slot.json'):raise GatewayLifecycleError('Canary paths differ')
 expected={p:{r:'pacbench-'+hashlib.sha256(str((root/p).resolve()).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver']} for p in PROVIDERS}
 if c['projects']!=expected or set(c['configs'])!=set(PROVIDERS):raise GatewayLifecycleError('Canary projects/configs differ')
 return c

def slot(c):
 p=Path(c['slot_path'])
 if p.is_symlink():raise GatewayLifecycleError('Canary slot cannot be a link')
 s=json.loads(p.read_text())
 if s.get('provider') not in PROVIDERS or s.get('kind') not in {'ingest','checkpoint','answer'}:raise GatewayLifecycleError('Canary active slot absent')
 expected={'kind','provider','group_id','batch_ids'}
 expected|={'batch_id'} if s['kind']=='ingest' else {'home','checkpoint','proof','ingest_receipt_ids'}
 if s['kind']=='answer':expected|={'question_id','observation'}
 if 'lifecycle_condition' in s:
  from .gateway.preclose import CONDITION
  if s['provider']!='reme' or s['lifecycle_condition']!=CONDITION:raise GatewayLifecycleError('Unknown canary lifecycle condition')
  expected.add('lifecycle_condition')
 if set(s)!=expected:raise GatewayLifecycleError('Canary slot fields differ')
 groups=c['specification']['groups'];g=next((g for g in groups if g['id']==s.get('group_id')),None)
 if not g or s.get('batch_ids')!=g['ready_batch_ids'] or (s['kind']=='ingest' and s['batch_id']!=g['ready_batch_ids'][-1]):raise GatewayLifecycleError('Canary source prefix differs')
 return s,g

def ready(c,s,g):
 from .checkpoint_evaluator import verify_checkpoint
 proof=read(s['proof'])
 verify_checkpoint(g,proof,c['specification']['schedule'],s['provider'])
 if proof['manifest']['sha256']!=s['checkpoint']:raise GatewayLifecycleError('Canary snapshot differs')
 if s['provider']=='memcal':
  for b in proof['native_closure']['evidence']:
   if read(b)['pins'].get('memcal')!=c['specification']['memcal']:raise GatewayLifecycleError('Canary MemCal SHA differs')
 return proof

def owner(b,env,home,checkpoint,provider,clock):
 c=load(b);s,g=slot(c)
 if s['kind']!='checkpoint' or s['provider']!=provider or s['checkpoint']!=checkpoint or clock is None:
  raise GatewayLifecycleError('Canary owner requires native checkpoint and clock')
 if str(Path(home).resolve())!=s['home'] or sha(Path(home)/'config.yaml')!=c['configs'][provider]:raise GatewayLifecycleError('Canary stock configuration differs')
 if env.get('PACBENCH_TOOL_ALLOWLIST')!=','.join(sorted(TOOLS[provider])):raise GatewayLifecycleError('Canary provider tool allowlist differs')
 if env.get('PACBENCH_OFFLINE_CONFORMANCE') or env.get('PACBENCH_OPENROUTER_PROFILE')!='luna-bedrock-global' or env.get('PACBENCH_ROLE_ROUTES')!='luna-all-roles-v1':raise GatewayLifecycleError('Canary funded route/condition differs')
 import yaml
 config=yaml.safe_load((Path(home)/'config.yaml').read_text())
 if (set(config)!={'model','memory','agent','provider_routing'} or
     config['model'].get('default')!='openai/gpt-6-luna' or config['model'].get('provider')!='openrouter' or
     config['provider_routing']!={'only':['amazon-bedrock/global']} or
     config['memory']!={'memory_enabled':False,'user_profile_enabled':False,'provider':provider}):
  raise GatewayLifecycleError('Canary stock model/provider configuration differs')
 from .gateway_client import validate_route
 validate_route(config['model']['base_url'])
 if env.get('PACBENCH_NATIVE_PRECLOSE')!=s.get('lifecycle_condition'):raise GatewayLifecycleError('Canary close condition binding differs')
 ready(c,s,g)
 return c

def question(b,owner,prompt,at,observation,session_id):
 c=load(b);s,g=slot(c)
 q=next((q for q in g['questions'] if q['id']==s.get('question_id')),None)
 if s['kind']!='answer' or not q or q['prompt']!=prompt or q['at']!=at or session_id:
  raise GatewayLifecycleError('Canary requires exact blank question session/clock')
 if (s['provider']!=owner.provider or s['home']!=str(owner.home) or s['checkpoint']!=owner.checkpoint or
     sha(owner.home/'config.yaml')!=c['configs'][owner.provider] or s.get('observation')!=observation):raise GatewayLifecycleError('Canary observation isolation differs')
 ready(c,s,g)
 return c

def admission(project,profile,body=None):
 if not os.environ.get(ENV):return None
 c=load();s,g=slot(c);provider=s['provider']
 allowed=[c['projects'][provider]['memory']]
 if s['kind']=='answer':allowed.append(c['projects'][provider]['driver'])
 if project not in allowed or profile!='luna-bedrock-global':raise GatewayLifecycleError('Canary accounting role/model route differs')
 if s['kind']!='ingest':ready(c,s,g)
 if project==c['projects'][provider]['driver']:
  if body is None:raise GatewayLifecycleError('Canary outgoing driver schema proof absent')
  check_tools(provider,body)
 if s['kind']=='answer':
  folder=Path(s['home']).parent/'reuse-clock'
  pointer=json.loads((folder/'active.json').read_text())
  d=read({'path':str(folder/'questions'/(pointer['nonce']+'.json')),'sha256':pointer['sha256']})
  b=json.loads((folder/'bindings'/(d['nonce']+'.json')).read_text())
  from .hermes_gateway import _identity
  _identity(Path(s['home']),d['gateway_pid'])
  if b.get('question_sha256')!=d['question_sha256'] or not b.get('session_id'):raise GatewayLifecycleError('Canary native request binding absent')
  q=next((q for q in g['questions'] if q['id']==s.get('question_id')),None)
  if (not q or d['at']!=q['at'] or d['question_sha256']!=hashlib.sha256(q['prompt'].encode()).hexdigest() or d['home']!=s['home'] or
      d['checkpoint']!=s['checkpoint'] or d['kind']!='fresh'):raise GatewayLifecycleError('Canary request clock/session proof differs')
 return {'capability':json.loads(os.environ[ENV]),'root':c['root'],'slot':s,'projects':[x for p in c['projects'].values() for x in p.values()]}

def serial(calls,permit):
 if permit is None:return
 own=[r for r in calls if r.get('checkpoint_canary_root')==permit['root']]
 if permit['slot']['kind']!='ingest':
  ids=permit['slot'].get('ingest_receipt_ids',[])
  matched=[r for r in own if r['id'] in ids and r.get('checkpoint_canary_slot',{}).get('kind')=='ingest']
  if not ids or len(matched)!=len(ids):raise GatewayLifecycleError('Canary native checkpoint reservations/receipts absent')
  if {r['checkpoint_canary_slot']['batch_id'] for r in matched}!=set(permit['slot']['batch_ids']):raise GatewayLifecycleError('Canary receipt/source prefix differs')
 if len({r['id'] for r in own})!=len(own):raise GatewayLifecycleError('Canary duplicate attempt identity')
 if len(own)>=MAX_CALLS:raise GatewayLifecycleError('Canary attempt ceiling reached')
 for r in own:
  if r.get('state')=='reserved' and not r.get('error_label'):continue
  if r.get('state')!='settled' or r.get('error_label') or not r.get('billing_receipt'):
   raise GatewayLifecycleError('Canary unsettled/invalid receipt; global serial admission stopped')
  b=r['billing_receipt'];from .openrouter_task import STATE
  if sha(STATE/b['relative_path'])!=b['sha256']:raise GatewayLifecycleError('Canary settlement receipt changed')

 if any(r.get('state')=='reserved' for r in own):raise CanaryBusy('Canary serial native request still in flight')
