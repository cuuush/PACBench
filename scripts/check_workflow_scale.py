"""Offline load and crash check of the production account stream with a durable synthetic fixture.

From the repository root, run python -m scripts.check_workflow_scale /new/output/path.
All source, answers and slot outcomes here are deterministic test data. No model is called.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

from pacbench.semantic_grade import Claim
from pacbench.workflows.progress import WorkflowProgress
from pacbench.workflows.runner import Condition, Journal
from pacbench.workflows.spec import Document, Turn
from pacbench.workflows.stream import run_account_stream


EPOCH = datetime(2020, 1, 1, 20, tzinfo=timezone.utc)
CHANNELS = ('email', 'imessage')
QUESTIONS = 20
RECORDS_PER_CHANNEL = 50
SLOTS = 4
CONDITION = Condition('memcal', 'first-party-connector', 'continuing-account', 'offline load fixture',
                      'simulated', 'offline', 'synthetic recording', 'scale-v1', 'none', 'fixed UTC', ())


def night(day):
    cutoff = (EPOCH + timedelta(days=day)).isoformat()
    for channel in CHANNELS:
        ids = tuple(f'{day:04}-{channel}-{i:02}' for i in range(RECORDS_PER_CHANNEL))
        bodies = tuple(f'Offline load test status for item {key} is confirmed in the simulated account.' for key in ids)
        yield Document(f'{day:04}-{channel}.txt', cutoff, channel, '\n'.join(bodies), ids, bodies)


def documents(days):
    for day in range(days):
        yield from night(day)


def turns(days):
    for day in range(days):
        for i in range(QUESTIONS):
            at = (EPOCH + timedelta(days=day, minutes=i+1)).isoformat()
            ident = f'q-{day:04}-{i:02}'
            claims = tuple(Claim(f'criterion-{k}', f'Simulated criterion {k}', (f'{day:04}-email-{k:02}',))
                           for k in range(SLOTS))
            yield Turn(ident, at, f'Simulated load question {ident}?', f'chat-{day}-{i//2}',
                       'scale-fixture', claims)


class DurableRecordingAccount:
    evidence_kind = 'recording-fake'

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS operations(phase TEXT, key TEXT, PRIMARY KEY(phase,key));
        CREATE TABLE IF NOT EXISTS docs(name TEXT UNIQUE, cutoff TEXT, channel TEXT);
        CREATE TABLE IF NOT EXISTS source(id TEXT PRIMARY KEY, name TEXT, body TEXT);
        CREATE INDEX IF NOT EXISTS doc_night ON docs(cutoff,channel);
        CREATE INDEX IF NOT EXISTS doc_source ON source(name);
        CREATE TABLE IF NOT EXISTS answers(at TEXT PRIMARY KEY, session TEXT, prompt TEXT, answer TEXT);
        CREATE INDEX IF NOT EXISTS chat_history ON answers(session,at);
        """)
        self.identity = str(path.resolve())
        self.gateway = self.identity + '/simulated-gateway'

    def record(self, phase, key):
        # Duplicate side effects are a test failure, including across process restarts.
        self.db.execute('INSERT INTO operations VALUES (?,?)', (phase, key))

    def verify_condition(self, condition):
        assert condition == CONDITION
        return dict(account=self.identity, store_identity=self.identity, condition=vars(condition),
                    surface=self.evidence_kind)

    def control_readback(self):
        return dict(account=self.identity, source_files=self.source_inventory(),
                    chat_count=self.db.execute('SELECT count(DISTINCT session) FROM answers').fetchone()[0],
                    conversation_writes=self.db.execute('SELECT count(*) FROM answers').fetchone()[0],
                    readback='durable recording')

    def source_inventory(self):
        return [r[0] for r in self.db.execute('SELECT name FROM docs ORDER BY rowid')]

    def advance(self, at):
        with self.db:
            self.record('clock', at)
        return dict(cutoff=at, as_of=at, service_at=at)

    def sync(self, at, channels):
        day = (datetime.fromisoformat(at) - EPOCH).days
        with self.db:
            self.record('release', at)
            for doc in night(day):
                assert doc.cutoff == at and doc.channel in channels
                self.db.execute('INSERT INTO docs VALUES (?,?,?)', (doc.filename, at, doc.channel))
                self.db.executemany('INSERT INTO source VALUES (?,?,?)',
                                    ((key, doc.filename, body) for key, body in zip(doc.source_ids, doc.bodies, strict=True)))
        return dict(at=at, native_id=at, accepted=True, request={'channels':channels,'through':at})

    def sync_readback(self, ack):
        at = ack['at']
        channels = {}
        for channel in CHANNELS:
            ids = [r[0] for r in self.db.execute(
                'SELECT id FROM source JOIN docs USING(name) WHERE cutoff=? AND channel=? ORDER BY id', (at, channel))]
            channels[channel] = dict(cursor=at, as_of=at, new_source_ids=ids)
        return dict(at=at, native_id=at, ready=True, readback='SQLite recording', source_cursor=at,
                    channel_readback=channels, new_source_ids=[i for c in channels.values() for i in c['new_source_ids']])

    def ask(self, prompt, *, at, session_id):
        session = session_id or 'synthetic-chat-' + at
        history = []
        for previous, answer in self.db.execute('SELECT prompt,answer FROM answers WHERE session=? ORDER BY at', (session,)):
            history.extend([{'role':'user','content':previous}, {'role':'assistant','content':answer}])
        assert (session_id is None) == (not history)
        answer = 'Simulated acknowledgement'
        with self.db:
            self.record('answer', at)
            self.db.execute('INSERT INTO answers VALUES (?,?,?,?)', (at, session, prompt, answer))
            self.record('write', at)
        history.append({'role':'user','content':prompt})
        return dict(session_id=session, history=[*history, {'role':'assistant','content':answer}], answer=answer,
                    gateway_identity=self.gateway, account=self.identity, question_at=at,
                    write_barrier=dict(session_id=session, pending_writes=0, readback='durable synthetic write'),
                    source_files=self.source_inventory(), offered_tools=[], tool_calls=[], tool_results=[],
                    model_requests=[dict(model=CONDITION.model, request_id='fixture-'+at, tools=[], messages=history)],
                    prefetch='', attempts=[])

    def end_chat(self, session):
        with self.db:
            self.record('close', session)
        return dict(session_id=session, pending_writes=0, readback='durable synthetic close')


