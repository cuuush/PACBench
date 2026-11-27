"""Offline load check of the frozen source index and durable turn/grade queue."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from pacbench.judging.grade import Claim
from pacbench.workflows.progress import WorkflowProgress
from pacbench.workflows.source_plan import SourcePlan
from pacbench.workflows.spec import Document, Turn


EPOCH = datetime(2020, 1, 1, 20, tzinfo=timezone.utc)
CHANNELS = ('email', 'imessage')
QUESTIONS = 20
RECORDS_PER_CHANNEL = 50
SLOTS = 4


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


def run(directory, days):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    start = time.monotonic()
    with SourcePlan(directory, documents(days), turns(days)) as plan:
        key = plan.digest
        with WorkflowProgress(directory, key, create=True) as progress:
            progress.bind('evidence_kind', 'recording-fake')
            for ordinal, turn, _ in plan.iter_turns():
                progress.plan_turn(ordinal, turn)
                surface = {'session_id': turn.session, 'gateway_identity': 'synthetic', 'model_requests': [], 'answer': turn.id}
                progress.verified_turn(turn, surface, {'fixture': True})
                progress.mark_turn_done(turn.id)
            progress.seal_execution(days * QUESTIONS, days * QUESTIONS * SLOTS)
    # Start from the saved database so a stale in-memory index cannot pass.
    with SourcePlan(directory, documents(days), turns(days)) as reopened:
        if reopened.digest != key:
            raise ValueError('source index changed on reopen')
    graded = 0
    with WorkflowProgress(directory, key) as progress:
        while (entry := progress.claim_grade()) is not None:
            turn, surface = entry
            if surface['answer'] != turn.id:
                raise ValueError('wrong saved turn')
            progress.finish_grade(turn.id, [
                {'id': slot.id, 'verdict': 'missed', 'reason': 'synthetic load check'} for slot in turn.claims
            ])
            graded += len(turn.claims)
        if (not progress.execution_sealed or progress.grade_states() != {'graded': days * QUESTIONS}
                or graded != days * QUESTIONS * SLOTS):
            raise ValueError('incomplete queue after reopening the run')
    result = {'fixture_days': days, 'documents': days * len(CHANNELS), 'turns': days * QUESTIONS,
              'graded_fixture_criteria': graded, 'source_plan_sha256': key,
              'elapsed_seconds': time.monotonic() - start}
    (directory / 'scale-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='new directory for the synthetic store')
    parser.add_argument('--days', type=int, default=365)
    args = parser.parse_args()
    if args.days < 1:
        parser.error('positive day count required')
    print(json.dumps(run(args.output, args.days)))


if __name__ == '__main__':
    main()
