#!/usr/bin/env python3
"""Only the approved frozen r2 39→62-message / three-question native canary."""
import argparse,fcntl,json,os,subprocess,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def preflight():
 from pacbench import full_run_capability as cap
 from pacbench.campaigns.pizza_week_checkpoints import MEMCAL
 from pacbench.provider_versions import verify_memcal_source
 from pacbench.direct_accounting import live_budget,request_cost_bound
 from pacbench.funded_run_budget import selected,credit_snapshot,describe_admission
 from pacbench.openrouter_run import fetch_catalog
 from provider_adapters.embedding_health import check
 if (os.environ.get('PACBENCH_OPENROUTER_PROFILE')!='luna-bedrock-global' or
     os.environ.get('PACBENCH_ROLE_ROUTES')!='luna-all-roles-v1' or
     os.environ.get('PACBENCH_LUNA_COMPATIBILITY')!='temperature-omitted-default-v1' or
     os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')!='32768' or
     os.environ.get('PACBENCH_RESERVATION_POLICY')!='openai-cache-total-shared-v1' or
     os.environ.get('PACBENCH_FUNDED_RUN_ID')!='pizza-week-scored-prepaid-20261114'):
  raise RuntimeError('Explicit existing Luna/all-role/output/reservation/prepaid condition required')
 budget=live_budget()
 from pacbench.observational_receipts import enabled as observing
 with budget.locked(write=False) as state:
  if observing():
   safety=state.get('funded_runs',{}).get('pizza-week-scored-prepaid-20261114',{}).get('prepaid_safety',{})
   if safety.get('auto_recharge')!='off' or not safety.get('evidence'):raise RuntimeError('Auto Recharge off confirmation missing')
  elif any(r['state']=='reserved' for r in state['calls']):
   raise RuntimeError('Original ownership guard: retained reserved requests lack verified terminal owners; refuse before network or service setup')
 for name in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE','PACBENCH_OFFLINE_CONFORMANCE',cap.ENV,'PACBENCH_CHECKPOINT_CANARY']:
  if os.environ.get(name):raise RuntimeError('Cross-scope or offline permission cannot fund canary')
 os.environ.update(PACBENCH_MEMCAL_SRC=str(MEMCAL),MEMCAL_SRC=str(MEMCAL),
  PACBENCH_MEMCAL_CONDITION='wiki-preference-operations-review-c19d216-v1')
 pin=verify_memcal_source(ROOT)
 if pin['revision']!=cap.specification()['memcal']:raise RuntimeError('Wrong exact reviewed MemCal tree')
 route,inventory=fetch_catalog(profile='luna-bedrock-global')
 if 'temperature' in inventory['supported_parameters']:raise RuntimeError('Reviewed endpoint parameter contract changed')
 embedding=check('http://127.0.0.1:11435/v1')
 if observing():
  from pacbench.observational_receipts import credit_snapshot as observed_credit
  credit=observed_credit()
 else:credit=credit_snapshot()
 with budget.locked(write=False) as state:
  entry=selected(state)
  if not entry:raise RuntimeError('Original prepaid budget missing')
  arithmetic={'mode':'observational','local_caps_or_holds':False} if observing() else describe_admission(state,entry,credit)
 bound=None if observing() else request_cost_bound({**budget.config,'prompt_per_million':'.25'},32768,shared_context=True)
 if not observing() and bound>Decimal(arithmetic['currently_enforced_admission_room_usd']):raise RuntimeError('Next conservative canary request cannot fit original guards')
 from pacbench.live_dispatch import CONDITION
 return budget,{'state':'free-preflight-pass','source':pin,'frozen':cap.FROZEN,'specification':cap.specification(),'dispatch_tracking':CONDITION,
  'route':route,'inventory':inventory,'embedding':embedding,'credit':credit,'admission':arithmetic,
  'per_request_ceiling_usd':str(bound) if bound is not None else None,'aggregate_forecast_reserved':False,'paid_calls':0}


def main():
 ap=argparse.ArgumentParser(description='Exact three-arm revised r2 scored run');ap.add_argument('--output',type=Path,required=True);ap.add_argument('--execute',action='store_true');ap.add_argument('--resume-from',type=Path);a=ap.parse_args()
 from pacbench.gateway_reuse import atomic
 from pacbench.scored_run_report import display_usd
 budget,plan=preflight();atomic(a.output.with_name(a.output.name+'-preflight.json'),plan)
 if not a.execute:
  print(json.dumps({'state':plan['state'],'per_request_ceiling':display_usd(plan['per_request_ceiling_usd']) if plan['per_request_ceiling_usd'] is not None else 'not applied','new_paid_calls':0}));return
 subprocess.run(['git','diff','--quiet'],cwd=ROOT,check=True)
 from pacbench.campaigns.pizza_week_checkpoints import run_full
 from pacbench.openrouter_direct import direct_environment
 from pacbench.gateway_client import client_settings
 from pacbench.full_run_capability import ENV
 from pacbench.live_dispatch import ENV as TRACKING_ENV,CONDITION
 os.environ[TRACKING_ENV]=CONDITION
 lock_root=budget.original_path.parent if hasattr(budget,'original_path') else budget.path.parent
 with (lock_root/'native-diagnostic-owner.lock').open('r') as lease:
  fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:
   with direct_environment(os.environ['OPENROUTER_API_KEY']):
    report=run_full(a.output,dict(os.environ),settings=client_settings,budget=budget,resume_from=a.resume_from)
  finally:
   rows=[r for r in budget.snapshot() if r.get('checkpoint_full_run_root')==str(a.output.resolve())]
   billing={'attempts':len(rows),'settled_actual_usd':str(sum((Decimal(r['settled_charge_usd']) for r in rows if r['state']=='settled' and r.get('settled_charge_usd') is not None),Decimal(0))),
    'unresolved_attempts':[r['id'] for r in rows if r['state']!='settled' or not r.get('billing_receipt')],
    'receipt_attempt_ids':[r['id'] for r in rows]}
   atomic(a.output/'billing.json',billing)
  print(json.dumps({'state':report['state'],'providers':report['providers'],'actual_settled':display_usd(billing['settled_actual_usd']),'unresolved_attempts':len(billing['unresolved_attempts'])}))

if __name__=='__main__':main()
