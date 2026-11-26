import json,os
from pathlib import Path
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench.observational_receipts import ObservedBudget,MODE
from pacbench.live_dispatch import CONDITION
from pacbench.direct_http import before_send
from pacbench.receipt_costs import direct_evidence, summarize

@pytest.fixture
def observed(tmp_path,monkeypatch):
 p=tmp_path/'historical.json';cfg={'cap':'20','model':'openai/gpt-6-luna','endpoint':'amazon-bedrock/global','context_tokens':1050000,'max_completion_tokens':128000,'prompt_per_million':'.45','completion_per_million':'.75'}
 p.write_text(json.dumps({'version':1,'config':cfg,'route_configs':{'luna-bedrock-global':cfg},'calls':[{'id':'legacy','project':'old','state':'reserved','accounted':'999999','reserved':'999999'}]}))
 b=ObservedBudget(DirectBudget(p,profile='luna-bedrock-global'),tmp_path/'new-receipts.jsonl')
 for k,v in {'PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1','PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768','PACBENCH_DISPATCH_TRACKING':CONDITION}.items():monkeypatch.setenv(k,v)
 permit={'root':str(tmp_path/'run'),'slot':{'kind':'ingest','provider':'mem0'},'capability':{},'projects':['test-memory']}
 monkeypatch.setattr('pacbench.execution_capability.admission',lambda *a:permit)
 monkeypatch.setattr('pacbench.execution_capability.fields',lambda p:{'checkpoint_full_run_root':p['root'],'checkpoint_full_run_slot':p['slot']})
 return b,p.read_bytes()

def begin(b,question='one'):
 r=b.reserve('test-memory',{'model':b.model,'messages':[{'role':'user','content':question}],'temperature':0});before_send({**r,'budget':b});return r

def response(b,cost='25.0000000000001'):
 return json.dumps({'id':'gen-one','model':b.model,'provider':'Amazon Bedrock','usage':{'cost':cost,'prompt_tokens':3,'completion_tokens':2}}).encode()


def _prepared_judge_evidence(b):
 from hashlib import sha256
 from pacbench.response_receipts import read_frames, receipt

 row=b.reserve('test-judge',{'model':b.model,'messages':[{'role':'user','content':'fixture'}]})
 before_send({**row,'budget':b})
 raw=response(b)
 wire_hash=sha256(raw).hexdigest()
 bill=(json.dumps(receipt(row['id'],read_frames(raw).events[-1],status=200,
                          body_sha256=wire_hash,complete=True),sort_keys=True)+'\n').encode()
 return row,raw,{
  'wire':(b.path.parent/'response-bodies'/(wire_hash+'.private.bin'),raw),
  'billing':(b.path.parent/'billing-receipts'/(sha256(bill).hexdigest()+'.json'),bill),
 }


@pytest.mark.parametrize('artifact',['wire','billing'])
def test_receipt_rejects_existing_mismatched_evidence(observed,artifact):
 from pacbench.canary_capability import CanaryBusy

 b,_=observed;row,raw,evidence=_prepared_judge_evidence(b)
 path,expected=evidence[artifact]
 path.parent.mkdir()
 partial=expected[:-1]
 path.write_bytes(partial)
 path.chmod(0o600)
 with pytest.raises(ValueError,match='Existing private receipt evidence differs'):
  b.settle(row['id'],200,raw,body_complete=True)
 assert path.read_bytes()==partial
 attempt=b.get_attempt(row['id'])
 assert attempt['finished'] is None and 'billing_receipt' not in attempt and 'direct_body_evidence' not in attempt
 assert not list(path.parent.glob('.pending-*'))
 with pytest.raises(CanaryBusy):
  b.prepare('test-judge',{'model':b.model,'messages':[{'role':'user','content':'different'}]})


