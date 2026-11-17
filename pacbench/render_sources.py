"""Render reviewed logical source only; never freeze evaluations or ingest providers."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
from datetime import datetime

REVIEW='4e0a248d181e92f5dc4aebfe1f3fe5cacd0de287'
SPEC='specs/pizza_week_assistant.json'

def render(spec,root,writer,verifier,*,model):
    from .prepare import resolve_spec,realization_binding
    from .authoring import realize_scene,build_scene_observations
    from .followups import save
    plans,_,resolved=resolve_spec(spec,root)
    root.mkdir(exist_ok=False,parents=True);save(root/'resolved-spec.json',resolved)
    messages=[];records=[];hist={};failures=[]
    for p in plans:
        try:
            previous=tuple((m['written_at'],m['channel']+':'+m['thread'],m['sender'],m['text'])
                for m in messages if datetime.fromisoformat(m['written_at'])<datetime.fromisoformat(p.at))
            result=realize_scene(p,writer,verifier,model=model,record_dir=root/'scene-verifications',
                prior_lines=tuple(hist.get((p.channel,p.thread),())),verification_lines=previous)
            rows=[o.public() for o in build_scene_observations(resolved['id'],p,list(result.lines))]
            if p.messages is not None:
                assert [(m['sender'],m['text'],m['written_at']) for m in rows]==[(m['sender'],m['text'],m['at']) for m in p.messages]
                assert result.record.judge_calls==result.record.attempts==0
            else:assert result.verdict['supported'] is True
            messages.extend(rows);hist.setdefault((p.channel,p.thread),[]).extend((m['written_at'],m['sender'],m['text']) for m in rows)
            records.append({'scene_id':p.id,'lines':result.lines,'record':vars(result.record),'verdict':result.verdict,
                'source_ids':[m['source_id'] for m in rows],'verification_binding':realization_binding(p,result.lines,previous)})
        except Exception as exc:
            failures.append({'scene_id':p.id,'error_type':type(exc).__name__,'reason':str(exc)[:500]})
            break  # No blind retries, no dependent continuation after a failed check.
        save(root/'scenes.json',{'scenes':records});save(root/'logical-messages.json',messages)
        transcript='\n\n'.join(f"{m['written_at']} — {m['sender']}\n{m['text']}" for m in sorted(messages,key=lambda m:m['written_at']))+'\n'
        (root/'transcript.txt').write_text(transcript)
    save(root/'render-status.json',{'state':'rendered-scene-checked' if not failures else 'partial',
        'scope':'Logical source only; no global corpus/question review, native fixture, provider ingest or benchmark. Not a native-ready bundle.',
        'exact_scenes':sum(p.messages is not None for p in plans),'writer_scenes':sum(p.messages is None for p in plans),
        'completed_scenes':len(records),'messages':len(messages),'failures':failures,'reviewed_spec_revision':REVIEW})
    return failures

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',required=True,type=Path);args=ap.parse_args()
    from .render_scope import active
    if not active():raise RuntimeError('Explicit source-render-v1 scope required')
    limit=os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS','')
    if not limit.isdigit() or not 0<int(limit)<=32768 or os.environ.get('PACBENCH_RESERVATION_POLICY')!='openai-cache-total-shared-v1':
        raise RuntimeError('Render requires an explicit output ceiling <=32768 and reviewed prepaid reservation policy')
    repo=Path(__file__).resolve().parents[1]
    reviewed=subprocess.check_output(['git','show',REVIEW+':'+SPEC],cwd=repo)
    if (repo/SPEC).read_bytes()!=reviewed:raise RuntimeError('Reviewed spec changed')
    from .openrouter_run import fetch_catalog
    from .funded_run_budget import credit_snapshot,selected
    from .direct_accounting import live_budget
    from .openrouter_direct import direct_environment
    from .model_calls import ModelClient
    from .gateway_client import client_settings
    from .followups import save
    fetch_catalog(profile='luna-bedrock-global');credit_snapshot();budget=live_budget()
    with budget.locked(write=False) as state:
        selected(state);baseline={r['id'] for r in state['calls']}
    key=os.environ['OPENROUTER_API_KEY']
    with direct_environment(key):
        clients=[]
        for project in ['pacbench-authoring','pacbench-scene-verifier']:
            base,capability=client_settings(project)
            clients.append(ModelClient(key=capability,model='openai/gpt-6-luna',record_dir=args.output/'model-calls',
                cache_dir=args.output/'model-cache',url=base+'/chat/completions',json_mode=True,
                reasoning_effort='low',timeout_s=180,max_attempts=1))
        failures=render(json.loads(reviewed),args.output,*clients,model='openai/gpt-6-luna')
    calls=[r for r in budget.snapshot() if r['id'] not in baseline and r['project'] in {'pacbench-authoring','pacbench-scene-verifier'}]
    from decimal import Decimal
    save(args.output/'usage.private.json',{'calls':calls,'actual_settled_usd':str(sum((Decimal(r['accounted']) for r in calls if r['state']=='settled'),Decimal(0))),
        'unknown_bound_usd':str(sum((Decimal(r['accounted']) for r in calls if r['state']!='settled'),Decimal(0))),
        'roles_by_project':{'pacbench-authoring':'source_writer','pacbench-scene-verifier':'scene_verifier'},
        'condition':{'scope':'source-render-v1','output_ceiling':os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS'),'reservation_policy':os.environ.get('PACBENCH_RESERVATION_POLICY')}})
    print(json.dumps({'state':'partial' if failures else 'rendered-scene-checked','calls':len(calls),'output':str(args.output)}))

if __name__=='__main__':main()
