"""Read-only recovery after post-answer reporting fails, never a model replay."""
import json,hashlib
from pathlib import Path
from .harness import TurnResult
from .hermes_gateway import GatewayLifecycleError
from .gateway_reuse import atomic
from .checkpoint_evaluator import audit_transport
from .context_isolation import memory_delivery


def recover(folder,destination):
 folder=Path(folder);ready=folder/'answer-ready.json';cleanup=folder/'cleanup.json'
 r=json.loads(ready.read_text());c=json.loads(cleanup.read_text());t=TurnResult(**r['native_turn'])
 e=r['observation_clock'];d=e['descriptor'];home=Path(d['home']);sample=c.get('native_observation',{})
 if (t.hermes_gateway is not None or c.get('state')!='drained' or c.get('preclose') is not None or
     sample.get('pid')!=r['gateway_pid'] or sample.get('start_time')!=d['gateway_start_time'] or
     not sample.get('sessions') or not all(s.get('clean') for s in sample['sessions']) or
     [m['content'] for m in r['answer_visible_records'] if m['role']=='user']!=[t.prompt]):
  raise GatewayLifecycleError('Closed answer recovery requires a complete independent drained session')
 delivery=memory_delivery(home,t.session_id,t.prompt,t.prefetch_context)
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 t.hermes_gateway={'version':'checkpoint-session-v1','pid':r['gateway_pid'],'start_time':d['gateway_start_time'],
  'clock_evidence':e,'receipt':str(folder),'native_drain':'complete','fresh_session':True,
  'authored_checkpoint_resume':False,'native_result_token_scope':'turn','prior_user_count':0,'new_user_count':1,
  'memory_delivery':delivery,'close_condition':None,'preclose':None,'answer_ready_sha256':sha(ready),
  'cleanup_sha256':sha(cleanup),'answer_visible_capture':'complete' if delivery['state'] in {'confirmed','empty'} else 'unknown'}
 t.lifecycle_timing={'protocol':'turn-lifecycle-timing-v1','runner_wall_s':r['answer_ready_latency_s']+c['post_answer_cleanup_s'],
  'answer_ready_latency_s':r['answer_ready_latency_s'],'post_answer_cleanup_s':c['post_answer_cleanup_s'],
  'gateway_persisted':True,'preclose_wait_s':0}
 audit_transport(t)
 atomic(Path(destination),vars(t))
 return str(destination)


def prepare_sessions(turn_paths,destination,checkpoint):
 """Restore unchanged closed native history for a pending omission follow-up."""
 import sqlite3
 from .checkpoint_clock import history
 from .hermes_gateway import _live
 destination=Path(destination);records={};homes=set()
 for path in turn_paths:
  t=TurnResult(**json.loads(Path(path).read_text()));audit_transport(t)
  r=json.loads((Path(t.hermes_gateway['receipt'])/'answer-ready.json').read_text())
  d=r['observation_clock']['descriptor'];c=json.loads((Path(t.hermes_gateway['receipt'])/'cleanup.json').read_text())
  if r['checkpoint']!=checkpoint or _live(r['gateway_pid']) or c['state']!='drained':
   raise GatewayLifecycleError('Recovered native session is not closed at this checkpoint')
  home=Path(d['home']);homes.add(home)
  if hashlib.sha256((home/'config.yaml').read_bytes()).hexdigest()!=hashlib.sha256((destination/'config.yaml').read_bytes()).hexdigest():
   raise GatewayLifecycleError('Recovered session configuration changed')
  h=history(home,t.session_id)
  if h['user_texts']!=[t.prompt]:raise GatewayLifecycleError('Recovered independent session has unexpected history')
  records[t.session_id]={'turn':t,'observation':r['observation'],'history':h,'horizon':d['horizon']}
 if not records:return {}
 if len(homes)!=1 or (destination/'state.db').exists():raise GatewayLifecycleError('Recovered history requires one stopped source and a blank destination')
 source=next(iter(homes))/'state.db'
 with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as old,sqlite3.connect(destination/'state.db') as new:
  old.backup(new)
  if new.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise GatewayLifecycleError('Recovered native history failed integrity check')
 for sid,record in records.items():
  if history(destination,sid)!=record['history']:raise GatewayLifecycleError('Recovered history differs')
 return records


