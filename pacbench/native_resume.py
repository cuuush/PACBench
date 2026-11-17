"""Resume an interrupted OpenViking import before any Hermes answers exist.

Only completed daily imports are reused. Failed/partial imports and other
backends require a separately verified native recovery protocol.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
import urllib.parse
import urllib.request

from .followups import save


def read(path):
    return json.loads(path.read_text())


def renew_empty_workspace(root, history):
    """A temporary workspace belongs to its runner's process lifetime."""
    from .document_ingestion import record_recall_isolation
    from .context_isolation import workspace_for
    record_recall_isolation(root)
    receipt=root/'recall-workspace.json'
    if receipt.exists():shutil.move(receipt,history/'recall-workspace.json')
    # Create this in the long-lived runner, so its atexit cleanup cannot run
    # before the actual Hermes questions. Never delete the old directory.
    return workspace_for(root/'hermes-home')


def ready_prefix(output, report, messages):
    from .document_ingestion import daily_batches, document_text
    root=output/'repeat-1/openviking'
    if list((root/'turns').glob('*/turn.json')) or (root/'recall.json').exists():
        raise ValueError('Ingestion resume cannot replay or replace existing answers')
    if (root/'evaluation.json').exists() and any(
            c.get('verdict') is not None for c in read(root/'evaluation.json').get('checks',[])):
        raise ValueError('Ingestion resume cannot replace existing grades')
    expected=daily_batches(messages,report['process_timezone'])
    schedule=report['ingestion_schedule']
    if [d for d,_ in expected]!=[r['id'] for r in schedule]:
        raise ValueError('Resume source schedule differs from frozen corpus')
    completed=[];missing=False
    for row,(_,batch) in zip(schedule,expected):
        source=output/'sources'/row['id']
        meta=read(source/'source.json')
        text=document_text(batch,row['at'],version=report['ingestion_version'])
        sha=hashlib.sha256(text.encode()).hexdigest()
        if (read(source/'messages.json')!=batch or (source/'messages.txt').read_text()!=text or
                row['source_ids']!=[m['source_id'] for m in batch] or meta['source_ids']!=row['source_ids'] or
                meta['at']!=row['at'] or
                meta['document_sha256']!=sha or
                hashlib.sha256((source/'source.json').read_bytes()).hexdigest()!=row['source_manifest_sha256']):
            raise ValueError('Resume source delivery changed')
        for name,expected_sha in meta.get('fixture_hashes',{}).items():
            if Path(name).name!=name or hashlib.sha256((source/name).read_bytes()).hexdigest()!=expected_sha:
                raise ValueError('Resume native source fixture changed')
        path=root/row['file']
        if not path.exists():missing=True;continue
        record=read(path)
        if (missing or record.get('ready') is not True or record.get('state')!='ready' or
                record.get('provider')!='openviking' or record.get('source_ids')!=row['source_ids'] or
                record.get('document_sha256')!=sha or record.get('at')!=row['at']):
            raise ValueError('Only a contiguous, successful import prefix can be resumed')
        native=record.get('result',{}).get('result',{}).get('result',{})
        from .document_audit import audit_openviking_receipt
        if (native.get('root_uri')!='viking://resources/'+row['id'] or
                audit_openviking_receipt(record.get('result',{}))):
            raise ValueError('Missing healthy native persisted resource receipt')
        completed.append((row['id'],native['root_uri']))
    if not completed:raise ValueError('No successful imports to resume')
    return completed