def account_worker(output, days, restart):
    account = DurableRecordingAccount(output/'recording.sqlite3')
    stop_turn = f'q-{(days-1)//2:04}-10'
    original = Journal.write
    def boundary(journal, phase, **record):
        original(journal, phase, **record)
        if not restart and phase == 'verified-turn' and record['question'] == stop_turn:
            os.kill(os.getpid(), signal.SIGSTOP)
    try:
        with patch.object(Journal, 'write', boundary):
            run_account_stream(CONDITION, account, documents(days), turns(days), output/'run',
                               spec_digest='offline-controller-scale-v1', source_basis={'fixture_days':days}, resume=restart)
    finally:
        account.db.close()


def progress_for(output):
    key = json.loads((output/'run'/'run.json').read_text())['run_key']
    return WorkflowProgress(output/'run', key)


def grade_worker(output, batch_size):
    """Exercise the production claim/settle queue with explicitly simulated slot outcomes."""
    count = 0
    with progress_for(output) as progress:
        while True:
            for _ in range(batch_size):
                item = progress.claim_grade()
                if item is None:
                    return count
                turn, surface = item
                assert surface['answer'] == 'Simulated acknowledgement'
                slots = [dict(id=c.id, verdict='correct', reason='simulated queue load only') for c in turn.claims]
                progress.finish_grade(turn.id, slots)
                count += 1