def admit_session(owner,record,question):
 from .checkpoint_clock import history
 t=record['turn'];sid=t.session_id
 if sid in owner.session_context or history(owner.home,sid)!=record['history'] or t.at!=question['at'] or t.prompt!=question['prompt']:
  raise GatewayLifecycleError('Recovered session binding changed')
 owner.session_context[sid]={'initial_at':t.at,'session_started_at':t.session_started_at or t.at,
  'history_sha256':record['history']['sha256'],'user_texts':[t.prompt],'observation':record['observation'],
  'followups':0,'horizon':record['horizon']}


def recover_preclose(folder,destination):
 """Requalify retained evidence under the nonce-bound manager correction.

 No original receipt is changed; this overlay records both the former rejection
 and the exact persisted quiescence/closure witnesses. It never reruns an answer.
 """
 import shutil
 from .gateway.preclose import qualified,CONDITION
 from .hermes_gateway import _live
 folder=Path(folder);destination=Path(destination);r=json.loads((folder/'answer-ready.json').read_text())
 p=json.loads((folder/'preclose.json').read_text());t=TurnResult(**r['native_turn']);d=r['observation_clock']['descriptor']
 latest=json.loads((folder/'native-lifecycle-failure.json').read_text())['latest_validated_observation']
 home=Path(d['home']);stops=[json.loads(x.read_text()) for x in (home.parent/'gateway-lifecycles').glob('*.json')]
 stopped=[x for x in stops if x.get('pid')==r['gateway_pid'] and x.get('start_time')==d['gateway_start_time'] and x.get('stopped') is True and x.get('returncode')==0]
 if (not stopped or _live(r['gateway_pid']) or latest.get('native_failures') or latest.get('native_memory_threads') or latest.get('native_release_threads') or
     latest.get('pid')!=r['gateway_pid'] or latest.get('start_time')!=d['gateway_start_time'] or
     latest.get('application_clock',{}).get('nonce')!=p['nonce'] or latest.get('observed_monotonic_ns',0)<=p['observation']['observed_monotonic_ns'] or
     not latest.get('sessions') or not all(x.get('clean') and x.get('shutdown_returned') for x in latest['sessions']) or
     not qualified(p['observation'],session=t.session_id,nonce=p['nonce'],after_ns=p['terminal_verified_before_ns'],prompt_hash=hashlib.sha256(t.prompt.encode()).hexdigest())):
  raise GatewayLifecycleError('Retained pre-close/closure evidence cannot qualify this answer')
 destination.mkdir()
 shutil.copyfile(folder/'answer-ready.json',destination/'answer-ready.json')
 sha=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
 p.update(state='quiescent',revalidation={'condition':'request-nonce-manager-selection-v1','original_preclose':{'path':str(folder/'preclose.json'),'sha256':sha(folder/'preclose.json')},
  'post_close_evidence':{'path':str(folder/'native-lifecycle-failure.json'),'sha256':sha(folder/'native-lifecycle-failure.json')},'gateway_stopped':stopped[0],'no_model_replay':True})
 atomic(destination/'preclose.json',p);binding={'path':str(destination/'preclose.json'),'sha256':sha(destination/'preclose.json')}
 c={'protocol':r['protocol'],'state':'drained','post_answer_cleanup_s':None,'timing_status':'not directly retained after original false rejection',
  'native_observation':latest,'preclose':binding};atomic(destination/'cleanup.json',c)
 delivery=memory_delivery(home,t.session_id,t.prompt,t.prefetch_context)
 users=[x for x in r['answer_visible_records'] if x['role']=='user']
 t.hermes_gateway={'version':'checkpoint-session-v1','pid':r['gateway_pid'],'start_time':d['gateway_start_time'],
  'clock_evidence':r['observation_clock'],'receipt':str(destination),'native_drain':'complete','fresh_session':len(users)==1,
  'authored_checkpoint_resume':False,'native_result_token_scope':'turn','prior_user_count':len(users)-1,'new_user_count':1,
  'memory_delivery':delivery,'close_condition':CONDITION,'preclose':binding,'answer_ready_sha256':sha(destination/'answer-ready.json'),
  'cleanup_sha256':sha(destination/'cleanup.json'),'answer_visible_capture':'complete' if delivery['state'] in {'confirmed','empty'} else 'unknown'}
 t.lifecycle_timing={'protocol':'turn-lifecycle-timing-v1','answer_ready_latency_s':r['answer_ready_latency_s'],
  'post_answer_cleanup_s':None,'gateway_persisted':True,'preclose_wait_s':p['preclose_wait_s'],'cleanup_timing_status':c['timing_status']}
 audit_transport(t);path=destination/'completed-turn.json';atomic(path,vars(t));return str(path)
