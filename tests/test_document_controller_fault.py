"""Exercise the document controller with synthetic accounts only.

Run the year-scale probe with python -m tests.test_document_controller_fault 365.
It kills the account worker after its last verified answer and before final close.
"""
from datetime import date, timedelta
import json
from multiprocessing import get_context
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from pacbench.semantic_grade import Claim
from pacbench.workflows.conformance import RecordingAccount
from pacbench.workflows.document_progress import run
from pacbench.workflows.progress import WorkflowProgress
from pacbench.workflows.spec import Document, Turn
from tests.test_workflow_realism import condition


def schedule(days):
    docs, turns = [], []
    for day in range(days):
        stamp = str(date(2026,1,1) + timedelta(days=day))
        sources = []
        for channel in ('email','imessage'):
            ids = tuple(f'{day}-{channel}-{i}' for i in range(50))
            bodies = tuple('Short synthetic event '+ident for ident in ids)
            docs.append(Document(f'{stamp}-{channel}.txt', stamp+'T16:00:00+00:00', channel,
                                 '\n'.join(bodies), ids, bodies))
            sources.extend(ids)
        for slot in range(20):
            key = f'q-{day}-{slot}'
            claims = tuple(Claim(str(i), 'A fixture criterion', (sources[slot+i],)) for i in range(4))
            turns.append(Turn(key,f'{stamp}T17:{slot:02d}:00+00:00',f'What is the detail for {key}?',
                              key,'initial',claims))
    return docs, turns


def account_worker(directory, days):
    # A hard exit bypasses TemporaryDirectory finalizers in the child.
    import tempfile
    tempfile.tempdir = str(directory.parent)
    docs, turns = schedule(days)
    route = condition(p='reme')
    class Recording(RecordingAccount):
        def end_chat(self, session_id):
            if len(self.native_writes) == len(turns):
                os._exit(75)
            return super().end_chat(session_id)
    account = Recording(route,docs)
    run(route,account,docs,turns,directory,{'version':'synthetic-fault-test'},
        'recording-fixture',{'kind':'recording'},docs,None,False)


def fault_check(directory, days):
    worker = get_context('spawn').Process(target=account_worker,args=(directory,days))
    worker.start()
    worker.join()
    assert worker.exitcode == 75
    key = json.loads((directory/'run.json').read_text())['run_key']
    count = 0
    with WorkflowProgress(directory,key) as progress:
        assert not progress.execution_sealed
        while (item := progress.claim_grade()) is not None:
            turn, surface = item
            assert surface['answer'] == 'Recording acknowledgement'
            progress.finish_grade(turn.id,[dict(id=c.id,verdict='missed',reason='synthetic slot') for c in turn.claims])
            count += 1
        assert count == days*20
        summary = progress.summary()['initial']
        assert summary['answered'] == count and summary['graded_criteria'] == count*4
        assert progress.next_ordinal() == count-1
        assert progress.db.execute("SELECT count(*) FROM effects WHERE phase='answer'").fetchone()[0] == count
    with WorkflowProgress(directory,key) as restarted:
        assert restarted.claim_grade() is None and not restarted.complete
    return dict(days=days, records=days*100, answers=count, grade_slots=count*4, worker_exit=worker.exitcode)


def test_document_controller_loses_last_close_without_losing_grade_queue(tmp_path):
    assert fault_check(tmp_path/'workflow',2)['grade_slots'] == 160


if __name__ == '__main__':
    with TemporaryDirectory() as root:
        print(json.dumps(fault_check(Path(root)/'workflow', int(sys.argv[1]))))
