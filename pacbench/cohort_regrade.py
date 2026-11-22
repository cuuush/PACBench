"""Plan all saved cohort answers, or analyze retained responses, without a transport."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from .semantic_grade import Claim, CONTEXT_JUDGE_PROTOCOL, VERDICTS, judge_prompt, grade_semantic
from .workflow_runner import judge_options
from .workflow_progress import WorkflowProgress, _turn
from .workflow_stream import fingerprint
from .workflow_spec import instant

# Registration is code-reviewed; arbitrary rubric paths or silent latest aliases are forbidden.
REVISIONS = {'luna-context-v4-v1': {'protocol': CONTEXT_JUDGE_PROTOCOL,
    'model': 'openai/gpt-6-luna', 'route': 'amazon-bedrock/global', 'fallback': False, 'supplement': ''},
    'luna-explicit-criteria-v1': {'protocol': CONTEXT_JUDGE_PROTOCOL,
    'model': 'openai/gpt-6-luna', 'route': 'amazon-bedrock/global', 'fallback': False,
    'supplement': ('Experimental criterion rules v1: Apply each rule only to a criterion that asks for that fact. '
      'Diet: judge person, item and polarity independently. '
      'RSVP: distinguish attendance state, a decline communicated to a named recipient, and cancellation of the event. '
      'Cannot make it establishes nonattendance, not necessarily communication or cancellation. '
      'Roster: a complete-sounding confirmed roster can establish the full set without literal only; '
      'including leaves the set open, and tentative guests are not confirmed. '
      'Schedule: preserve temporal precedence and distinguish one-off changes from the recurring schedule. '
      'Credit unambiguous paraphrases and each independent attribute. Do not invent omitted facts or '
      'resolve genuine rubric ambiguity by adding requirements. Contradictory extras must be considered '
      'against the particular criterion they contradict.')}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    path = Path(path).absolute()
    if path != path.resolve():
        raise ValueError('symlinked input is not supported')
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def discover(path):
    """Accept a native run.json or an explicit cohort of hash-bound run manifests."""
    path = Path(path).absolute()
    meta, sha = read(path)
    if meta.get('schema') == 'pacbench-regrade-cohort-v1':
        rows = meta['runs']
    elif 'run_key' in meta:
        rows = [{'provider': meta['identity']['condition']['provider'],
                 'manifest': path.name, 'sha256': sha}]
    else:
        raise ValueError('unsupported saved manifest schema; no directory guessing')
    if not rows:
        raise ValueError('empty cohort')
    seen = set()
    paths = set()
    result = []
    for row in rows:
        name = row['provider']
        target = (path.parent / row['manifest']).absolute()
        if not isinstance(name, str) or not name or name in seen or target.resolve() in paths:
            raise ValueError('duplicate/invalid provider or run manifest')
        seen.add(name); paths.add(target.resolve())
        result.append((name, target, row['sha256']))
    return result, sha


def snapshot(path, revision):
    if revision not in REVISIONS:
        raise ValueError('unregistered judge/rubric revision')
    runs, manifest_sha = discover(path)
    items, coverage, roots = [], {}, []
    run_keys = set()
    for provider, manifest, expected_sha in runs:
        meta, actual_sha = read(manifest)
        if actual_sha != expected_sha:
            raise ValueError('stale run manifest digest')
        identity = meta['identity']; directory = manifest.parent
        if (fingerprint(identity) != meta['run_key'] or meta['run_key'] in run_keys
                or identity['condition']['provider'] != provider
                or meta.get('evidence_kind') != 'native' or identity.get('evidence_kind') != 'native'):
            raise ValueError('run identity/provider/evidence mismatch')
        run_keys.add(meta['run_key']); roots.append(str(directory))
        database = directory/'workflow.sqlite3'
        if database != database.resolve():
            raise ValueError('symlinked progress database')
        with WorkflowProgress(directory, meta['run_key'], read_only=True) as progress:
            progress.db.execute('BEGIN')  # one consistent read-only SQLite snapshot
            if progress.binding('identity') != identity or not progress.binding('plan_registered'):
                raise ValueError('unregistered or stale native plan')
            progress.audit_artifacts()
            rows = list(progress.db.execute('SELECT * FROM turns ORDER BY ordinal'))
            if not rows or [r['ordinal'] for r in rows] != list(range(len(rows))):
                raise ValueError('missing/noncontiguous planned turns')
            expected_ids, completed_ids = [], []
            expected_criteria = completed_criteria = 0
            missing = []
            for row in rows:
                turn = _turn(row['plan']); ids = [c.id for c in turn.claims]
                if turn.id != row['id'] or len(set(ids)) != len(ids) or len(ids) != row['criteria_count']:
                    raise ValueError('duplicate/stale criterion or turn plan')
                expected_ids.append(turn.id); expected_criteria += len(ids)
                if not row['done'] or not row['surface']:
                    missing.append(turn.id); continue
                verified = progress.get_verified(turn.id)
                _, surface, activity = verified
                if not isinstance(surface.get('answer'), str) or not surface['answer'].strip():
                    raise ValueError('completed answer is empty')
                options = judge_options(turn, surface)
                history = surface['history']
                last_user = next(r for r in reversed(history) if r['role'] == 'user')
                if last_user['content'] != turn.prompt:
                    raise ValueError('saved final question differs from plan')
                for message in options['conversation']:
                    if instant(message['at']) > instant(turn.at):
                        raise ValueError('future conversation context')
                original = progress.grade_record(turn.id)
                criteria = original['criteria']
                if criteria is not None and ([c['id'] for c in criteria] != ids
                        or any(c['verdict'] not in VERDICTS for c in criteria)):
                    raise ValueError('original grade criterion mismatch')
                completed_ids.append(turn.id); completed_criteria += len(ids)
                if not ids:
                    continue
                saved = {'turn': asdict(turn), 'surface': surface, 'activity': activity,
                         'original': original, 'run_key': meta['run_key']}
                prompt = judge_prompt(turn.prompt, surface['answer'], turn.claims, **options)
                supplement = REVISIONS[revision]['supplement']
                if supplement:
                    prompt += '\n' + supplement
                items.append({'id': provider + '/' + turn.id, 'provider': provider,
                              'saved': saved, 'input_sha256': digest(saved), 'options': options,
                              'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
            coverage[provider] = {'expected_turns': len(expected_ids), 'completed_turns': len(completed_ids),
                'expected_criteria': expected_criteria, 'completed_criteria': completed_criteria,
                'missing_turns': missing, 'execution_sealed': progress.execution_sealed}
    registry = dict(REVISIONS[revision])
    registry['planner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    registry['scorer_sha256'] = hashlib.sha256(Path(__file__).with_name('semantic_grade.py').read_bytes()).hexdigest()
    plan = {'schema': 'pacbench-cohort-regrade-plan-v1', 'revision': revision, 'judge': registry,
            'manifest_sha256': manifest_sha, 'source_roots': roots, 'coverage': coverage, 'items': items,
            'dispatch_enabled': False}
    plan['plan_id'] = digest(plan)
    return plan


def public_summary(plan):
    return {k: plan[k] for k in ('plan_id', 'revision', 'judge', 'coverage', 'dispatch_enabled')}


class Overlay:
    """Separate durable offline overlay. Claimed attempts never become eligible again."""
    def __init__(self, output, plan):
        self.plan = plan; self.directory = Path(output).absolute()
        if self.directory != self.directory.resolve():
            raise ValueError('symlinked output')
        if any(self.directory == Path(r) or Path(r) in self.directory.parents for r in plan['source_roots']):
            raise ValueError('output must be outside original run directories')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        database = self.directory/'overlay.sqlite3'
        if database != database.resolve():
            raise ValueError('symlinked overlay database')
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), plan TEXT);'
            'CREATE TABLE IF NOT EXISTS attempts (cell TEXT PRIMARY KEY, attempt TEXT UNIQUE, response TEXT, result TEXT);')
        encoded = json.dumps(plan, sort_keys=True)
        self.db.execute('INSERT OR IGNORE INTO binding VALUES (1,?)', (encoded,))
        if self.db.execute('SELECT plan FROM binding').fetchone()[0] != encoded:
            self.db.close(); raise ValueError('stale inputs/revision; use a new versioned output directory')

    def close(self):
        self.db.close()

    def reserve(self, cell, attempt):
        """A future admitted dispatcher must reserve before send; no retry/reset API."""
        if cell not in {i['id'] for i in self.plan['items']} or not isinstance(attempt,str) or not attempt:
            raise ValueError('unknown cell or missing unique attempt')
        try:
            self.db.execute('INSERT INTO attempts(cell,attempt) VALUES (?,?)', (cell, attempt))
        except sqlite3.IntegrityError as exc:
            raise ValueError('attempt already exists; reconcile, never resend') from exc

    def import_response(self, record):
        if record['plan_id'] != self.plan['plan_id']:
            raise ValueError('response belongs to another frozen plan')
        item = next((i for i in self.plan['items'] if i['id'] == record['cell']), None)
        if item is None or record['prompt_sha256'] != item['prompt_sha256']:
            raise ValueError('unknown cell or stale response prompt')
        if not isinstance(record['response'], str):
            raise ValueError('response must be raw text')
        prior = self.db.execute('SELECT attempt,response FROM attempts WHERE cell=?', (record['cell'],)).fetchone()
        if prior is None:
            self.reserve(record['cell'], record['attempt_id'])
        elif prior[0] != record['attempt_id'] or (prior[1] is not None and prior[1] != record['response']):
            raise ValueError('attempt or immutable response mismatch')
        if prior and prior[1] is not None:
            return
        t = item['saved']['turn']; claims = tuple(Claim(c['id'], c['description'], tuple(c['evidence'])) for c in t['claims'])
        try:
            grade = grade_semantic(t['id'], t['prompt'], item['saved']['surface']['answer'], claims,
                                   lambda _: record['response'], **item['options'])
            result = {'state': 'parsed-offline', 'criteria': [vars(c) for c in grade.slots]}
        except ValueError:
            result = {'state': 'invalid-output', 'criteria': None}
        # No claim that imported text has a verified route, billing receipt or paid provenance.
        result['receipt_verified'] = False
        self.db.execute('UPDATE attempts SET response=?, result=? WHERE cell=? AND response IS NULL',
                        (record['response'], json.dumps(result), record['cell']))

    def report(self):
        attempts = {r[0]: r[1:] for r in self.db.execute('SELECT cell,response,result FROM attempts')}
        providers = {}; deltas = []; states = {}
        for item in self.plan['items']:
            row = attempts.get(item['id']); parsed = json.loads(row[1]) if row and row[1] else None
            state = parsed['state'] if parsed else 'ambiguous-attempt' if row else 'unsent'
            states[item['id']] = state
            old = {c['id']: c['verdict'] for c in item['saved']['original']['criteria'] or []}
            new = {c['id']: c['verdict'] for c in (parsed or {}).get('criteria') or []}
            p = providers.setdefault(item['provider'], {'compared_criteria': 0, 'credit_delta': 0})
            for c in item['saved']['turn']['claims']:
                a, b = old.get(c['id']), new.get(c['id'])
                delta = int(b == 'correct') - int(a == 'correct') if a is not None and b is not None else None
                if delta is not None:
                    p['compared_criteria'] += 1; p['credit_delta'] += delta
                deltas.append({'cell': item['id'], 'criterion': c['id'], 'original': a, 'regrade': b, 'credit_delta': delta})
        return {'plan_id': self.plan['plan_id'], 'coverage': self.plan['coverage'], 'states': states,
                'providers': providers, 'criterion_deltas': deltas, 'receipt_verified': False,
                'provenance': 'offline analysis of supplied responses; not official or billing-verified grades'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='saved run.json or cohort manifest; all turns discovered')
    parser.add_argument('--revision', required=True, choices=sorted(REVISIONS))
    parser.add_argument('--output', type=Path, help='separate versioned overlay; never writes original run')
    parser.add_argument('--responses', type=Path, help='offline saved response records, not a dispatch request')
    args = parser.parse_args(argv)
    if args.responses and not args.output:
        parser.error('--responses requires --output')
    plan = snapshot(args.manifest, args.revision)
    if not args.output:
        print(json.dumps(public_summary(plan), indent=2)); return
    overlay = Overlay(args.output, plan)
    try:
        if args.responses:
            records, _ = read(args.responses)
            keys = [(r['cell'], r['attempt_id']) for r in records]
            if len(keys) != len(set(keys)) or len({r['cell'] for r in records}) != len(records):
                raise ValueError('duplicate responses in import')
            for record in records:
                overlay.import_response(record)
        print(json.dumps(overlay.report(), indent=2))
    finally:
        overlay.close()


if __name__ == '__main__':
    main()