@pytest.mark.parametrize('artifact',['wire','billing'])
def test_receipt_interrupted_publication_leaves_no_partial_digest_file(observed,monkeypatch,artifact):
 b,_=observed;row,raw,evidence=_prepared_judge_evidence(b)
 path,_=evidence[artifact]
 original=os.link

 def interrupted(source,dest,**options):
  if dest==path.name:
   raise OSError('injected publish interruption')
  return original(source,dest,**options)

 with monkeypatch.context() as patch:
  patch.setattr(os,'link',interrupted)
  with pytest.raises(OSError,match='injected publish interruption'):
   b.settle(row['id'],200,raw,body_complete=True)
 assert not path.exists() and not list(path.parent.iterdir())
 assert b.get_attempt(row['id'])['finished'] is None
 b.settle(row['id'],200,raw,body_complete=True)
 attempt=b.get_attempt(row['id'])
 assert attempt['state']=='settled'
 for path,expected in evidence.values():
  assert path.read_bytes()==expected and path.stat().st_mode & 0o777==0o600


def test_receipt_adopts_exact_existing_legacy_bytes(observed):
 b,_=observed;row,raw,evidence=_prepared_judge_evidence(b)
 inodes={}
 for path,expected in evidence.values():
  path.parent.mkdir()
  path.write_bytes(expected)
  path.chmod(0o600)
  inodes[path]=path.stat().st_ino
 b.settle(row['id'],200,raw,body_complete=True)
 assert b.get_attempt(row['id'])['state']=='settled'
 assert {path:path.stat().st_ino for path in inodes}==inodes


def test_successful_capture_with_conflicting_billing_cannot_settle_as_disconnect(observed):
 from pacbench.direct_http import Capture

 b,_=observed;row,raw,evidence=_prepared_judge_evidence(b)
 billing,_=evidence['billing']
 billing.parent.mkdir()
 billing.write_bytes(b'partial')
 billing.chmod(0o600)
 capture=Capture({**row,'budget':b},200)
 capture.add(raw)
 with pytest.raises(ValueError,match='Existing private receipt evidence differs'):
  try:
   capture.finish(complete=True)
  finally:
   capture.finish()
 assert b.get_attempt(row['id'])['finished'] is None
 assert list(billing.parent.iterdir())==[billing]
 assert billing.read_bytes()==b'partial'


@pytest.mark.parametrize('link_parent',[True,False])
def test_receipt_evidence_cannot_follow_symlink_outside_run(observed,link_parent):
 b,_=observed;row,raw,evidence=_prepared_judge_evidence(b)
 path,expected=evidence['wire']
 outside=b.path.parent.parent/(b.path.parent.name+'-outside')
 outside.mkdir()
 outside_path=outside/path.name
 outside_path.write_bytes(expected)
 outside_path.chmod(0o600)
 if link_parent:
  path.parent.symlink_to(outside,target_is_directory=True)
 else:
  path.parent.mkdir()
  path.symlink_to(outside_path)
 with pytest.raises(OSError):
  b.settle(row['id'],200,raw,body_complete=True)
 assert b.get_attempt(row['id'])['finished'] is None
 assert list(outside.iterdir())==[outside_path] and outside_path.read_bytes()==expected


def test_no_money_admission_and_historical_files_read_only(observed):
 b,original=observed;r=begin(b);prefix=b.path.read_bytes();b.settle(r['id'],200,response(b),body_complete=True)
 row=b.snapshot()[-1];assert row['settled_charge_usd']=='25.0000000000001'
 assert 'reserved' not in row and 'accounted' not in row
 assert b.path.read_bytes().startswith(prefix) and b.original_path.read_bytes()==original
 assert summarize([direct_evidence(b.path,row)])['actual_reported_usd']=='25.0000000000001'
 begin(b,'another')
 with pytest.raises(RuntimeError):
  with b.locked():pass

def test_missing_receipt_unknown_and_not_an_admission_block(observed):
 b,original=observed;r=begin(b);b.settle(r['id'],200,b'{"id":"gen-missing"}',body_complete=True)
 row=b.snapshot()[-1];assert row['state']=='receipt_missing' and 'settled_charge_usd' not in row
 assert summarize([direct_evidence(b.path,row)])['actual_reported_usd'] is None
 begin(b,'another');assert b.original_path.read_bytes()==original

def test_402_is_terminal_and_does_not_replay(observed):
 b,_=observed;r=begin(b);b.settle(r['id'],402,b'{"error":{"code":402}}',body_complete=True);before=b.path.read_bytes()
 with pytest.raises(RuntimeError,match='refusal'):begin(b,'another')
 assert b.path.read_bytes()==before

