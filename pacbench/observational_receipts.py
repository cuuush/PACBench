"""Prepaid observational accounting: append-only receipts, no local money admission.

The original ledger is READ ONLY. This journal is evidence, not another budget.
Execution locking, route/contract checks and no automatic replay remain separate.
"""
import os,json,hashlib,uuid,fcntl
from pathlib import Path
from contextlib import contextmanager
from decimal import Decimal
from .openrouter_route import with_route, PROFILES
from .response_receipts import parse,sanitized,receipt
from .receipt_projection import IndexedAttempts, Projection, account_refused
from .live_dispatch import sender_identity,operation_id,CONDITION,OwnershipConflict

ENV='PACBENCH_RECEIPT_JOURNAL'
MODE='prepaid-observational-v1'
COHORT_STOP='PACBENCH_OBSERVATIONAL_COHORT_STOP'

def check_cohort_stop():
 path=os.environ.get(COHORT_STOP)
 if path and Path(path).exists():raise RuntimeError('Shared OpenRouter account stopped')

def stop_cohort(reason,attempt):
 path=os.environ.get(COHORT_STOP)
 if not path:return
 try:
  with Path(path).open('x') as stream:
   json.dump({'reason':reason,'attempt_id':attempt},stream)
 except FileExistsError:pass

def rejection_402(data,headers=None):
 try:value=json.loads(data).get('error',{})
 except (ValueError,TypeError):value={}
 def strings(v):
  if isinstance(v,str):return [v]
  if isinstance(v,dict):return [s for x in v.values() for s in strings(x)]
  if isinstance(v,list):return [s for x in v for s in strings(x)]
  return []
 labels=strings(value)
 names=['openrouter_in_flight_budget','in_flight_budget_exhausted','openrouter_key_limit','weight_exceeds_budget','workspace_limit','guardrail_limit','insufficient_credits']
 name=next((n for n in names if any(n==s for s in labels)),'unknown_402')
 retry=next((v for k,v in (headers or {}).items() if k.lower()=='retry-after'),None)
 try:delay=float(retry)
 except (ValueError,TypeError):delay=None
 return name,delay

def enabled():return bool(os.environ.get(ENV))

def shared_pool_429(data):
 try:meta=json.loads(data).get('error',{}).get('metadata',{})
 except (ValueError,TypeError):return False
 return (meta.get('provider_error_code')=='rate_limit_exceeded' and meta.get('limit_source')=='upstream_provider_shared_pool' and meta.get('provider_name')=='Amazon Bedrock' and meta.get('is_byok') is False)

def replay_attempts(stream):
 """Reconstruct each run-owned attempt from the append-only receipt journal."""
 rows={}
 for line in stream:
  if not line.strip():continue
  event=json.loads(line)
  ident=event['id']
  if event['kind']=='prepared':
   if ident in rows or event['row']['id']!=ident:raise ValueError('Duplicate or foreign prepared receipt')
   rows[ident]=event['row']
  else:rows[ident].update(event['patch'])
 return rows

