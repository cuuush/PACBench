"""Use Honcho's shipped source collector and the benchmark's split-clock Hermes entry point."""
from contextlib import closing
from dataclasses import asdict
import json
import sqlite3

from . import harness
from .context_isolation import memory_delivery
from .observation_adapters import native_source
from .workflow_runner import Condition, captured_turn
from .workflow_tracks import HERMES_TOOLS, PROVIDERS
from .workflow_native import capture_tool_results, tool_provenance


def answer_and_verify(root,progress,env,turn,doc,plan,ledger):
    from provider_adapters.honcho_checkpoint import verify_persisted_turn
    from .gateway_reuse import atomic
    from .clock_context import NATIVE
    from .gateway_python_clock import audit_witness
    from .harness import TurnResult
    from .native_writes import describe_captures, write_inputs
    from .canary_capability import sha
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
    from .canary_capability import binding, read
    from .gateway_reuse import atomic
    from .observational_receipts import RunReceiptJournal
    from .pizza_day_controller import BASIS, DAY
    from .pizza_day_lineup import SCOPE
    from .workflow_progress import WorkflowProgress
    from provider_adapters.honcho_checkpoint import archive_source_store
    old=old.resolve()
    scope=read(binding(old/'workflow-scope.json'))
    if old.name!='honcho' or scope['admission']!=SCOPE or scope['frozen_inputs']!=BASIS or old==root:
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
    from .canary_capability import binding, digest, read
    from .pizza_day_lineup import SCOPE
    from .semantic_grade import judge_prompt
    from .workflow_runner import judge_options

    root = Path(scope['root'])
    directory = progress.directory
    if (scope['provider'] != 'honcho' or scope['admission'] != SCOPE or root/'workflow' != directory
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


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    from .workflow_grading import paid_batch
    from .workflow_progress import WorkflowProgress
    parser = argparse.ArgumentParser(description='Grade the saved Honcho answer after verified unsent local admission.')
    parser.add_argument('root', type=Path)
    root = parser.parse_args().root.resolve()
    scope = json.loads((root/'workflow-scope.json').read_text())
    with WorkflowProgress(root/'workflow', scope['run_key']) as progress:
        paid_batch(progress, root/'workflow', 1, resume_unsent_day=True)