def test_wrong_route_and_duplicate_operation_refused(observed):
 b,_=observed
 with pytest.raises(ValueError):b.reserve('test-memory',{'model':'other'})
 r=begin(b);b.settle(r['id'],0)
 with pytest.raises(RuntimeError,match='replay'):begin(b)

def test_ordinary_dispatch_only_decodes_its_own_receipts(observed,monkeypatch):
 from pacbench import receipt_projection as projection
 from pacbench.observational_receipts import RunReceiptJournal

 budget,_=observed
 # A pre-index run includes arbitrary attribution and an existing terminal outcome.
 legacy={'id':'pre-index','project':'test-memory','finished':'done','state':'receipt_missing',
         'accounting_mode':MODE,'attribution':{'witness':['private']}}
 proof={'honcho_validation_rejection':{'reason':'retained'}}
 events=[{'kind':'prepared','id':'pre-index','row':legacy},
         {'kind':'validation_rejection','id':'pre-index','patch':proof}]
 prefix=''.join(json.dumps(event)+'\n' for event in events).encode()
 budget.path.write_bytes(prefix)
 legacy.update(proof)
 budget.path.chmod(0o400)
 before=budget.path.stat()
 assert RunReceiptJournal(budget.path).snapshot()==[legacy]
 assert budget.path.stat().st_ctime_ns==before.st_ctime_ns
 assert not projection.Projection(budget.path).index.exists()
 budget.path.chmod(0o600)
 assert budget.get_attempt('pre-index')==legacy
 index=projection.Projection(budget.path).index
 assert index.stat().st_mode & 0o777==0o600

 decoded_events=[]
 loads=json.loads
 def track(payload,*args,**kwargs):
  value=loads(payload,*args,**kwargs)
  if isinstance(value,dict) and value.get('kind') in ('prepared','dispatch','outcome'):
   decoded_events.append(value['id'])
  return value

 with monkeypatch.context() as m:
  m.setattr(projection.json,'loads',track)
  def no_full_scan(self):
   pytest.fail('ordinary dispatch scanned prior attempts')
  m.setattr(projection.IndexedAttempts,'values',no_full_scan)
  for i in range(200):
   call=begin(budget,str(i))
   budget.settle(call['id'],0)
   assert decoded_events[i*3:]==[call['id']]*3
 assert len(decoded_events)==600
 assert budget.path.read_bytes().startswith(prefix)
 assert RunReceiptJournal(budget.path).snapshot()[0]==legacy
 assert len(budget.snapshot())==202
 # Losing a clean cache must not require rebuilding or rewriting the authority.
 expected=budget.get_attempt(call['id'])
 original=budget.path.read_bytes()
 index.unlink()
 assert budget.get_attempt(call['id'])==expected
 assert budget.path.read_bytes()==original

def test_distinct_hindsight_consolidation_calls_keep_transport_replay_guard(observed,monkeypatch):
 b,_=observed
 work={'kind':'hindsight-consolidation-call-v1','ordinal':1}
 slot={'kind':'answer','question_id':'same-question','provider':'hindsight','native_work':work}
 permit={'root':str(b.path.parent/'run'),'slot':slot,'capability':{},'projects':['test-memory']}
 monkeypatch.setattr('pacbench.execution_capability.admission',lambda *a:permit)
 monkeypatch.setattr('pacbench.execution_capability.fields',lambda p:{'workflow_slot':p['slot']})
 first=begin(b);b.settle(first['id'],0)
 with pytest.raises(RuntimeError,match='replay'):begin(b)
 slot['native_work']={**work,'ordinal':2}
 second=begin(b);b.settle(second['id'],0)
 with pytest.raises(RuntimeError,match='replay'):begin(b)
 first,second=[row for row in b.snapshot() if (row.get('workflow_slot') or {}).get('provider')=='hindsight']
 assert first['request_sha256']==second['request_sha256']
 assert first['dispatch_context']['operation_id']!=second['dispatch_context']['operation_id']
 assert first['dispatch_context']['attempt_number']==second['dispatch_context']['attempt_number']==1

def test_stream_id_retained_before_interruption(observed):
 from pacbench.direct_http import Capture
 b,_=observed;r=begin(b);c=Capture({**r,'budget':b},200)
 c.add(b'data: {"id":"gen-first","choices":[{"delta":{"content":"PRIVATE"}}]}\n\n')
 assert b.snapshot()[-1]['upstream_request_id']=='gen-first'
 c.finish();assert b.snapshot()[-1]['state']=='receipt_missing'
 assert 'PRIVATE' not in b.path.read_text()
 begin(b,'after interruption')

