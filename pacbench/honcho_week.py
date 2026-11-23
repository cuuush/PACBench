"""Run the frozen seven-document, single-session Honcho Week on the verified prepaid route."""
import argparse
import json
import math
import os
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from . import harness, honcho_week_capture as capture, honcho_week_state as week
from .checkpoint_clock import CheckpointClock, atomic
from .gateway_reuse import PersistentGateway
from .honcho_first_answer import check_stops, run, upload
from .honcho_first_answer_admission import TOOLS, settled, verify_receipts
from .openrouter_route import ENDPOINT, MODEL
from .workflow_native import WarmHermes
from .workflow_runner import Condition, captured_turn, inspect_source_cutoff

RUN='honcho-seven-daily-stock-week-v1'


def answers(root,scope,env,marker,ledger,result):
    docs,turns,schedule=week.plan()
    home=root/'hermes-home'
    child=harness.turn_env(scope['plan'][0]['at'],home,','.join(TOOLS),{
        **env,'PACBENCH_WORKFLOW_CAPTURE':scope['protocol'],'PACBENCH_PROVIDER_ONLY':'1',
        'PACBENCH_API_INPUT_TRACE':str(root/'api-inputs.jsonl'),'HERMES_ENABLE_PROJECT_PLUGINS':'0',
        'OPENROUTER_API_KEY':marker,'TZ':'America/New_York',
    })
    clock=CheckpointClock(home,turns[0].at,scope['spec_sha256'],bounded=True)
    prior=None
    latencies=[]
    driver_ids=set()
    ready=result['documents_verified']
    result.update(protocol=schedule['protocol'],ordered_schedule_sha256=scope['week_schedule']['sha256'],
                  canonical_manifest_sha256=scope['manifest']['sha256'],document_index_sha256=schedule['document_index_sha256'],
                  scheduled_turns=len(turns),scheduled_criteria=schedule['criteria'],tool_attempts=0,driver_requests=0)
    with TemporaryDirectory(prefix='pacbench-honcho-week-') as cwd:
        with PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
                               checkpoint=scope['spec_sha256'],observation_clock=clock,provider='honcho') as owner:
            atomic(root/'startup.json',dict(pins=owner.pins,controller=scope['controller'],
                                           gateway=owner.witness['pid'],source_ready=result['source_ready']))
            condition=Condition('honcho','nightly-document','continuing-account','Hermes '+owner.pins['hermes'],
                MODEL,ENDPOINT,schedule['protocol'],'3.6.1 / SDK 2.9.0',owner.pins['hermes_isolation_sha256'],
                'Physical Honcho server, bounded authored Hermes checkpoints',TOOLS)
            frontend=WarmHermes(owner,clock,lambda:capture.barrier(scope,owner,ledger,captures=week.position(scope)))
            for index,(turn,scheduled) in enumerate(zip(turns,schedule['turns'],strict=True)):
                check_stops(root.parent.parent)
                prefix=scheduled['ready_files']
                if ready>len(prefix):
                    raise ValueError('Honcho would expose a later source release to this question')
                while ready<len(prefix):
                    upload(root,scope,env,ready)
                    result['source_records']+=len(docs[ready].source_ids)
                    ready+=1
                    result['documents_verified']=ready
                sid=prior['session_id'] if prior else None
                week.arm(scope,owner,turn,sid)
                surface=frontend.ask(turn.prompt,at=turn.at,session_id=sid,
                        on_return=lambda raw:atomic(week.path_for(root,index,'cli-return.private'),raw))
                atomic(week.path_for(root,index,'wire.private'),surface)
                idle=capture.barrier(scope,owner,ledger,captures=index+1)
                receipts={row['id']:row for row in ledger.snapshot()}
                verify_receipts(receipts)
                ids=[req['request_id'] for req in surface['model_requests']]
                actual={ident for ident,row in receipts.items() if row['workflow_slot'].get('question_id')==turn.id
                        and row['workflow_slot']['kind']=='answer'}
                if (len(ids)!=len(set(ids)) or actual!=set(ids) or driver_ids.intersection(ids)
                        or not all(settled(receipts[ident]) for ident in ids)):
                    raise ValueError('Honcho turn has missing, reused or unmatched answer requests')
                native=idle['readback']
                surface.update(source_files=prefix,write_barrier=dict(session_id=surface['session_id'],pending_writes=0,
                    readback=native),attempts=[dict(id=ident,parent=None,origin='stock Hermes',
                    operation=receipts[ident]['dispatch_context']['operation_id']) for ident in ids])
                captured_turn(surface,turn,prior,condition,prefix)
                inspect_source_cutoff({'model_requests':[req['request'] for req in surface['model_requests']],
                    'prefetch':surface['prefetch']},docs,prefix,[t.prompt for t in turns[:index+1]])
                week.publish(scope,index,surface)
                driver_ids.update(ids)
                prior=surface
                latency=surface['timing']['cli_result_received_s']
                latencies.append(latency)
                result.update(answers_received=index+1,last_question=turn.id,driver_requests=len(driver_ids),
                    tool_attempts=result['tool_attempts']+len(surface['tool_calls']),answer_latency_p50_seconds=median(latencies),
                    answer_latency_p95_seconds=sorted(latencies)[math.ceil(.95*len(latencies))-1],
                    native_captures=len(native['messages']),ineligible_capture_queue_items=len(native['deferred_queue_ids']))
                atomic(root/'progress.json',result)
            frontend.end_chat(prior['session_id'])
            atomic(root/'native-chat-close.json',owner.fence(prior['session_id']))
    if (ready,result['source_records'],week.position(scope))!=(7,142,49):
        raise ValueError('Honcho did not complete the frozen seven-file Week')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','checkpoint','plugin_home'):
        parser.add_argument(name,type=Path)
    options=parser.parse_args()
    os.umask(0o077)
    result=run(*(getattr(options,name).resolve(strict=True) for name in ('campaign','checkpoint','plugin_home')),full_week=True)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
