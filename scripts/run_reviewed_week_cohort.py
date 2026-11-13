"""Fresh native functional cohort; fixed audited corpus, no blind resumes."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal
import json,os,subprocess,threading,hashlib
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacbench.preparation_audit import audit_prepared
from pacbench.direct_accounting import live_budget,money
from pacbench.provider_versions import verify_memcal_source,verify_memos_package
from pacbench.openrouter_run import fetch_catalog
from pacbench.openrouter_route import MODEL,ENDPOINT
from pacbench.followups import digest
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--concurrency',type=int,choices=range(1,10),default=3)
parser.add_argument('--detach',action='store_true',help='Keep the cohort running while the terminal or chat turn ends; save private PID/log receipt')
parser.add_argument('--import-only',action='store_true',help='Run native import qualification without benchmark questions')
parser.add_argument('--preflight-only',action='store_true',help='Verify frozen inputs, clean source, runtime and accounting without creating a run')
ALL_PROVIDERS=['memcal','mem0','hindsight','supermemory','openviking','honcho','reme','cognee','memos']
parser.add_argument('--providers',nargs='+',choices=ALL_PROVIDERS,default=ALL_PROVIDERS,
    help='Run only these arms in fresh stores; retain completed arms from earlier cohorts')
args=parser.parse_args()
if len(args.providers)!=len(set(args.providers)):parser.error('Duplicate providers')
if args.detach:
 from pacbench.durable_job import launch
 print(json.dumps(launch([sys.executable,str(Path(__file__).resolve()),*[a for a in sys.argv[1:] if a!='--detach']],args.output)))
 raise SystemExit(0)
repo=Path(__file__).resolve().parents[1]
prepared=repo/'artifacts/pizza-week-hybrid-stream-v7-20261113/week.json';root=args.output.resolve()
assert prepared.is_file(),'Full source review and freeze must complete first'
assert audit_prepared(prepared.parent)['state']=='pass'
manifest=json.loads(prepared.read_text());body={k:v for k,v in manifest.items() if k!='corpus_hash'};assert digest(body)==manifest['corpus_hash']
for name,sha in manifest['files'].items():assert hashlib.sha256((prepared.parent/name).read_bytes()).hexdigest()==sha
portable_path='review-attempts/independent-source-audit/portable-bindings.json'
portable=json.loads((prepared.parent/portable_path).read_text());report_path=portable['report']
assert manifest['files'][report_path]==portable['sha256']
audit=json.loads((prepared.parent/report_path).read_text());assert audit['overall_verdict']=='pass' and audit['summary']['claims_reviewed']==155
for binding in audit['file_bindings']:
 name=binding['path']
 if name.startswith(portable['original_preparation_prefix']):
  rel=name.removeprefix(portable['original_preparation_prefix'])
  rel=rel if rel in manifest['files'] else portable['moved_root_evidence'][rel]
  assert manifest['files'][rel]==binding['sha256'];p=prepared.parent/rel
 else:p=repo/name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==binding['sha256'],name
assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=repo,text=True).strip()
verify_memcal_source(repo);verify_memos_package(repo);fetch_catalog();budget=live_budget()
assert sum(money(r['accounted']) for r in budget.snapshot())+Decimal('1')<=Decimal('20')
with budget.locked() as state:
 if state.get('handoff',{}).get('frozen') is True:raise RuntimeError('Transferred task accounting must be verified and activated in its destination session first')
from pacbench.context_isolation import validate_recall_environment
os.environ['TMPDIR']='/var/tmp'
validate_recall_environment()
from pacbench.bootstrap import verify,COMPONENTS
assert verify(COMPONENTS)['state']=='pass','Run python3 -m pacbench.bootstrap --install --start-services; reviewed dependencies must match before paid work'
if args.preflight_only:
 print(json.dumps({'state':'preflight-passed','paid_calls':0}));raise SystemExit(0)
root.mkdir(exist_ok=False)
providers=args.providers
record={'version':'stable-native-week-cohort-v3','state':'running','providers':providers,'concurrency':args.concurrency,
 'execution_scope':'import-only' if args.import_only else 'benchmark',
 'started':datetime.now(timezone.utc).isoformat(),'performance_scope':'Concurrent functional trials; no latency ranking',
 'execution_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
 'frozen_preparation':str(prepared.relative_to(repo)),'frozen_corpus_hash':manifest['corpus_hash'],
 'independent_source_audit_sha256':portable['sha256'],'questions':37,'atomic_claims':155,'source_messages':125,
 'source_review_protocol':'source-realism-v10','native_prompt_protocol':'native-question-v2',
 'source_provenance':'source-provenance-v3','model':MODEL,'endpoint':ENDPOINT,'judge_reasoning_effort':'low','judge_wall_deadline_s':600,'recall_wall_deadline_s':600,
 'honcho_condition':'simulated native clock; documented deriver thinking_effort none; native batching/output defaults',
 'cap_usd':'20','external_allocations_held_usd':'3','original_unknown_control_cost_held':True,'results':[]}
stop=threading.Event();stopfile=root/'STOP'
def run(provider):
 if stop.is_set():return {'provider':provider,'state':'not-started-task-hold'}
 target=root/provider;log=root/(provider+'.execution.private.log')
 cmd=[str(repo/'vendor/venv-hermes-isolated/bin/python'),'-m','pacbench.openrouter_run','--prepared',str(prepared),'--providers',provider,'--judge-reasoning-effort','low','--judge-timeout-s','600','--recall-timeout-s','600','--output',str(target),'--spend-limit-usd','20','--max-messages','150']
 if provider=='honcho':cmd+=['--honcho-clock','simulated']
 if args.import_only:cmd+=['--import-only']
 env={**os.environ,'TMPDIR':'/var/tmp','DERIVER_MODEL_CONFIG__THINKING_EFFORT':'none','PACBENCH_STOP_FILE':str(stopfile)}
 started=datetime.now(timezone.utc).isoformat()
 with log.open('xb') as stream:
  log.chmod(0o600);proc=subprocess.run(cmd,cwd=repo,env=env,stdout=stream,stderr=subprocess.STDOUT)
 summary=json.loads((target/'summary.json').read_text()) if (target/'summary.json').exists() else {}
 text=log.read_text(errors='replace')
 if any(s in text for s in ['Task authentication blocked','upstream_auth_blocked','Task spending cap reached','Reported task liability exceeded']):
  stop.set();stopfile.touch(exist_ok=True)
 return {'provider':provider,'returncode':proc.returncode,'state':summary.get('state','execution-failed'),'audit':summary.get('audit'),'started':started,'finished':datetime.now(timezone.utc).isoformat(),'output':str(target.relative_to(repo))}
(root/'cohort.json').write_text(json.dumps(record,indent=2)+'\n')
with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
 futures={pool.submit(run,name):name for name in providers}
 for future in as_completed(futures):
  try:row=future.result()
  except Exception as e:row={'provider':futures[future],'state':'orchestration-failed','error_class':type(e).__name__}
  record['results'].append(row);(root/'cohort.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(row),flush=True)
record['state']='finished';record['finished']=datetime.now(timezone.utc).isoformat();(root/'cohort.json').write_text(json.dumps(record,indent=2)+'\n')