def test_practical_grading_actual_saved_contract_gate_offline(tmp_path,monkeypatch):
 from pacbench import retained_r2_grading as module
 from pacbench import full_run_capability as cap
 from tests.test_evaluation import correct_judge
 from pacbench.observational_receipts import ENV
 original=Path(__file__).resolve().parents[1]/'runs/three-arm-revised-week-live-20261116'
 if not original.exists():pytest.skip('Private retained cohort is unavailable')
 monkeypatch.setenv(ENV,str(tmp_path/'journal.jsonl'))
 monkeypatch.setenv(cap.ENV,'test-value-restored-after-run')
 monkeypatch.setenv('PACBENCH_LUNA_COMPATIBILITY','temperature-omitted-default-v1')
 calls=[]
 class FakeClient:
  def __init__(self,**kwargs):self.kw=kwargs
  def __call__(self,prompt):
   permit=cap.admission(self.kw['key'],'luna-bedrock-global',{'messages':[{'role':'user','content':prompt}]})
   assert permit['slot']['kind']=='retained_judge';calls.append(permit['slot']['question_id'])
   v=json.loads(correct_judge(prompt));claims=json.loads(prompt.split('Claims (hidden from the assistant): ')[1])
   v.update(interaction='answer',benchmark_error=None,evidence_diagnostic={'claims':[{'id':c['id'],'status':'not_found','reason':'Synthetic offline fixture','citations':[]} for c in claims],'extra_assertions':[]})
   return json.dumps(v)
 monkeypatch.setattr(module,'ModelClient',FakeClient)
 out=tmp_path/'overlay'
 def settings(project):return 'https://openrouter.ai/api/v1',project
 result=module.run(out,original,None,settings)
 assert len(calls)==42 and result['coverage']['criteria']==188 and result['new_answers']==0
 assert result['strict_scores_modified'] is False
 for item in result['checks']:assert item['strict_credit_awarded'] is False

@pytest.mark.parametrize('code,retried',[('openrouter_in_flight_budget',True),('in_flight_budget_exhausted',True),('openrouter_key_limit',False),('weight_exceeds_budget',False),('unknown',False)])
def test_actual_402_httpx_transport_distinguishes_predispatch(observed,monkeypatch,code,retried):
 import httpx
 from pacbench import direct_http as hook
 b,original=observed;count=[]
 monkeypatch.setenv('PACBENCH_DIRECT','1');monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY','offline-key')
 monkeypatch.setattr(hook,'live_budget',lambda **kw:b)
 monkeypatch.setattr(httpx.Client,'send',httpx.Client.send)
 monkeypatch.setattr(httpx.AsyncClient,'send',httpx.AsyncClient.send)
 hook._install_httpx(httpx)
 def upstream(request):
  count.append(1)
  if len(count)==1:return httpx.Response(402,json={'error':{'code':402,'metadata':{'code':code}}},headers={'retry-after':'0'})
  return httpx.Response(200,content=response(b,'0.00001'))
 with httpx.Client(transport=httpx.MockTransport(upstream)) as client:
  r=client.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer pbdirect-test-memory'},json={'model':b.model,'messages':[]})
 assert len(count)==(2 if retried else 1)
 assert r.status_code==(200 if retried else 402)
 assert b.original_path.read_bytes()==original
 if retried:assert b.snapshot()[-1]['retry_of']==b.snapshot()[-2]['id']