def resume(output, *, max_messages=4500, question_dir=None):
    from .live_trials import require_live_trials_ready
    require_live_trials_ready()
    from .native_pilot import (read_prepared, provision, probe, smoke_succeeded,
                               stop_arm_model_work)
    from .gateway_client import client_settings, require_live_cost_boundary, validate_route
    from .creds import load_driver
    from .evaluation import load_evaluations, judge_evidence
    from .model_calls import ModelClient
    from .native_checks import run_checks
    from .document_ingestion import ingest_day, record_recall_isolation, ready_sources
    from .tool_policy import policy_record
    from .usage import snapshot, provider_model_health
    from .native_audit import audit
    output=output.resolve();report=read(output/'summary.json')
    from .semantic_grade import DIAGNOSTIC_VERSIONS, LEGACY_DIAGNOSTIC_VERSION, JUDGE_PROTOCOLS, LEGACY_JUDGE_PROTOCOL
    if report.get('provenance_version', 'source-provenance-v1') not in {'source-provenance-v1', 'source-provenance-v2', 'source-provenance-v3'}:
        raise ValueError('Unsupported provenance protocol')
    if 'evidence_diagnostic_protocol' in report and report['evidence_diagnostic_protocol'] not in DIAGNOSTIC_VERSIONS:
        raise ValueError('Unsupported evidence diagnostic protocol')
    if report.get('judge_protocol', LEGACY_JUDGE_PROTOCOL) not in JUDGE_PROTOCOLS:
        raise ValueError('Unsupported semantic judge protocol')
    from .harness import PROMPT_PROTOCOL
    from .clock_context import NATIVE
    if report.get('question_prompt_protocol') != PROMPT_PROTOCOL:
        raise ValueError('Resume requires the current question prompt protocol; legacy or unknown runs cannot change condition')
    if report.get('clock_context_protocol') != NATIVE:
        raise ValueError('Resume requires native-question-v2 clock context; historical instruction conditions are audit-only')
    if (report.get('state')!='incomplete' or len(report['results'])!=1 or
            report['results'][0]['provider']!='openviking' or report['results'][0]['repetition']!=1):
        raise ValueError('Resume requires one interrupted OpenViking arm')
    messages,contract,resolved=read_prepared((output/report['source']).resolve(),max_messages)
    source=(output/report['source']).resolve()
    if (hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=report['input_hash'] or
            contract.contract_hash!=report['contract_hash'] or
            read(source/'summary.json')['bundle_hash']!=report['bundle_hash'] or
            resolved['timezone']!=report['process_timezone']):
        raise ValueError('Resume frozen input identity or timezone changed')
    for name in ('bundle.json','contract.json','resolved-spec.json','evaluation-plan.json'):
        if (output/name).read_bytes()!=(source/name).read_bytes():
            raise ValueError('Resume frozen source/contract changed')
    completed=ready_prefix(output,report,messages);root=output/'repeat-1/openviking'
    record_recall_isolation(root)
    cfg=read(root/'ov.conf');_,key=client_settings('resume-validation')
    validate_route(cfg['vlm']['api_base'],cfg['vlm']['api_key'])
    if (Path(cfg['storage']['workspace']).resolve()!=root/'openviking-data' or
            not (root/'openviking-data').is_dir() or cfg['vlm'].get('max_tokens') is not None):
        raise ValueError('Resume requires the original native store and processing defaults')
    health=require_live_cost_boundary();driver=load_driver()
    if cfg['vlm']['model']!=driver.model_id:raise ValueError('Resume model changed')
    plan,checks=load_evaluations(output,messages,resolved)
    if plan['plan_hash']!=report['evaluation_plan_hash']:raise ValueError('Resume questions changed')
    if any(ready_sources(report['ingestion_schedule'],'openviking',row['at'],report['process_timezone'])
           !=report['ingestion_schedule'] for row,_,_ in checks):
        raise ValueError('Ingestion resume currently requires final-only questions')
    history=root/'resumes'/str(1+len(list((root/'resumes').glob('*'))))
    history.mkdir(parents=True)
    for name in ('summary.json','native-audit.json'):
        if (output/name).exists():shutil.copyfile(output/name,history/name)
    for name in ('evaluation.json','model-usage.json','model-usage-after-settlement.json','native-server-setup.json'):
        if (root/name).exists():shutil.copyfile(root/name,history/name)
    renew_empty_workspace(root,history)
    env=None
    try:
        env=provision(root,'openviking',driver.model_id,local_timezone=report['process_timezone'],resume=True)
        if report.get('clock_context_protocol'):
            env['PACBENCH_CLOCK_CONTEXT_PROTOCOL']=report['clock_context_protocol']
            from .clock_context import NATIVE as CLOCK_CONTEXT
            if report['clock_context_protocol']==CLOCK_CONTEXT:env['PACBENCH_API_INPUT_TRACE']=str(root/'api-inputs.jsonl')
        restarted=read(root/'ov.conf')
        if any(restarted.get(k)!=cfg.get(k) for k in ('storage','embedding','vlm')):
            raise ValueError('Native processing configuration changed on restart')
        save(history/'resume.json',{'input_hash':report['input_hash'],
             'completed_days':[day for day,_ in completed],'gateway_before':health,
             'processing_configuration_preserved':True,'policy':'Successful imports only; no prior answers'})
        (root/'model-usage-after-settlement.json').unlink(missing_ok=True)
        # Confirm the native server reopened every retained resource before
        # skipping its import. This is ingestion telemetry, not Hermes recall.
        persisted=[]
        for day,uri in completed:
            url=env['OPENVIKING_ENDPOINT']+'/api/v1/fs/stat?'+urllib.parse.urlencode({'uri':uri})
            request=urllib.request.Request(url,headers={'X-OpenViking-Account':env['OPENVIKING_ACCOUNT'],
                                                       'X-OpenViking-User':env['OPENVIKING_USER']})
            with urllib.request.urlopen(request,timeout=10) as response:status=json.load(response)
            if status.get('status')!='ok' or not status.get('result'):
                raise RuntimeError('Native persisted resource unavailable: '+day)
            persisted.append({'day':day,'uri':uri,'stat':status})
        save(history/'persisted-resources.json',persisted)
        capability=probe(root,'openviking',env,smoke=True,smoke_timeout_s=120)
        if not smoke_succeeded(capability):raise RuntimeError('Resumed native capability check failed')
        policy=policy_record('openviking',capability['tools'])
        if policy!=read(root/'tool-policy.json'):raise ValueError('Resume tool policy changed')
        env['PACBENCH_SOURCE_ARGUMENT_RULES']=json.dumps(policy['source_argument_rules'])
        result=report['results'][0];result.update(state='available-unverified',capability=capability)
        from .native_writes import VERSION as WRITE_READINESS
        report['write_readiness_protocol']=result['write_readiness_protocol']=WRITE_READINESS
        capability['wiring_verified']=True
        report.pop('spending_stop',None);report['state']='running';save(output/'summary.json',report)
        ingested=[day for day,_ in completed];failed=False
        def before_check(row,directory):
            nonlocal failed
            require_live_cost_boundary()
            health=provider_model_health(snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
            save(root/'provider-model-health.json',health)
            if health['state']=='stop':failed=True;raise RuntimeError(health['reason'])
            if failed:raise RuntimeError('Native import failed; source state unavailable')
            for batch in report['ingestion_schedule']:
                if batch['id'] in ingested:continue
                record=ingest_day('openviking',output/'sources'/batch['id'],root,env,batch['at'],report['process_timezone'])
                save(root/batch['file'],record)
                if not record['ready']:failed=True;raise RuntimeError('Native import failed after resume')
                ingested.append(batch['id'])
                require_live_cost_boundary()
                health=provider_model_health(snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
                if health['state']=='stop':failed=True;raise RuntimeError(health['reason'])
            if not (root/'pre-recall-isolation.json').exists():record_recall_isolation(root)
            from .native_writes import ensure_ready
            ensure_ready('openviking',row,checks,plan['context'],root,env,directory)
            from .document_ingestion import source_state
            save(directory/'source-state.json',source_state(row['at'],ingested))
        # No prior answers exist; preserve the earlier ungraded statuses.
        if (root/'turns').exists():shutil.move(root/'turns',history/'turns')
        (root/'evaluation.json').unlink(missing_ok=True)
        judge=ModelClient(key=key,model=driver.model_id,record_dir=output/'model-calls',
                          url=client_settings('pacbench-judge')[0]+'/chat/completions',
                          reasoning_effort=report.get('judge_generation_conditions',{}).get('reasoning_effort'))
        def evidence_for_check(row):
            return judge_evidence(output, messages, resolved, row, plan['context'], diagnostic_version=report.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION))
        sequence=run_checks(checks,plan['context'],root,judge,','.join(policy['allowed_tools']),env,
                            question_dir=question_dir or output/'combined-questions',before_check=before_check,
                            provenance_version=report.get('provenance_version','source-provenance-v1'),
                            evidence_by_id=evidence_for_check if report.get('evidence_diagnostic_protocol') else None,
                            diagnostic_version=report.get('evidence_diagnostic_protocol', LEGACY_DIAGNOSTIC_VERSION),
                            judge_protocol=report.get('judge_protocol', LEGACY_JUDGE_PROTOCOL))
        result.update(state=sequence['state'],checks=sequence['checks'],
                      reason='' if sequence['state']=='diagnostic' else 'Resumed evaluation incomplete')
        capability['live_verified']=sequence['state']=='diagnostic'
    finally:
        stop_arm_model_work(root)
        if env:save(root/'model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
    report['state']='diagnostic' if result['state']=='diagnostic' else 'incomplete'
    try:report['gateway_after']=require_live_cost_boundary()
    except RuntimeError as exc:report.update(state='incomplete',spending_stop=str(exc))
    save(root/'status.json',result);save(output/'summary.json',report)
    checked=audit(output);report['audit']=checked
    if checked['state']!='pass':report['state']='incomplete'
    save(output/'native-audit.json',checked);save(output/'summary.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--max-messages',type=int,default=4500)
    parser.add_argument('--question-dir',type=Path)
    args=parser.parse_args();report=resume(args.output,max_messages=args.max_messages,question_dir=args.question_dir)
    print(report['state']);return int(report['state']!='diagnostic')


if __name__=='__main__':raise SystemExit(main())
