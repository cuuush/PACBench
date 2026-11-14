"""Recover a complete scored prefix in the same MemOS/Supermemory native store.

Reject partial ingests, future state and successful-but-ungraded tail turns.
Archive original evidence/stores before changing any runner state. No completed
question, import, native memory or grade is rewritten or replayed.
"""
import argparse,hashlib,json,os,shutil
from pathlib import Path
from .followups import save


def read(path):return json.loads(path.read_text())


def renew_scored_workspace(root,history):
    """Reopen the same empty cwd for retained Hermes sessions, without asserting
    that their legitimate prior scored conversations must not exist."""
    from .context_isolation import outside_project
    receipt=root/'recall-workspace.json';data=read(receipt);workspace=Path(data['workspace'])
    if (not workspace.is_absolute() or not workspace.name.startswith('pacbench-chat-') or
        workspace.parent!=Path('/var/tmp') or not outside_project(workspace)):
        raise ValueError('Retained scored workspace is not isolated')
    if workspace.exists() and any(workspace.iterdir()):raise ValueError('Retained scored workspace is not empty')
    workspace.mkdir(mode=0o700,exist_ok=True)
    shutil.copyfile(receipt,history/'recall-workspace.json')
    return workspace


def verify_prefix(output,report,checks):
    from .document_ingestion import ready_sources
    from .harness import question_prompt
    provider=report['results'][0]['provider'];root=output/'repeat-1'/provider
    evaluation=read(root/'evaluation.json');prefix=[]
    for item in evaluation['checks']:
        if item['state']!='diagnostic' or item.get('recovery',{}).get('state','complete')!='complete':break
        prefix.append(item)
    if not 0<len(prefix)<len(checks):raise ValueError('No incomplete scored prefix to resume')
    for (row,contract,_),item in zip(checks,prefix):
        d=root/'turns'/row['id'];t=read(d/'turn.json');g=read(d/'grade.json');j=read(d/'judgment.json')
        if (item['id']!=row['id'] or item['contract_hash']!=contract.contract_hash or
            read(d/'status.json')!=item or t['returncode'] or t['policy_blocks'] or
            t['context_isolation']['state']!='pass' or not t['session_id'] or
            t['prompt']!=question_prompt(contract.question,row['at'],report['question_prompt_protocol']) or t['at']!=row['at'] or
            j['answer']!=t['answer'] or json.loads(j['response'])['verdicts']!=g['claims']):
            raise ValueError('Completed scored evidence differs')
        expected={b['id'] for b in ready_sources(report['ingestion_schedule'],provider,row['at'],report['process_timezone'])}
        if set(read(d/'source-state.json')['ingested_batches'])!=expected:raise ValueError('Completed chronology differs')
    completed=[];gap=False
    for batch in report['ingestion_schedule']:
        p=root/batch['file']
        if not p.exists():gap=True;continue
        receipt=read(p);source=output/'sources'/batch['id']/'messages.txt'
        if (gap or receipt.get('ready') is not True or receipt.get('state')!='ready' or
            receipt['source_ids']!=batch['source_ids'] or
            receipt['document_sha256']!=hashlib.sha256(source.read_bytes()).hexdigest()):
            raise ValueError('Partial or changed native ingest cannot be resumed')
        completed.append(batch['id'])
    next_row=checks[len(prefix)][0]
    allowed={b['id'] for b in ready_sources(report['ingestion_schedule'],provider,next_row['at'],report['process_timezone'])}
    if not set(completed)<=allowed:raise ValueError('Retained store contains future sources')
    for row,_,_ in checks[len(prefix):]:
        p=root/'turns'/row['id']/'turn.json'
        if p.exists():
            t=read(p)
            if not t.get('returncode') and t.get('answer','').strip():raise ValueError('Ungraded successful turn needs judge-only recovery')
        if (root/'turns'/row['id']/'grade.json').exists():raise ValueError('Non-prefix grades require a different recovery protocol')
    return prefix,completed