def test_real_model_recorder_saves_observational_response_without_reserved_field(observed,tmp_path):
 import subprocess,sys
 b,_=observed;script=tmp_path/'record.py'
 script.write_text('''import os,sys,json,io,urllib.request
from pathlib import Path
from pacbench.direct_accounting import DirectBudget
from pacbench.observational_receipts import ObservedBudget
from pacbench.model_calls import ModelClient
import pacbench.execution_capability as cap
import pacbench.direct_http as hook
b=ObservedBudget(DirectBudget(Path(sys.argv[1]),profile='luna-bedrock-global'),Path(sys.argv[2]))
cap.admission=lambda *a:{'root':'offline','slot':{'kind':'judge','provider':'memcal'},'capability':{}}
cap.fields=lambda p:{'checkpoint_full_run_root':'offline','checkpoint_full_run_slot':p['slot']}
hook.live_budget=lambda **kw:b
os.environ.update(PACBENCH_DIRECT='1',PACBENCH_DIRECT_UPSTREAM_KEY='offline-key')
class Response(io.BytesIO):
 status=200
 headers={}
 def __init__(self):super().__init__(json.dumps({'id':'gen-recorder','model':b.model,'provider':'Amazon Bedrock','choices':[{'message':{'content':'saved answer'},'finish_reason':'stop'}],'usage':{'cost':'0.0000123','prompt_tokens':3,'completion_tokens':2}}).encode())
urllib.request.OpenerDirector.open=lambda *args,**kwargs:Response()
hook.install()
client=ModelClient(key='pbdirect-recorder-judge',model=b.model,url='https://openrouter.ai/api/v1/chat/completions',record_dir=Path(sys.argv[3]),streaming=False)
assert client('offline question').text=='saved answer'
record=json.loads(next(Path(sys.argv[3]).glob('*.json')).read_text())
assert record['status']=='ok' and record['text']=='saved answer' and record['accounting_request_id']
assert record['cost_usd']==0.0000123
''')
 env={k:v for k,v in os.environ.items() if 'KEY' not in k and 'TOKEN' not in k}
 env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
 env['PACBENCH_EFFECTIVE_OUTPUT_TOKENS']='32768'
 result=subprocess.run([sys.executable,str(script),str(b.original_path),str(tmp_path/'child-receipts.jsonl'),str(tmp_path/'model-records')],env=env,capture_output=True,text=True,timeout=15)
 assert result.returncode==0,result.stderr

def test_current_execution_serial_queue_is_separate_from_money(observed):
 import time
 from concurrent.futures import ThreadPoolExecutor
 b,_=observed;r=begin(b,'first')
 with ThreadPoolExecutor(max_workers=1) as pool:
  waiting=pool.submit(b.reserve,'test-memory',{'model':b.model,'messages':[{'role':'user','content':'second'}]})
  time.sleep(.1);assert not waiting.done()
  b.settle(r['id'],200,b'{"id":"gen-no-cost"}',body_complete=True)
  second=waiting.result(timeout=2)
 assert second['id']!=r['id']


def test_async_observational_queue_allows_active_request_to_settle(observed,monkeypatch):
 import asyncio
 import httpx
 from pacbench import direct_http as hook
 from pacbench.observational_receipts import ENV
 b,_=observed
 b.execution_queue_timeout_s=.3
 monkeypatch.setenv(ENV,str(b.path))
 monkeypatch.setenv('PACBENCH_DIRECT','1')
 monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY','offline-key')
 monkeypatch.setattr(hook,'live_budget',lambda **kw:b)
 monkeypatch.setattr(httpx.Client,'send',httpx.Client.send)
 monkeypatch.setattr(httpx.AsyncClient,'send',httpx.AsyncClient.send)
 hook._install_httpx(httpx)

 async def scenario():
  active=asyncio.Event()
  calls=[]
  async def upstream(request):
   prompt=json.loads(request.content)['messages'][0]['content']
   calls.append(prompt)
   if prompt=='first':
    active.set()
    await asyncio.sleep(.08)
   payload=json.loads(response(b,'0.00001'))
   payload['id']='gen-'+prompt
   return httpx.Response(200,json=payload)
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
   async def request(prompt):
    return await client.post('https://openrouter.ai/api/v1/chat/completions',
      headers={'Authorization':'Bearer pbdirect-test-memory'},
      json={'model':b.model,'messages':[{'role':'user','content':prompt}]})
   async with asyncio.timeout(5), asyncio.TaskGroup() as group:
    first=group.create_task(request('first'))
    await active.wait()
    second=group.create_task(request('second'))
   assert [first.result().status_code, second.result().status_code]==[200,200]
   assert calls==['first','second']

 asyncio.run(scenario())
 assert [r['state'] for r in b.snapshot() if r.get('accounting_mode')==MODE]==['settled','settled']


