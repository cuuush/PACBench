"""Audit and export a completed published Day arm without opening its provider or judge."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from .canary_capability import binding, read
from .observational_receipts import RunReceiptJournal
from .pizza_day_controller import AT, BASIS, DAY, DOCUMENT, SPEC, inputs
from .pizza_day_lineup import SCOPE
from .workflow_grading import retained_grade
from .workflow_metrics import progress_metrics
from .workflow_progress import WorkflowProgress
from .workflow_receipts import receipt_summary


def verified_result(root, preparation):
    _,doc,turn=inputs(preparation)
    directory=root/'workflow'
    scope=read(binding(root/'workflow-scope.json'))
    if (scope['admission']!=SCOPE or scope['root']!=str(root) or scope['provider']!=root.name or
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
        return dict(state='complete',provider=root.name,condition=SCOPE,session=surface['session_id'],answer=surface['answer'],
                    native_import_latency_s=imported['latency_s'],import_count=len(doc.source_ids),source_sha256=DOCUMENT,
                    elapsed_s=old.get('elapsed_s'),credit_check_to_last_settled_s=(end-start).total_seconds(),
                    metrics=metrics,billing=receipt_summary(root,rows,scope['run_id']),judgment=binding(directory/'judgments'/turn.id/'judgment.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('preparation',type=Path)
    args=parser.parse_args()
    result=verified_result(args.root.resolve(),args.preparation.resolve())
    print(json.dumps(result,indent=2))
