"""Offline, private comparison export from an existing Week workflow directory."""
import argparse
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..evidence_store import read_json
from ..provider_versions import MEMCAL_WEEK_VERSION
from pacbench.providers.memcal.contract import manifest, verify_captured_driver
from .grading import retained_grade
from .progress import WorkflowProgress
from pacbench.campaigns.pizza_week import compile_week
from .stream import fingerprint


def saved_events(path):
    with path.open('rb') as stream:
        for raw in stream:
            try:
                yield json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                if raw.endswith(b'\n'):
                    raise
                return


def legacy_rows(directory):
    """Rebuild verified pairs from the original dated journal without running a judge."""
    seen = set()
    history_times = {}
    pending = None
    path = directory/'workflow.jsonl'
    judgments = {}
    for event in saved_events(path):
        if event['phase'] in ('grade','ungraded'):
            q = event['question']
            if q in judgments:
                raise ValueError('duplicate journal grade')
            judgments[q] = dict(grade_state='graded' if event['phase']=='grade' else 'failed',
                                criteria=event.get('criteria'), failure=event.get('reason'))
    from contextlib import closing
    with closing(saved_events(path)) as stream:
        spec = next(stream)
        condition = next(stream)['condition']
        _, _, schedule, basis = compile_week(condition['state'])
        if spec.get('canary'):
            schedule = schedule[:4]
        from pacbench.campaigns.pizza_week import SPEC
        from ..canary_capability import sha
        if (spec['sha256'] != sha(SPEC) or basis != spec['source_basis'] or
                spec['schedule'] != [dict(id=t.id, at=t.at, chat=t.session, measure=t.measure) for t in schedule]):
            raise ValueError('journal differs from the frozen Week contract')
        for event in stream:
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
            yield turn, surface, event['activity'], judgments.pop(turn.id, dict(grade_state='ungraded',criteria=None))
            pending = None
    if judgments:
        raise ValueError('grade without a verified answer')


def ungraded_attempts(directory, turn, surface, folder):
    from ..canary_capability import digest
    from ..semantic_grade import RETRY_GUIDANCE, judge_prompt
    from .runner import judge_options
    prompt = judge_prompt(turn.prompt,surface['answer'],turn.claims,**judge_options(turn,surface))
    records = []
    for member in sorted(folder.glob('attempt-*.json')):
        record = {'file':member.name, 'provenance':'unverified', 'request_id':None}
        if member != member.resolve():
            raise ValueError('judge attempt outside original run')
        try:
            raw = member.read_bytes()
            record.update(attempt_sha256=hashlib.sha256(raw).hexdigest(), attempt_bytes=len(raw))
            attempt = json.loads(raw)
            if not isinstance(attempt, dict):
                raise ValueError('invalid attempt record')
        except (OSError, ValueError, UnicodeError) as exc:
            record['evidence_error'] = type(exc).__name__
            records.append(record)
            continue
        if (attempt.get('checkpoint') == turn.id and attempt.get('answer') == surface['answer']
                and attempt.get('prompt') in (prompt,prompt+RETRY_GUIDANCE)):
            record.update(prompt=attempt['prompt'], response=attempt.get('response'))
            model = attempt.get('model_call')
            if isinstance(model,str):
                call_path = directory/'model-calls'/Path(model).name
                if call_path == Path(model) == call_path.resolve() and call_path.is_file():
                    try:
                        call = read_json(call_path)
                        if not isinstance(call,dict):
                            raise ValueError('invalid call record')
                    except (OSError, ValueError, UnicodeError) as exc:
                        record['evidence_error'] = type(exc).__name__
                        records.append(record)
                        continue
                    receipt = call.get('judge_transport') or {}
                    if (digest(call) == attempt.get('model_call_hash') and call.get('text') == record['response']
                            and receipt.get('request',{}).get('messages') == [{'role':'user','content':record['prompt']}]
                            and receipt.get('request_sha256') == digest(receipt['request'])):
                        record.update(provenance='retained-return', model_call_hash=digest(call),
                                      request_id=receipt.get('id'), model=call.get('model'), endpoint=receipt.get('endpoint'))
        records.append(record)
    return records


def comparison_row(directory, turn, surface, activity, checkpoint, *, audit_path=None):
    path = audit_path or directory/'judgments'/turn.id/'judgment.json'
    attempts = []
    criteria = None
    if path.exists() and (not checkpoint or checkpoint['grade_state'] == 'graded'):
        grade = retained_grade(turn, surface, directory, audit_path=path)
        criteria = [vars(slot) for slot in grade.slots]
        for member in sorted((path.parent/'judgment-attempts').glob('attempt-*.json')):
            attempt = read_json(member)
            call = read_json(directory/'model-calls'/Path(attempt['model_call']).name)
            receipt = call['judge_transport']
            attempts.append(dict(prompt=attempt['prompt'], response=attempt['response'],
                                 model_call_hash=attempt['model_call_hash'], request_id=receipt['id'],
                                 request_sha256=receipt['request_sha256'], model=call['model'],
                                 endpoint=receipt['endpoint'], judge_protocol=attempt['judge_protocol']))
    elif checkpoint and checkpoint['grade_state'] != 'graded' and turn.claims:
        attempts = ungraded_attempts(directory,turn,surface,path.parent/'judgment-attempts')
    if checkpoint and checkpoint['grade_state'] == 'graded' and checkpoint['criteria'] != criteria:
        raise ValueError('checkpoint grade differs from its audited judgment')
    return dict(grade_failure=(checkpoint or {}).get('failure'), turn=asdict(turn), answer=surface['answer'], conversation=surface['history'],
                conversation_times=surface['conversation_times'], native_session=surface['session_id'],
                prefetch=surface.get('prefetch'), prefetch_provenance=surface.get('prefetch_provenance'),
                delivery=surface.get('memory_delivery'), tool_provenance=surface.get('tool_provenance'),
                tool_calls=surface['tool_calls'], tool_results=surface['tool_results'],
                driver_inputs=[dict(request_id=r['request_id'], messages=r['messages'])
                               for r in surface['model_requests']],
                attempt_lineage=surface.get('attempts', []), driver_coverage=activity.get('driver_receipts'), criteria=criteria,
                checkpoint_grade_state=checkpoint['grade_state'] if checkpoint else None,
                judge_attempts=attempts)


