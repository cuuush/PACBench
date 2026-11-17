"""Serial native collector/Dream phases; preparation is separate from execution.

The caller supplies a guarded route or an explicit offline completion fixture.
No native wiki state is seeded or repaired, and no benchmark questions are sent.
"""
from pathlib import Path
import hashlib,json,shutil,sqlite3
from .gateway_reuse import atomic

PHASES=('monday','wednesday','friday')
CASES=('natural','same-scope-positive')


def freeze_inputs(repo):
    prep=Path(repo)/'runs/pizza-week-persistent-recovery-offline-20261115/memcal-followup-preparation.json'
    receipt=json.loads(prep.read_text());items=[]
    for item in receipt['inputs']:
        path=Path(repo)/item['path'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Retained diagnostic input changed')
        items.append({**item,'messages':json.loads(raw)})
    if [(r['case'],r['phase']) for r in items]!=[(c,p) for c in CASES for p in PHASES]:raise ValueError('Six-input diagnostic order differs')
    digest=hashlib.sha256(json.dumps([{k:v for k,v in r.items() if k!='messages'} for r in items],sort_keys=True).encode()).hexdigest()
    return items,digest


def run_native(repo,output,env,*,before_phase=None,after_phase=None):
    from .imessage_fixture import append_messages
    from .document_ingestion import ingest_day
    output=Path(output);output.mkdir(exist_ok=False);inputs,manifest=freeze_inputs(repo)
    record={'condition':'native-six-input-collector-dream-v1','input_manifest_sha256':manifest,'phases':[],'state':'running'}
    atomic(output/'diagnostic.json',record)
    try:
        for case in CASES:
            arm=output/case;arm.mkdir();(arm/'hermes-home').mkdir();(arm/'os-home').mkdir();previous=None
            child={**env,'HOME':str(arm/'os-home'),'MEMCAL_HOME':str(arm/'memory')}
            for phase in PHASES:
                item=next(i for i in inputs if (i['case'],i['phase'])==(case,phase));batch=item['messages']
                if before_phase:before_phase(case,phase,batch)
                source=arm/phase;source.mkdir()
                if previous:
                    for f in previous.glob('chat.*'):shutil.copyfile(f,source/f.name)
                append_messages(source/'chat.db',batch)
                atomic(source/'messages.json',batch);(source/'messages.txt').write_text('\n'.join(m['text'] for m in batch))
                atomic(source/'source.json',{'source_ids':[m['source_id'] for m in batch]})
                result=ingest_day('memcal',source,arm,child,batch[-1]['collected_at'],'America/New_York',timeout_s=120)
                atomic(source/'native-ingestion.private.json',result)
                # Snapshot every actual nested page and native brief/database
                # before allowing another phase. No synthetic historical receipts.
                if (arm/'memory/wiki').exists():shutil.copytree(arm/'memory/wiki',source/'wiki')
                for name in ['brief.md','brief.txt']:
                    if (arm/'memory'/name).exists():shutil.copyfile(arm/'memory'/name,source/name)
                with sqlite3.connect(f'file:{arm/"memory/memcal.db"}?mode=ro',uri=True) as old,sqlite3.connect(source/'native-state.private.db') as snapshot:
                    old.backup(snapshot)
                row={'case':case,'phase':phase,'ready':result['ready'],'state':result['state'],'input_sha256':item['sha256'],
                    'native_errors':(result.get('result',{}).get('processing') or {}).get('errors',[])}
                record['phases'].append(row);atomic(output/'diagnostic.json',record)
                if not result['ready']:raise RuntimeError('Native phase incomplete; no replay or later phase')
                if after_phase:after_phase(case,phase,source,row)
                previous=source
        record['state']='native-phases-complete-quality-unverified';return record
    except BaseException as exc:record.update(state='stopped',error_type=type(exc).__name__);raise
    finally:atomic(output/'diagnostic.json',record)


def preflight(repo,source):
    import os
    from decimal import Decimal
    from .provider_versions import verify_memcal_source,MEMCAL_CONTEXT_CONDITION
    from .direct_accounting import live_budget,request_cost_bound
    from .funded_run_budget import selected,credit_snapshot,describe_admission
    from .diagnostic_scope import scope_for_revision,INPUT_MANIFEST_SHA256,MAX_REQUEST_USD
    if os.environ.get('PACBENCH_OPENROUTER_PROFILE')!='luna-bedrock-global' or os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')!='32768' or os.environ.get('PACBENCH_RESERVATION_POLICY')!='openai-cache-total-shared-v1':
        raise RuntimeError('Use the existing explicit Luna/cache-total/32768 condition before starting Python')
    os.environ.update(PACBENCH_MEMCAL_SRC=str(Path(source).resolve()),
        PACBENCH_MEMCAL_CONDITION=os.environ.get('PACBENCH_MEMCAL_CONDITION',MEMCAL_CONTEXT_CONDITION))
    pin=verify_memcal_source(repo);items,manifest=freeze_inputs(repo)
    if manifest!=INPUT_MANIFEST_SHA256:raise RuntimeError('Diagnostic input manifest differs from reviewed six-input scope')
    scope=scope_for_revision(pin['revision'],manifest);project=scope['project']
    budget=live_budget();credit=credit_snapshot()
    with budget.locked(write=False) as state:
        run=selected(state)
        if not run:raise RuntimeError('Existing named prepaid run must be selected; no replacement enrollment')
        arithmetic=describe_admission(state,run,credit)
        if any(c.get('project')==project for c in state['calls']):raise RuntimeError('Diagnostic already has attempts; native replay/resume requires separate evidence')
    bound=request_cost_bound({**budget.config,'prompt_per_million':'.25'},32768,shared_context=True)
    if bound!=MAX_REQUEST_USD:raise RuntimeError('Diagnostic route pricing/context bound differs; no paid execution')
    return budget,{'state':'prepared-no-spend','source_pin':pin,'scope':scope,'native_phases':len(items),
        'observed_offline_one_pass_calls':12,'maximum_attempts_including_native_retries':12,
        'per_request_maximum_usd':str(bound),'diagnostic_aggregate_maximum_usd':str(bound*12),
        'initial_request_fits':bound<=Decimal(arithmetic['currently_enforced_admission_room_usd']),
        'whole_maximum_fits':bound*12<=Decimal(arithmetic['currently_enforced_admission_room_usd']),
        'admission':'Serial, per request on the original ledger; never reserve the entire forecast. Stop on the real cap; native invalid/ambiguous calls are not replayed.',
        'old_run_guard_applies_to_new_namespace':True,'arithmetic':arithmetic,'provider_questions':0,'paid_calls':0,'new_reservations':0}


def main():
    import argparse,os,fcntl
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--preflight',action='store_true');mode.add_argument('--execute',action='store_true')
    parser.add_argument('--source',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[1]
    budget,plan=preflight(repo,args.source)
    if args.preflight:
        # Exact internal arithmetic is saved privately; console dollars are cents.
        args.output.parent.mkdir(parents=True,exist_ok=True);atomic(args.output.with_suffix('.plan.json'),plan)
        from .scored_run_report import display_usd
        print(json.dumps({'state':plan['state'],'native_phases':plan['native_phases'],'maximum_attempts':12,
            'per_request_maximum':display_usd(plan['per_request_maximum_usd']),
            'aggregate_maximum':display_usd(plan['diagnostic_aggregate_maximum_usd']),
            'initial_request_fits':plan['initial_request_fits'],'whole_maximum_fits':plan['whole_maximum_fits'],
            'plan':str(args.output.with_suffix('.plan.json'))}))
        return
    if not plan['initial_request_fits']:raise RuntimeError('Original ledger/credit cannot admit the first bounded request; no execution')
    from .openrouter_direct import direct_environment
    from .gateway_client import client_settings
    from .diagnostic_scope import ENV
    key=os.environ['OPENROUTER_API_KEY']
    # A second colocated coordinator cannot race the same diagnostic. Actual
    # admission/settlement continues to use the original ledger's atomic guard.
    with (budget.path.parent/'native-diagnostic-owner.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with direct_environment(key):
            os.environ[ENV]=json.dumps(plan['scope'],sort_keys=True)
            base,capability=client_settings(plan['scope']['project'])
            env={k:v for k,v in os.environ.items() if k.startswith(('PACBENCH_','NODE_'))}
            env.update(MEMCAL_SRC=str(args.source.resolve()),MEMCAL_LLM_PROVIDER='openai-compatible',MEMCAL_OPENAI_BASE_URL=base,
                OPENAI_COMPAT_API_KEY=capability,OPENAI_API_KEY=capability,OPENROUTER_API_KEY=capability,
                MEMCAL_PROPOSE_MODEL='openai/gpt-6-luna',MEMCAL_SWEEP_MODEL='openai/gpt-6-luna',MEMCAL_MATCH_MODEL='openai/gpt-6-luna',
                MEMCAL_MAX_PARALLEL='1',MEMCAL_FALLBACK_PROVIDER='',PACBENCH_USAGE_ROLES=json.dumps({plan['scope']['project']:'memory_provider'}),
                PACBENCH_STOP_FILE=str(args.output/'STOP'),PACBENCH_DIRECT_STOP_ON_429=str(args.output/'STOP'),TZ='America/New_York')
            run_native(repo,args.output,env)

if __name__=='__main__':main()
