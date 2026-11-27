"""Retained scope."""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from pacbench.corpus.r2 import cache_bound_schedule
from pacbench.evidence_store import file_sha as sha
from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.receipt_costs import money
from pacbench.workflows.spec import instant

FROZEN='a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'


IDS=('rsvp-before','cancellation-before','rsvp-after')


PROVIDERS=('memcal','mem0','reme')


def read_canary_binding(b):
 p=Path(b['path'])
 if p.is_symlink() or sha(p)!=b['sha256']:raise GatewayLifecycleError('Canary bound evidence changed')
 return json.loads(p.read_text())


def frozen_canary(*,full=False):
 from pacbench.workflows.turn_evidence import freeze_inputs
 repo=Path(__file__).resolve().parents[2]
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
 from pacbench.workflows.source_plan import daily_batches, document_text
 batches=dict(daily_batches(messages,'America/New_York'))
 for b in schedule:
  native=json.loads((preparation/'sources'/b['id']/'messages.json').read_text())
  if native!=batches[b['id']] or [m['source_id'] for m in native]!=b['source_ids'] or hashlib.sha256(document_text(native,b['at']).encode()).hexdigest()!=b['document_sha256']:
   raise GatewayLifecycleError('Canary source order or transport differs')
 if full:return preparation,schedule,all_checks,plan
 return preparation,schedule,checks,{**plan,'groups':groups,'questions':3,'criteria':sum(len(c.claims) for _,c,_ in checks)}


ENV='PACBENCH_CHECKPOINT_FULL_RUN'


PROTOCOL='r2-three-arm-scored-checkpoint-v1'


MAX_CALLS=2500


def read_full_binding(b):
 p=Path(b['path'])
 if p.is_symlink() or sha(p)!=b['sha256']:raise GatewayLifecycleError('Full-run bound evidence changed')
 return json.loads(p.read_text())


def frozen_full():
 from pacbench.judging.retained_scope import frozen_canary as source
 return source(full=True)


def specification():
 _,schedule,_,plan=frozen_full()
 return {'full_frozen':FROZEN,'groups':plan['groups'],'schedule':schedule,
  'model':'openai/gpt-6-luna','profile':'luna-bedrock-global','endpoint':'amazon-bedrock/global',
  'scoring':'semantic-judge-date-scope-v2','strict_memory':'strict-memory-only-v1',
  'session_transfer':'native-cold-session-checkpoint-v1',
  'luna_compatibility':'temperature-omitted-default-v1',
  'memcal':'c19d216a7e5dd3913f69ab79c24d2d339bacef16','max_calls':MAX_CALLS}


