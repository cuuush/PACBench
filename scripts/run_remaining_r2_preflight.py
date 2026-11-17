"""Independent remaining-arm startup and first-checkpoint readiness; no questions."""
from pathlib import Path
import os,sys,json,hashlib,fcntl,time,argparse,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacbench import remaining_r2_capability as cap
from pacbench.gateway_reuse import atomic

def run(root,provider):
 root=Path(root).resolve();root.mkdir(exist_ok=False)
 env=os.environ
 for key in ['PACBENCH_CHECKPOINT_FULL_RUN','PACBENCH_CHECKPOINT_CANARY','PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE']:env.pop(key,None)
 env.update(PACBENCH_TRANSPORT='openrouter',PACBENCH_DIRECT='0',PACBENCH_OPENROUTER_PROFILE='luna-bedrock-global',PACBENCH_ROLE_ROUTES='luna-all-roles-v1',
  PACBENCH_EFFECTIVE_OUTPUT_TOKENS='32768',PACBENCH_LUNA_COMPATIBILITY='temperature-omitted-default-v1',PACBENCH_RECEIPT_JOURNAL=str(root/'receipts.jsonl'),
  PACBENCH_DISPATCH_TRACKING='direct-process-evidence-v1',PACBENCH_STOP_FILE=str(root/'STOP'),PACBENCH_DIRECT_STOP_ON_429=str(root/'STOP'),TMPDIR='/var/tmp')
 if provider=='memos':env['PACBENCH_MEMOS_INGEST_CONDITION']='native-raw-document-semantic-ready-v1'
 from pacbench import full_run_capability as frozen
 from pacbench.document_ingestion import prepare_sources,ingest_day
 prep,schedule,checks,plan=frozen.frozen();bundle=json.loads((prep/'bundle.json').read_text());messages=[m for v in bundle['lives'].values() for m in v['messages']]
 generated=prepare_sources(root/'input',messages,'America/New_York',native_fixtures=False)
 assert len(messages)==142 and len(checks)==42 and sum(len(c.claims) for _,c,_ in checks)==188
 for b in generated:
  expected=next(x for x in schedule if x['id']==b['id']);assert b['source_ids']==expected['source_ids']
  assert frozen.sha(root/'input'/b['source_file'])==expected['document_sha256']
 binding=cap.create(root,provider);env[cap.ENV]=json.dumps(binding)
 first=schedule[0];atomic(root/'active-slot.json',{'kind':'ingest','provider':provider,'batch_id':first['id'],'source_file':str(root/'input'/first['source_file']),'document_sha256':first['document_sha256']})
 from pacbench.observational_receipts import credit_snapshot
 from pacbench.openrouter_run import fetch_catalog
 route,_=fetch_catalog(profile='luna-bedrock-global');credit=credit_snapshot()
 ledger=Path('/workspace/shared/pacbench-openrouter-pizza-day-20261112/ledger.json');original=ledger.read_bytes();old=json.loads(original)
 safety=old['funded_runs']['pizza-week-scored-prepaid-20261114']['prepaid_safety'];assert safety['auto_recharge']=='off' and safety['evidence']
 record={'protocol':cap.PROTOCOL,'provider':provider,'state':'starting','questions_dispatched':0,'frozen_inputs':plan['frozen_inputs'],
  'membership_transport':'unchanged frozen per-message roster in logical documents','source_ids':first['source_ids'],'route':route,'prepaid_positive_verified':True,
  'auto_recharge':'off-owner-confirmed','historical_ledger_sha256':hashlib.sha256(original).hexdigest(),'concurrency_condition':'two independent native preflights; functional timing, not isolated latency',
  'execution_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
 atomic(root/'preflight.json',record);started=time.monotonic();shutdown=None
 with (root/'execution.lock').open('x') as lease:
  fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
  from pacbench.openrouter_direct import direct_environment
  key=env['OPENROUTER_API_KEY']
  with direct_environment(key):
   from pacbench.native_pilot import provision,probe,_ARM_SHUTDOWNS
   try:
    clock = {}
    if provider == 'honcho':
     from provider_adapters.honcho_enrollment import initial_at
     clock['honcho_simulated_at'] = initial_at(messages)
    native=provision(root,provider,'openai/gpt-6-luna',local_timezone='America/New_York',inference_route='openrouter',**clock)
    shutdown=_ARM_SHUTDOWNS.get(root);child={**env,**native}
    tools=probe(root,provider,child);atomic(root/'native-tools.json',tools)
    if tools.get('available') is not True:
     raise RuntimeError('Installed native Hermes provider did not pass its tool probe')
    result=ingest_day(provider,(root/'input'/first['source_file']).parent,root,child,first['at'],'America/New_York',timeout_s=240)
    atomic(root/'first-checkpoint-ingest.json',result)
    record.update(state='native-prefix-ready' if result.get('ready') else 'native-import-failed',native_ingest_ready=result.get('ready') is True,submitted_source_count=len(first['source_ids']),ingested_source_count=len(first['source_ids']) if result.get('ready') else None,tools=tools['tools'])
   except Exception as exc:
    record.update(state='blocked',error_type=type(exc).__name__,reason=str(exc)[:350])
   finally:
    if shutdown:
     try:shutdown();record['shutdown_requested']=True
     except Exception as exc:record['shutdown_error']=type(exc).__name__
    else:
     fallback=_ARM_SHUTDOWNS.get(root)
     if fallback:
      try:fallback();record['shutdown_requested']=True
      except Exception as exc:record['shutdown_error']=type(exc).__name__
    record['wall_s']=time.monotonic()-started;assert ledger.read_bytes()==original
    atomic(root/'preflight.json',record)
 print(json.dumps({k:record.get(k) for k in ['provider','state','error_type','reason','ingested_source_count','wall_s']}),flush=True)
 return record

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('provider',choices=cap.PROVIDERS);p.add_argument('root',type=Path);args=p.parse_args();run(args.root,args.provider)