def test_shared_pool_rate_limit_is_distinct_from_depletion(observed):
 b,_=observed
 # Use a judge project so its authenticated response body is retained privately.
 r=b.reserve('rate-judge',{'model':b.model,'messages':[]});before_send({**r,'budget':b})
 raw=json.dumps({'error':{'code':429,'metadata':{'provider_error_code':'rate_limit_exceeded','limit_source':'upstream_provider_shared_pool','provider_name':'Amazon Bedrock','is_byok':False}}}).encode()
 b.settle(r['id'],429,raw,body_complete=True)
 row=b.snapshot()[-1];assert row['provider_rejection']=='upstream_provider_shared_pool_rate_limit'
 assert b.retry_delay(r['id'])==30 and 'settled_charge_usd' not in row
 b.classify_retained_shared_pool_refusal(r['id'])
 again=b.reserve('rate-judge',{'model':b.model,'messages':[]})
 assert b.snapshot()[-1]['retry_of']==r['id']

def test_httpx_shared_pool_rate_retry_preserves_separate_attempts(observed,monkeypatch):
 import httpx
 from pacbench import direct_http as hook
 b,_=observed;monkeypatch.setenv('PACBENCH_DIRECT','1');monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY','offline')
 monkeypatch.setattr(hook,'live_budget',lambda **kw:b)
 monkeypatch.setattr(httpx.Client,'send',httpx.Client.send)
 monkeypatch.setattr(httpx.AsyncClient,'send',httpx.AsyncClient.send)
 hook._install_httpx(httpx);calls=[]
 def fake(req):
  calls.append(1)
  if len(calls)==1:return httpx.Response(429,json={'error':{'code':429,'metadata':{'provider_error_code':'rate_limit_exceeded','limit_source':'upstream_provider_shared_pool','provider_name':'Amazon Bedrock','is_byok':False}}},headers={'retry-after':'0'})
  return httpx.Response(200,content=response(b,'0.00001'))
 with httpx.Client(transport=httpx.MockTransport(fake)) as client:
  r=client.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer pbdirect-test-memory'},json={'model':b.model,'messages':[]})
 assert r.status_code==200 and len(calls)==2
 assert b.snapshot()[-1]['retry_of']==b.snapshot()[-2]['id']


def test_nested_token_details_are_numeric_and_history_unchanged(observed):
 from pacbench.usage import direct_call,summarize_calls
 b,original=observed;r=begin(b)
 value=json.loads(response(b));value['usage'].update(prompt_tokens_details={'cached_tokens':2},completion_tokens_details={'reasoning_tokens':1},irrelevant={'x':5})
 b.settle(r['id'],200,json.dumps(value).encode(),body_complete=True)
 before=b.path.read_bytes();row=b.snapshot()[-1]
 assert row['usage_detail']['completion_tokens_details.reasoning_tokens']==1
 row['usage_detail']=value['usage'] # historical raw-detail projection, no rewrite
 totals=summarize_calls([direct_call(row,'hermes')])
 assert totals['token_details']['prompt_tokens_details.cached_tokens']==2
 assert totals['token_details']['completion_tokens_details.reasoning_tokens']==1
 assert b.path.read_bytes()==before and b.original_path.read_bytes()==original


def test_retained_drained_answers_recover_without_source_mutation(tmp_path):
 from pacbench.closed_answer_recovery import recover
 from pacbench.harness import TurnResult
 from pacbench.checkpoint_evaluator import audit_transport
 root=Path(__file__).resolve().parents[1]/'runs/revised-r2-observational-live-20261117/remaining-native/mem0'
 files=list(root.glob('checkpoint-*/reuse-turns/*/answer-ready.json'))
 if not files:pytest.skip('Private retained captures unavailable')
 for i,p in enumerate(files):
  before=p.read_bytes();out=tmp_path/(str(i)+'.json');recover(p.parent,out)
  audit_transport(TurnResult(**json.loads(out.read_text())))
  assert p.read_bytes()==before