def load_full_scope(b=None):
 if any(os.environ.get(k) for k in ['PACBENCH_EXECUTION_SCOPE','PACBENCH_NATIVE_DIAGNOSTIC_SCOPE','PACBENCH_CHECKPOINT_CANARY']):
  raise GatewayLifecycleError('Full-run cannot share render or other diagnostic capability')
 if b is None:
  value=os.environ.get(ENV)
  if not value:raise GatewayLifecycleError('Full-run capability absent')
  b=json.loads(value)
 c=read_full_binding(b)
 if set(c)!={'protocol','root','specification','projects','configs','slot_path','recovery'} or c['protocol']!=PROTOCOL or c['specification']!=specification():raise GatewayLifecycleError('Full-run scope/contract/route differs')
 root=Path(c['root']).resolve()
 if Path(b['path']).resolve()!=root/'capability.json' or c['slot_path']!=str(root/'active-slot.json'):raise GatewayLifecycleError('Full-run paths differ')
 expected={p:{r:'pacbench-'+hashlib.sha256(str((root/p).resolve()).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver','judge']} for p in PROVIDERS}
 if c['projects']!=expected or set(c['configs'])!=set(PROVIDERS):raise GatewayLifecycleError('Full-run projects/configs differ')
 if c['recovery']:
  prior=read_full_binding(c['recovery']['capability']);report=read_full_binding(c['recovery']['report'])
  if (prior['root']!=c['recovery']['root'] or prior['protocol']!=PROTOCOL or prior['specification']['full_frozen']!=FROZEN or prior['configs']!=c['configs'] or report['frozen_inputs']['sha256']!=FROZEN):raise GatewayLifecycleError('Recovery source/condition differs')
 return c


def recovery_origins(c):
 """Verify the immutable recovery chain before including ancestor receipts."""
 result=[]
 while True:
  if c['root'] in result or len(result)>=8:raise GatewayLifecycleError('Invalid recovery ancestry')
  result.append(c['root'])
  if not c.get('recovery'):return result
  parent=read_full_binding(c['recovery']['capability']);report=read_full_binding(c['recovery']['report'])
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


FUNDED_RUN_BUDGET_ENV = 'PACBENCH_FUNDED_RUN_ID'


def recorded_funded_run(state):
    ident=os.environ.get(FUNDED_RUN_BUDGET_ENV)
    if not ident:return None
    entry=state.get('funded_runs',{}).get(ident)
    if (not entry or entry.get('id')!=ident or entry.get('status')!='active' or
        not Decimal(0)<money(entry['limit_usd'])):
        raise RuntimeError('Explicit funded run authorization unavailable')
    if money(entry['limit_usd'])>20:
        alignment=entry.get('prepaid_limit_alignment',{})
        if (alignment.get('protocol')!='verified-prepaid-cumulative-limit-v1' or
            not alignment.get('authorization') or alignment.get('new_limit_usd')!=entry['limit_usd'] or
            money(alignment['new_limit_usd'])!=money(alignment['cumulative_exposure_usd'])+money(alignment['payable_room_usd'])):
            raise RuntimeError('Explicit prepaid cumulative-limit authorization unavailable')
    safety=entry.get('prepaid_safety',{})
    if not safety.get('evidence') or safety.get('auto_recharge') not in {'off','verified-threshold'}:
        raise RuntimeError('Auto Recharge verification unavailable; paid admission paused')
    if safety['auto_recharge']=='verified-threshold' and money(safety.get('local_stop_floor_usd',0))<=money(safety['threshold_usd']):
        raise RuntimeError('Prepaid stop floor must exceed verified Auto Recharge threshold')
    return entry


MEMCAL_SCOPE = 'memcal-source-operation-v1'


@cache_bound_schedule(maxsize=2)
def _memcal_schedule(spec_sha, canary):
    from pacbench.judging.retained_scope import retained_schedule as schedule
    from pacbench.providers.memcal.source import verify_contract
    from pacbench.workflows.source_plan import _encode
    full_docs, full_turns, frozen = schedule(spec_sha, False)
    verified = verify_contract(full_docs, full_turns, frozen)
    docs, turns, _ = schedule(spec_sha, canary)
    plan = hashlib.sha256(b'pacbench-source-plan-v1\n')
    for kind, entries in [('documents', docs), ('turns', turns), ('all_source_documents', full_docs)]:
        plan.update(kind.encode() + b'\n')
        for obj in entries:
            plan.update(_encode(obj).encode() + b'\n')
    releases = {}
    for doc in docs:
        night = releases.setdefault(doc.cutoff, {})
        for sid, body in zip(doc.source_ids, doc.bodies, strict=True):
            night[sid] = hashlib.sha256(body.encode()).hexdigest()
    by_id = {}
    latest = {}
    for index, turn in enumerate(turns):
        cutoff = max((at for at in releases if instant(at) <= instant(turn.at)), default=None)
        by_id[turn.id] = index, turn, cutoff
        latest[turn.session] = turn.id
    return verified, plan.hexdigest(), releases, by_id, latest


PROFILE = 'luna-bedrock-global'


MEMOS_OSS_ADMISSION = 'memos-oss-native-file-document-week-v1'


class _DocumentArm(NamedTuple):
    admission: str
    canary_module: str
    store: str


DOCUMENT_ARMS = {
    'mem0': _DocumentArm('mem0-native-oss-document-week-v1', '.providers.mem0', 'mem0-state'),
    'hindsight': _DocumentArm('hindsight-native-file-document-week-v1', '.providers.hindsight.workflow', 'fallback-home'),
    'openviking': _DocumentArm('openviking-native-file-document-week-v1', '.providers.openviking', 'openviking-data'),
    'reme': _DocumentArm('reme-native-watched-resource-week-v1', '.providers.reme', 'reme-workspace'),
    'cognee': _DocumentArm('cognee-native-permanent-document-week-v1', '.providers.cognee', 'cognee-data'),
    'memos': _DocumentArm(MEMOS_OSS_ADMISSION, '.providers.memos', 'memos-oss-service'),
}


@cache_bound_schedule(maxsize=2)
def retained_schedule(spec_sha, canary):
    from pacbench.corpus.r2 import compile_pizza_week

    plan = compile_pizza_week(canary=canary)
    if spec_sha != plan.spec_sha256:
        raise ValueError('workflow spec changed')
    return list(plan.documents), list(plan.turns), plan.source_basis
