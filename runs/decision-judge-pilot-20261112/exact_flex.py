import sys,json,pathlib,urllib.request,urllib.error,os,time,hashlib,statistics
sys.path.insert(0,'/workspace/PACBench')
from pacbench.semantic_grade import Claim,judge_prompt,DATE_SCOPED_JUDGE_PROTOCOL,DIAGNOSTIC_VERSION,grade_semantic
p=pathlib.Path(__file__).parent;out=p/'exact-flex';out.mkdir(exist_ok=True)
cases=json.loads((p/'cases.json').read_text());labels=['correct','missed','misrepresented'];rows=[]
for x in cases:
 evidence={'answer_visible':[{'id':'source','text':x['evidence']}],'source_evidence':[{'id':'source','text':x['evidence']}]}
 claim=(Claim(x['id'],x['claim'],(x['evidence'],)),)
 kwargs=dict(assess_recovery=True,assess_interaction=True,diagnostic_inputs=evidence,diagnostic_version=DIAGNOSTIC_VERSION,judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL)
 question='Report the requested detail: '+x['claim']
 prompt=judge_prompt(question,x['answer'],claim,**kwargs)
 body={'model':'deepseek/deepseek-v4.1-flash-1017','messages':[{'role':'user','content':prompt}],'temperature':0,'reasoning':{'enabled':False},'max_tokens':4096,'response_format':{'type':'json_object'},'provider':{'only':['deepinfra/fp8/flex'],'allow_fallbacks':False,'require_parameters':True}}
 f=out/(x['id']+'.json');assert not f.exists(),'No duplicate calls'
 assert len(json.dumps(body).encode())<10000
 (out/(x['id']+'-request.json')).write_text(json.dumps(body,indent=2))
 req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY'],'Content-Type':'application/json'})
 t=time.monotonic();status=None
 try:
  with urllib.request.urlopen(req,timeout=110) as r:status=r.status;raw=r.read()
 except urllib.error.HTTPError as e:status=e.code;raw=e.read()
 except Exception as e:raw=json.dumps({'transport_error_class':type(e).__name__}).encode()
 elapsed=time.monotonic()-t;f.write_bytes(raw)
 try:d=json.loads(raw)
 except Exception:d={}
 error=None;verdict='failure';diag=None
 try:
  text=d['choices'][0]['message']['content'];g=grade_semantic(x['id'],question,x['answer'],claim,lambda _:text,**kwargs);verdict=g.slots[0].verdict;diag=g.evidence_diagnostic
 except Exception as e:error=type(e).__name__
 row=dict(id=x['id'],expected=x['label'],verdict=verdict,status=status,latency_seconds=elapsed,usage=d.get('usage'),generation_id=d.get('id'),provider=d.get('provider'),served_model=d.get('model'),error=error,diagnostic=diag)
 rows.append(row);(out/'metrics.json').write_text(json.dumps(rows,indent=2));print(x['id'],status,verdict,round(elapsed,2),flush=True)
 if status in [401,403,402] or status is None:break
matrix={a:{b:0 for b in labels+['failure']} for a in labels}
for r in rows:matrix[r['expected']][r['verdict']]+=1
lat=sorted(r['latency_seconds'] for r in rows)
s=dict(protocol='36 separate actual judge_prompt and grade_semantic calls, one claim per case, full recovery/interaction/evidence diagnostics, date-scope-v2; JSON mode, no strict schema, no retries',n=len(rows),correct=sum(r['expected']==r['verdict'] for r in rows),failures=sum(r['verdict']=='failure' for r in rows),confusion_matrix=matrix,reported_cost_usd=sum((r['usage'] or {}).get('cost',0) for r in rows),latency_total_seconds=sum(lat),latency_median_seconds=statistics.median(lat),latency_p95_seconds=lat[min(len(lat)-1,int(.95*len(lat)))],source_code_sha256=hashlib.sha256(pathlib.Path('/workspace/PACBench/pacbench/semantic_grade.py').read_bytes()).hexdigest(),noncomparabilities=['Earlier decision models saw all cases and shared rubric in one batch, with label-only outputs.','Exact baseline uses native PACBench rubric, full diagnostic and recovery output, one case per request.','Synthetic question is constructed from target claim; not an original real user question.','4096 output cap and disabled reasoning are recorded safeguards; parent request did not supply original output settings.','No frozen label changes or independent human review.'])
(out/'summary.json').write_text(json.dumps(s,indent=2));print(json.dumps(s),flush=True)