def test_closed_native_session_history_restores_without_answer_replay(tmp_path,monkeypatch):
 from types import SimpleNamespace
 from pacbench.closed_answer_recovery import recover,prepare_sessions,admit_session
 from pacbench.checkpoint_clock import history
 from pacbench.hermes_gateway import GatewayLifecycleError
 root=Path(__file__).resolve().parents[1]/'runs/revised-r2-observational-live-20261117/remaining-native/mem0'
 files=list(root.glob('checkpoint-*/reuse-turns/*/answer-ready.json'))
 if not files:pytest.skip('Private retained captures unavailable')
 ready=json.loads(files[0].read_text());home=Path(ready['observation_clock']['descriptor']['home'])
 target=tmp_path/'hermes-home';target.mkdir();(target/'config.yaml').write_bytes((home/'config.yaml').read_bytes())
 turn_path=tmp_path/'turn.json';recover(files[0].parent,turn_path)
 records=prepare_sessions([turn_path],target,ready['checkpoint']);sid=ready['session_id'];record=records[sid]
 assert history(target,sid)==history(home,sid)
 owner=SimpleNamespace(home=target,session_context={})
 q={'at':record['turn'].at,'prompt':record['turn'].prompt}
 admit_session(owner,record,q)
 assert owner.session_context[sid]['user_texts']==[q['prompt']]
 assert owner.session_context[sid]['followups']==0
 with pytest.raises(GatewayLifecycleError):admit_session(owner,record,q)
 fresh=tmp_path/'fresh';fresh.mkdir();(fresh/'config.yaml').write_bytes((home/'config.yaml').read_bytes())
 monkeypatch.setattr('pacbench.hermes_gateway._live',lambda pid:True)
 with pytest.raises(GatewayLifecycleError):prepare_sessions([turn_path],fresh,ready['checkpoint'])
 assert not (fresh/'state.db').exists()


def test_saved_reme_followup_requalifies_only_complete_native_evidence(tmp_path):
 from pacbench.closed_answer_recovery import recover_preclose
 from pacbench.checkpoint_evaluator import audit_transport
 from pacbench.harness import TurnResult
 folder=Path(__file__).resolve().parents[1]/'runs/revised-r2-observational-live-20261117/remaining-native-recovery-v3/reme/checkpoint-4/reuse-turns/2655d7c511664e769d1ce1c1a8ee3d00'
 if not folder.exists():pytest.skip('Private native witness unavailable')
 before={p.name:p.read_bytes() for p in folder.glob('*.json')}
 out=Path(recover_preclose(folder,tmp_path/'requalified'))
 t=TurnResult(**json.loads(out.read_text()));audit_transport(t)
 assert t.hermes_gateway['prior_user_count']==1 and t.lifecycle_timing['post_answer_cleanup_s'] is None
 assert before=={p.name:p.read_bytes() for p in folder.glob('*.json')}


def test_openviking_response_validation_is_independent_of_cost(observed):
 from pacbench.document_audit import openviking_response_evidence
 b,_=observed
 for i,(reason,complete,expected) in enumerate([('stop',True,'complete'),('length',True,'incomplete'),('stop',False,'incomplete')]):
  r=begin(b,str(i));body={'id':'gen-'+str(i),'choices':[{'message':{'content':'PRIVATE SUMMARY'},'finish_reason':reason}]}
  b.settle(r['id'],200,json.dumps(body).encode(),body_complete=complete)
  row=b.snapshot()[-1]
  assert row['state']=='receipt_missing'
  assert openviking_response_evidence([row])['state']==expected
 assert 'PRIVATE SUMMARY' not in b.path.read_text()


def test_luna_top_p_omission_applies_only_to_the_memos_oss_native_role(observed,monkeypatch):
    ledger,_=observed
    monkeypatch.setenv('PACBENCH_MEMOS_OSS_LUNA_COMPAT','omit-unsupported-top-p-v1')
    input_request={'model':ledger.model,'messages':[{'role':'user','content':'Local fixture'}],
                   'temperature':0.6,'top_p':0.95}
    native=ledger.reserve('test-memos-memory',input_request)
    assert 'temperature' not in native['request'] and 'top_p' not in native['request']
    assert ledger.snapshot()[-1]['native_request_compatibility']=='omit-unsupported-top-p-v1'
    before_send({**native,'budget':ledger})
    ledger.settle(native['id'],0)
    control=ledger.reserve('test-cognee-memory',input_request)
    assert control['request']['top_p']==0.95
    assert 'native_request_compatibility' not in ledger.snapshot()[-1]
