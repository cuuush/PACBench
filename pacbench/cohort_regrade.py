"""Plan all saved cohort answers, or analyze retained responses, without a transport."""
import argparse
from dataclasses import asdict
import re
import hashlib
import json
from pathlib import Path
import sqlite3

from .semantic_grade import Claim, CONTEXT_JUDGE_PROTOCOL, VERDICTS, judge_prompt, grade_semantic
from . import cohort_regrade_retained as retained
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
    raw, sha = retained.read_bytes(path)
    return json.loads(raw), sha


def _native_input(directory):
    if (directory/'run.json').is_file() or (directory/'workflow.sqlite3').is_file():
        return directory/'run.json'
    if (directory/'workflow.jsonl').is_file():
        return directory/'workflow.jsonl'
    return directory/'run.json'


def _entry_path(path):
    path = Path(path).absolute()
    if path.is_dir():
        if path != path.resolve():
            raise ValueError('symlinked run directory')
        if (path/'workflow-scope.json').is_file():
            return path/'workflow-scope.json'
        return _native_input(path/'workflow' if (path/'workflow').is_dir() else path)
    return path


def discover(path):
    """Discover arms from declarations, never from a count or recursive directory scan."""
    path = _entry_path(path)
    if path.name == 'workflow.jsonl':
        events, sha, _ = retained.journal(path)
        provider = events[1]['condition']['provider']
        rows = [{'provider': provider, 'manifest': path.name, 'sha256': sha}]
    else:
        meta, sha = retained.read_json(path)
        if meta.get('schema') == 'pacbench-regrade-cohort-v1':
            rows = meta['runs']
        else:
            if meta.get('schema') == 'pacbench/pizza-week-results-v1':
                raise ValueError('public aggregate only; need each arm’s retained run.json, SQLite store and artifacts, or original workflow.jsonl')
            if 'run_key' in meta:
                provider = meta['identity']['condition']['provider']
            elif path.name == 'workflow-scope.json' and meta.get('protocol') == retained.WEEK:
                provider = meta['provider']
            elif path.name == 'result.json' and type(meta.get('canary')) is bool:
                provider = meta.get('provider', 'memcal' if 'scope' in meta else None)
            else:
                raise ValueError('unsupported retained run or cohort manifest format')
            rows = [{'provider': provider, 'manifest': path.name, 'sha256': sha}]
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty or invalid cohort')
    seen, paths = set(), set()
    result = []
    for row in rows:
        provider = row['provider']
        target = (path.parent / row['manifest']).absolute()
        expected = row['sha256']
        if (not isinstance(provider, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', provider)
                or provider in seen or target.resolve() in paths):
            raise ValueError('duplicate/invalid provider or run manifest')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('cohort arm requires the original manifest SHA-256')
        seen.add(provider)
        paths.add(target.resolve())
        result.append((provider, target, expected))
    return result, sha


def _original(turn, record):
    criteria = record['criteria']
    if (record['grade_state'] == 'graded') != (criteria is not None):
        raise ValueError('original grade state differs from its saved criteria')
    if criteria is not None and ([c['id'] for c in criteria] != [c.id for c in turn.claims]
            or not criteria or any(c['verdict'] not in VERDICTS or not isinstance(c['reason'], str)
                                   or not c['reason'].strip() for c in criteria)):
        raise ValueError('original grade criterion mismatch')
    return record


def _checkpoint_arm(manifest, provider, scope_sha):
    meta, sha = retained.read_json(manifest)
    identity = meta['identity']
    key = meta['run_key']
    directory = manifest.parent
    if (fingerprint(identity) != key or identity['condition']['provider'] != provider
            or meta.get('evidence_kind') != 'native' or identity.get('evidence_kind') != 'native'):
        raise ValueError('run identity/provider/evidence mismatch')
    database = directory/'workflow.sqlite3'
    if not database.is_file():
        raise FileNotFoundError(f'missing retained workflow SQLite database: {database}')
    if database != database.resolve():
        raise ValueError('symlinked progress database')
    with WorkflowProgress(directory, key, read_only=True) as progress:
        progress.db.execute('BEGIN')
        if progress.binding('identity') != identity or progress.binding('plan_registered') is not True:
            raise ValueError('unregistered or stale native plan')
        progress.audit_artifacts()
        rows = list(progress.db.execute('SELECT * FROM turns ORDER BY ordinal'))
        if [r['ordinal'] for r in rows] != list(range(len(rows))):
            raise ValueError('missing or noncontiguous planned turns')
        turns = [_turn(row['plan']) for row in rows]
        for row, turn in zip(rows, turns, strict=True):
            if ((row['id'], row['session'], row['measure'], row['criteria_count'])
                    != (turn.id, turn.session, turn.measure, len(turn.claims))
                    or len({c.id for c in turn.claims}) != len(turn.claims)):
                raise ValueError('duplicate/stale criterion or turn plan')
        documents, binding = retained.verify_progress(progress, identity, turns, directory, scope_sha)
        saved, originals = {}, {}
        verified_pending = []
        for row, turn in zip(rows, turns, strict=True):
            if not row['done']:
                if row['surface']:
                    verified_pending.append(turn.id)
                continue
            if not row['surface'] or not row['activity']:
                raise ValueError('completed turn lacks its saved answer or activity artifact')
            _, surface, activity = progress.get_verified(turn.id)
            saved[turn.id] = surface, activity
            originals[turn.id] = _original(turn, progress.grade_record(turn.id))
        sealed = progress.execution_sealed
        if list(saved) != [t.id for t in turns[:len(saved)]] or (sealed and len(saved) != len(turns)):
            raise ValueError('retained completed turns do not form their original ordered prefix')
        binding.update(run_manifest_sha256=sha, run_key=key)
        journal_path = directory/'workflow.jsonl'
        if binding['protocol'] == retained.MEMCAL_PROGRESS:
            provenance = retained.memcal_readbacks(progress, documents)
        elif journal_path.is_file():
            events, journal_sha, torn = retained.journal(journal_path)
            retained.verify_journal_header(events, turns, identity['condition'],
                                           identity['spec_sha256'], identity['source_basis'], identity['canary'])
            latest = turns[len(saved)-1].at if saved else None
            provenance = retained.document_readbacks(events, documents, turns, through=latest, complete=sealed)
            binding.update(journal_sha256=journal_sha, journal_torn_tail=torn)
        else:
            provenance = {'kind': 'document-readbacks-unavailable', 'ready_documents': None,
                          'ready_releases': None, 'missing_evidence': str(journal_path)}
        return retained.RetainedArm(documents, turns, identity['condition'], binding, provenance, saved,
                                    originals, sealed, verified_pending)


def _arm(provider, manifest, expected_sha):
    if manifest.name == 'workflow.jsonl':
        directory = manifest.parent
        if (directory/'run.json').exists() or (directory/'workflow.sqlite3').exists():
            raise ValueError('journal belongs to a checkpoint store; select the original run.json or workflow-scope.json')
        events, sha, torn = retained.journal(manifest)
        if sha != expected_sha:
            raise ValueError('stale workflow journal digest')
        data = retained.legacy_journal(events, sha, torn)
        if data.condition['provider'] != provider:
            raise ValueError('legacy journal provider differs from the cohort')
        return directory, data
    meta, sha = retained.read_json(manifest)
    if sha != expected_sha:
        raise ValueError('stale run manifest digest')
    scope_sha = None
    if manifest.name in ('workflow-scope.json', 'result.json'):
        directory = manifest.parent/'workflow'
        if manifest.name == 'workflow-scope.json':
            scope_sha = sha
        elif 'scope' in meta:
            bound = meta['scope']
            if bound['path'] != str(manifest.parent/'workflow-scope.json'):
                raise ValueError('result refers to a foreign workflow scope')
            scope_sha = bound['sha256']
        data = _checkpoint_arm(directory/'run.json', provider, scope_sha)
        if (meta.get('provider', data.condition['provider']) != provider
                or meta.get('canary') is not data.binding['canary']):
            raise ValueError('arm manifest provider or canary condition differs')
        if manifest.name == 'result.json':
            state = meta['state']
            full = state == 'live-week-complete'
            if full and (not data.execution_sealed or data.binding['canary']):
                raise ValueError('result claims a completed Week but its retained execution is unsealed or canary')
            data.binding.update(result_sha256=sha, original_result_state=state)
            declared = meta.get('coverage') or (meta.get('workflow') or {}).get('coverage')
            if declared:
                _check_result_coverage(declared, data)
    elif 'run_key' in meta:
        directory = manifest.parent
        data = _checkpoint_arm(manifest, provider, None)
        if data.binding['run_manifest_sha256'] != expected_sha:
            raise ValueError('run manifest changed while being read')
    else:
        raise ValueError('unsupported arm manifest')
    return directory, data


def _check_result_coverage(declared, data):
    """A supplied result can describe an older checkpoint. Do not combine it with newer grades."""
    grades = [c for row in data.originals.values() for c in row['criteria'] or []]
    expected = {'planned_turns': len(data.turns),
                'planned_criteria': sum(len(t.claims) for t in data.turns),
                'graded_criteria': len(grades),
                'correct': sum(c['verdict'] == 'correct' for c in grades) if grades else None}
    if (any(field in declared and declared[field] != value for field, value in expected.items())
            or 'verified_turns' in declared and declared['verified_turns'] not in (
                len(data.answers), len(data.answers) + len(data.verified_pending_completion))):
        raise ValueError('result coverage differs from the retained store; select run.json to inspect its current snapshot')


def _make_items(provider, data, revision):
    binding, provenance, saved = data.binding, data.source_provenance, data.answers
    items, previous, native_sessions = [], {}, {}
    completed = original_graded = 0
    supplement = REVISIONS[revision]['supplement']
    collections = {row['cutoff']: row for row in provenance.get('collections', [])}
    collection_input = provenance['kind'] in ('native-connector-collection', 'legacy-native-connector-collection')
    for turn in data.turns:
        if turn.id not in saved:
            continue
        surface, activity = saved[turn.id]
        options, cutoff = retained.verify_surface(turn, surface, previous.get(turn.session), data.documents,
                                                  qualified_memcal='memcal_product' in binding)
        sid = surface['session_id']
        if native_sessions.setdefault(sid, turn.session) != turn.session:
            raise ValueError('native session was reused for a different logical chat')
        previous[turn.session] = surface
        if cutoff and collection_input:
            collection = collections.get(cutoff)
            if collection is None or instant(collection['ready_at']) > instant(turn.at):
                raise ValueError('saved answer lacks original native collection coverage at its cutoff')
        original = _original(turn, data.originals.get(turn.id, {'grade_state': 'ungraded', 'criteria': None, 'failure': None}))
        completed += len(turn.claims)
        if original['criteria'] is not None:
            original_graded += len(original['criteria'])
        if not turn.claims:
            continue
        frozen = {'turn': asdict(turn), 'surface': surface, 'activity': activity, 'original': original,
                  'run_key': binding.get('run_key'), 'arm_binding_sha256': digest(binding),
                  'source_cutoff': cutoff}
        prompt = judge_prompt(turn.prompt, surface['answer'], turn.claims, **options)
        if supplement:
            prompt += '\n' + supplement
        items.append({'id': provider + '/' + turn.id, 'provider': provider, 'saved': frozen,
                      'input_sha256': digest(frozen), 'options': options, 'prompt': prompt,
                      'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
    return items, {
        'state': 'verified-retained-inputs',
        'expected_turns': len(data.turns), 'completed_turns': len(saved),
        'expected_criteria': sum(len(t.claims) for t in data.turns), 'completed_criteria': completed,
        'original_graded_criteria': original_graded, 'judge_requests_planned': len(items),
        'missing_turns': [t.id for t in data.turns if t.id not in saved],
        'verified_pending_completion': data.verified_pending_completion, 'execution_sealed': data.execution_sealed,
        'original_condition': data.condition, 'binding': binding, 'source_provenance': provenance,
    }


def snapshot(path, revision, *, verify_all=False):
    if revision not in REVISIONS:
        raise ValueError('unregistered judge/rubric revision')
    runs, manifest_sha = discover(path)
    items, coverage, roots = [], {}, []
    run_keys = set()
    for provider, manifest, expected_sha in runs:
        try:
            directory, data = _arm(provider, manifest, expected_sha)
            binding = data.binding
            key = binding.get('run_key') or binding['journal_sha256']
            if key in run_keys:
                raise ValueError('duplicate retained run identity')
            arm_items, report = _make_items(provider, data, revision)
        except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
            if not verify_all:
                raise
            coverage[provider] = {'state': 'unavailable-or-invalid', 'manifest': str(manifest),
                                  'reason': str(exc) if isinstance(exc, (ValueError, OSError)) else type(exc).__name__}
            continue
        run_keys.add(key)
        roots.append(str(directory.parent if directory.name == 'workflow' else directory))
        items.extend(arm_items)
        coverage[provider] = report
    registry = dict(REVISIONS[revision])
    registry['planner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    registry['input_adapter_sha256'] = hashlib.sha256(Path(retained.__file__).read_bytes()).hexdigest()
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
        update = self.db.execute('UPDATE attempts SET response=?, result=? WHERE cell=? AND response IS NULL',
                                 (record['response'], json.dumps(result), record['cell']))
        if update.rowcount == 0:
            winner = self.db.execute('SELECT attempt,response FROM attempts WHERE cell=?',
                                     (record['cell'],)).fetchone()
            if winner != (record['attempt_id'], record['response']):
                raise ValueError('attempt or immutable response mismatch')

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
    parser.add_argument('manifest', type=Path, help='retained Week arm, workflow manifest or cohort manifest')
    parser.add_argument('--revision', required=True, choices=sorted(REVISIONS))
    parser.add_argument('--verify', action='store_true', help='check each declared arm and report missing or invalid inputs')
    parser.add_argument('--output', type=Path, help='separate versioned overlay; never writes original run')
    parser.add_argument('--responses', type=Path, help='offline saved response records, not a dispatch request')
    args = parser.parse_args(argv)
    if args.responses and not args.output:
        parser.error('--responses requires --output')
    if args.verify and args.output:
        parser.error('--verify reports coverage without opening an overlay')
    try:
        plan = snapshot(args.manifest, args.revision, verify_all=args.verify)
    except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
        parser.error(str(exc))
    if not args.output:
        print(json.dumps(public_summary(plan), indent=2))
        return int(args.verify and any(row['state'] != 'verified-retained-inputs'
                                       for row in plan['coverage'].values()))
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
    raise SystemExit(main())
