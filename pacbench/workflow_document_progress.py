"""Capture verified document turns for independent grading, without replaying native work."""
from dataclasses import asdict
import json
import os
from pathlib import Path

from .workflow_progress import WorkflowProgress
from .workflow_stream import fingerprint

PROTOCOL = 'pizza-week-native-document-progress-v1'
PROVIDERS = frozenset(('mem0', 'hindsight', 'openviking', 'reme', 'cognee', 'memos'))


def run(condition, account, docs, turns, directory, spec, spec_digest, basis, all_docs, judge, canary, *, resume=False, daily_manifest=None):
    from .gateway_reuse import atomic
    from .workflow_grading import grade_verified
    from .workflow_runner import Journal, run_workflow

    directory = Path(directory).resolve()
    identity = dict(protocol=PROTOCOL, condition=asdict(condition), account=account.identity,
                    source_basis=basis, spec_sha256=spec_digest, evidence_kind=account.evidence_kind,
                    canary=canary, schedule_sha256=fingerprint([asdict(t) for t in turns]))
    if daily_manifest is not None:
        identity.update(input_projection=daily_manifest['source_layout'],
                        document_index_sha256=daily_manifest['normalized_index_sha256'])
    key = fingerprint(identity)
    if resume:
        from uuid import uuid4
        metadata = json.loads((directory/'run.json').read_text())
        if metadata.get('identity') != json.loads(json.dumps(identity)) or metadata.get('run_key') != key:
            raise ValueError('resume differs from the original document account or schedule')
        with WorkflowProgress(directory,key) as progress:
            if (progress.binding('identity') != metadata['identity'] or not progress.binding('plan_registered')):
                raise ValueError('resume differs from registered document checkpoints')
            if judge is not None:
                from contextlib import ExitStack
                from . import workflow_capability
                from .live_dispatch import process_identity
                from .workflow_grading import judge_only_owner
                with ExitStack() as stack:
                    if progress.binding('workflow_scope') is not None:
                        if json.loads(os.environ.get(workflow_capability.ENV,'null')) != progress.binding('workflow_scope'):
                            raise ValueError('resume requires the original bound judge scope before claiming any grade')
                        scope = workflow_capability.load()
                        if scope['controller'] != process_identity():
                            stack.enter_context(judge_only_owner(progress,scope))
                    journal = stack.enter_context(Journal(directory/'judge-recovery'/uuid4().hex))
                    grade_verified(progress, judge, directory, journal, limit=len(turns))
            if not progress.execution_sealed:
                raise ValueError('native document resume needs an account recovery witness before further work; '
                                 'verified answers and independently claimed grades remain available')
            return progress.summary()
    with Journal(directory) as journal, WorkflowProgress(directory, key, create=True) as progress:
        with progress.account_owner():
            atomic(directory/'run.json', dict(identity=identity, run_key=key, evidence_kind=account.evidence_kind))
            progress.bind('identity', identity)
            progress.bind('evidence_kind', account.evidence_kind)
            progress.bind('canary', canary)
            if 'PACBENCH_WORKFLOW_SCOPE' in os.environ:
                progress.bind('workflow_scope', json.loads(os.environ['PACBENCH_WORKFLOW_SCOPE']))
            for ordinal, turn in enumerate(turns):
                progress.plan_turn(ordinal, turn)
            progress.bind('plan_registered', True)
            journal.write('spec', version=spec['version'], canary=canary, sha256=spec_digest,
                          source_basis=basis, schedule=[dict(id=t.id, at=t.at, chat=t.session, measure=t.measure)
                                                      for t in turns])
            run_workflow(condition, account, docs, turns, journal, spec_digest=spec_digest,
                         evidence_kind=account.evidence_kind, all_source_documents=all_docs, progress=progress)
            progress.seal_execution(len(turns), sum(len(t.claims) for t in turns))
        if judge is not None and turns:
            grade_verified(progress, judge, directory, journal, limit=len(turns))
        summary = progress.summary()
        journal.write('completion', by_measure=summary, prompt_caching='unmeasured')
        return summary
