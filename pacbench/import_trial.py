"""Import-only native qualification: never run Hermes questions or a judge."""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .followups import save
from .gateway_client import require_live_cost_boundary
from .native_pilot import (read_prepared, copy_prepared_files, provision, probe,
    ingest_pending, stop_arm_model_work)
from .document_ingestion import prepare_sources, ingest_day, processing_at, record_recall_isolation, VERSION
from .usage import snapshot, provider_model_health
from .openrouter_route import MODEL


def run(source, output, *, providers, honcho_clock='real', max_messages=150):
    from .context_isolation import validate_recall_environment
    from .prepare import import_week
    from .document_audit import audit_ingestion
    validate_recall_environment()
    require_live_cost_boundary()
    output=Path(output).resolve();output.mkdir(exist_ok=False)
    source=Path(source)
    if source.is_file():source=import_week(source,output/'preparation')
    messages,_,resolved=read_prepared(source,max_messages)
    copy_prepared_files(source,output)
    zone=resolved.get('timezone') or 'America/New_York'
    schedule=prepare_sources(output,messages,zone)
    report={'version':'native-import-validation-v1','mode':'native-import-validation',
        'state':'running','benchmark_questions_sent':0,'judge_calls':0,
        'semantic_review_state':'pending','message_count':len(messages),
        'input_hash':hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'ingestion_schedule':schedule,'ingestion_version':VERSION,'process_timezone':zone,
        'gateway_before':require_live_cost_boundary(),'results':[],
        'scope':'Full-week import qualification only. These stores cannot be used for chronological benchmark questions.'}
    save(output/'summary.json',report)
    for provider in providers:
        root=output/'repeat-1'/provider
        result={'provider':provider,'repetition':1,'state':'running','completed_batches':[],
                'semantic_review_state':'pending','benchmark_questions_sent':0}
        report['results'].append(result);env=None
        try:
            options={}
            if provider=='honcho' and honcho_clock=='simulated':
                from provider_adapters.honcho_enrollment import initial_at
                from provider_adapters.honcho_enrollment import PROTOCOL as MEMBERSHIP
                from provider_adapters.honcho_clock import VERSION as CLOCK_VERSION
                options['honcho_simulated_at']=initial_at(messages)
                report['provider_clocks']={'honcho':CLOCK_VERSION}
                report['provider_membership_protocols']={'honcho':MEMBERSHIP}
            env=provision(root,provider,MODEL,local_timezone=zone,inference_route='openrouter',**options)
            if provider=='hindsight':
                result['native_request_timeout_s']=int(env.get('HINDSIGHT_API_LLM_TIMEOUT','120'))
            env['PACBENCH_STOP_FILE']=os.environ.get('PACBENCH_STOP_FILE',str(output/'STOP'))
            capability=probe(root,provider,env,smoke=False)
            save(root/'preflight.json',capability)
            if not capability['available']:raise RuntimeError('Native provider initialization failed')
            roles=json.loads(env['PACBENCH_USAGE_ROLES'])
            def health():
                require_live_cost_boundary()
                status=provider_model_health(snapshot(roles))
                save(root/'provider-model-health.json',status)
                if status['state']=='stop':raise RuntimeError(status['reason'])
            failed=set()
            for index,batch in enumerate(schedule):
                if provider=='honcho' and honcho_clock=='simulated':
                    horizon=(processing_at(schedule[index+1]['at'],provider,zone)
                        if index+1<len(schedule) else
                        (datetime.fromisoformat(processing_at(batch['at'],provider,zone))+timedelta(days=1)).isoformat())
                    env['PACBENCH_CLOCK_HORIZON']=horizon
                ingest_pending([batch],result['completed_batches'],failed,output,root,provider,env,
                    zone,env['PACBENCH_STOP_FILE'],ingest_day,health)
                save(root/'status.json',result);save(output/'summary.json',report)
            record_recall_isolation(root)
            errors=audit_ingestion(output,root,messages,report,import_only=True)
            result['ingestion_audit']={'state':'fail' if errors else 'pass','errors':errors}
            result['state']='incomplete' if errors else 'processing-complete'
            result['stored_fact_review']='pending; processing receipts are not semantic verification'
        except Exception as exc:
            reason=str(exc)
            for name,value in {**os.environ,**(env or {})}.items():
                if value and len(value)>8 and any(part in name.upper() for part in ('KEY','TOKEN','SECRET','PASSWORD')):
                    reason=reason.replace(value,'<redacted>')
            result.update(state='incomplete',error_class=type(exc).__name__,reason=reason)
        finally:
            if env is not None:
                try:save(root/'pre-shutdown-model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
                finally:stop_arm_model_work(root)
            else:stop_arm_model_work(root)
            save(root/'status.json',result);save(output/'summary.json',report)
    report['state']='processing-complete' if all(r['state']=='processing-complete' for r in report['results']) else 'incomplete'
    report['gateway_after']=require_live_cost_boundary()
    save(output/'summary.json',report)
    return report
