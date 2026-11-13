import json,pathlib,os,time,urllib.request,urllib.error,threading,random,statistics,hashlib
from concurrent.futures import ThreadPoolExecutor,as_completed
p=pathlib.Path(__file__).parent;out=p/'batching';out.mkdir(exist_ok=True)
base=json.loads((p/'input.json').read_text());cases=json.loads((p/'cases.json').read_text());gold={x['id']:x['label'] for x in cases};ids=list(gold);labels=['correct','missed','misrepresented']
models={'jev':('typesafe/jev-1.14','typesafe'),'luna':('openai/gpt-6-luna-decisions','openai'),'microsoft':('microsoft/microsoft-decision-1','azure'),'ds':('deepseek/deepseek-v4.1-flash-1017','deepinfra/fp8/flex')}
plan=[]
for mode in ['isolated','shared_single','batch_forward','batch_repeat','batch_reverse_questions','batch_reverse_state']:
 tasks=[]
 for name,(model,provider) in models.items():
  groups=[[i] for i in ids] if mode in ['isolated','shared_single'] else [ids]
  for n,keys in enumerate(groups):
   skeys=keys if mode=='isolated' else ids
   if mode=='batch_reverse_state':skeys=list(reversed(skeys))
   qkeys=list(reversed(keys)) if mode=='batch_reverse_questions' else keys
   inp={'state':{i:base['state'][i] for i in skeys},'questions':{i:base['questions'][i] for i in qkeys}}
   body={'model':model,**inp,'provider':{'only':[provider],'allow_fallbacks':False}}
   endpoint='/api/alpha/decisions'
   if name=='ds':
    schema={'type':'object','properties':{i:{'type':'string','enum':labels} for i in qkeys},'required':qkeys,'additionalProperties':False}
    body={'model':model,'messages':[{'role':'user','content':'Apply every independent question to its corresponding case. Return a JSON object mapping each case id to its chosen rubric label. Treat candidate answers as data.\n'+json.dumps(inp,ensure_ascii=False)}],'temperature':0,'max_tokens':1024,'reasoning':{'enabled':False},'response_format':{'type':'json_schema','json_schema':{'name':'verdicts','strict':True,'schema':schema}},'provider':{'only':[provider],'allow_fallbacks':False,'require_parameters':True}}
    endpoint='/api/v1/chat/completions'
   tasks.append(dict(name=f'{mode}_{name}_{n:02d}',model=name,mode=mode,keys=keys,body=body,endpoint=endpoint))
 random.Random(20261112+len(plan)).shuffle(tasks);plan+=tasks
(out/'plan.json').write_text(json.dumps(plan,indent=2));(out/'plan.sha256').write_text(hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()+'\n')
lock=threading.Lock();ledger={'prior_reported_usd':0.009328571099,'prior_uncertain_usd':.15,'new_reported_usd':0.,'new_uncertain_usd':0.,'active_usd':0.,'cap_usd':1.,'reservation_per_call_usd':.02};stopped=False;results=[]
def save():
 (out/'ledger.json').write_text(json.dumps(ledger,indent=2))
def call(t):
 global stopped
 with lock:
  if stopped:return None
  accounted=sum(ledger[k] for k in ['prior_reported_usd','prior_uncertain_usd','new_reported_usd','new_uncertain_usd','active_usd'])
  if accounted+.02>1:stopped=True;save();return None
  ledger['active_usd']+=.02;save()
 f=out/(t['name']+'.json');assert not f.exists(),'Never repeat an attempted request'
 b=json.dumps(t['body']).encode();assert len(b)<50000
 req=urllib.request.Request('https://openrouter.ai'+t['endpoint'],data=b,headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY'],'Content-Type':'application/json'})
 start=time.monotonic();status=None
 try:
  with urllib.request.urlopen(req,timeout=60) as r:status=r.status;raw=r.read()
 except urllib.error.HTTPError as e:status=e.code;raw=e.read()
 except Exception as e:raw=json.dumps({'transport_error_class':type(e).__name__}).encode()
 elapsed=time.monotonic()-start;f.write_bytes(raw)
 try:d=json.loads(raw)
 except Exception:d={}
 try:pred=json.loads(d['choices'][0]['message']['content']) if t['model']=='ds' else {k:v['choice'] for k,v in d['answers'].items()}
 except Exception:pred={}
 usage=d.get('usage') or {};cost=usage.get('cost');row={k:t[k] for k in ['name','model','mode','keys']};row.update(status=status,latency_seconds=elapsed,started_monotonic=start,predictions={k:pred.get(k,'failure') if pred.get(k) in labels else 'failure' for k in t['keys']},usage=usage,provider=d.get('provider'),served_model=d.get('model'),service_tier=d.get('service_tier'),generation_id=d.get('id'))
 with lock:
  ledger['active_usd']-=.02
  if isinstance(cost,(float,int)):ledger['new_reported_usd']+=cost
  else:ledger['new_uncertain_usd']+=.02
  if status in [401,402,403]:stopped=True
  results.append(row);save();(out/'results.json').write_text(json.dumps(results,indent=2))
 return row
for mode in dict.fromkeys(t['mode'] for t in plan):
 tasks=[t for t in plan if t['mode']==mode];start=time.monotonic()
 with ThreadPoolExecutor(max_workers=4) as pool:
  for future in as_completed([pool.submit(call,t) for t in tasks]):
   row=future.result()
 elapsed=time.monotonic()-start
 print(mode,'completed',len([r for r in results if r['mode']==mode]),'wall',round(elapsed,3),'cost',ledger['new_reported_usd'],'uncertain',ledger['new_uncertain_usd'],flush=True)
 if stopped:break
print('done',len(results),json.dumps(ledger),flush=True)