def resume(output):
    from .native_pilot import read_prepared,provision,probe,smoke_succeeded,stop_arm_model_work,ingest_pending
    from .evaluation import load_evaluations,judge_evidence
    from .native_checks import run_checks
    from .gateway_client import client_settings,require_live_cost_boundary
    from .model_calls import ModelClient
    from .document_ingestion import ready_sources,ingest_day,source_state
    from .tool_policy import policy_record
    from .stop import check_stop
    from .usage import snapshot
    from .native_audit import audit
    from .import_lifecycle import scan
    from .openrouter_route import MODEL
    output=output.resolve();report=read(output/'summary.json');provider=report['results'][0]['provider'];root=output/'repeat-1'/provider
    if provider not in {'memos','supermemory'} or len(report['results'])!=1:raise ValueError('Unsupported scored store recovery')
    if report['question_prompt_protocol']!='native-question-v2' or report['role_routes']['condition']!='luna-all-roles-v1':raise ValueError('Scored model/prompt condition differs')
    cohort=read(output.parent/'cohort.json')
    for field,variable in [('memos_reasoning_condition','PACBENCH_MEMOS_REASONING_CONDITION'),
        ('effective_output_ceiling_condition','PACBENCH_EFFECTIVE_OUTPUT_TOKENS'),('compatibility_condition','PACBENCH_LUNA_COMPATIBILITY')]:
        if cohort.get(field)!=os.environ.get(variable):raise ValueError('Recovery inference condition differs: '+field)
    if scan(root,'scored-resume-inspection')[0]:raise ValueError('Previous owned workers are still alive')
    messages,contract,resolved=read_prepared((output/report['source']).resolve(),150)
    if hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=report['input_hash'] or contract.contract_hash!=report['contract_hash']:raise ValueError('Frozen source identity differs')
    plan,checks=load_evaluations(output,messages,resolved)
    if plan['plan_hash']!=report['evaluation_plan_hash']:raise ValueError('Frozen evaluation plan differs')
    prefix,ingested=verify_prefix(output,report,checks)
    history=root/'scored-resumes'/str(1+len(list((root/'scored-resumes').glob('*'))));history.mkdir(parents=True)
    for name in ('summary.json','native-audit.json'):
        if (output/name).exists():shutil.copyfile(output/name,history/name)
    for name in ('evaluation.json','status.json','native-server-setup.json','model-usage.json','model-usage-after-settlement.json','pre-recall-isolation.json'):
        if (root/name).exists():shutil.copyfile(root/name,history/name)
    # Quiescent native stores and Hermes session DBs retain an immutable backup.
    for name in ('hermes-home','memos-home' if provider=='memos' else 'supermemory-data'):
        shutil.copytree(root/name,history/name,symlinks=True)
    for row,_,_ in checks[len(prefix):]:
        d=root/'turns'/row['id']
        if d.exists():(history/'tail-turns').mkdir(exist_ok=True);shutil.move(d,history/'tail-turns'/row['id'])
    save(root/'evaluation.json',{'state':'running','context':plan['context'],'checks':prefix})
    workspace=renew_scored_workspace(root,history)
    stop=history/'STOP';os.environ['PACBENCH_STOP_FILE']=str(stop)
    save(history/'resume.json',{'provider':provider,'completed_questions_preserved':[p['id'] for p in prefix],
        'completed_imports_preserved':ingested,'protocol':'scored-prefix-retained-store-v1','funded_run_id':os.environ.get('PACBENCH_FUNDED_RUN_ID'),
        'memos_reasoning_condition':os.environ.get('PACBENCH_MEMOS_REASONING_CONDITION'),'effective_output_ceiling_condition':os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')})
    env=None;result=report['results'][0]
    try:
        env=provision(root,provider,MODEL,local_timezone=report['process_timezone'],resume=True,inference_route='openrouter')
        env.update(PACBENCH_STOP_FILE=str(stop),PACBENCH_CLOCK_CONTEXT_PROTOCOL=report['clock_context_protocol'],PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'))
        capability=probe(root,provider,env,smoke=True,smoke_timeout_s=120)
        if not smoke_succeeded(capability):raise RuntimeError('Retained native capability failed')
        policy=policy_record(provider,capability['tools'])
        if policy!=read(root/'tool-policy.json'):raise ValueError('Native tool policy changed')
        env['PACBENCH_SOURCE_ARGUMENT_RULES']=json.dumps(policy['source_argument_rules']);failed=set()
        def before(row,directory):
            check_stop(stop);require_live_cost_boundary()
            needed=ready_sources(report['ingestion_schedule'],provider,row['at'],report['process_timezone'])
            ingest_pending(needed,ingested,failed,output,root,provider,env,report['process_timezone'],stop,ingest_day,require_live_cost_boundary)
            from .native_writes import ensure_ready
            ensure_ready(provider,row,checks,plan['context'],root,env,directory)
            save(directory/'source-state.json',source_state(row['at'],ingested))
        base,key=client_settings('pacbench-judge');conditions=report['judge_generation_conditions']
        judge_client=ModelClient(key=key,model=MODEL,url=base+'/chat/completions',record_dir=output/'model-calls',reasoning_effort=conditions['reasoning_effort'],json_mode=True,timeout_s=conditions['wall_deadline_s'])
        def judge(*a,**kw):check_stop(stop);return judge_client(*a,**kw)
        def evidence(row):return judge_evidence(output,messages,resolved,row,plan['context'],diagnostic_version=report['evidence_diagnostic_protocol'])
        sequence=run_checks(checks,plan['context'],root,judge,','.join(policy['allowed_tools']),env,question_dir=output/'combined-questions',
            before_check=before,evidence_by_id=evidence,provenance_version=report['provenance_version'],diagnostic_version=report['evidence_diagnostic_protocol'],
            judge_protocol=report['judge_protocol'],turn_timeout_s=report['recall_wall_deadline_s'],resume_prefix=len(prefix))
        result.update(state=sequence['state'],checks=sequence['checks'],capability=capability,reason='' if sequence['state']=='diagnostic' else 'Recovered sequence incomplete')
        capability['live_verified']=sequence['state']=='diagnostic'
    finally:
        stop_arm_model_work(root)
        if env:save(root/'model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
        from .process_cleanup import cleanup_run_processes
        save(history/'workspace-cleanup.json',cleanup_run_processes(workspace))
        shutil.rmtree(workspace,ignore_errors=True)
    report['state']='diagnostic' if result['state']=='diagnostic' else 'incomplete';save(root/'status.json',result);save(output/'summary.json',report)
    checked=audit(output);report['audit']=checked
    if checked['state']!='pass':report['state']='incomplete'
    save(output/'native-audit.json',checked);save(output/'summary.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--detach',action='store_true');a=p.parse_args()
    if a.detach:
        from .durable_job import launch
        import sys
        target=a.output/'scored-recovery-job'
        print(json.dumps(launch([sys.executable,'-m','pacbench.scored_resume',str(a.output.resolve())],target)));return
    from .openrouter_direct import direct_environment
    key=os.environ['OPENROUTER_API_KEY']
    with direct_environment(key):report=resume(a.output)
    print(json.dumps({'provider':report['results'][0]['provider'],'state':report['state'],'audit':report['audit']['state']}))


if __name__=='__main__':main()
