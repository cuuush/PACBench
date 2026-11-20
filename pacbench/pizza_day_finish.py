"""Reconcile a completed native turn after the terminal gateway has actually exited."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .canary_capability import read
from .pizza_day_controller import AT, BASIS, DAY, DOCUMENT, QUESTION, inputs
from .pizza_day_lineup import SCOPE
from .workflow_grading import paid_batch
from .workflow_progress import WorkflowProgress
from .workflow_runner import Condition, captured_turn
from .workflow_source_plan import SourcePlan
from .workflow_tracks import HERMES_TOOLS, PROVIDERS


def recover(root, preparation, verify_only=False):
    from .observational_receipts import RunReceiptJournal
    from .workflow_receipts import receipt_summary
    from .workflow_metrics import progress_metrics
    _,doc,turn=inputs(preparation)
    scope=json.loads((root/'workflow-scope.json').read_text())
    if root.name not in ('memos','cognee') or scope['admission']!=SCOPE or scope['frozen_inputs']!=BASIS:
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


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('preparation',type=Path)
    parser.add_argument('--verify-only',action='store_true')
    a=parser.parse_args()
    recover(a.root.resolve(),a.preparation.resolve(),a.verify_only)