def counts(output):
    with closing(sqlite3.connect(output/'recording.sqlite3')) as db:
        ops = dict(db.execute('SELECT phase,count(*) FROM operations GROUP BY phase'))
        ops['records'] = db.execute('SELECT count(*) FROM source').fetchone()[0]
        ops['documents'] = db.execute('SELECT count(*) FROM docs').fetchone()[0]
        ops['distinct_answers'] = db.execute('SELECT count(*) FROM answers').fetchone()[0]
        return ops


def command(args, mode):
    return [sys.executable, '-m', 'scripts.check_workflow_scale', str(args.output), '--days', str(args.days),
            '--batch', str(args.batch), '--worker', mode]


def run(args):
    output, days = args.output, args.days
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    start = time.monotonic()
    initial = subprocess.Popen(command(args, 'account'))
    _, status = os.waitpid(initial.pid, os.WUNTRACED)
    if not os.WIFSTOPPED(status) or os.WSTOPSIG(status) != signal.SIGSTOP:
        initial.returncode = os.waitstatus_to_exitcode(status)
        raise RuntimeError(f'worker exited before the durable fault boundary ({initial.returncode})')
    try:
        with progress_for(output) as progress:
            before = progress.summary()['scale-fixture']
            assert not progress.execution_sealed and not progress.complete
        frozen_counts = counts(output)
    finally:
        initial.kill()
        initial.wait()
    assert initial.returncode == -signal.SIGKILL
    # This subprocess opens only the progress store while the account is dead.
    subprocess.run(command(args, 'grades'), check=True)
    with progress_for(output) as progress:
        partial = progress.summary()['scale-fixture']
        assert partial['answered'] == before['answered'] == frozen_counts['answer']
        assert 0 < partial['graded_criteria'] == before['answered'] * SLOTS < days * QUESTIONS * SLOTS
        assert not progress.complete and not progress.execution_sealed
    assert counts(output) == frozen_counts

    with subprocess.Popen(command(args, 'resume')) as resumed:
        if resumed.wait() != 0:
            raise RuntimeError('new account worker failed to resume')
    subprocess.run(command(args, 'grades'), check=True)
    expected = dict(clock=days, release=days, answer=days*QUESTIONS, write=days*QUESTIONS,
                    close=days*QUESTIONS//2, records=days*RECORDS_PER_CHANNEL*2, documents=days*2,
                    distinct_answers=days*QUESTIONS)
    observed = counts(output)
    assert observed == expected, (observed, expected)
    with progress_for(output) as progress:
        final = progress.summary()['scale-fixture']
        assert progress.execution_sealed and not progress.complete
        assert final['answered'] == days*QUESTIONS and final['graded_criteria'] == days*QUESTIONS*SLOTS
        assert progress.grade_states() == {'graded':days*QUESTIONS}
    result = dict(evidence_kind='offline recording fixture', model_calls=0, days=days,
                  expected=expected, observed=observed, judge_batch=args.batch, grades_simulated=True,
                  full_score_published=False, sealed_fixture_execution=True,
                  interrupt={'pid':initial.pid, 'signal':'SIGKILL', 'verified_turns':before['answered'],
                             'counts_at_stop':frozen_counts, 'graded_criteria_while_account_dead':partial['graded_criteria'],
                             'resume_pid':resumed.pid},
                  graded_criteria=final['graded_criteria'], elapsed_seconds=time.monotonic()-start,
                  max_worker_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    with (output/'scale-result.json').open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New disposable directory for fixture and controller evidence')
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--worker', choices=['account','resume','grades'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.days < 1 or not 1 <= args.batch <= 64:
        parser.error('positive day count and batch size from 1 to 64 required')
    args.output = args.output.resolve()
    if args.worker == 'grades':
        grade_worker(args.output, args.batch)
    elif args.worker:
        account_worker(args.output, args.days, args.worker == 'resume')
    else:
        run(args)


if __name__ == '__main__':
    main()
