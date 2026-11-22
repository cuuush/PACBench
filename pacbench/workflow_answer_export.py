"""Offline, private comparison export from an existing Week workflow directory."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .evidence_store import read_json
from .workflow_grading import retained_grade
from .workflow_progress import WorkflowProgress
from .workflow_spec import compile_week
from .workflow_stream import fingerprint


def legacy_rows(directory):
    """Rebuild verified pairs from the original dated journal without running a judge."""
    seen = set()
    history_times = {}
    pending = None
    with (directory/'workflow.jsonl').open('rb') as stream:
        spec = json.loads(next(stream))
        condition = json.loads(next(stream))['condition']
        _, _, schedule, basis = compile_week(condition['state'])
        if spec.get('canary'):
            schedule = schedule[:4]
        from .workflow_spec import SPEC
        from .canary_capability import sha
        if (spec['sha256'] != sha(SPEC) or basis != spec['source_basis'] or
                spec['schedule'] != [dict(id=t.id, at=t.at, chat=t.session, measure=t.measure) for t in schedule]):
            raise ValueError('journal differs from the frozen Week contract')
        for raw in stream:
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                if raw.endswith(b'\n'):
                    raise
                break
            if event['phase'] == 'answer-visible':
                pending = event
            if event['phase'] != 'verified-turn':
                continue
            turn = schedule[len(seen)]
            if (not pending or pending['question'] != turn.id or event['question'] != turn.id
                    or turn.id in seen):
                raise ValueError('verified answer or chronology differs')
            seen.add(turn.id)
            surface = pending['surface']
            if surface['question_at'] != turn.at or surface['history'][-1]['content'] != surface['answer']:
                raise ValueError('saved answer differs from native history')
            surface['conversation_times'] = history_times.setdefault(surface['session_id'], []) + [turn.at]
            history_times[surface['session_id']] = surface['conversation_times']
            yield turn, surface, event['activity'], None
            pending = None


def comparison_row(directory, turn, surface, activity, checkpoint):
    path = directory/'judgments'/turn.id/'judgment.json'
    attempts = []
    criteria = None
    if path.exists():
        grade = retained_grade(turn, surface, directory)
        criteria = [vars(slot) for slot in grade.slots]
        for member in sorted((path.parent/'judgment-attempts').glob('attempt-*.json')):
            attempt = read_json(member)
            call = read_json(directory/'model-calls'/Path(attempt['model_call']).name)
            receipt = call['judge_transport']
            attempts.append(dict(prompt=attempt['prompt'], response=attempt['response'],
                                 model_call_hash=attempt['model_call_hash'], request_id=receipt['id'],
                                 request_sha256=receipt['request_sha256'], model=call['model'],
                                 endpoint=receipt['endpoint'], judge_protocol=attempt['judge_protocol']))
    if checkpoint and checkpoint['grade_state'] == 'graded' and checkpoint['criteria'] != criteria:
        raise ValueError('checkpoint grade differs from its audited judgment')
    return dict(turn=asdict(turn), answer=surface['answer'], conversation=surface['history'],
                conversation_times=surface['conversation_times'], native_session=surface['session_id'],
                prefetch=surface.get('prefetch'), delivery=surface.get('memory_delivery'),
                tool_calls=surface['tool_calls'], tool_results=surface['tool_results'],
                driver_inputs=[dict(request_id=r['request_id'], messages=r['messages'])
                               for r in surface['model_requests']],
                driver_coverage=activity.get('driver_receipts'), criteria=criteria,
                checkpoint_grade_state=checkpoint['grade_state'] if checkpoint else None,
                judge_attempts=attempts)


def export(directory, destination):
    directory, destination = Path(directory).resolve(strict=True), Path(destination).absolute()
    if destination.is_relative_to(directory) or destination.parent != destination.parent.resolve():
        raise ValueError('select a new private output outside the run')
    counts = dict(verified_answers=0, audited_judgments=0, graded_criteria=0, correct=0)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        root = Path(tmp)
        def write(rows):
            with (root/'answers.private.jsonl').open('x', encoding='utf-8') as out:
                os.fchmod(out.fileno(), 0o600)
                for turn, surface, activity, checkpoint in rows:
                    row = comparison_row(directory, turn, surface, activity, checkpoint)
                    out.write(json.dumps(row, ensure_ascii=False) + '\n')
                    counts['verified_answers'] += 1
                    if row['criteria'] is not None:
                        counts['audited_judgments'] += 1
                        counts['graded_criteria'] += len(row['criteria'])
                        counts['correct'] += sum(c['verdict']=='correct' for c in row['criteria'])
        if (directory/'workflow.sqlite3').exists():
            metadata = read_json(directory/'run.json')
            if fingerprint(metadata['identity']) != metadata['run_key']:
                raise ValueError('run identity mismatch')
            with WorkflowProgress(directory, metadata['run_key']) as progress:
                progress.db.execute('BEGIN')
                if progress.binding('identity') != metadata['identity']:
                    raise ValueError('checkpoint identity mismatch')
                write((*item, progress.grade_record(item[0].id)) for item in progress.iter_verified())
        else:
            write(legacy_rows(directory))
        (root/'summary.public.json').write_text(json.dumps(counts, indent=2)+'\n')
        destination.mkdir(mode=0o700, exist_ok=False)
        # The public summary appears only after the complete private export exists.
        for name in ('answers.private.jsonl','summary.public.json'):
            (root/name).rename(destination/name)
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.directory, args.destination)))
