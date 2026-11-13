import json,pathlib,statistics,hashlib
p=pathlib.Path(__file__).parent;q=p/'batching';rows=json.loads((q/'results.json').read_text());cases=json.loads((p/'cases.json').read_text());gold={c['id']:c['label'] for c in cases};labels=['correct','missed','misrepresented'];summaries=[];maps={}
for model in ['jev','luna','microsoft','ds']:
 for mode in ['isolated','shared_single','batch_forward','batch_repeat','batch_reverse_questions','batch_reverse_state']:
  rr=[r for r in rows if r['model']==model and r['mode']==mode]
  if not rr:continue
  pred={k:v for r in rr for k,v in r['predictions'].items()};maps[model,mode]=pred
  matrix={a:{b:0 for b in labels+['failure']} for a in labels}
  for k,v in pred.items():matrix[gold[k]][v]+=1
  lat=sorted(r['latency_seconds'] for r in rr);span=max(r['started_monotonic']+r['latency_seconds'] for r in rr)-min(r['started_monotonic'] for r in rr)
  inp=sum(r['usage'].get('input_tokens',r['usage'].get('prompt_tokens',0)) for r in rr);output=sum(r['usage'].get('output_tokens',r['usage'].get('completion_tokens',0)) for r in rr)
  correct=sum(gold[k]==v for k,v in pred.items());cost=sum(r['usage'].get('cost',0) for r in rr)
  summaries.append(dict(model=model,mode=mode,requests=len(rr),claims=len(pred),correct=correct,accuracy=correct/len(pred),failures=sum(v=='failure' for v in pred.values()),http_failures=sum(r['status']!=200 for r in rr),confusion_matrix=matrix,input_tokens=inp,output_tokens=output,cost_usd=cost,cost_per_claim_usd=cost/len(pred),request_latency_median_seconds=statistics.median(lat),request_latency_p95_seconds=lat[min(len(lat)-1,int(.95*len(lat)))],sum_request_latencies_seconds=sum(lat),amortized_latency_seconds_per_claim=sum(lat)/len(pred),mixed_4worker_window_seconds=span,mixed_4worker_window_claims_per_second=len(pred)/span,providers=sorted(set(str(r['provider']) for r in rr)),served_models=sorted(set(str(r['served_model']) for r in rr)),service_tiers=sorted(set(str(r.get('service_tier')) for r in rr)),errors=[dict(id=k,expected=gold[k],actual=v) for k,v in pred.items() if gold[k]!=v]))
comparisons=[]
for model in ['jev','luna','microsoft','ds']:
 for mode in ['isolated','shared_single','batch_repeat','batch_reverse_questions','batch_reverse_state']:
  a=maps.get((model,'batch_forward'),{});b=maps.get((model,mode),{});both=set(a)&set(b)
  comparisons.append(dict(model=model,reference='batch_forward',comparison=mode,n=len(both),agreement=sum(a[k]==b[k] for k in both),changes=[dict(id=k,reference=a[k],comparison=b[k]) for k in sorted(both) if a[k]!=b[k]]))
(q/'summary.json').write_text(json.dumps({'conditions':summaries,'consistency':comparisons},indent=2)+'\n')
for model in ['jev','luna','microsoft','ds']:
 print(model)
 for s in summaries:
  if s['model']==model:print(s['mode'],s['correct'],s['failures'],'tokens',s['input_tokens'],'cost',round(s['cost_usd'],7),'median',round(s['request_latency_median_seconds'],3),'sumlat',round(s['sum_request_latencies_seconds'],3))
 print('changes',[(c['comparison'],c['changes']) for c in comparisons if c['model']==model])
# Compare returned distributions as well as argmax labels. DS has no probabilities.
prob={}
for r in rows:
 if r['model']=='ds':continue
 raw=json.loads((q/(r['name']+'.json')).read_text())
 for k,v in raw.get('answers',{}).items():prob[r['model'],r['mode'],k]=v.get('probabilities',{})
for c in comparisons:
 if c['model']=='ds':continue
 vals=[]
 for k in gold:
  a=prob.get((c['model'],'batch_forward',k));b=prob.get((c['model'],c['comparison'],k))
  if a and b:vals.append(sum(abs(a.get(x,0)-b.get(x,0)) for x in labels)/2)
 c['probability_total_variation_mean']=statistics.mean(vals) if vals else None;c['probability_total_variation_max']=max(vals) if vals else None
(q/'summary.json').write_text(json.dumps({'conditions':summaries,'consistency':comparisons},indent=2)+'\n')
