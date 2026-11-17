"""Answer availability and cleanup are distinct measured lifecycle boundaries."""
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import statistics

def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def gateway_events(text,timezone):
    if not timezone:return []
    zone=ZoneInfo(timezone);out=[]
    for line in text.splitlines():
        match=re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) ',line)
        if not match:continue
        kind=('answer_ready' if 'response ready:' in line else
              'shutdown_requested' if 'Shutdown context:' in line else None)
        if kind:
            naive=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S,%f')
            for ts in {naive.replace(tzinfo=zone,fold=f).timestamp() for f in [0,1]}:
                out.append((ts,kind))
    return out

def measure(turn,events=()):
    t=turn if isinstance(turn,dict) else vars(turn);g=t.get('hermes_gateway') or {}
    result={'protocol':'turn-lifecycle-timing-v1','runner_wall_s':t.get('latency_s'),
        'native_reported_turn_s':t.get('native_result_duration_s'),
        'answer_ready_latency_s':None,'post_answer_cleanup_s':None,
        'answer_ready_evidence':None,'shutdown_cleanup_s':None}
    if t.get('timed_out') or t.get('returncode')!=0 or not t.get('answer'):return result
    try:a=stamp(g['real_started_at']);b=stamp(g['real_finished_at'])
    except (KeyError,ValueError,TypeError):return result
    if b<a:return result
    ready=[ts for ts,kind in events if kind=='answer_ready' and a<=ts<=b]
    if len(ready)==1:at=ready[0];source='native-gateway-response-ready-log'
    elif not ready and g.get('real_cli_result_received_at'):
        at=stamp(g['real_cli_result_received_at']);source='CLI-result-received: upper bound on answer availability'
    else:return result
    if not a<=at<=b:return result
    result.update(answer_ready_latency_s=at-a,post_answer_cleanup_s=b-at,answer_ready_evidence=source)
    shut=[ts for ts,kind in events if kind=='shutdown_requested' and at<=ts<=b]
    if len(shut)==1:result['shutdown_cleanup_s']=b-shut[0]
    elif g.get('real_shutdown_started_at'):
        s=stamp(g['real_shutdown_started_at'])
        if at<=s<=b:result['shutdown_cleanup_s']=b-s
    return result

def distribution(values):
    v=sorted(x for x in values if x is not None)
    if not v:return {'count':0,'mean':None,'median':None,'p95':None,'max':None}
    p=(len(v)-1)*.95;lo=int(p);hi=min(lo+1,len(v)-1)
    return {'count':len(v),'mean':statistics.mean(v),'median':statistics.median(v),
            'p95':v[lo]+(v[hi]-v[lo])*(p-lo),'max':v[-1]}

def summary(records):
    return {key:distribution(r[key] for r in records) for key in
            ['runner_wall_s','native_reported_turn_s','answer_ready_latency_s','post_answer_cleanup_s','shutdown_cleanup_s']}
