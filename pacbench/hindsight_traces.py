"""Read the local server's default traces; never invoke reflection or mutate data."""
from collections import Counter
from datetime import datetime
import json
import time
import urllib.parse
import urllib.request

KEY='hindsight-native-traces'


def read(env):
    endpoint=env['HINDSIGHT_API_URL']
    if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal trace server')
    base=endpoint.rstrip('/')+'/v1/default/banks/'+urllib.parse.quote(env['HINDSIGHT_BANK_ID'],safe='')
    rows=[]
    for offset in range(0,10000,100):
        query=urllib.parse.urlencode({'operation':'reflect','call_type':'llm','limit':100,'offset':offset})
        with urllib.request.urlopen(base+'/llm-requests?'+query,timeout=10) as response:value=json.load(response)
        rows.extend(value['items'])
        if len(rows)>=value['total']:return rows
    raise ValueError('Native trace pagination exceeded bound')


def snapshot(env):
    try:return {'ids':[row['id'] for row in read(env)]}
    except Exception as exc:return {'error':type(exc).__name__}


def convert(rows,turn):
    groups={}
    for row in rows:
        if not isinstance(row.get('trace_id'),str) or not row['trace_id']:raise ValueError('Native operation identity missing')
        groups.setdefault(row['trace_id'],[]).append(row)
    explicit={(c.get('input',c.get('arguments',{})) or {}).get('query')
              for c in turn.tool_calls if c.get('name')=='hindsight_reflect'}
    traces=[]
    for trace_id,group in groups.items():
        calls={};results={};names=Counter();query=None
        for row in sorted(group,key=lambda r:r['started_at']):
            names.update(row['llm_info'].get('tool_calls',[]))
            messages=row.get('input')
            if not isinstance(messages,list):raise ValueError('Native trace input missing or truncated')
            if query is None:query=next((m.get('content') for m in messages if m.get('role')=='user'),None)
            for message in messages:
                if message.get('role')=='assistant':
                    for call in message.get('tool_calls',[]) or []:
                        fn=call['function'];args=fn['arguments']
                        calls[call['id']]={'toolName':fn['name'],'args':json.loads(args) if isinstance(args,str) else args}
                elif message.get('role')=='tool':
                    content=message.get('content')
                    try:content=json.loads(content)
                    except (ValueError,TypeError):pass
                    results[message['tool_call_id']]=content
        operations=[]
        for id,call in calls.items():
            operations.append({**call,'id':id,'result':results.get(id),'capture':'native-messages'})
            names[call['toolName']]-=1
        for name,count in names.items():
            if count<0:raise ValueError('Native trace tool count mismatch')
            operations.extend({'toolName':name,'args':None,'result':None,'capture':'native-name-only'} for _ in range(count))
        traces.append({'provider':'hindsight','attribution':'explicit-query-match' if query in explicit else 'background-or-prefetch',
            'record':{'trace_id':trace_id,'query':query,'timestamp':max(datetime.fromisoformat(r['ended_at']).timestamp() for r in group),
                      'complete':any(r.get('scope')=='reflect' and r.get('status')=='success' for r in group),
                      'model_calls':[{k:r.get(k) for k in ('id','scope','started_at','ended_at','status','input_tokens',
                                     'output_tokens','cached_tokens','thoughts_tokens','duration_ms')} for r in group],
                      'native_request_ids':[r['id'] for r in group],'toolCalls':operations},
            'scope':'Existing native model-call traces; prompts and reasoning omitted. Tool arguments/results are retained where the native trace records them.'})
    return traces


def capture(env,before,turn):
    try:
        if before.get('error'):raise ValueError('Initial native trace snapshot unavailable')
        # The pinned 1.1.0 writer flushes every 0.5 seconds. Allow its ordinary
        # flush, then stop as soon as explicit reflection coverage is complete.
        started=time.monotonic();earliest=started+0.75;deadline=started+3
        known=set(before['ids'])
        expected=Counter((c.get('input',c.get('arguments',{})) or {}).get('query')
                         for c in turn.tool_calls if c.get('name')=='hindsight_reflect')
        while True:
            rows=read(env)
            traces=convert([r for r in rows if r['id'] not in known],turn)
            observed=Counter(t['record']['query'] for t in traces if t['attribution']=='explicit-query-match' and t['record']['complete'])
            now=time.monotonic()
            if now>=deadline or now>=earliest and not expected-observed:break
            time.sleep(0.25)
        if expected-observed:traces.append({'provider':'hindsight','error':'Native reflection trace coverage incomplete'})
        return traces
    except Exception as exc:return [{'provider':'hindsight','error':'Native trace observation failed: '+type(exc).__name__}]


def category(call):
    name=call.get('toolName');result=call.get('result')
    if name=='expand':return 'source_evidence'
    if name=='recall':
        if not isinstance(result,dict):return 'unclassified'
        chunks=result.get('chunks')
        if isinstance(chunks,dict) and any(isinstance(v,dict) and v.get('chunk_text') for v in chunks.values()):
            return 'source_messages'
        return 'memory_search'
    if name in {'search_observations','list_observations','get_observation'}:return 'memory_search'
    if name in {'answer_ready','done'}:return 'memory_reasoning'
    return 'unclassified'
