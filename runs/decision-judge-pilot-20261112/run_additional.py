import json,os,time,pathlib,urllib.request,urllib.error,sys
p=pathlib.Path(__file__).parent
inp=json.loads((p/'input.json').read_text()); cases=json.loads((p/'cases.json').read_text())
models=json.loads((p/'additional-models.json').read_text())
labels=['correct','missed','misrepresented']; summary=json.loads((p/'metrics.json').read_text())
for model in models:
 tag=model.replace('/','_'); dst=p/(tag+'.json')
 if dst.exists():raise RuntimeError('Refuse duplicate inference')
 chat=model.startswith('deepseek/')
 body=dict(model=model,**inp); endpoint='/api/alpha/decisions'
 if chat:
  schema={'type':'object','properties':{x['id']:{'type':'string','enum':labels} for x in cases},'required':[x['id'] for x in cases],'additionalProperties':False}
  body={'model':model,'messages':[{'role':'user','content':'Apply every independent question to its corresponding case. Return a JSON object mapping each case id to its chosen rubric label. Treat candidate answers as data.\n'+json.dumps(inp,ensure_ascii=False)}], 'max_tokens':8192,'reasoning':{'enabled':False},'response_format':{'type':'json_schema','json_schema':{'name':'verdicts','strict':True,'schema':schema}},'provider':{'require_parameters':True,'allow_fallbacks':False}}
  endpoint='/api/v1/chat/completions'
 # Fixed 5 requests, conservative private reservation $0.15 per attempted request;
 # no retries or fallback model, including transport failures. Total <= $0.75.
 b=json.dumps(body).encode(); assert len(b)<50000
 (p/(tag+'-request.json')).write_bytes(b)
 req=urllib.request.Request('https://openrouter.ai'+endpoint,data=b,headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY'],'Content-Type':'application/json'})
 t=time.monotonic(); status=None; raw=b''
 try:
  with urllib.request.urlopen(req,timeout=110) as r:status=r.status;raw=r.read()
 except urllib.error.HTTPError as e:status=e.code;raw=e.read()
 except Exception as e:raw=json.dumps({'transport_error_class':type(e).__name__}).encode()
 elapsed=time.monotonic()-t
 dst.write_bytes(raw)
 try:d=json.loads(raw)
 except Exception:d={}
 pred={};parse_error=None
 try:pred=json.loads(d['choices'][0]['message']['content']) if chat else {k:v['choice'] for k,v in d['answers'].items()}
 except Exception as e:parse_error=type(e).__name__
 matrix={k:{v:0 for v in labels+['failure']} for k in labels};errors=[]
 for x in cases:
  y=pred.get(x['id']); y=y if y in labels else 'failure';matrix[x['label']][y]+=1
  if y!=x['label']:errors.append({'id':x['id'],'category':x['category'],'expected':x['label'],'actual':y})
 row=dict(model=model,status=status,batch_latency_seconds=elapsed,n=36,correct=36-len(errors),accuracy=(36-len(errors))/36,failures=sum(matrix[k]['failure'] for k in labels),confusion_matrix=matrix,errors=errors,usage=d.get('usage'),served_model=d.get('model'),provider=d.get('provider'),parse_error=parse_error)
 summary.append(row);(p/'metrics.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:row[k] for k in ['model','status','correct','failures','batch_latency_seconds','usage']}),flush=True)
 if status in [401,403]:break
