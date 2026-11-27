"""Plan all saved cohort answers, or analyze retained responses, without a transport."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pacbench import harness
from pacbench.context_isolation import memory_delivery
from pacbench.corpus.audit import audit_prepared
from pacbench.corpus.contract import (
    ContractClaim,
    _dialogue,
    load_contract,
    shared_context_review_prompt,
)
from pacbench.corpus.evaluation import contract_evidence
from pacbench.corpus.prepare import resolve_spec
from pacbench.corpus.r2 import DAY, LINEUP_ADMISSION
from pacbench.corpus.r2 import DAY_AT as AT
from pacbench.corpus.r2 import DAY_BASIS as BASIS
from pacbench.corpus.r2 import DAY_DOCUMENT as DOCUMENT
from pacbench.corpus.r2 import DAY_SPEC as SPEC
from pacbench.corpus.r2 import pizza_day_inputs as inputs
from pacbench.corpus.review import VERSION, review_prompt
from pacbench.corpus.sources import load_bundle, messages_for
from pacbench.evidence_store import binding_digest, file_sha, read_json
from pacbench.evidence_store import file_binding as binding
from pacbench.gateway_reuse import atomic
from pacbench.harness import TurnResult
from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.judging import retained as response_journal
from pacbench.judging import retained_week as retained
from pacbench.judging.grade import (
    CONTEXT_JUDGE_PROTOCOL,
    DATE_SCOPED_JUDGE_PROTOCOL,
    DIAGNOSTIC_VERSION,
    LEGACY_JUDGE_PROTOCOL,
    RETRY_GUIDANCE,
    VERDICTS,
    BenchmarkContractError,
    Claim,
    grade_semantic,
    judge_prompt,
    validate_diagnostic,
)
from pacbench.judging.retained_scope import read_canary_binding
from pacbench.model_calls import parse_chat_stream
from pacbench.openrouter_route import PROFILES
from pacbench.receipt_costs import read_attempts
from pacbench.workflows.grading import retained_grade
from pacbench.workflows.progress import WorkflowProgress, _turn
from pacbench.workflows.progress import workflow_fingerprint as fingerprint
from pacbench.workflows.recording import progress_metrics, receipt_summary
from pacbench.workflows.spec import instant
from pacbench.workflows.turn_evidence import audit_transport

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

# These revision names freeze the rubric and parser independently of the
# model and route declared in a verified response journal.
REVISIONS.update({
    revision.removeprefix('luna-'): {key: rule[key] for key in ('protocol', 'supplement')}
    for revision, rule in list(REVISIONS.items())
})

IMPLEMENTATION_FILES = {
    'planner_sha256': 'overlays.py',
    'input_adapter_sha256': 'retained_week.py',
    'scorer_sha256': 'grade.py',
    'response_adapter_sha256': 'retained.py',
}


@dataclass(frozen=True)
class ArmInput:
    arm: str
    provider: str
    path: Path
    sha256: str
    projection: str | None
    expected_admission: str | None


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
        arm = row.get('arm', provider)
        target = (path.parent / row['manifest']).absolute()
        expected = row['sha256']
        if (any(not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', name)
                for name in (arm, provider)) or arm in seen or target.resolve() in paths):
            raise ValueError('duplicate or invalid arm/run manifest')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('cohort arm requires the original manifest SHA-256')
        seen.add(arm)
        paths.add(target.resolve())
        result.append(ArmInput(arm, provider, target, expected, row.get('input_projection'),
                               row.get('ingestion_protocol')))
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


def _checkpoint_arm(manifest, entry, scope_sha, projection):
    meta, sha = retained.read_json(manifest)
    identity = meta['identity']
    key = meta['run_key']
    directory = manifest.parent
    if (fingerprint(identity) != key or identity['condition']['provider'] != entry.provider
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
        journal_path = directory/'workflow.jsonl'
        journal = None
        if identity.get('protocol') == retained.DOCUMENT_PROGRESS and journal_path.is_file():
            journal = retained.journal(journal_path)
        documents, binding = retained.verify_progress(
            progress, identity, turns, directory, scope_sha, projection=projection,
            events=journal[0] if journal is not None else (), expected_admission=entry.expected_admission)
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
        answered = [turn for turn in turns if turn.id in saved or turn.id in verified_pending]
        if binding['protocol'] == retained.MEMCAL_PROGRESS:
            provenance = retained.memcal_readbacks(progress, documents, answered=answered)
        elif journal is not None:
            events, journal_sha, torn = journal
            retained.verify_journal_header(events, turns, identity['condition'],
                                           identity['spec_sha256'], identity['source_basis'], identity['canary'])
            provenance = retained.document_readbacks(events, documents, turns, answered=answered, complete=sealed)
            binding.update(journal_sha256=journal_sha, journal_torn_tail=torn)
        else:
            provenance = {'kind': 'document-readbacks-unavailable', 'ready_documents': None,
                          'ready_releases': None, 'missing_evidence': str(journal_path)}
        return retained.RetainedArm(documents, turns, identity['condition'], binding, provenance, saved,
                                    originals, sealed, verified_pending)


def _arm(entry):
    provider, manifest, expected_sha = entry.provider, entry.path, entry.sha256
    if manifest.name == 'workflow.jsonl':
        directory = manifest.parent
        if (directory/'run.json').exists() or (directory/'workflow.sqlite3').exists():
            raise ValueError('journal belongs to a checkpoint store; select the original run.json or workflow-scope.json')
        events, sha, torn = retained.journal(manifest)
        if sha != expected_sha:
            raise ValueError('stale workflow journal digest')
        data = retained.legacy_journal(events, sha, torn, projection=entry.projection,
                                       expected_admission=entry.expected_admission)
        if data.condition['provider'] != provider:
            raise ValueError('legacy journal provider differs from the cohort')
        return directory, data
    meta, sha = retained.read_json(manifest)
    if sha != expected_sha:
        raise ValueError('stale run manifest digest')
    if provider == 'honcho' and manifest.name == 'workflow-scope.json' and meta.get('week_schedule'):
        root = manifest.parent
        return root, retained.honcho_checkpoint(root, meta, sha, entry.expected_admission, entry.projection)
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
        projection = retained.select_projection(entry.projection, meta.get('input_projection'))
        data = _checkpoint_arm(directory/'run.json', entry, scope_sha, projection)
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
        data = _checkpoint_arm(manifest, entry, None, entry.projection)
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


def _make_items(entry, data, revision):
    binding, provenance, saved = data.binding, data.source_provenance, data.answers
    items, previous, native_sessions = [], {}, {}
    completed = original_graded = 0
    gateways = set()
    supplement = REVISIONS[revision]['supplement']
    collections = {row['cutoff']: row for row in provenance.get('collections', [])}
    collection_input = provenance['kind'] in ('native-connector-collection', 'legacy-native-connector-collection')
    for turn in data.turns:
        if turn.id not in saved:
            continue
        surface, activity = saved[turn.id]
        chat = 'one-continuing-hermes-session' if binding.get('single_native_session') else turn.session
        options, cutoff = retained.verify_surface(turn, surface, previous.get(chat), data.documents,
                                                  qualified_memcal='memcal_product' in binding)
        sid = surface['session_id']
        gateways.add(digest(surface['gateway_identity']))
        if data.condition['state'] == 'continuing-account' and len(gateways) > 1:
            raise ValueError('saved continuing account changed its warm gateway between chats')
        if native_sessions.setdefault(sid, chat) != chat:
            raise ValueError('native session was reused for a different logical chat')
        previous[chat] = surface
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
        items.append({'id': entry.arm + '/' + turn.id, 'arm': entry.arm, 'provider': entry.provider, 'saved': frozen,
                      'input_sha256': digest(frozen), 'options': options, 'prompt': prompt,
                      'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
    return items, {
        'state': 'verified-retained-inputs', 'provider': entry.provider,
        'expected_turns': len(data.turns), 'completed_turns': len(saved),
        'expected_criteria': sum(len(t.claims) for t in data.turns), 'completed_criteria': completed,
        'original_graded_criteria': original_graded, 'judge_requests_planned': len(items),
        'missing_turns': [t.id for t in data.turns if t.id not in saved],
        'verified_pending_completion': data.verified_pending_completion, 'execution_sealed': data.execution_sealed,
        'original_condition': data.condition, 'binding': binding, 'source_provenance': provenance,
        'observed_native_sessions': len(native_sessions),
    }


def snapshot(path, revision, *, verify_all=False):
    if revision not in REVISIONS:
        raise ValueError('unregistered judge/rubric revision')
    runs, manifest_sha = discover(path)
    items, coverage, roots = [], {}, []
    run_keys = set()
    for entry in runs:
        try:
            directory, data = _arm(entry)
            binding = data.binding
            key = binding.get('run_key') or binding['journal_sha256']
            if key in run_keys:
                raise ValueError('duplicate retained run identity')
            arm_items, report = _make_items(entry, data, revision)
        except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
            if not verify_all:
                raise
            coverage[entry.arm] = {'state': 'unavailable-or-invalid', 'manifest': str(entry.path),
                                  'reason': str(exc) if isinstance(exc, (ValueError, OSError)) else type(exc).__name__}
            continue
        run_keys.add(key)
        roots.append(str(directory.parent if directory.name == 'workflow' else directory))
        items.extend(arm_items)
        coverage[entry.arm] = report
    registry = dict(REVISIONS[revision])
    registry.update({field: hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
                     for field, filename in IMPLEMENTATION_FILES.items()})
    plan = {'schema': 'pacbench-cohort-regrade-plan-v1', 'revision': revision, 'judge': registry,
            'manifest_sha256': manifest_sha, 'source_roots': roots, 'coverage': coverage, 'items': items,
            'dispatch_enabled': False}
    plan['plan_id'] = digest(plan)
    return plan


def replay_plan(database, current):
    """Rebind a freshly verified plan to the exact plan archived in an overlay."""
    database = Path(database).absolute()
    if database != database.resolve() or not database.is_file():
        raise ValueError('frozen plan requires the original, non-symlinked overlay database')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT plan FROM binding WHERE id=1').fetchone()
    if row is None:
        raise ValueError('overlay has no frozen plan')
    archived = response_journal._object(row[0])
    if set(archived) == {'plan', 'selected_judge'}:
        archived = archived['plan']
    if not isinstance(archived, dict):
        raise ValueError('invalid archived frozen plan')
    plan_id = archived['plan_id']
    if digest({key: value for key, value in archived.items() if key != 'plan_id'}) != plan_id:
        raise ValueError('frozen plan ID does not match its archived contents')
    implementation = {key: archived['judge'][key] for key in IMPLEMENTATION_FILES}
    if any(not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)
           for sha in implementation.values()):
        raise ValueError('invalid frozen plan implementation fingerprints')
    if any(implementation[field] != current['judge'][field]
           for field in ('scorer_sha256', 'response_adapter_sha256')):
        raise ValueError('current scorer or response adapter differs from the frozen plan')
    # Keep the current cohort order for reports after reinstating the original planner and reader fingerprints.
    rebuilt = {**current, 'judge': {**current['judge'], **implementation}}
    if digest({key: value for key, value in rebuilt.items() if key != 'plan_id'}) != plan_id:
        raise ValueError('current retained inputs or judge revision differ from the frozen plan')
    return {**rebuilt, 'plan_id': plan_id}


def public_summary(plan):
    return {k: plan[k] for k in ('plan_id', 'revision', 'judge', 'coverage', 'dispatch_enabled')}


def condition_basis(coverage):
    source = coverage['binding']
    condition = coverage['original_condition']
    fields = ('input_projection', 'release_clock', 'dataset_sha256', 'ordered_questions_sha256',
              'document_schedule_sha256', 'canary')
    setup = ('state', 'track', 'frontend', 'model', 'model_route', 'gateway_patch')
    return {**{key: source.get(key) for key in fields}, **{key: condition.get(key) for key in setup},
            'session_layout': ('single-continuing-chat' if source.get('single_native_session')
                               else 'authored-chats')}


class Overlay:
    """Separate durable offline overlay. Claimed attempts never become eligible again."""
    def __init__(self, output, plan, *, journal=None):
        self.plan = plan
        self.journal = journal
        self.directory = Path(output).absolute()
        if self.directory != self.directory.resolve():
            raise ValueError('symlinked output')
        if any(self.directory == Path(r) or Path(r) in self.directory.parents for r in plan['source_roots']):
            raise ValueError('output must be outside original run directories')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        database = self.directory/'overlay.sqlite3'
        if database != database.resolve() or (database.exists() and database.stat().st_nlink != 1):
            raise ValueError('linked overlay database')
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), plan TEXT);'
            'CREATE TABLE IF NOT EXISTS attempts (cell TEXT PRIMARY KEY, attempt TEXT UNIQUE, response TEXT, result TEXT);')
        encoded = json.dumps(plan if journal is None else {'plan': plan, 'selected_judge': journal.provenance},
                             sort_keys=True)
        self.db.execute('INSERT OR IGNORE INTO binding VALUES (1,?)', (encoded,))
        if self.db.execute('SELECT plan FROM binding').fetchone()[0] != encoded:
            self.db.close()
            raise ValueError('stale inputs, revision or judge recording; use a new versioned output directory')

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

    def import_journal(self):
        """Import the prevalidated batch in a single transaction."""
        if self.journal is None:
            raise ValueError('no validated judge journal selected')
        rows = [(r.cell, r.attempt, r.response, json.dumps(r.result, sort_keys=True))
                for r in self.journal.verdicts]
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior = self.db.execute('SELECT cell,attempt,response,result FROM attempts').fetchall()
            if prior:
                if set(prior) != set(rows):
                    raise ValueError('selected judge overlay conflicts with its original imported recording')
            else:
                self.db.executemany('INSERT INTO attempts VALUES (?,?,?,?)', rows)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise


    def report(self):
        attempts = dict(self.db.execute('SELECT cell,result FROM attempts'))
        states, deltas = {}, []
        arms = {arm: {
            'provider': row['provider'], 'condition': row['original_condition'],
            'expected_criteria': row['expected_criteria'], 'original_graded_criteria': 0,
            'original_correct': 0, 'regraded_criteria': 0, 'regrade_correct': 0,
            'compared_criteria': 0, 'paired_original_correct': 0, 'paired_regrade_correct': 0,
            'judge_states': {},
        } for arm, row in self.plan['coverage'].items()}
        for item in self.plan['items']:
            cell = item['id']
            parsed = json.loads(attempts[cell]) if attempts.get(cell) else None
            states[cell] = parsed['state'] if parsed else 'ambiguous-attempt' if cell in attempts else 'unsent'
            old = {c['id']: c['verdict'] for c in item['saved']['original']['criteria'] or []}
            new = {c['id']: c['verdict'] for c in (parsed or {}).get('criteria') or []}
            arm = arms[item['arm']]
            judge_states = arm['judge_states']
            judge_states[states[cell]] = judge_states.get(states[cell], 0) + 1
            for claim in item['saved']['turn']['claims']:
                claim_id = claim['id']
                original, regrade = old.get(claim_id), new.get(claim_id)
                arm['original_graded_criteria'] += original is not None
                arm['original_correct'] += original == 'correct'
                arm['regraded_criteria'] += regrade is not None
                arm['regrade_correct'] += regrade == 'correct'
                delta = None
                if original is not None and regrade is not None:
                    arm['compared_criteria'] += 1
                    arm['paired_original_correct'] += original == 'correct'
                    arm['paired_regrade_correct'] += regrade == 'correct'
                    delta = int(regrade == 'correct') - int(original == 'correct')
                deltas.append({'cell': cell, 'criterion': claim_id, 'original': original,
                               'regrade': regrade, 'credit_delta': delta})

        conditions = {}
        for arm, row in arms.items():
            coverage = self.plan['coverage'][arm]
            basis = condition_basis(coverage)
            basis_complete = all(value is not None for value in basis.values())
            # Older recordings can lack setup details. Never group unknown conditions.
            group_id = digest(basis if basis_complete else [arm, basis])
            group = conditions.setdefault(group_id, {**basis, 'comparison_basis_complete': basis_complete,
                                                    'arms': [], 'fully_regraded_arms': []})
            group['arms'].append(arm)
            if (row['regraded_criteria'] == row['expected_criteria'] > 0
                    and coverage['completed_turns'] == coverage['expected_turns']
                    and not coverage['verified_pending_completion']
                    and (coverage['execution_sealed'] or coverage['binding'].get('journal_completed'))
                    and not coverage['source_provenance'].get('missing_evidence')):
                group['fully_regraded_arms'].append(arm)
            row['credit_delta'] = (row['paired_regrade_correct'] - row['paired_original_correct']
                                   if row['compared_criteria'] else None)
            row['original_correct'] = row['original_correct'] if row['original_graded_criteria'] else None
            row['regrade_correct'] = row['regrade_correct'] if row['regraded_criteria'] else None
            row['original_ungraded_criteria'] = row['expected_criteria'] - row['original_graded_criteria']
            row['regrade_ungraded_criteria'] = row['expected_criteria'] - row['regraded_criteria']

        result = {'plan_id': self.plan['plan_id'], 'revision': self.plan['revision'],
                  'coverage': self.plan['coverage'], 'states': states,
                  'conditions': list(conditions.values()), 'criterion_deltas': deltas,
                  'receipt_verified': False}
        if self.journal is None:
            result.update(providers=arms, provenance='offline analysis of supplied responses')
        else:
            result.update(schema='pacbench-cohort-regrade-comparison-v1', arms=arms,
                          provenance=self.journal.provenance)
        return result


def write_report(path, result):
    """Atomically publish the same immutable comparison on exact replay."""
    raw = (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.report-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                old, _ = retained.read_bytes(path)
                if old != raw:
                    raise ValueError('existing report differs from the selected judge recording')
        finally:
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='retained Week arm, workflow manifest or cohort manifest')
    parser.add_argument('--revision', required=True, choices=sorted(REVISIONS))
    parser.add_argument('--verify', action='store_true', help='check each declared arm and report missing or invalid inputs')
    parser.add_argument('--output', type=Path, help='separate versioned overlay; never writes original run')
    parser.add_argument('--frozen-plan', type=Path, help='replay the exact plan from a retained overlay.sqlite3')
    imports = parser.add_mutually_exclusive_group()
    imports.add_argument('--responses', type=Path, help='legacy offline observations without journal provenance')
    imports.add_argument('--response-journal', type=Path, help='import one complete result from a recorded JSONL journal')
    parser.add_argument('--judge', help='select one judge ID from the recorded journal')
    parser.add_argument('--journal-sha256', help='expected SHA-256 of the original recorded journal')
    args = parser.parse_args(argv)
    if (args.responses or args.response_journal) and not args.output:
        parser.error('response import requires --output')
    if args.verify and args.output:
        parser.error('--verify reports coverage without opening an overlay')
    if args.verify and args.frozen_plan:
        parser.error('--verify checks current coverage; --frozen-plan requires a fully verified plan')
    if args.responses and 'model' not in REVISIONS[args.revision]:
        parser.error('model-neutral results require a selected --response-journal')
    if args.response_journal:
        if not args.judge or not re.fullmatch(r'[0-9a-f]{64}', args.journal_sha256 or ''):
            parser.error('--response-journal requires --judge and --journal-sha256')
    elif args.judge is not None or args.journal_sha256 is not None:
        parser.error('--judge and --journal-sha256 require --response-journal')
    try:
        plan = snapshot(args.manifest, args.revision, verify_all=args.verify)
        if args.frozen_plan:
            plan = replay_plan(args.frozen_plan, plan)
        if not args.output:
            print(json.dumps(public_summary(plan), indent=2))
            return int(args.verify and any(row['state'] != 'verified-retained-inputs'
                                           for row in plan['coverage'].values()))
        recorded = (response_journal.load(args.response_journal, plan, args.judge, args.journal_sha256)
                    if args.response_journal else None)
        overlay = Overlay(args.output, plan, journal=recorded)
        try:
            if recorded:
                overlay.import_journal()
            elif args.responses:
                records, _ = read(args.responses)
                if len({r['cell'] for r in records}) != len(records):
                    raise ValueError('duplicate responses in import')
                for record in records:
                    overlay.import_response(record)
            result = overlay.report()
            if recorded:
                write_report(overlay.directory/'report.json', result)
            print(json.dumps(result, indent=2))
        finally:
            overlay.close()
    except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
        parser.error(str(exc))


def unsent_judge(progress, scope, budget):
    """Bind the original failed local admission to the same live grade claim.

    The direct transport fsyncs its prepared row before sending any HTTP bytes.
    Preserve the failed client record; a prepared row, even unsettled, forbids replay.
    """
    from pathlib import Path

    from pacbench.judging.grade import judge_prompt
    from pacbench.workflows.runner import judge_options

    root = Path(scope['root'])
    directory = progress.directory
    if (scope['provider'] != 'honcho' or scope['admission'] != LINEUP_ADMISSION or root/'workflow' != directory
            or not progress.execution_sealed or progress.grade_states() != {'claimed': 1}):
        raise ValueError('expected a sealed, ungraded native Honcho Pizza Day')
    turn, surface = progress.get_claimed_grade('pizza-plan')
    claim = progress.active_grade(turn.id)
    prompt = judge_prompt(turn.prompt, surface['answer'], turn.claims, **judge_options(turn, surface))
    path, = (directory/'model-calls').glob('*.json')
    original = binding(path)
    call = read_canary_binding(original)
    rows = budget.snapshot()
    if (call['error'] != 'OperationalError: disk I/O error' or call['status'] != 'failed'
            or call.get('response') or call.get('accounting_receipt') or call.get('direct_response')
            or call['request']['messages'] != [{'role':'user','content':prompt}]
            or progress.binding('judge-intent/'+turn.id+'/'+binding_digest(prompt)) != claim
            or any(p.is_file() for p in (directory/'judgments'/turn.id).rglob('*'))
            or not rows or any(r['state'] != 'settled' or r['accounting_role'] == 'judge'
                               or r['project'] == scope['projects']['honcho']['judge'] for r in rows)):
        raise ValueError('previous judge dispatch cannot be ruled out')
    return {'turn_id': turn.id, 'original': original, 'claim': claim,
            'previous_settled_ids': sorted(row['id'] for row in rows)}


def verified_result(root, preparation):
    _,doc,turn=inputs(preparation)
    directory=root/'workflow'
    scope=read_canary_binding(binding(root/'workflow-scope.json'))
    if (scope['admission']!=LINEUP_ADMISSION or scope['root']!=str(root) or scope['provider']!=root.name or
            scope['spec_sha256']!=SPEC or scope['frozen_inputs']!=BASIS):
        raise ValueError('incompatible published Day run')
    with WorkflowProgress(directory, scope['run_key'], read_only=True) as progress:
        actual,surface,_=progress.get_verified(turn.id)
        if (actual!=turn or not progress.execution_sealed or progress.last_cutoff()!=AT or
                surface['source_files']!=[doc.filename] or surface['conversation_times']!=[turn.at]):
            raise ValueError('published checkpoint not sealed')
        adopted=progress.binding('reopened-import')
        imported=read_canary_binding(read_canary_binding(adopted)['original']) if adopted else read_canary_binding(progress.get_effect('ingest',DAY))
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
        if not progress.complete:
            raise ValueError('full frozen result incomplete')
        metrics=progress_metrics(progress)
        rows=read_attempts(root / 'receipts.jsonl')
        ids=[x['request_id'] for x in surface['model_requests']]
        if len(set(ids))!=len(ids) or set(ids)!={r['id'] for r in rows if r['accounting_role']=='hermes'}:
            raise ValueError('actual driver wire and journal differ')
        metrics['turns'][0]['driver_attempt_coverage']=dict(coverage='complete',observed_requests=len(ids),reused=[],missing=[])
        initial=root/'result.json'
        old=json.loads(initial.read_text()) if initial.exists() else {}
        end=max(datetime.fromisoformat(r['finished']) for r in rows if r['finished'])
        start=datetime.fromisoformat(scope['prepaid']['request_started'])
        return dict(state='complete',provider=root.name,condition=LINEUP_ADMISSION,session=surface['session_id'],answer=surface['answer'],
                    native_import_latency_s=imported['latency_s'],import_count=len(doc.source_ids),source_sha256=DOCUMENT,
                    elapsed_s=old.get('elapsed_s'),credit_check_to_last_settled_s=(end-start).total_seconds(),
                    metrics=metrics,billing=receipt_summary(root,rows,scope['run_id']),judgment=binding(directory/'judgments'/turn.id/'judgment.json'))


def review_packet(root: Path) -> dict:
    root = Path(root)
    read = lambda name: json.loads((root / name).read_text())
    summary = read('summary.json')
    bundle = load_bundle(root / 'bundle.json', summary['bundle_hash'])
    if len(bundle['lives']) != 1:
        raise ValueError('Expected exactly one historical source life')
    life_id = next(iter(bundle['lives']))
    messages = messages_for(bundle, life_id)
    ids = [m['source_id'] for m in messages]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Historical source IDs must be present and unique')
    contract = load_contract(root / 'contract.json')
    if contract.contract_hash != summary['contract_hash']:
        raise ValueError('Historical contract differs from summary')
    if any(not c.evidence or set(c.evidence) - set(ids) for c in contract.claims):
        raise ValueError('Historical claims cite missing source IDs')
    original = read('spec.json')
    resolved_old = read('resolved-spec.json')
    target = next(s for s in resolved_old['scenes'] if s['id'] == resolved_old['test_scene_id'])
    # This is a proposed current check, not a reviewed or frozen contract.
    authored = dict(original)
    for key in ('providers', 'prepare_only', 'exposure_context', 'exposure_delivery', 'maintenance_at'):
        authored.pop(key, None)
    authored['checks'] = [{'id': 'pizza-plan', 'at': resolved_old['question_at'],
                           'prompt': contract.question, 'facts': target['facts']}]
    _, _, resolved = resolve_spec(authored, root)
    check = resolved['checks'][0]
    available = contract_evidence(messages, resolved['checks'], check, resolved['evaluation_context'])
    claims = tuple(ContractClaim(c.id, c.statement, tuple(c.evidence)) for c in contract.claims)
    contract_prompt = shared_context_review_prompt(
        claims=claims, author_facts=tuple(check['facts']), dialogue=_dialogue(available),
        question_at=check['at'], user_question=check['prompt'], test_intent=check['prompt'])
    return {
        'state': 'blocked',
        'purpose': 'offline review planning only; not a preparation or approval',
        'source_bundle_hash': summary['bundle_hash'],
        'historical_contract_hash': summary['contract_hash'],
        'historical_audit': audit_prepared(root),
        'source_messages': messages,
        'proposed_spec': authored,
        'compiled_spec': resolved,
        'proposed_claims': [vars(c) for c in claims],
        'fresh_review_inputs': {
            'combined_claim_and_question': {'prompt_format': 'shared-context-v2', 'prompt': contract_prompt},
            'corpus_realism': {'review_version': VERSION, 'prompt': review_prompt(messages, resolved, version=VERSION)},
        },
        'blockers': [
            'Historical scene records lack current source IDs; the current scene compiler generates different opaque message and thread IDs. Resolve source provenance and current audit compatibility before freezing.',
            'The proposed claims and question require a fresh independent combined review with a bound model receipt; historical reviews are not transferred.',
            'The frozen corpus and public questions require a fresh independent corpus realism review with a bound model receipt.',
            'Complete any separately required source-only semantic audit, then pass the actual preparation audit and export a week manifest before provider execution.',
        ],
    }


def dialogue_import(root: Path) -> tuple[dict, dict]:
    """Keep original text, compile new metadata, and require every fresh review."""
    root = Path(root)
    packet = review_packet(root)
    rows = json.loads((root/'scenes.json').read_text())['scenes']
    flattened = [(s, t) for row in rows for s, t in row['lines']]
    if flattened != [(m['sender'], m['text']) for m in packet['source_messages']]:
        raise ValueError('Archived scene dialogue differs from the hash-verified source bundle')
    spec = packet['proposed_spec']
    if len(rows) != len(spec['scenes']) or len({r['scene_id'] for r in rows}) != len(rows):
        raise ValueError('Historical scene inventory differs')
    for scene in spec['scenes']:
        row = next(r for r in rows if r['scene_id'] == scene['id'])
        scene['message_range'] = [len(row['lines']), len(row['lines'])]
        scene['participants'] = list(dict.fromkeys(s for s, _ in row['lines']))
    return spec, {'source_bundle_hash': packet['source_bundle_hash'],
                  'condition': 'historical dialogue text; current compiler identities/timing; fresh reviews',
                  'scenes': {r['scene_id']: r['lines'] for r in rows}}


def recover(folder,destination):
 folder=Path(folder);ready=folder/'answer-ready.json';cleanup=folder/'cleanup.json'
 r=json.loads(ready.read_text());c=json.loads(cleanup.read_text());t=TurnResult(**r['native_turn'])
 e=r['observation_clock'];d=e['descriptor'];home=Path(d['home']);sample=c.get('native_observation',{})
 if (t.hermes_gateway is not None or c.get('state')!='drained' or c.get('preclose') is not None or
     sample.get('pid')!=r['gateway_pid'] or sample.get('start_time')!=d['gateway_start_time'] or
     not sample.get('sessions') or not all(s.get('clean') for s in sample['sessions']) or
     [m['content'] for m in r['answer_visible_records'] if m['role']=='user']!=[t.prompt]):
  raise GatewayLifecycleError('Closed answer recovery requires a complete independent drained session')
 delivery=memory_delivery(home,t.session_id,t.prompt,t.prefetch_context)
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 t.hermes_gateway={'version':'checkpoint-session-v1','pid':r['gateway_pid'],'start_time':d['gateway_start_time'],
  'clock_evidence':e,'receipt':str(folder),'native_drain':'complete','fresh_session':True,
  'authored_checkpoint_resume':False,'native_result_token_scope':'turn','prior_user_count':0,'new_user_count':1,
  'memory_delivery':delivery,'close_condition':None,'preclose':None,'answer_ready_sha256':sha(ready),
  'cleanup_sha256':sha(cleanup),'answer_visible_capture':'complete' if delivery['state'] in {'confirmed','empty'} else 'unknown'}
 t.lifecycle_timing={'protocol':'turn-lifecycle-timing-v1','runner_wall_s':r['answer_ready_latency_s']+c['post_answer_cleanup_s'],
  'answer_ready_latency_s':r['answer_ready_latency_s'],'post_answer_cleanup_s':c['post_answer_cleanup_s'],
  'gateway_persisted':True,'preclose_wait_s':0}
 audit_transport(t)
 atomic(Path(destination),vars(t))
 return str(destination)


PROFILE = 'luna-bedrock-global'


MODEL = PROFILES[PROFILE]['model']


def _matches_judge_receipt(row, cell):
    return (row['workflow_slot']['question_id'] == cell['id']
            and row['request_sha256'] == cell['request_sha256'] and row['route_profile'] == PROFILE
            and row['dispatch_context']['attempt_number'] == 1 and not row.get('retry_of'))


def _verified_response(root, row, cell, text, seen):
    generation = row.get('upstream_request_id')
    if (not _matches_judge_receipt(row, cell) or row.get('state') != 'settled' or row.get('http_status') != 200
            or not generation or generation in seen):
        raise RuntimeError('missing, duplicate or ambiguous settled judge receipt')
    billing = receipt_summary(root, [row], CAMPAIGN.name)
    if billing['generations'] != 1 or billing['missing_attempt_ids']:
        raise RuntimeError('saved-answer cell has no unique settled judge receipt')
    verify_frozen_judge_stream(root, row)
    response, _ = parse_chat_stream((root/row['direct_body_evidence']['relative_path']).read_bytes(), MODEL)
    if (response['choices'][0]['message']['content'] != text
            or Decimal(str(response['usage']['cost'])) != Decimal(billing['complete_cost_usd'])):
        raise RuntimeError('saved-answer response differs from its settled wire body')
    seen.add(generation)
    return Decimal(billing['complete_cost_usd'])


DEVELOPMENT_LIVE_PROFILE = 'luna-bedrock-global'


CAMPAIGN = Path('/workspace/scratch/pizza-v2-live-20261121')


def verify_frozen_judge_stream(root, row):
    from pacbench.model_calls import parse_chat_stream
    from pacbench.openrouter_route import PROFILES
    # ModelClient may stop reading at the SSE terminator without a socket EOF.
    # Authenticate the saved bytes and require the normal complete-stream parser.
    item = row.get('direct_body_evidence', {})
    path = root/item.get('relative_path', '')
    if path != path.resolve() or path.parent != root/'response-bodies':
        raise ValueError('Missing or foreign A/B response evidence')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
        raise ValueError('A/B response evidence changed')
    served, _ = parse_chat_stream(raw, PROFILES[DEVELOPMENT_LIVE_PROFILE]['model'])
    if served.get('id') != row['upstream_request_id']:
        raise ValueError('A/B response and billing generation differ')
    if served.get('provider') != 'Amazon Bedrock' or row.get('served_model') != PROFILES[DEVELOPMENT_LIVE_PROFILE]['model']:
        raise ValueError('A/B receipt does not confirm the pinned served model and provider')
    return served


def _no_symlinks(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink(): raise ValueError(f'symlink is not allowed: {part}')
    return path


def _safe(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe review path')
    path = root / relative
    _no_symlinks(path)
    return path


def _overlay_tree(overlay):
    _no_symlinks(overlay)
    for current, dirs, files in os.walk(overlay, followlinks=False):
        for name in dirs + files:
            if (Path(current) / name).is_symlink():
                raise ValueError('symlink in review overlay')


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def _args(doc):
    claims = tuple(Claim(c['id'], c['description'], tuple(c.get('evidence', ()))) for c in doc['claims'])
    kwargs = dict(conversation=doc.get('conversation', ()), assess_recovery='Also return "recovery"' in doc['prompt'],
                  assess_interaction='Also return "interaction"' in doc['prompt'],
                  diagnostic_inputs=doc.get('diagnostic_inputs'),
                  diagnostic_version=doc.get('diagnostic_protocol', 'answer-visible-evidence-v1'))
    return claims, kwargs


def _rejudge_original(root, rel):
    path = _safe(root, rel)
    doc = read_json(path)
    turn = read_json(_safe(root, path.parent.relative_to(root) / 'turn.json'))
    if doc['question'] != turn['prompt'] or doc['answer'] != turn['answer']:
        raise ValueError(f'{rel}: saved answer differs from judgment')
    claims, kwargs = _args(doc)
    protocol = doc.get('judge_protocol', LEGACY_JUDGE_PROTOCOL)
    suffix = doc.get('prompt_suffix', '')
    if suffix not in ('', RETRY_GUIDANCE) or (protocol == LEGACY_JUDGE_PROTOCOL and suffix):
        raise ValueError(f'{rel}: invalid original prompt suffix')
    if judge_prompt(doc['question'], doc['answer'], claims, judge_protocol=protocol, **kwargs) + suffix != doc['prompt']:
        raise ValueError(f'{rel}: original prompt mismatch')
    call_path = _safe(root, Path('model-calls') / Path(doc['model_call']).name)
    call = read_json(call_path)
    if call.get('status') != 'ok' or call.get('text') != doc['response'] or call.get('request', {}).get('messages') != [{'role':'user','content':doc['prompt']}]:
        raise ValueError(f'{rel}: original model call mismatch')
    if doc.get('model_call_hash') is not None:
        digest = hashlib.sha256(json.dumps(call, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != doc['model_call_hash']: raise ValueError(f'{rel}: original model call hash mismatch')
    return doc, call_path


def _manifest(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    manifest = read_json(_safe(overlay, 'manifest.json'))
    if manifest.get('version') != 'rejudge-overlay-v1' or manifest.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL:
        raise ValueError('unsupported overlay')
    if manifest.get('source_root') != '../..' or overlay.parent.name != 'reviews':
        raise ValueError('overlay source must be its containing run')
    root = _no_symlinks(overlay.parent.parent)
    expected = sorted(p.relative_to(root).as_posix() for p in (root/'repeat-1').glob('*/turns/*/judgment.json'))
    expected += sorted(p.relative_to(root).as_posix() for p in (root/'repeat-1').glob('*/turns/*/followups/**/judgment.json'))
    if [c['judgment'] for c in manifest['cases']] != expected: raise ValueError('saved judgment set changed')
    for case in manifest['cases']:
        doc, call = _rejudge_original(root, case['judgment'])
        path = _safe(root, case['judgment'])
        contract = _safe(root, Path('evaluations') / doc['checkpoint'] / 'contract.json')
        expected_files = {p.relative_to(root).as_posix() for p in (path, path.parent/'turn.json', contract, call)}
        if set(case['files']) != expected_files: raise ValueError('invalid original file inventory')
        for path, digest in case['files'].items():
            if file_sha(_safe(root, path)) != digest: raise ValueError(f'original file changed: {path}')
    return manifest, root


def _case_dir(overlay, rel):
    return overlay / 'cases' / Path(rel).parent.relative_to('repeat-1')


def audit_semantic_overlay(overlay):
    overlay = _no_symlinks(overlay)
    manifest, root = _manifest(overlay)
    for case in manifest['cases']:
        directory = _case_dir(overlay, case['judgment'])
        result = read_json(directory/'result.json')
        doc, _ = _rejudge_original(root, case['judgment'])
        claims, kwargs = _args(doc)
        if result.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or result.get('original_judgment') != case['judgment']:
            raise ValueError('result identity mismatch')
        first, second = directory/'attempt-1.json', directory/'attempt-2.json'
        attempts = [first] + ([second] if second.exists() else [])
        for i, path in enumerate(attempts):
            attempt = read_json(path)
            suffix = RETRY_GUIDANCE if i else ''
            expected = judge_prompt(doc['question'], doc['answer'], claims, judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs) + suffix
            if attempt.get('prompt') != expected or attempt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or attempt.get('prompt_suffix') != suffix:
                raise ValueError('attempt prompt mismatch')
            model_path = overlay/'model-calls'/Path(attempt['model_call']).name
            call = read_json(model_path)
            if call.get('status') != 'ok' or call.get('text') != attempt['response'] or call.get('request',{}).get('messages') != [{'role':'user','content':expected}]:
                raise ValueError('attempt model call mismatch')
            if i == 0 and len(attempts) == 2:
                try:
                    grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims, lambda _:attempt['response'], judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
                except BenchmarkContractError as exc:
                    raise ValueError('retry followed benchmark contract error') from exc
                except ValueError:
                    pass
                else: raise ValueError('retry followed a valid initial response')
        try:
            actual = grade_semantic(doc['checkpoint'], doc['question'], doc['answer'], claims,
                lambda _:read_json(attempts[-1])['response'], judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, **kwargs)
        except ValueError as exc:
            if (result.get('state') != 'ungraded' or result.get('error_type') != type(exc).__name__ or
                    not isinstance(exc, BenchmarkContractError) and len(attempts) != 2):
                raise ValueError('ungraded result mismatch') from exc
        else:
            if result.get('state') != 'graded' or asdict(actual) != result.get('grade'):
                raise ValueError('reviewed grade mismatch')
    return len(manifest['cases'])


def validate_published_audit(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    receipt = read_json(overlay/'audit.json')
    if (receipt.get('state') != 'pass' or receipt.get('version') != 'rejudge-overlay-v1' or
        receipt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL):
        raise ValueError('invalid overlay audit receipt')
    actual = {p.relative_to(overlay).as_posix():file_sha(p) for p in sorted(overlay.rglob('*.json'))
              if p.is_file() and p.name != 'audit.json'}
    if actual != receipt.get('files'): raise ValueError('overlay changed after audit')
    audit_controls(overlay)
    count = audit_semantic_overlay(overlay)
    if count != receipt.get('case_count'): raise ValueError('overlay case count mismatch')
    return count


def _control_specs():
    claim = Claim('day', 'The upcoming dinner is on Saturday.', ('source-1',))
    text = '〔E2〕 Sat Oct 24  "Dinner", 7pm — confirmed · with Ben'
    inputs = {'answer_visible':[{'id':'prefetch','text':text,'truncated':False}],
        'source_evidence':[{'id':'source-1','text':'Dinner on Saturday October 24.', 'truncated':False}],
        'source_scope':'partial: expected-claim citations only', 'missing_source_ids':[],
        'answer_visible_scope':'provider evidence returned to Hermes: captured prefetch and direct tool results only; user prompts, tool arguments and background traces excluded',
        'per_item_character_limit':16000}
    question = '[Simulated now: 2026-10-19T10:30:00-04:00.] When is upcoming dinner?'
    return [(name, question, answer, (claim,), inputs, expected) for name, answer, expected in [
        ('right_date', 'Saturday October 24.', 'correct'),
        ('past_same_weekday', 'Saturday October 17.', 'misrepresented')]]


def audit_controls(overlay):
    overlay = _no_symlinks(overlay)
    _overlay_tree(overlay)
    for name, question, answer, claims, inputs, expected in _control_specs():
        directory = overlay/'controls'/name
        result = read_json(directory/'result.json')
        paths = [directory/'attempt-1.json'] + ([directory/'attempt-2.json'] if (directory/'attempt-2.json').exists() else [])
        kwargs = dict(judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL, diagnostic_version=DIAGNOSTIC_VERSION, diagnostic_inputs=inputs)
        for i, path in enumerate(paths):
            attempt = read_json(path)
            suffix = RETRY_GUIDANCE if i else ''
            prompt = judge_prompt(question, answer, claims, **kwargs) + suffix
            call = read_json(overlay/'model-calls'/Path(attempt['model_call']).name)
            if (attempt.get('prompt') != prompt or attempt.get('prompt_suffix') != suffix or
                attempt.get('judge_protocol') != DATE_SCOPED_JUDGE_PROTOCOL or call.get('status') != 'ok' or
                call.get('text') != attempt.get('response') or
                call.get('request',{}).get('messages') != [{'role':'user','content':prompt}]):
                raise ValueError('control receipt mismatch')
            if i == 0 and len(paths) == 2:
                try: grade_semantic(name, question, answer, claims, lambda _:attempt['response'], **kwargs)
                except BenchmarkContractError as exc: raise ValueError('control retry followed benchmark contract error') from exc
                except ValueError: pass
                else: raise ValueError('control retry followed valid response')
        grade = grade_semantic(name, question, answer, claims, lambda _:read_json(paths[-1])['response'], **kwargs)
        bad = {'claims':[{'id':'day','status':'supported','reason':'Altered marker',
            'citations':[{'id':'prefetch','quote':'〈E2〉'}]}], 'extra_assertions':[]}
        if (not result.get('passed') or not result.get('invalid_quote_rejected') or asdict(grade) != result.get('grade') or
            grade.slots[0].verdict != expected or grade.evidence_diagnostic.get('state') != 'complete' or
            grade.evidence_diagnostic['claims'][0]['status'] != 'supported' or
            validate_diagnostic(bad, claims, answer, inputs, DIAGNOSTIC_VERSION)['state'] != 'ungraded'):
            raise ValueError(f'control failed: {name}')
    return True


def _read(path):
    return json.loads(path.read_text())


def normalize_native_continuation(arm, turn):
    """Fix only a demonstrated observer rejection of a pinned native retry."""
    from pacbench.context_isolation import NATIVE_QUESTION as NATIVE
    from pacbench.context_isolation import delivery
    from pacbench.context_isolation import input_rows as rows
    from pacbench.provider_clock import audit_witness
    if (turn.policy_blocks!=1 or (turn.clock_context or {}).get('state')!='fail' or
            (turn.clock_context or {}).get('reason')!='Scenario clock or natural question differs at HTTP boundary' or
            (turn.context_isolation or {}).get('state')!='pass'):
        raise ValueError('Not the native network-continuation observer failure')
    gateway=turn.hermes_gateway or {}
    identifiers=[r['request_id'] for r in rows(arm/'api-inputs.jsonl')
                 if gateway.get('turn_nonce') and r.get('turn_nonce')==gateway['turn_nonce']]
    observed=delivery(arm,turn,identifiers=identifiers,protocol=NATIVE)
    if observed['state']!='pass' or not observed.get('native_network_continuation_api_input_ids'):
        raise ValueError('Pinned native continuation and original actor inputs are not independently verified')
    audit_witness(arm,vars(turn),_read(arm/'provider-clock.json'))
    policy=_read(arm/'tool-policy.json')
    turn.policy_blocks=0
    harness.enforce_provider_tools(turn,{'PACBENCH_PROVIDER_ONLY':'1',
        'PACBENCH_TOOL_ALLOWLIST':','.join(policy['allowed_tools']),
        'PACBENCH_SOURCE_ARGUMENT_RULES':json.dumps(policy['source_argument_rules'])})
    if turn.policy_blocks:raise ValueError('Actual tool policy violation cannot be repaired')
    turn.clock_context=observed
    return turn

def day_review_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read an archived Pizza Day preparation.')
    parser.add_argument('root', type=Path)
    parser.add_argument('--source-only', action='store_true',
                        help='emit only original sources and public question times for an independent audit')
    args = parser.parse_args(argv)
    packet = review_packet(args.root)
    if args.source_only:
        packet = {'source_bundle_hash': packet['source_bundle_hash'],
                  'source_messages': packet['source_messages'],
                  'public_questions': [{k: c[k] for k in ('at', 'prompt')}
                                       for c in packet['compiled_spec']['checks']]}
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
