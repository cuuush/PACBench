"""Free, bounded generation observations. Never mutate spending history."""
import argparse,json,os,time,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from .openrouter_route import MODEL
from .direct_accounting import observed_generation_id


def collect(rows,folder,*,concurrency=4,interval=.25):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
    with urlopen('https://openrouter.ai/api/v1/models',timeout=30) as response:
        model=next(m for m in json.load(response)['data'] if m['id']==MODEL)
    key=os.environ['OPENROUTER_API_KEY'];lock=threading.Lock();next_at=0.;halt=threading.Event()
    def observe(row):
        nonlocal next_at
        try:identifier,identifier_source=observed_generation_id(row)
        except ValueError:
            state='conflicting-generation-ids' if row.get('upstream_request_id') and row.get('response_diagnostic_headers',{}).get('x-generation-id') else 'missing-generation-id'
            return row['id'],{'state':state}
        path=folder/(row['id']+'.json')
        if path.exists():data=json.loads(path.read_text())
        else:
            if halt.is_set():return row['id'],{'state':'read-api-stopped'}
            with lock:
                delay=max(0,next_at-time.monotonic());next_at=max(next_at,time.monotonic())+interval
            time.sleep(delay)
            try:
                request=Request('https://openrouter.ai/api/v1/generation?'+urlencode({'id':identifier}),headers={'Authorization':'Bearer '+key})
                with urlopen(request,timeout=15) as response:data=json.load(response)['data']
                with path.open('x') as stream:path.chmod(0o600);json.dump(data,stream)
            except Exception as exc:
                status=exc.code if isinstance(exc,HTTPError) else None
                if status in (401,403,429):halt.set()
                return row['id'],{'state':'unavailable','error_class':type(exc).__name__,'http_status':status,'identifier_source':identifier_source}
        if data.get('id')!=identifier or data.get('model') not in {MODEL,model.get('canonical_slug')} or data.get('provider_name')!='DeepInfra':
            return row['id'],{'state':'identity-mismatch'}
        return row['id'],{'state':'observed','identifier_source':identifier_source,'generation':{k:v for k,v in data.items() if k in ('id','model','provider_name','created_at','latency','generation_time','moderation_latency','native_tokens_prompt','native_tokens_completion','native_tokens_reasoning','native_tokens_cached','tokens_prompt','tokens_completion','finish_reason','cancelled','total_cost','usage')}}
    results={}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending=[pool.submit(observe,row) for row in rows]
        for future in as_completed(pending):
            identifier,result=future.result();results[identifier]=result
            if len(results)%100==0:print('Generation observations',len(results),'/',len(rows),flush=True)
    report={'version':'generation-observations-v1','items':results,'model_binding':{'id':MODEL,'canonical_slug':model.get('canonical_slug')},'paid_model_calls':0,'ledger_mutations':0}
    target=folder/'index.json'
    with target.open('w') as stream:target.chmod(0o600);json.dump(report,stream)
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--ledger',type=Path,required=True);p.add_argument('--since-index',type=int,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rows=json.loads(a.ledger.read_text())['calls'][a.since_index:];r=collect(rows,a.output)
    from collections import Counter
    print(json.dumps(dict(Counter(i['state'] for i in r['items'].values()))),flush=True)

if __name__=='__main__':main()
