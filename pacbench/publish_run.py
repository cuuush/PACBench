"""Publish portable native evidence without private stores or colliding call records."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .followups import save

REPO=Path(__file__).resolve().parents[1]
COMMON=('input_hash','bundle_hash','contract_hash','evaluation_plan_hash','followup_plan_hash',
        'tool_policy','ingestion_version','process_timezone','evaluation_context','message_count',
        'judge_protocol','evidence_diagnostic_protocol','provenance_version','write_readiness_protocol',
        'ingestion_mode','builtin_memory','session_search','followup_policy','document_provider_blockers')
LEGACY_DEFAULTS={'judge_protocol':'semantic-judge-v1','provenance_version':'source-provenance-v1'}


def read(path):
    return json.loads(path.read_text())


def same_frozen(first, second):
    if first.name == second.name == 'contract.json':
        from .evidence_store import read_json
        return read_json(first) == read_json(second)
    return first.read_bytes() == second.read_bytes()


def selected_clock(source,summary,row):
    provider=row['provider'];declared=summary.get('provider_clocks',{}).get(provider)
    arm=source/f"repeat-{row['repetition']}"/provider
    config=arm/'provider-clock.json'
    if provider!='honcho':
        if declared is not None or config.exists():raise ValueError('Unsupported selected provider clock condition')
        return None
    if config.is_symlink():raise ValueError('Unsafe selected provider clock evidence')
    observed=[read(config)['version']] if config.exists() else []
    for path in (arm/'ingestion').glob('*.json'):
        if path.is_symlink():raise ValueError('Unsafe selected provider clock receipt')
        observed.append((read(path).get('result') or {}).get('clock_protocol'))
    if declared is not None:
        from provider_adapters.honcho_clock import SUPPORTED
        if provider!='honcho' or declared not in SUPPORTED:
            raise ValueError('Unsupported selected provider clock condition')
        if row['state']=='diagnostic' and not config.exists():
            raise ValueError('Selected provider clock evidence is missing')
    if any(value!=declared for value in observed):
        raise ValueError('Selected provider clock receipts differ from declared condition')
    return declared


def copy_public(source,destination,virtual):
    """Apply the repository's public-run exclusions to each destination path."""
    if not source.exists():return
    destination.mkdir(parents=True,exist_ok=True)
    def excluded(names):
        if not names:return set()
        result=subprocess.run(['git','check-ignore','--no-index','-z','--stdin'],cwd=REPO,
            input=('\0'.join(names)+'\0').encode(),capture_output=True,check=False)
        if result.returncode not in (0,1):raise RuntimeError('Cannot verify public artifact exclusions')
        return set(result.stdout.decode().split('\0'))
    for folder,dirs,files in os.walk(source,followlinks=False):
        here=Path(folder);relative=here.relative_to(source)
        paths=[str(virtual/relative/name)+'/' for name in dirs];blocked=excluded(paths)
        dirs[:]=[name for name,path in zip(dirs,paths) if path not in blocked and not (here/name).is_symlink()]
        paths=[str(virtual/relative/name) for name in files];blocked=excluded(paths)
        for name,path in zip(files,paths):
            if path in blocked or name.endswith(('.tmp','.lock')) or (here/name).is_symlink():continue
            target=destination/relative/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(here/name,target)