def checkpoint_rows(progress, directory, temporary):
    identity = progress.binding('identity') or {}
    # Earlier experimental products also reported 0.8.12. Only the new run
    # identity carries this independently recorded Week product manifest.
    qualified = 'product_manifest' in identity
    if qualified:
        from ..canary_capability import read
        condition = identity['condition']
        if ((condition['provider'], condition['version']) != ('memcal', MEMCAL_WEEK_VERSION)
                or identity['product_manifest'] != manifest()):
            raise ValueError('unexpected MemCal export product manifest')
        bound = progress.binding('workflow_scope')
        if not bound or Path(bound['path']) != directory.parent/'workflow-scope.json':
            raise ValueError('MemCal export lacks its run-owned product manifest')
        scope = read(bound)
        if (scope['provider'] != 'memcal' or scope['root'] != str(directory.parent)
                or (scope['account'], scope['store']) != (identity['account'], identity['store'])
                or any(scope['access'].get(k) != v for k, v in manifest().items())):
            raise ValueError('MemCal export differs from the qualified product manifest')
    for turn, surface, activity in progress.iter_verified():
        checkpoint = progress.grade_record(turn.id)
        recovered = progress.binding('recovered-judge-attempts/'+turn.id)
        audit_path = None
        if recovered and not (directory/'judgments'/turn.id/'judgment.json').exists():
            folder = temporary/turn.id/'judgment-attempts'
            folder.mkdir(parents=True)
            for i, attempt in enumerate(recovered, 1):
                (folder/f'attempt-{i}.json').write_text(json.dumps(attempt))
            audit_path = folder.parent/'judgment.json'
            audit_path.write_text(json.dumps(recovered[-1]))
        row = comparison_row(directory, turn, surface, activity, checkpoint, audit_path=audit_path)
        if qualified:
            if not surface['model_requests']:
                raise ValueError('missing MemCal driver evidence')
            for request, saved in zip(surface['model_requests'], row['driver_inputs'], strict=True):
                verify_captured_driver(request)
                saved['canonical_request_sha256'] = request['canonical_request_sha256']
            row['product_manifest'] = manifest()
        yield row


def financial_evidence(directory):
    from ..observational_receipts import replay_attempts
    from .recording import receipt_summary
    root = directory.parent
    journal = root/'receipts.jsonl'
    attempts = {}
    status = {'state':'absent'}
    def lines(stream):
        while True:
            position = stream.tell()
            raw = stream.readline()
            if not raw:
                return
            if not raw.endswith(b'\n'):
                status.update(state='incomplete-tail',offset=position,bytes=len(raw))
                return
            yield raw
    if journal.is_file():
        status['state'] = 'read'
        if journal != journal.resolve():
            raise ValueError('foreign receipt journal')
        with journal.open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            rows = replay_attempts(lines(stream))
        scope = read_json(root/'workflow-scope.json')
        receipt_summary(root, list(rows.values()), scope['run_id'], attempts=attempts)
    return attempts, status


def export(directory, destination):
    directory, destination = Path(directory).resolve(strict=True), Path(destination).absolute()
    if destination.is_relative_to(directory) or destination.parent != destination.parent.resolve():
        raise ValueError('select a new private output outside the run')
    counts = dict(verified_answers=0, audited_judgments=0, graded_criteria=0, correct=0)
    financial, journal_state = financial_evidence(directory)
    by_question = {}
    for ident, receipt in financial.items():
        if receipt.get('role') == 'judge' and receipt.get('question_id'):
            by_question.setdefault(receipt['question_id'], {})[ident] = receipt
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        root = Path(tmp)
        def write(rows):
            with (root/'answers.private.jsonl').open('x', encoding='utf-8') as out:
                os.fchmod(out.fileno(), 0o600)
                for row in rows:
                    for attempt in [*row['driver_inputs'], *row['judge_attempts']]:
                        attempt['receipt'] = financial.get(attempt['request_id'], {'actual_usd':None})
                    row['financial_journal'] = journal_state
                    row['judge_receipts'] = by_question.get(row['turn']['id'], {})
                    row['attempt_receipts'] = {a['id']:financial.get(a['id'], {'actual_usd':None})
                                               for a in row['attempt_lineage'] if isinstance(a,dict) and a.get('id')}
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
            with WorkflowProgress(directory, metadata['run_key'], read_only=True) as progress:
                progress.db.execute('BEGIN')
                if progress.binding('identity') != metadata['identity']:
                    raise ValueError('checkpoint identity mismatch')
                write(checkpoint_rows(progress, directory, root/'recovered'))
        else:
            write(comparison_row(directory, *item) for item in legacy_rows(directory))
        (root/'summary.public.json').write_text(json.dumps(counts, indent=2)+'\n')
        destination.mkdir(mode=0o700, exist_ok=False)
        # The public summary appears only after the complete private export exists.
        for name in ('answers.private.jsonl','summary.public.json'):
            (root/name).rename(destination/name)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.directory, args.destination)))


if __name__ == '__main__':
    main()