class ObservedBudget:
 def __init__(self,base,path):
  self.base=base;self.original_path=base.path;self.path=Path(path).resolve()
  if self.path==self.original_path.resolve() or self.path.is_symlink():raise RuntimeError('Receipt journal cannot replace historical ledger')
  for name in ['config','profile_name','profile','model','endpoint','provider']:setattr(self,name,getattr(base,name))
  if self.profile_name!='luna-bedrock-global':raise RuntimeError('Luna/Bedrock Global exclusively')
  if not self.path.parent.is_dir():raise RuntimeError('Explicit run-owned journal directory required')

 @contextmanager
 def journal(self):
  with self.path.open('a+') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   if os.fstat(f.fileno()).st_mode & 0o777 != 0o600:os.fchmod(f.fileno(),0o600)
   f.seek(0)
   rows=replay_attempts(f)
   def append(kind,ident,patch):
    event={'kind':kind,'id':ident,'row':patch} if kind=='prepared' else {'kind':kind,'id':ident,'patch':patch}
    f.seek(0,2);f.write(json.dumps(event,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
   yield rows,append

 @contextmanager
 def locked(self,*,write=True):
  if write:raise RuntimeError('Historical ledger writes are disabled in observational mode')
  with self.base.locked(write=False) as state:
   historical=dict(state);old=list(state['calls'])
  with self.journal() as (rows,_):pass
  yield {**historical,'calls':old+list(rows.values())}

 def snapshot(self):
  with self.locked(write=False) as state:
   from .response_receipts import financials
   return [{**r,'accounted_upper_bound_usd':float(r['accounted']) if 'accounted' in r else None,
     'financials':financials(r) if r.get('accounting_mode')!=MODE else {'actual_usd':r.get('settled_charge_usd'),'local_reservations_applied':False}} for r in state['calls']]

 def reserve(self,project,body,*,cohort_scope=None,sender=None,wait=True):
  import time
  from .canary_capability import CanaryBusy
  timeout=getattr(self,'execution_queue_timeout_s',60)
  deadline=time.monotonic()+timeout
  while True:
   try:return self.prepare(project,body,cohort_scope=cohort_scope,sender=sender)
   except CanaryBusy as exc:
    if not wait:
     exc.queue_timeout_s=timeout
     raise
    if time.monotonic()>=deadline:raise RuntimeError('Current execution queue deadline; no request dispatched') from None
    time.sleep(.05)

 def prepare(self,project,body,*,cohort_scope=None,sender=None):
  from .execution_capability import admission,fields
  from .direct_accounting import now
  if body.get('model')!=self.model:raise ValueError('Exact Luna model required')
  if os.environ.get('PACBENCH_LUNA_COMPATIBILITY')!='temperature-omitted-default-v1' or os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')!='32768':raise ValueError('Established native inference condition required')
  request={k:v for k,v in body.items() if k!='temperature'}
  from .workflow_tracks import MEMOS_OSS_LUNA_COMPAT
  memos_compat=(project.endswith('-memos-memory') and
                os.environ.get('PACBENCH_MEMOS_OSS_LUNA_COMPAT')==MEMOS_OSS_LUNA_COMPAT)
  if memos_compat:
   request.pop('top_p',None)
  if 'max_tokens' in request and 'max_completion_tokens' in request:raise ValueError('Conflicting output limits')
  key='max_tokens' if 'max_tokens' in request else 'max_completion_tokens';limit=request.get(key,32768)
  if type(limit) is not int or limit<=0:raise ValueError('Invalid output limit')
  request[key]=min(limit,32768)
  supplied=request.pop('provider',None)
  if supplied is not None and (not isinstance(supplied,dict) or any(k not in self.provider or self.provider[k]!=v for k,v in supplied.items())):raise ValueError('Wrong provider route')
  request=with_route(request,profile=self.profile_name)
  if request.get('stream'):request['stream_options']={**request.get('stream_options',{}),'include_usage':True}
  permit=admission(project,self.profile_name,request)
  if permit is None:raise RuntimeError('Frozen r2 execution capability required')
  fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  row={'id':str(uuid.uuid4()),'project':project,'model':self.model,'endpoint':self.endpoint,'route_profile':self.profile_name,
   'started':now(),'finished':None,'state':'receipt_pending','accounting_mode':MODE,'transport':'direct-client-reservations-v1',
   'http_status':None,'input_tokens':None,'output_tokens':None,'error_label':None,'request_sha256':fingerprint,
   'dispatch_owner':sender_identity(sender),'dispatch_tracking_condition':CONDITION,'dispatch_execution':'prepared',
   'dispatch_events':[{'kind':'prepared','at':now()}],'effective_output_maximum':request[key],**fields(permit)}
  row['accounting_role']='hermes' if project.endswith('-driver') else 'judge' if project.endswith('-judge') else 'memory_provider'
  if memos_compat:
   row['native_request_compatibility']=MEMOS_OSS_LUNA_COMPAT
  row['dispatch_context']={'run_id':permit.get('run_id','pizza-week-scored-prepaid-20261114'),'arm_id':permit['slot']['provider'],
   'phase':'judging' if permit['slot']['kind'] in {'judge','retained_judge'} else 'answering' if row['accounting_role']=='hermes' else permit['slot']['kind'] if permit['slot']['kind'] in {'ingest','cleanup'} else 'background',
   'attempt_id':row['id'],'attempt_number':1,'operation_id':operation_id(row)}
  with self.journal() as (rows,append):
   check_cohort_stop()
   stop=os.environ.get('PACBENCH_STOP_FILE')
   if stop and Path(stop).exists():raise RuntimeError('Execution stopped')
   indexed=isinstance(rows,IndexedAttempts)
   refused=rows.refused() if indexed else any(account_refused(r) for r in rows.values())
   if refused:
    raise RuntimeError('Provider refusal; no further dispatch or retry')
   operation=row['dispatch_context']['operation_id']
   previous=(rows.previous(project,operation) if indexed else
             [r for r in rows.values() if r['project']==project and r.get('dispatch_context',{}).get('operation_id')==operation])
   if previous:
    grant=json.loads(os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY','{}'))
    lost_output=(project.endswith('-judge') and len(previous)==1 and grant.get('attempt_id')==previous[0]['id'] and previous[0].get('state')=='settled' and grant.get('reason')=='model_recorder_failed_before_saving_judgment')
    if not lost_output and (len(previous)>=3 or previous[-1].get('provider_rejection') not in ('openrouter_in_flight_budget','in_flight_budget_exhausted','upstream_provider_shared_pool_rate_limit')):raise OwnershipConflict('No automatic replay of a prepared request')
    row['retry_of']=previous[-1]['id'];row['dispatch_context']['attempt_number']=len(previous)+1
    if lost_output:row['retry_reason']=grant['reason']
   busy=rows.active() if indexed else any(not r.get('finished') for r in rows.values())
   if busy:
    from .canary_capability import CanaryBusy
    raise CanaryBusy('Current execution request still active')
   append('prepared',row['id'],row)
  return {'id':row['id'],'request':request}

 def mark_dispatch_possible(self,ident,*,sender=None):
  from .live_dispatch import mark_possible
  from .direct_accounting import now
  with self.journal() as (rows,append):
   check_cohort_stop()
   row=rows[ident];mark_possible(row,now(),sender)
   append('dispatch',ident,{k:row[k] for k in ['dispatch_execution','dispatch_events']})

 def observe_response(self,ident,status,event=None,*,headers=None,sender=None):
  from .live_dispatch import check_owner
  safe=sanitized(event or {})
  if not safe and headers:
   gen=next((v for k,v in headers.items() if k.lower()=='x-generation-id'),None)
   if gen:safe={'id':gen}
  if not safe:return
  with self.journal() as (rows,append):
   row=rows[ident];check_owner(row,sender)
   if row.get('response_observation')==safe:return
   patch={'response_observation':safe,'http_status':status}
   if safe.get('id'):patch['upstream_request_id']=safe['id']
   append('response',ident,patch)

 def settle(self,ident,status,data=b'',*,headers=None,body_complete=False,sender=None):
  from .live_dispatch import record_end
  from .direct_accounting import now,response_validation,stop_measured_admission
  raw=data.encode() if isinstance(data,str) else data;events,errors=parse(raw)
  validation=response_validation(events,body_complete)
  validation['wire_parse_errors']=errors
  validation['envelope_errors']=[{'code': error.get('code')} for error in validation['envelope_errors']]
  previous=None
  for event in events:
   safe=sanitized(event)
   if safe and safe!=previous:self.observe_response(ident,status,event,headers=headers,sender=sender);previous=safe
  with self.journal() as (rows,append):
   row=rows[ident];record_end(row,now(),body_complete,sender)
   patch={'finished':now(),'http_status':status or None,'state':'receipt_missing','error_label':'actual_cost_unknown',
    'response_body_complete':body_complete,'response_validation':validation,
    'dispatch_execution':row['dispatch_execution'],'dispatch_events':row['dispatch_events']}
   if raw and row['project'].endswith('-judge'):
    digest=hashlib.sha256(raw).hexdigest();folder=self.path.parent/'response-bodies';folder.mkdir(exist_ok=True)
    dest=folder/(digest+'.private.bin')
    if not dest.exists():
     with dest.open('xb') as f:dest.chmod(0o600);f.write(raw);f.flush();os.fsync(f.fileno())
    patch['direct_body_evidence']={'relative_path':str(dest.relative_to(self.path.parent)),'sha256':digest,'bytes':len(raw),'body_complete':body_complete}
   if status==402:
    code,delay=rejection_402(raw,headers);patch.update(provider_rejection=code,retry_after_s=delay)
   if status==429 and shared_pool_429(raw):
    _,delay=rejection_402(raw,headers);patch.update(provider_rejection='upstream_provider_shared_pool_rate_limit',retry_after_s=30 if delay is None else delay)
   event=next((e for e in reversed(events) if 'cost' in (e.get('usage') or {})),None)
   if event:
    if not event.get('id') and row.get('upstream_request_id'):event={**event,'id':row['upstream_request_id']}
    safe=sanitized(event);cost=safe.get('usage',{}).get('cost')
    if cost is not None and safe.get('id'):
     value=receipt(ident,event,status=status,body_sha256=hashlib.sha256(raw).hexdigest(),complete=body_complete)
     encoded=(json.dumps(value,sort_keys=True)+'\n').encode();digest=hashlib.sha256(encoded).hexdigest()
     folder=self.path.parent/'billing-receipts';folder.mkdir(exist_ok=True)
     dest=folder/(digest+'.json')
     if not dest.exists():
      with dest.open('xb') as f:f.write(encoded);f.flush();os.fsync(f.fileno());dest.chmod(0o600)
     from .usage import token_details
     patch.update(state='settled',settled_charge_usd=cost,error_label=None,
      upstream_request_id=safe['id'],served_model=safe.get('model'),served_provider=safe.get('provider'),usage_detail=token_details(safe.get('usage',{})),
      billing_receipt={'relative_path':str(dest.relative_to(self.path.parent)),'sha256':digest,'protocol':value['protocol'],'cost_source':'response.usage.cost'})
     for k,src in [('input_tokens','prompt_tokens'),('output_tokens','completion_tokens')]:patch[k]=safe.get('usage',{}).get(src)
     if row.get('settled_charge_usd') not in (None,cost):patch.update(state='receipt_conflict',error_label='conflicting_receipts')
   append('outcome',ident,patch)
  if status in (401,403):
   stop_cohort('openrouter_access_refused',ident)
  if status==402 and patch.get('provider_rejection')=='insufficient_credits':
   stop_cohort('insufficient_credits',ident)
  if status in (401,403) or status==429 and patch.get('provider_rejection')!='upstream_provider_shared_pool_rate_limit':stop_measured_admission('provider_access_or_rate_refusal',ident)
  if status==402 and patch['provider_rejection'] not in ('openrouter_in_flight_budget','in_flight_budget_exhausted'):
   stop_measured_admission('provider_402:'+patch['provider_rejection'],ident)

 def retry_delay(self,ident):
  with self.journal() as (rows,_):
   row=rows[ident];delay=row.get('retry_after_s');attempt=row.get('dispatch_context',{}).get('attempt_number',1)
  if row.get('provider_rejection') in ('openrouter_in_flight_budget','in_flight_budget_exhausted','upstream_provider_shared_pool_rate_limit') and attempt<3 and delay is not None and 0<=delay<=30:return delay
  if row.get('provider_rejection')=='upstream_provider_shared_pool_rate_limit':
   from .direct_accounting import stop_measured_admission
   stop_measured_admission('upstream_rate_limit_retry_exhausted',ident)
  return None

 def classify_retained_shared_pool_refusal(self,ident):
  with self.journal() as (rows,append):
   row=rows[ident];proof=row.get('direct_body_evidence') or {};path=self.path.parent/proof.get('relative_path','missing')
   raw=path.read_bytes()
   if row.get('http_status')!=429 or hashlib.sha256(raw).hexdigest()!=proof.get('sha256') or not shared_pool_429(raw):raise RuntimeError('Retained upstream rate refusal proof missing')
   append('classification',ident,{'provider_rejection':'upstream_provider_shared_pool_rate_limit','retry_after_s':30,'classification_evidence_sha256':proof['sha256'],'unknown_charge_not_released':True})

def credit_snapshot():
 """Read prepaid credit; preserve the response only beside the new journal."""
 import urllib.request
 from .direct_accounting import now
 key=os.environ['OPENROUTER_API_KEY']
 request=urllib.request.Request('https://openrouter.ai/api/v1/credits',headers={'Authorization':'Bearer '+key})
 start=now()
 with urllib.request.urlopen(request,timeout=15) as response:raw=response.read(65537)
 if len(raw)>65536:raise RuntimeError('Unexpected credit response')
 value=json.loads(raw,parse_float=Decimal)['data'];remaining=Decimal(str(value['total_credits']))-Decimal(str(value['total_usage']))
 if remaining<=0:raise RuntimeError('OpenRouter prepaid credit exhausted')
 folder=Path(os.environ[ENV]).parent/'prepaid-verification';folder.mkdir(exist_ok=True)
 dest=folder/(hashlib.sha256(raw).hexdigest()+'.private.json')
 if not dest.exists():
  with dest.open('xb') as f:dest.chmod(0o600);f.write(raw)
 return {'request_started':start,'observed_at':now(),'remaining_usd':str(remaining),'receipt_sha256':hashlib.sha256(raw).hexdigest(),'source':'OpenRouter GET /api/v1/credits'}


class RunReceiptJournal(ObservedBudget):
    """Observational Luna journal with no historical store or local spending limit."""
    execution_queue_timeout_s = 180

    def __init__(self, path):
        self.path = path
        self.profile_name = 'luna-bedrock-global'
        self.profile = PROFILES[self.profile_name]
        self.model = self.profile['model']
        self.endpoint = self.profile['endpoint']
        self.provider = with_route({}, profile=self.profile_name)['provider']

    @contextmanager
    def locked(self, *, write=True):
        if write:
            raise RuntimeError('Run receipts are append-only')
        with self.journal() as (rows, _):
            yield {'calls': list(rows.values())}

    def snapshot(self):
        with self.locked(write=False) as state:
            return state['calls']

class IndexedRunReceiptJournal(RunReceiptJournal):
    """Run-owned MemCal journal. Full replay is explicit for an existing run."""
    def journal(self):
        return Projection(self.path).open()

    def initialize_from_journal(self):
        with Projection(self.path).open(initialize=True):
            pass

    def get_attempt(self, ident):
        with self.journal() as (rows, _):
            return rows[ident]

    def has_offline_generations(self):
        with self.journal() as (rows, _):
            return rows.db.execute('SELECT 1 FROM attempts WHERE offline=1 LIMIT 1').fetchone() is not None

    def has_active_attempts(self, roles=None):
        with self.journal() as (rows, _):
            if roles is None:
                return rows.active()
            return any(rows.db.execute('SELECT 1 FROM attempts WHERE active=1 AND role=? LIMIT 1',
                                       (role,)).fetchone() for role in roles)

    def lineage_for_requests(self, ids):
        with self.journal() as (rows, _):
            pending = set(ids)
            found = {}
            while pending:
                ident = pending.pop()
                result = rows.db.execute('SELECT rowid,payload FROM attempts WHERE id=?',(ident,)).fetchone()
                if result:
                    ordinal, encoded = result
                    row = json.loads(encoded)
                    found[ident] = (ordinal, row)
                    parent = row.get('retry_of')
                    if parent and parent not in found:
                        pending.add(parent)
            return [row for _,row in sorted(found.values(),key=lambda r:r[0])]