def publish(sources,destination,*,preflight=None,selection=None,retain_replaced=True):
    """Combine only source-identical terminal runs; preflight failures stay unscored."""
    sources=[p.resolve() for p in sources];destination=destination.resolve()
    if len(set(sources))!=len(sources):raise ValueError('Duplicate component run paths')
    if destination.parent!=REPO/'runs' or destination.exists():
        raise ValueError('Use a new direct child of runs/ for public evidence')
    summaries=[read(p/'summary.json') for p in sources]
    if not summaries:raise ValueError('No native runs to publish')
    reference=deepcopy(summaries[0])
    # Execution stops belong to their original component. The combined run's
    # state is computed from the selected arms below; a replaced interruption
    # must not become the explanation for a fresh arm or another provider.
    for key in ('interruption','spending_stop','access_stop','gateway_stop'):
        reference.pop(key,None)
    if any(not reference.get(k) for k in ('input_hash','bundle_hash','contract_hash','tool_policy')):
        raise ValueError('Native run is missing frozen input/contract/policy identity')
    selection={name:Path(path).resolve() for name,path in (selection or {}).items()}
    candidates={}
    for source,summary in zip(sources,summaries):
        if summary.get('mode')!='native-hermes' or summary.get('state') in ('running','inventory'):
            raise ValueError('Only terminal native runs can be published')
        if any(summary.get(k,LEGACY_DEFAULTS.get(k))!=reference.get(k,LEGACY_DEFAULTS.get(k)) for k in COMMON):
            raise ValueError('Cannot combine different frozen inputs, questions or execution policies')
        for row in summary['results']:
            options=candidates.setdefault(row['provider'],[])
            if source not in options:options.append(source)
    if any(name not in candidates or source not in candidates[name] for name,source in selection.items()):
        raise ValueError('Provider selection must name a supplied run containing that provider')
    choices={}
    for name,options in candidates.items():
        if len(options)>1 and name not in selection:
            raise ValueError('Duplicate provider arm; select one reviewed run explicitly')
        choices[name]=selection.get(name,options[0])
    providers=set(choices)
    with tempfile.TemporaryDirectory(prefix='publish-',dir=REPO/'artifacts') as temporary:
        staging=Path(temporary)/'run';virtual=Path('runs')/destination.name
        copy_public(sources[0],staging,virtual)
        # Remove nonselected arms before copying replacements. Old follow-ups or
        # diagnostics must never survive inside a replacement's evidence folder.
        for row in summaries[0]['results']:
            if choices[row['provider']]!=sources[0]:
                stale=staging/f"repeat-{row['repetition']}"/row['provider']
                if stale.exists():shutil.rmtree(stale)
        clocks={}
        for provider,selected in choices.items():
            selected_summary=summaries[sources.index(selected)]
            for row in selected_summary['results']:
                if row['provider']!=provider:continue
                declared=selected_clock(selected,selected_summary,row)
                if declared is not None:clocks[provider]=declared
            condition=staging/'provider-conditions'/(provider+'.json')
            condition.unlink(missing_ok=True)
            original=selected/'provider-conditions'/(provider+'.json')
            if original.exists():
                if original.is_symlink() or original.parent.is_symlink():
                    raise ValueError('Unsafe selected provider condition')
                condition.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(original,condition)
        reference.pop('provider_clocks',None)
        if clocks:reference['provider_clocks']=clocks
        # Normalize the preparation pointer for a clean checkout.
        preparation=(sources[0]/reference['source']).resolve()
        if preparation!=sources[0]/'preparation':copy_public(preparation,staging/'preparation',virtual/'preparation')
        reference['source']='preparation';reference['component_runs']=[]
        results=[];audits=[];attempts=[]
        for index,(source,summary) in enumerate(zip(sources,summaries)):
            chosen=[row for row in summary['results'] if choices[row['provider']]==source]
            public_root='.' if index==0 else 'components/'+source.name
            reference['component_runs'].append({'original_root':str(source),'public_root':public_root,
                'selected_providers':sorted({r['provider'] for r in chosen})})
            if index:
                for row in chosen:
                    relative=Path(f"repeat-{row['repetition']}")/row['provider']
                    copy_public(source/relative,staging/relative,virtual/relative)
                for name in ('model-calls','judge-scope-review'):
                    copy_public(source/name,staging/public_root/name,virtual/public_root/name)
            save(staging/'components'/source.name/'summary.json',summary)
            results.extend(chosen)
            if len(chosen)==len(summary['results']):component_audit=read(source/'native-audit.json')
            elif chosen:
                from .native_audit import audit as audit_selected
                component_audit=audit_selected(source,providers=[r['provider'] for r in chosen])
            else:component_audit={'state':'pass','errors':[],'selected_providers':[]}
            save(staging/'components'/source.name/'native-audit.json',component_audit)
            audits.append({'component':source.name,'audit':component_audit})
            for row in summary['results']:
                if row in chosen:continue
                if not retain_replaced:continue
                relative=Path(f"repeat-{row['repetition']}")/row['provider']
                evidence=Path('attempts')/source.name/relative
                copy_public(source/relative,staging/evidence,virtual/evidence)
                condition=source/'provider-conditions'/(row['provider']+'.json')
                if condition.exists():
                    if condition.is_symlink() or condition.parent.is_symlink():
                        raise ValueError('Unsafe replaced provider condition')
                    target=staging/'attempts'/source.name/'provider-conditions'/condition.name
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(condition,target)
                attempts.append({'provider':row['provider'],'state':row['state'],
                    'reason':'Explicitly replaced by another supplied run; original evidence and cost retained.',
                    'original_root':str(source),'evidence':str(evidence),
                    'selected_root':str(choices[row['provider']])})
            for name in ('bundle.json','contract.json','resolved-spec.json','evaluation-plan.json'):
                if not same_frozen(source/name,sources[0]/name):
                    raise ValueError('Component frozen files differ despite matching summary hashes')
            for day in reference.get('ingestion_schedule',[]):
                relative=Path('sources')/day['id']
                for filename in ('messages.json','messages.txt'):
                    if (source/relative/filename).read_bytes()!=(sources[0]/relative/filename).read_bytes():
                        raise ValueError('Component delivery differs despite matching input hashes')
                if read(source/relative/'source.json')['fixture_hashes']!=read(sources[0]/relative/'source.json')['fixture_hashes']:
                    raise ValueError('Native source fixtures differ between components')
        reference['results']=results;reference['other_attempts']=attempts
        from .document_ingestion import remove_duplicate_stdout
        for path in staging.rglob('ingestion/*.json'):
            record=read(path)
            if remove_duplicate_stdout(record):save(path,record)
        from .document_ingestion import compact_source_state
        for path in staging.rglob('source-state.json'):
            save(path,compact_source_state(read(path),reference['ingestion_schedule']))
        # Native execution imports the portable corpus into ordinary files.
        # Re-export those audited, frozen bytes so a clean checkout can reuse
        # the same corpus without any authoring or verification model calls.
        from .prepare import export_week
        for name in ('bundle.json','contract.json','resolved-spec.json'):
            if not same_frozen(staging/'preparation'/name,staging/name):
                raise ValueError('Published preparation differs from the executed frozen files')
        if (staging/'preparation/scenes.json').exists():
            from .evidence_store import compact_preparation
            compact_preparation(staging/'preparation')
        from .evidence_store import compact_contracts
        compact_contracts(staging)
        export_week(staging/'preparation',staging/'preparation/week.json')
        latest=max(zip(sources,summaries),key=lambda item:(item[0]/'summary.json').stat().st_mtime)[1]
        if latest.get('gateway_after'):reference['gateway_after']=latest['gateway_after']
        blocked=[]
        if preflight is not None:
            preflight=preflight.resolve();canary=read(preflight/'summary.json')
            if canary.get('state') in ('running','inventory'):raise ValueError('Native preflight is unfinished')
            copy_public(preflight,staging/'preflight',virtual/'preflight')
            for row in canary['results']:
                if row['provider'] in providers:continue
                if row['state']=='diagnostic':raise ValueError('A healthy preflight provider is missing from month execution')
                reason='Not run on the month: native canary ingestion/model readiness failed; inspect preflight evidence.'
                result={'provider':row['provider'],'repetition':1,'state':'preflight-failed','reason':reason,
                    'failure_phase':'native-canary','capability':row['capability'],
                    'preflight_evidence':f"preflight/repeat-{row['repetition']}/{row['provider']}",
                    'preflight_input_hash':canary['input_hash'],'expected_month_input_hash':reference['input_hash']}
                results.append(result);blocked.append(result['provider'])
                arm=staging/'repeat-1'/row['provider'];save(arm/'status.json',result)
                checks=[{**{k:c[k] for k in ('id','at','session') if k in c},'state':'ungraded','verdict':None,
                         'error_type':'native_preflight','reason':reason} for c in read(staging/'evaluation-plan.json')['checks']]
                save(arm/'evaluation.json',{'state':'not-run','checks':checks,'counts':{'pass':0,'fail':0,'ungraded':len(checks)}})
        errors=[e for a in audits for e in a['audit'].get('errors',[])]
        audit={'state':'fail' if errors else 'pass','errors':errors,'component_audits':audits,
            'preflight_failed_providers':blocked,'scope':'Saved native trace audits of source-identical components; preflight failures have no month score'}
        reference.update(state='diagnostic' if not blocked and not errors and all(r['state']=='diagnostic' for r in results) else 'incomplete',audit=audit)
        save(staging/'summary.json',reference);save(staging/'native-audit.json',audit)
        destination.parent.mkdir(exist_ok=True);staging.rename(destination)
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',type=Path,nargs='+');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--preflight',type=Path)
    parser.add_argument('--select',action='append',default=[],metavar='PROVIDER=RUN',
        help='Explicitly select a replacement arm; retain the other attempt separately')
    parser.add_argument('--discard-replaced',action='store_true',
        help='Publish only selected development arms; leave original evidence and the spending ledger untouched')
    args=parser.parse_args()
    try:selection=dict(value.split('=',1) for value in args.select)
    except ValueError:parser.error('--select requires PROVIDER=RUN')
    print(publish(args.sources,args.output,preflight=args.preflight,selection=selection,
                  retain_replaced=not args.discard_replaced))
    return 0


if __name__=='__main__':raise SystemExit(main())
