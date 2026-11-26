"""Verify and recover saved Pizza Day native work without repeating source imports."""
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .. import harness
from ..canary_capability import binding, digest, read
from ..context_isolation import memory_delivery
from ..observation_adapters import native_source
from ..observational_receipts import RunReceiptJournal
from ..workflows.grading import paid_batch, retained_grade
from ..workflows.native import capture_tool_results, tool_provenance
from ..workflows.progress import WorkflowProgress
from ..workflows.recording import progress_metrics, receipt_summary
from ..workflows.runner import Condition, captured_turn
from ..workflows.source_plan import SourcePlan
from ..workflows.tracks import HERMES_TOOLS, PROVIDERS
from .pizza_day import AT, BASIS, DAY, DOCUMENT, LINEUP_ADMISSION, QUESTION, SPEC, inputs


def answer_and_verify(root,progress,env,turn,doc,plan,ledger):
    from provider_adapters.honcho_checkpoint import verify_persisted_turn
    from ..gateway_reuse import atomic
    from ..clock_context import NATIVE
    from ..gateway_python_clock import audit_witness
    from ..harness import TurnResult
    from ..native_writes import describe_captures, write_inputs
    from ..canary_capability import sha
    home=root/'hermes-home'
    tools=HERMES_TOOLS['honcho']
    env['PACBENCH_CLOCK_CONTEXT_PROTOCOL']=NATIVE
    native_source('honcho',home,harness.REPO,env,harness.ISOLATED_BIN.parent/'python')
    def ask():
        response=harness.run_turn(turn.prompt,at=turn.at,home=home,allowlist=','.join(tools),extra_env=env,timeout_s=240)
        atomic(root/'native-answer.json',asdict(response))
        return asdict(response)
    saved=progress.effect('answer',turn.id,turn.prompt,ask,role='driver')
    response=TurnResult(**saved)
    clock=json.loads((root/'provider-clock.json').read_text())
    gateway=audit_witness(root,saved,clock)
    if (response.returncode or response.policy_blocks or not response.answer or response.context_isolation['state']!='pass'
            or response.clock_context['state']!='pass' or not gateway['stopped']):
        raise ValueError('Honcho native question, clock, or context isolation unverified')
    sid=response.session_id
    config=json.loads((home/'honcho.json').read_text())
    actual=describe_captures(root,write_inputs([saved],'honcho'))
    if config['hosts']['hermes']['workspace']!=actual['workspace']:
        raise ValueError('native Honcho workspace differs from capture transform')
    observation=dict(service_root=str(root),hermes_home=str(home),endpoint=config['baseUrl'])
    barrier=progress.effect('chat-close',turn.id,sid,lambda:verify_persisted_turn(observation,response,actual['workspace']),role='memory')
    with closing(sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        history=[dict(r) for r in db.execute('SELECT role,content,api_content,tool_name,tool_call_id,tool_calls FROM messages WHERE session_id=? ORDER BY id',(sid,))]
    requests=[r for r in map(json.loads,(root/'api-inputs.jsonl').read_text().splitlines()) if r['request_id'] in response.clock_context['actor_api_input_ids']]
    native_results=[r for r in history if r['role']=='tool']
    results = capture_tool_results(native_results, response.tool_results)
    ids={r['request_id'] for r in requests}
    attempts=[dict(id=r['id'],parent=r.get('retry_of'),operation=r['dispatch_context']['operation_id'],origin='unknown')
              for r in ledger.snapshot() if r['id'] in ids]
    if not ids or len(requests)!=len(ids) or len(ids)!=len(attempts):
        raise ValueError('missing Honcho driver receipts')
    surface=dict(answer=response.answer,session_id=sid,history=history,question_at=turn.at,account=str(root),
        gateway_identity=[gateway['pid'],gateway['start_time']],clock_evidence=gateway,context_isolation=response.context_isolation,
        model_requests=requests,offered_tools=requests[0]['tools'],tool_calls=response.tool_calls,tool_results=results,
        prefetch=response.prefetch_context,memory_delivery=memory_delivery(home,sid,turn.prompt,response.prefetch_context),
        tool_provenance=tool_provenance(response.tool_calls,results),source_files=[doc.filename],conversation_times=[turn.at],
        write_barrier=dict(session_id=sid,pending_writes=0,readback=barrier),attempts=attempts,
        timing=dict(cli_result_received_s=response.latency_s,answer_ready_bound='upper bound includes gateway lifecycle'))
    plan.verify_no_leak(surface,turn.at,turn.prompt)
    condition=Condition('honcho','native-messages','continuing-account','Hermes','openai/gpt-6-luna','amazon-bedrock/global',
                        'local native 3.6.1',PROVIDERS['honcho'].reviewed_version,sha(harness.PATCH_RECORD),clock['version'],tools,('imessage',))
    activity=captured_turn(surface,turn,None,condition,[doc.filename])
    activity['driver_receipts']=dict(coverage='complete',observed_requests=len(ids),missing=[],reused=[])
    progress.verified_turn(turn,surface,activity)
    progress.mark_turn_done(turn.id)
    progress.seal_execution(1,5)


def source_snapshot(old, root, doc):
    from ..gateway_reuse import atomic
    from provider_adapters.honcho_checkpoint import archive_source_store
    old=old.resolve()
    scope=read(binding(old/'workflow-scope.json'))
    if old.name!='honcho' or scope['admission']!=LINEUP_ADMISSION or scope['frozen_inputs']!=BASIS or old==root:
        raise ValueError('wrong Honcho prefix')
    with WorkflowProgress(old/'workflow',scope['run_key']) as p, p.account_owner():
        receipt=read(p.get_effect('ingest',DAY))
        rows=RunReceiptJournal(old/'receipts.jsonl').snapshot()
        lives=[json.loads(f.read_text()) for f in (old/'gateway-lifecycles').glob('*.json')]
        if (not rows or any(r['state']!='settled' or r['dispatch_context']['phase']!='ingest' for r in rows)
                or len(lives)!=1 or not lives[0]['stopped'] or lives[0].get('cli_launch_at')
                or lives[0].get('question_accepted_at') or p.get_verified('pizza-plan') or
                not receipt['ready'] or receipt['document_sha256']!=doc.sha256 or receipt['source_ids']!=list(doc.source_ids)):
            raise ValueError('only a verified source-only, unsent Honcho attempt can be continued')
        final=receipt['result']['processing_status'][-1]
        if any(final[k] for k in ('pending','claims','eligible','embeddings','errors')):
            raise ValueError('Honcho source processing incomplete')
        dest=root/'source-checkpoint'
        archive_source_store(old,dest,receipt,old/'import.json')
        proof=read(binding(dest/'native-snapshot.json'))
        if proof['baseline']['source_count']!=len(doc.source_ids):
            raise ValueError('source-only PostgreSQL readback differs')
        atomic(root/'reopened.json',dict(original=binding(old/'import.json'),snapshot=binding(dest/'native-snapshot.json')))
        return receipt,dict(honcho_simulated_at=final['db_now'],
                            honcho_checkpoint=dict(archive=str(dest/'checkpoint.pgdump'),workspace=proof['workspace']))


def unsent_judge(progress, scope, budget):
    """Bind the original failed local admission to the same live grade claim.

    The direct transport fsyncs its prepared row before sending any HTTP bytes.
    Preserve the failed client record; a prepared row, even unsettled, forbids replay.
    """
    from pathlib import Path
    from ..semantic_grade import judge_prompt
    from ..workflows.runner import judge_options

    root = Path(scope['root'])
    directory = progress.directory
    if (scope['provider'] != 'honcho' or scope['admission'] != LINEUP_ADMISSION or root/'workflow' != directory
            or not progress.execution_sealed or progress.grade_states() != {'claimed': 1}):
        raise ValueError('expected a sealed, ungraded native Honcho Pizza Day')
    turn, surface = progress.get_claimed_grade('pizza-plan')
    claim = progress.active_grade(turn.id)
    prompt = judge_prompt(turn.prompt, surface['answer'], turn.claims, **judge_options(turn, surface))
    path, = (directory/'model-calls').glob('*.json')
    original = binding(path)
    call = read(original)
    rows = budget.snapshot()
    if (call['error'] != 'OperationalError: disk I/O error' or call['status'] != 'failed'
            or call.get('response') or call.get('accounting_receipt') or call.get('direct_response')
            or call['request']['messages'] != [{'role':'user','content':prompt}]
            or progress.binding('judge-intent/'+turn.id+'/'+digest(prompt)) != claim
            or any(p.is_file() for p in (directory/'judgments'/turn.id).rglob('*'))
            or not rows or any(r['state'] != 'settled' or r['accounting_role'] == 'judge'
                               or r['project'] == scope['projects']['honcho']['judge'] for r in rows)):
        raise ValueError('previous judge dispatch cannot be ruled out')
    progress.bind('judge-unsent-local-failure/'+turn.id, dict(original=original, claim=claim,
                                                              previous_settled_ids=sorted(r['id'] for r in rows)))
    # Keep the failed attempt's empty staging directory. The normal judge
    # creates its checkpoint directory exclusively before contacting the model.
    staging = directory/'judgments'/turn.id
    if staging.exists():
        staging.rename(directory/('unsent-judge-staging-'+claim['attempt_id']))
    return turn.id


def recover(root, preparation, verify_only=False):
    _,doc,turn=inputs(preparation)
    scope=json.loads((root/'workflow-scope.json').read_text())
    if root.name not in ('memos','cognee') or scope['admission']!=LINEUP_ADMISSION or scope['frozen_inputs']!=BASIS:
        raise ValueError('wrong recovery root')
    directory=root/'workflow'
    ledger=RunReceiptJournal(root/'receipts.jsonl')
    with WorkflowProgress(directory,scope['run_key']) as p, p.account_owner():
        imported=read(p.get_effect('ingest',DAY))
        surface=p.get_effect('answer',turn.id)
        if not imported['ready'] or imported['document_sha256'] != DOCUMENT or p.last_cutoff()!=AT:
            raise ValueError('original import not verified')
        sid=surface['session_id']
        def verify(claim):
            observation=json.loads((root/'hermes-home/reuse-observer.json').read_text())
            lifecycle,=[json.loads(f.read_text()) for f in (root/'gateway-lifecycles').glob('*.json')]
            runtime=root/'memos-home'
            session,=observation['sessions']
            provider,=session['providers']
            if (not lifecycle['stopped'] or lifecycle['returncode']!=0 or Path(f"/proc/{lifecycle['pid']}").exists()
                    or [lifecycle['pid'],lifecycle['start_time']]!=surface['gateway_identity']
                    or [observation['pid'],observation['start_time']]!=surface['gateway_identity']
                    or observation['observer_sha256']!=p.binding('native-pins')['reuse_observer_sha256']
                    or any(observation[k] for k in ('native_failures','native_memory_threads','native_release_threads'))
                    or session['session_id']!=sid or not session['clean'] or not session['shutdown_returned']
                    or session['pending_futures'] or session['native_drain']['status']!='drained'
                    or any(session['native_drain'][k] for k in ('abandoned_writes','active_tasks','abandoned_prefetches'))
                    or not provider['clean']):
                raise ValueError('native shutdown and final gateway observation do not agree')
            if root.name == 'memos':
                if provider['name'] != 'memtensor' or provider['native_runtime_home'] != str(runtime):
                    raise ValueError('MemOS used a different store')
                with closing(sqlite3.connect((runtime/'data/memos.db').as_uri()+'?mode=ro',uri=True)) as db:
                    actual=db.execute('SELECT id,user_text,agent_text FROM traces').fetchall()
                if (len(actual)!=2 or ('pacbench-document-'+DOCUMENT,doc.text,'') not in actual
                        or sum((q,a)==(QUESTION,surface['answer']) for _,q,a in actual)!=1):
                    raise ValueError('stock MemOS stored document or final conversational write differs')
                native=dict(saved_turn_id=next(k for k,q,a in actual if q==QUESTION))
            else:
                from urllib.parse import urlsplit, unquote
                from provider_adapters.cognee_completion import pipeline_rows, require_idle, completed
                setup=json.loads((root/'native-server-setup.json').read_text())
                original=imported['result']
                if (provider['name']!='cognee' or provider['dataset']!=original['dataset'] or
                        provider['endpoint']!=setup['endpoint'] or provider['native_session']!='hermes_'+sid):
                    raise ValueError('Cognee native session or dataset differs')
                pipelines=pipeline_rows(root)
                require_idle(pipelines)
                if not completed(pipelines,{x['id'] for x in original['native_operation_completion']['rows']}):
                    raise ValueError('Cognee native post-session improve incomplete')
                with closing(sqlite3.connect((root/'cognee-system/databases/cognee_db').as_uri()+'?mode=ro',uri=True)) as db:
                    documents=db.execute('SELECT id,raw_data_location FROM data WHERE dataset_id=?',
                                         (original['dataset_id'].replace('-',''),)).fetchall()
                observed={}
                for ident,url in documents:
                    loc=urlsplit(url)
                    path=Path(unquote(loc.path))
                    if loc.scheme!='file' or loc.netloc or not path.resolve().is_relative_to(root/'cognee-data'):
                        raise ValueError('foreign Cognee data path')
                    observed[ident]=path.read_text()
                source_id=original['data_id'].replace('-','')
                if observed.pop(source_id)!=doc.text or len(observed)!=1:
                    raise ValueError('Cognee source or post-session document count differs')
                ident,transcript=next(iter(observed.items()))
                # The stock Hermes plugin adds its displayed memory-credit signature
                # after storing this exchange. Judge the original full displayed answer.
                stored=transcript.partition('\n\nAnswer: ')[2].rstrip()
                displayed=surface['answer']
                prefix,signature,footer=displayed.partition('\n\n⡏⠁⡏⡇⡏⡁⡧⣸⢸⠅⡯')
                if (QUESTION not in transcript or provider['native_session'] not in transcript or not stored or
                        (displayed!=stored and not (signature and prefix==stored and footer.endswith('⠓⠂⠓⠃⠓⠃⠃⠙⠘⠂⠓')))):
                    raise ValueError('native Cognee promotion differs from this answer')
                native=dict(promoted_id=ident,pipelines=pipelines,source_id=source_id)
            return observation,dict(operation=claim,native_evidence=dict(gateway=lifecycle,session=sid,**native))
        if not p.get_verified(turn.id):
            proof=p.reconcile_effect('chat-close',turn.id,sid,verify) if p.active_effect('chat-close',turn.id,sid) else p.get_effect('chat-close',turn.id)
            surface.update(write_barrier=dict(session_id=sid,pending_writes=0,readback=proof),source_files=[doc.filename],
                           account=str(root),conversation_times=[turn.at])
            requests={r['request_id'] for r in surface['model_requests']}
            surface['attempts']=[dict(id=r['id'],parent=r.get('retry_of'),operation=r['dispatch_context']['operation_id'],
                                      origin='unknown') for r in ledger.snapshot() if r['id'] in requests]
            if len(requests)!=len(surface['model_requests']) or {r['id'] for r in surface['attempts']}!=requests:
                raise ValueError('unbound or repeated Hermes request')
            with SourcePlan(directory,[doc],[turn]) as plan:
                plan.verify_no_leak(surface,turn.at,QUESTION)
            condition=Condition(root.name,'native-document','continuing-account','Hermes','openai/gpt-6-luna',
                'amazon-bedrock/global',root.name,PROVIDERS[root.name].reviewed_version,p.binding('native-pins')['hermes_isolation_sha256'],
                'scheduled native question',HERMES_TOOLS[root.name],('imessage',))
            activity=captured_turn(surface,turn,None,condition,[doc.filename])
            activity['driver_receipts']=dict(coverage='complete',observed_requests=len(requests),missing=[],reused=[])
            p.verified_turn(turn,surface,activity)
            p.mark_turn_done(turn.id)
            p.seal_execution(1,5)
        if verify_only:
            return
        paid_batch(p,directory,1)
        result=dict(state='complete' if p.complete else 'ungraded',condition='original answer; terminal native shutdown reconciliation',
                    metrics=progress_metrics(p),billing=receipt_summary(root,ledger.snapshot(),root.parent.name))
        with (root/'recovery-result.json').open('x') as f:json.dump(result,f,indent=2)


def verified_result(root, preparation):
    _,doc,turn=inputs(preparation)
    directory=root/'workflow'
    scope=read(binding(root/'workflow-scope.json'))
    if (scope['admission']!=LINEUP_ADMISSION or scope['root']!=str(root) or scope['provider']!=root.name or
            scope['spec_sha256']!=SPEC or scope['frozen_inputs']!=BASIS):
        raise ValueError('incompatible published Day run')
    with WorkflowProgress(directory,scope['run_key']) as progress, progress.account_owner():
        actual,surface,_=progress.get_verified(turn.id)
        if (actual!=turn or not progress.execution_sealed or progress.last_cutoff()!=AT or
                surface['source_files']!=[doc.filename] or surface['conversation_times']!=[turn.at]):
            raise ValueError('published checkpoint not sealed')
        adopted=progress.binding('reopened-import')
        imported=read(read(adopted)['original']) if adopted else read(progress.get_effect('ingest',DAY))
        if (not imported['ready'] or imported['document_sha256']!=DOCUMENT or
                imported['source_ids']!=list(doc.source_ids)):
            raise ValueError('native source prefix differs')
        grade=retained_grade(turn,surface,directory)
        stored=progress.grade_record(turn.id)
        if (stored['grade_state']!='graded' or [vars(c) for c in grade.slots]!=stored['criteria']
                or len(grade.slots)!=5 or len(progress.summary())!=1):
            raise ValueError('stored verdicts differ from Luna wire')
        # Early document launcher versions omitted the scope classification. This
        # binding follows verification of the whole frozen corpus, turn and grade.
        progress.bind('canary',False)
        if not progress.complete:
            raise ValueError('full frozen result incomplete')
        metrics=progress_metrics(progress)
        rows=RunReceiptJournal(root/'receipts.jsonl').snapshot()
        ids=[x['request_id'] for x in surface['model_requests']]
        if len(set(ids))!=len(ids) or set(ids)!={r['id'] for r in rows if r['accounting_role']=='hermes'}:
            raise ValueError('actual driver wire and journal differ')
        metrics['turns'][0]['driver_attempt_coverage']=dict(coverage='complete',observed_requests=len(ids),reused=[],missing=[])
        initial=root/'result.json'
        old=json.loads(initial.read_text()) if initial.exists() else {}
        end=max(datetime.fromisoformat(r['finished']) for r in rows if r['finished'])
        start=datetime.fromisoformat(scope['prepaid']['request_started'])
        return dict(state='complete',provider=root.name,condition=LINEUP_ADMISSION,session=surface['session_id'],answer=surface['answer'],
                    native_import_latency_s=imported['latency_s'],import_count=len(doc.source_ids),source_sha256=DOCUMENT,
                    elapsed_s=old.get('elapsed_s'),credit_check_to_last_settled_s=(end-start).total_seconds(),
                    metrics=metrics,billing=receipt_summary(root,rows,scope['run_id']),judgment=binding(directory/'judgments'/turn.id/'judgment.json'))


def honcho_judge_main(argv=None):
    parser = argparse.ArgumentParser(description='Grade a saved Honcho answer after verified unsent local admission.')
    parser.add_argument('root', type=Path)
    root = parser.parse_args(argv).root.resolve()
    scope = json.loads((root/'workflow-scope.json').read_text())
    with WorkflowProgress(root/'workflow', scope['run_key']) as progress:
        paid_batch(progress, root/'workflow', 1, resume_unsent_day=True)


def recover_main(argv=None):
    parser = argparse.ArgumentParser(description='Verify terminal native shutdown and recover a saved Pizza Day answer.')
    parser.add_argument('root', type=Path)
    parser.add_argument('preparation', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    recover(args.root.resolve(), args.preparation.resolve(), args.verify_only)


def result_main(argv=None):
    parser = argparse.ArgumentParser(description='Report verified results for a saved Pizza Day answer.')
    parser.add_argument('root', type=Path)
    parser.add_argument('preparation', type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(verified_result(args.root.resolve(), args.preparation.resolve()), indent=2))
