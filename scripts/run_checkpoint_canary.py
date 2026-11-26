#!/usr/bin/env python3
"""Only the approved frozen r2 39→62-message / three-question native canary."""
import argparse,fcntl,json,os,subprocess,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def preflight():
 from pacbench import canary_capability as cap
 from pacbench.campaigns.pizza_week_checkpoints import MEMCAL
 from pacbench.provider_versions import verify_memcal_source
 from pacbench.direct_accounting import live_budget,request_cost_bound
 from pacbench.funded_run_budget import selected,credit_snapshot,describe_admission
 from pacbench.openrouter_run import fetch_catalog
 from provider_adapters.embedding_health import check
 if (os.environ.get('PACBENCH_OPENROUTER_PROFILE')!='luna-bedrock-global' or
     os.environ.get('PACBENCH_ROLE_ROUTES')!='luna-all-roles-v1' or
     os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')!='32768' or
     os.environ.get('PACBENCH_RESERVATION_POLICY')!='openai-cache-total-shared-v1' or
     os.environ.get('PACBENCH_FUNDED_RUN_ID')!='pizza-week-scored-prepaid-20261114'):
  raise RuntimeError('Explicit existing Luna/all-role/output/reservation/prepaid condition required')
 for name in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE','PACBENCH_OFFLINE_CONFORMANCE',cap.ENV]:
  if os.environ.get(name):raise RuntimeError('Cross-scope or offline permission cannot fund canary')
 os.environ.update(PACBENCH_MEMCAL_SRC=str(MEMCAL),MEMCAL_SRC=str(MEMCAL),
  PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 pin=verify_memcal_source(ROOT)
 if pin['revision']!=cap.specification()['memcal']:raise RuntimeError('Wrong exact reviewed MemCal tree')
 route,inventory=fetch_catalog(profile='luna-bedrock-global')
 embedding=check('http://127.0.0.1:11435/v1')
 budget=live_budget();credit=credit_snapshot()
 with budget.locked(write=False) as state:
  entry=selected(state)
  if not entry:raise RuntimeError('Original prepaid budget missing')
  arithmetic=describe_admission(state,entry,credit)
 bound=request_cost_bound({**budget.config,'prompt_per_million':'.25'},32768,shared_context=True)
 if bound>Decimal(arithmetic['currently_enforced_admission_room_usd']):raise RuntimeError('Next conservative canary request cannot fit original guards')
 return budget,{'state':'free-preflight-pass','source':pin,'frozen':cap.FROZEN,'specification':cap.specification(),
  'route':route,'inventory':inventory,'embedding':embedding,'credit':credit,'admission':arithmetic,
  'per_request_ceiling_usd':str(bound),'aggregate_forecast_reserved':False,'paid_calls':0}


def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--execute',action='store_true');ap.add_argument('--resume-tool-wiring',action='store_true');ap.add_argument('--resume-reme-preclose',action='store_true');a=ap.parse_args()
 from pacbench.gateway_reuse import atomic
 from pacbench.scored_run_report import display_usd
 if a.resume_tool_wiring and a.resume_reme_preclose:raise RuntimeError('One recovery stage only')
 if a.resume_reme_preclose:
  from pacbench.gateway.preclose import ENV,CONDITION
  os.environ[ENV]=CONDITION
 budget,plan=preflight();path=a.output.with_name(a.output.name+('-reme-preclose-preflight.json' if a.resume_reme_preclose else '-recovery-preflight.json' if a.resume_tool_wiring else '-preflight.json'))
 atomic(path,plan)
 if not a.execute:
  print(json.dumps({'state':plan['state'],'per_request_ceiling':display_usd(plan['per_request_ceiling_usd']),'new_paid_calls':0}));return
 # No new ledger, allocation, key, provider route or general scoring permission.
 subprocess.run(['git','diff','--quiet'],cwd=ROOT,check=True)
 from pacbench.campaigns.pizza_week_checkpoints import run_canary, canary_receipts
 from pacbench.openrouter_direct import direct_environment
 from pacbench.gateway_client import client_settings
 key=os.environ['OPENROUTER_API_KEY']
 with (budget.path.parent/'native-diagnostic-owner.lock').open('a') as lease:
  fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
  initial_ids={r['id'] for r in canary_receipts(budget,a.output.resolve())} if a.output.exists() else set()
  with direct_environment(key):
   env=dict(os.environ)
   if a.resume_tool_wiring or a.resume_reme_preclose:
    canary_receipts(budget,a.output.resolve())
    from pacbench.hermes_gateway import _live
    prior=json.loads((a.output/'report.json').read_text())
    pids={json.loads(Path(t['receipt']).read_text())['hermes_gateway']['pid'] for arm in prior['providers'].values() for t in arm['turns']}
    pids|={json.loads(p.read_text())['pid'] for p in a.output.rglob('native-server-setup.json')}
    pids|={json.loads(p.read_text())['gateway_pid'] for p in a.output.rglob('answer-ready.json')}
    if any(_live(pid) for pid in pids):raise RuntimeError('Prior canary process remains live; recovery admission stopped')
    stopped=a.output/'STOP'
    reason='reme-native-shutdown-drain-unverified' if a.resume_reme_preclose else 'canary-empty-provider-tool-allowlist'
    if json.loads(stopped.read_text()).get('reason')!=reason:raise RuntimeError('Recovery cannot waive unrelated stop')
    stopped.rename(a.output/('STOP.preclose-order-fixed.json' if a.resume_reme_preclose else 'STOP.tool-wiring-fixed.json'))
   report=run_canary(a.output,env,settings=client_settings,budget=budget,resume=a.resume_tool_wiring or a.resume_reme_preclose,only_reme=a.resume_reme_preclose,preclose_recovery=a.resume_reme_preclose)
  rows=canary_receipts(budget,a.output.resolve())
  actual=sum((Decimal(r['settled_charge_usd']) for r in rows),Decimal(0))
  report['billing']={'new_stage_actual_settled_usd':str(sum((Decimal(r['settled_charge_usd']) for r in rows if r['id'] not in initial_ids),Decimal(0))),
   'new_stage_attempts':sum(r['id'] not in initial_ids for r in rows),'actual_settled_usd':str(actual),'attempts':len(rows),'unknown_outcomes':0,
   'receipt_attempt_ids':[r['id'] for r in rows]}
  atomic((a.output/('reme-preclose-recovery-v3' if a.resume_reme_preclose else 'tool-wiring-recovery-v2') if a.resume_tool_wiring or a.resume_reme_preclose else a.output)/'report.json',report)
  print(json.dumps({'state':report['state'],'providers':{p:{'state':r['state'],'questions':len(r['turns'])} for p,r in report['providers'].items()},
   'all_canary_actual_settled':display_usd(actual),'new_stage_actual_settled':display_usd(report['billing']['new_stage_actual_settled_usd']),'new_stage_attempts':report['billing']['new_stage_attempts']}))

if __name__=='__main__':main()
