"""Capture verified document turns for independent grading, without replaying native work."""
from dataclasses import asdict
import json
import os
from pathlib import Path

from .workflow_progress import WorkflowProgress
from .workflow_stream import fingerprint

PROTOCOL = 'pizza-week-native-document-progress-v1'
PROVIDERS = frozenset(('mem0', 'hindsight', 'openviking', 'reme', 'cognee', 'memos'))


def run(condition, account, docs, turns, directory, spec, spec_digest, basis, all_docs, judge, canary):
    from .gateway_reuse import atomic
    from .workflow_grading import grade_verified
    from .workflow_runner import Journal, run_workflow

    directory = Path(directory).resolve()
    identity = dict(protocol=PROTOCOL, condition=asdict(condition), account=account.identity,
                    source_basis=basis, spec_sha256=spec_digest, evidence_kind=account.evidence_kind,
                    canary=canary, schedule_sha256=fingerprint([asdict(t) for t in turns]))
    key = fingerprint(identity)
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
