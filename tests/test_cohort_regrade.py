"""Synthetic answers in the real retained Week formats. No provider or transport."""
from collections import defaultdict
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pacbench.canary_capability import binding, digest as wire_digest
from pacbench.cohort_regrade import Overlay, main, snapshot
from pacbench.cohort_regrade_retained import ADMISSIONS, DOCUMENT_PROGRESS, MEMCAL_PROGRESS, WEEK
from pacbench.workflow_capability import _memcal_schedule
from pacbench.workflow_memcal_tools import TOOLS, manifest as memcal_product
from pacbench.workflow_progress import WorkflowProgress
from pacbench.workflow_source_plan import SourcePlan
from pacbench.workflow_spec import SPEC, compile_week, instant
from pacbench.workflow_stream import fingerprint

REVISION = 'luna-context-v4-v1'
SPEC_SHA = hashlib.sha256(SPEC.read_bytes()).hexdigest()


@lru_cache
def week(canary=False):
    _, full_docs, turns, basis = compile_week('continuing-account')
    if canary:
        turns = turns[:4]
    docs = [d for d in full_docs if not canary or instant(d.cutoff) <= instant(turns[-1].at)]
    return docs, turns, basis, full_docs


def write(path, value):
    path.write_text(json.dumps(value) + '\n')
    return path


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def condition(provider, version=None, track='nightly-document'):
    return dict(provider=provider, track=track, state='continuing-account',
                version=version or 'synthetic fixture', channels=['email', 'imessage'],
                deployment='synthetic fixture only')


def saved_surface(turn, documents, previous):
    user = {'role': 'user', 'content': turn.prompt}
    answer = {'role': 'assistant', 'content': 'Synthetic retained answer.'}
    return {
        'answer': answer['content'], 'question_at': turn.at, 'history': [*previous.get('history', []), user, answer],
        'conversation_times': [*previous.get('conversation_times', []), turn.at], 'session_id': turn.session,
        'gateway_identity': 'synthetic', 'model_requests': [], 'evidence': {'synthetic': True},
        'source_files': [doc.filename for doc in documents if instant(doc.cutoff) <= instant(turn.at)],
    }


def original_criteria(turn):
    return [{'id': c.id, 'verdict': 'missed', 'reason': 'synthetic original'} for c in turn.claims]


def journal_header(cond, turns, basis, canary):
    return [
        dict(phase='spec', version=WEEK, sha256=SPEC_SHA, source_basis=basis, canary=canary,
             schedule=[dict(id=t.id, at=t.at, chat=t.session, measure=t.measure) for t in turns]),
        dict(phase='condition', condition=cond, evidence_kind='native'),
    ]


def journal_documents(docs):
    events = []
    for doc in docs:
        events.extend([
            dict(phase='submit', filename=doc.filename, content=doc.text, sha256=doc.sha256,
                 source_ids=list(doc.source_ids), at=doc.cutoff),
            dict(phase='acceptance', evidence=dict(native_id='native-'+doc.filename, at=doc.cutoff, accepted=True,
                                                  request={'synthetic': True})),
            dict(phase='readback', evidence=dict(native_id='native-'+doc.filename, at=doc.cutoff, ready=True,
                                                submitted_sha256=doc.sha256, readback={'synthetic': True})),
        ])
    return events


def write_journal(path, events):
    path.write_text(''.join(json.dumps(event) + '\n' for event in events))


class CohortRegradeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runs = [self.make_run('mem0'), self.make_run('reme')]
        self.manifest = self.cohort(self.runs)

    def cohort(self, paths):
        providers = []
        for path in paths:
            provider = path.parent.parent.name if path.parent.name == 'workflow' else path.parent.name
            providers.append({'provider': provider, 'manifest': str(path.relative_to(self.root)),
                              'sha256': hash_file(path)})
        return write(self.root/'cohort.json', {'schema': 'pacbench-regrade-cohort-v1', 'runs': providers})

    def make_run(self, provider, *, count=2, future=False, canary=False, current_memcal=False,
                 historical_memcal=False, journal=True, seal=False):
        arm = self.root/provider
        directory = arm/'workflow'
        directory.mkdir(parents=True)
        docs, turns, basis, full_docs = week(canary)
        is_memcal = current_memcal or historical_memcal
        cond = condition(provider, '0.8.12' if is_memcal else None,
                         'first-party-connector' if is_memcal else 'nightly-document')
        identity = dict(protocol=MEMCAL_PROGRESS if is_memcal else DOCUMENT_PROGRESS, condition=cond,
                        account=str(arm), spec_sha256=SPEC_SHA, source_basis=basis,
                        evidence_kind='native', canary=canary)
        scope = dict(protocol=WEEK, provider=provider, root=str(arm), run_id=self.root.name, canary=canary,
                     admission=ADMISSIONS[provider], spec_sha256=SPEC_SHA, frozen_inputs=basis)
        if is_memcal:
            with SourcePlan(directory, docs, turns, all_source_documents=full_docs) as plan:
                identity.update(plan_sha256=plan.digest, store=str(arm/'memcal-home'))
            contract, plan_sha, *_ = _memcal_schedule(SPEC_SHA, canary)
            scope.update(contract=contract, plan_sha256=plan_sha, account=str(arm), store=identity['store'])
            if current_memcal:
                identity['product_manifest'] = memcal_product()
                scope['access'] = memcal_product()
        else:
            identity['schedule_sha256'] = fingerprint([asdict(t) for t in turns])
        scope_file = write(arm/'workflow-scope.json', scope)
        key = fingerprint(identity)
        manifest = write(directory/'run.json', dict(run_key=key, identity=identity, evidence_kind='native'))
        with WorkflowProgress(directory, key, create=True) as progress:
            progress.bind('identity', identity)
            progress.bind('evidence_kind', 'native')
            progress.bind('canary', canary)
            progress.bind('workflow_scope', binding(scope_file))
            progress.bind('plan_registered', True)
            for ordinal, turn in enumerate(turns):
                progress.plan_turn(ordinal, turn)
            if is_memcal:
                self.record_memcal_collections(progress, docs)
            previous = {}
            for turn in turns[:count]:
                surface = saved_surface(turn, docs, previous.get(turn.session, {}))
                if current_memcal:
                    messages = [{'role': 'user', 'content': turn.prompt}]
                    tools = json.loads((Path(__file__).parent/'fixtures/memcal-hermes-0.8.12.json').read_text())
                    body = {'messages': messages, 'model': 'openai/gpt-6-luna', 'tools': tools}
                    surface['model_requests'] = [dict(request_id='synthetic-'+turn.id, request=body, messages=messages,
                        model=body['model'], tools=list(TOOLS), canonical_request_sha256=wire_digest(body))]
                previous[turn.session] = surface
                if future:
                    surface['conversation_times'] = ['2026-10-30T09:00:00-04:00']
                progress.verified_turn(turn, surface, {'synthetic': True})
                progress.mark_turn_done(turn.id)
                if turn.claims:
                    progress.claim_grade()
                    progress.finish_grade(turn.id, original_criteria(turn))
            if seal:
                progress.seal_execution(len(turns), sum(len(t.claims) for t in turns))
        if journal and not is_memcal:
            events = journal_header(cond, turns, basis, canary) + journal_documents(docs)
            write_journal(directory/'workflow.jsonl', events)
        return manifest

    def record_memcal_collections(self, progress, documents):
        groups = defaultdict(list)
        for doc in documents:
            groups[doc.cutoff].append(doc)
        mapping = 'a'*64
        for index, (cutoff, docs) in enumerate(groups.items(), 101):
            native_id = str(index)
            cursors = {'email': {'synthetic': index}, 'imessage': {'synthetic': index}}
            rows = {sid: {'source_sha256': hashlib.sha256(body.encode()).hexdigest(), 'archive_sha256': 'b'*64}
                    for doc in docs for sid, body in zip(doc.source_ids, doc.bodies, strict=True)}
            clock = dict(cutoff=cutoff, as_of=cutoff, source_map_sha256=mapping, observation_sha256='c'*64)
            accepted = dict(native_id=native_id, accepted=True, at=cutoff, rows=rows, native_cursors=cursors,
                            source_map_sha256=mapping, source_observation_sha256=clock['observation_sha256'],
                            request={'sync_through': cutoff, 'channels': ['email', 'imessage']})
            dream = dict(run_id=index, finished_at=cutoff, pending_spool=0)
            ready = dict(native_id=native_id, ready=True, at=cutoff, readback=dream, source_cursor=cursors,
                         new_source_ids=sorted(rows), channel_readback={
                channel: dict(cursor=cursors[channel], as_of=cutoff,
                              new_source_ids=[sid for d in docs if d.channel == channel for sid in d.source_ids])
                for channel in cursors})
            for phase, record in [('source-clock', clock), ('sync', accepted), ('dream', dream)]:
                progress.effect(phase, cutoff, {'fixture': phase}, lambda: record)
            progress.advance_cutoff(cutoff, native_ready=ready)

    def plan(self):
        return snapshot(self.manifest, REVISION)

    def response(self, plan):
        item = plan['items'][0]
        response = {'verdicts': [dict(id=c['id'], verdict='correct', reason='mock response')
                                 for c in item['saved']['turn']['claims']]}
        return {'plan_id': plan['plan_id'], 'cell': item['id'], 'prompt_sha256': item['prompt_sha256'],
                'attempt_id': 'mock-'+item['id'], 'response': json.dumps(response)}

    def test_all_providers_discovered_read_only(self):
        before = {p: hash_file(p) for p in self.root.rglob('*') if p.is_file()}
        plan = self.plan()
        self.assertEqual(len(plan['items']), 4)
        report = plan['coverage']['mem0']
        self.assertEqual((report['expected_turns'], report['expected_criteria']), (49, 192))
        self.assertEqual(report['completed_criteria'], sum(len(t.claims) for t in week()[1][:2]))
        self.assertEqual(report['binding']['ingestion_protocol'], ADMISSIONS['mem0'])
        self.assertEqual(before, {p: hash_file(p) for p in before})
        self.assertFalse(plan['dispatch_enabled'])

    def test_direct_scope_result_and_no_hand_listed_turns(self):
        run = self.make_run('cognee', count=49, seal=True)
        manifest = run.parent.parent/'workflow-scope.json'
        result = write(manifest.with_name('result.json'), dict(provider='cognee', canary=False,
                                                              state='live-week-complete', scope=binding(manifest)))
        for entry in (run, manifest, run.parent.parent, result):
            report = snapshot(entry, REVISION)['coverage']['cognee']
            self.assertEqual((report['completed_turns'], report['completed_criteria']), (49, 192))
            self.assertEqual(report['judge_requests_planned'], 47)
            self.assertEqual(report['source_provenance']['ready_documents'], 48)
            self.assertTrue(report['execution_sealed'])

    def test_result_coverage_conflict_requires_original_store(self):
        run = self.make_run('cognee', count=49, seal=True)
        result = write(run.parent.parent/'result.json', dict(
            provider='cognee', canary=False, state='live-week-complete',
            coverage={'planned_turns': 49, 'verified_turns': 49, 'graded_criteria': 191}))
        with self.assertRaisesRegex(ValueError, 'result coverage differs'):
            snapshot(result, REVISION)
        self.assertEqual(snapshot(run, REVISION)['coverage']['cognee']['original_graded_criteria'], 192)

    def test_missing_journal_withholds_document_provenance(self):
        run = self.make_run('hindsight', journal=False)
        report = snapshot(run, REVISION)['coverage']['hindsight']
        self.assertEqual(report['completed_turns'], 2)
        self.assertIsNone(report['source_provenance']['ready_documents'])
        self.assertEqual(report['source_provenance']['missing_evidence'], str(run.with_name('workflow.jsonl')))

    def test_current_memcal_uses_native_collection_witnesses(self):
        run = self.make_run('memcal', count=49, current_memcal=True, seal=True)
        report = snapshot(run, REVISION)['coverage']['memcal']
        self.assertEqual(report['binding']['memcal_product'], memcal_product())
        provenance = report['source_provenance']
        self.assertIsNone(provenance['ready_documents'])
        self.assertEqual(provenance['ready_releases'], 7)
        self.assertEqual(sum(c['source_records'] for c in provenance['collections']), 142)
        self.assertEqual([c['native_collection_id'] for c in provenance['collections']],
                         [str(i) for i in range(101, 108)])

    def test_historical_memcal_same_version_keeps_original_product_condition(self):
        run = self.make_run('memcal', canary=True, historical_memcal=True)
        report = snapshot(run, REVISION)['coverage']['memcal']
        self.assertEqual(report['original_condition']['version'], '0.8.12')
        self.assertNotIn('memcal_product', report['binding'])
        self.assertTrue(report['binding']['canary'])
        self.assertEqual(report['expected_turns'], 4)

    def test_memcal_missing_readiness_is_not_a_collection_count(self):
        run = self.make_run('memcal', canary=True, historical_memcal=True)
        with sqlite3.connect(run.parent/'workflow.sqlite3') as db:
            db.execute("DELETE FROM observations WHERE name='native-ready'")
        with self.assertRaisesRegex(ValueError, 'Dream readback'):
            snapshot(run, REVISION)

    def test_legacy_native_journal_retains_its_condition_and_partial_grade(self):
        docs, turns, basis, _ = week()
        directory = self.root/'supermemory'
        directory.mkdir()
        path = directory/'workflow.jsonl'
        cond = condition('supermemory', 'historical-test-version')
        turn = turns[0]
        events = journal_header(cond, turns, basis, False) + journal_documents(docs)
        events.extend([dict(phase='answer-visible', question=turn.id, surface=saved_surface(turn, docs, {})),
                       dict(phase='verified-turn', question=turn.id, activity={'synthetic': True}),
                       dict(phase='grade', question=turn.id, criteria=original_criteria(turn), judge=REVISION)])
        write_journal(path, events)
        with path.open('ab') as stream:
            stream.write(b'{"phase":')
        plan = snapshot(path, REVISION)
        report = plan['coverage']['supermemory']
        self.assertEqual((report['completed_turns'], report['expected_turns']), (1, 49))
        self.assertEqual(report['original_condition'], cond)
        self.assertTrue(report['binding']['journal_torn_tail'])
        self.assertEqual(plan['items'][0]['saved']['original']['criteria'], original_criteria(turn))
        self.assertFalse(report['execution_sealed'])

    def test_stale_manifest_and_duplicate_provider(self):
        self.runs[0].write_text(self.runs[0].read_text()+' ')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.plan()
        meta = json.loads(self.manifest.read_text())
        meta['runs'] = [meta['runs'][1]]*2
        write(self.manifest, meta)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.plan()

    def test_verify_reports_missing_store_alongside_verified_arm(self):
        database = self.runs[0].parent/'workflow.sqlite3'
        database.unlink()
        with patch('builtins.print') as out:
            status = main([str(self.manifest), '--revision', REVISION, '--verify'])
        self.assertEqual(status, 1)
        report = json.loads(out.call_args.args[0])['coverage']
        self.assertIn(str(database), report['mem0']['reason'])
        self.assertEqual(report['reme']['completed_turns'], 2)

    def test_scope_ingestion_protocol_and_bound_hash(self):
        path = self.runs[0].parent.parent/'workflow-scope.json'
        scope = json.loads(path.read_text())
        scope['admission'] = 'some-other-ingestion'
        write(path, scope)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.plan()
        with sqlite3.connect(self.runs[0].parent/'workflow.sqlite3') as db:
            db.execute("UPDATE bindings SET value=? WHERE name='workflow_scope'", (json.dumps(binding(path)),))
        with self.assertRaisesRegex(ValueError, 'ingestion protocol'):
            self.plan()

    def test_future_context_rejected(self):
        run = self.make_run('hindsight', future=True)
        with self.assertRaisesRegex(ValueError, 'conversation times'):
            snapshot(run, REVISION)

    def test_unregistered_revision(self):
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            snapshot(self.manifest, 'latest')

    def test_resume_never_reclaims_attempt(self):
        plan = self.plan()
        path = self.root/'regrade-v1'
        overlay = Overlay(path, plan)
        record = self.response(plan)
        record['attempt_id'] = 'attempt-one'
        overlay.reserve(record['cell'], record['attempt_id'])
        overlay.close()
        overlay = Overlay(path, plan)
        self.addCleanup(overlay.close)
        self.assertEqual(overlay.report()['states'][record['cell']], 'ambiguous-attempt')
        with self.assertRaisesRegex(ValueError, 'never resend'):
            overlay.reserve(record['cell'], 'attempt-two')
        overlay.import_response(record)
        self.assertGreater(overlay.report()['providers']['mem0']['credit_delta'], 0)
        overlay.import_response(record)
        with self.assertRaises(ValueError):
            overlay.import_response({**record, 'response': 'other'})

    def test_invalid_and_unknown_are_not_zero(self):
        plan = self.plan()
        overlay = Overlay(self.root/'results', plan)
        self.addCleanup(overlay.close)
        record = self.response(plan)
        overlay.import_response({**record, 'response': 'invalid'})
        report = overlay.report()
        self.assertEqual(report['states'][record['cell']], 'invalid-output')
        self.assertTrue(all(d['credit_delta'] is None for d in report['criterion_deltas']))
        self.assertFalse(report['receipt_verified'])

    def test_stale_overlay_and_response(self):
        plan = self.plan()
        overlay = Overlay(self.root/'results', plan)
        self.addCleanup(overlay.close)
        with self.assertRaises(ValueError):
            overlay.import_response({**self.response(plan), 'prompt_sha256': 'wrong'})
        changed = json.loads(json.dumps(plan))
        changed['items'][0]['saved']['surface']['answer'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'stale'):
            Overlay(self.root/'results', changed)

    def test_original_arm_directory_forbidden(self):
        with self.assertRaises(ValueError):
            Overlay(self.runs[0].parent.parent/'regrade', self.plan())

    def test_revision_only_changes_prompt_supplement(self):
        baseline = self.plan()
        explicit = snapshot(self.manifest, 'luna-explicit-criteria-v1')
        self.assertNotEqual(baseline['plan_id'], explicit['plan_id'])
        for old, new in zip(baseline['items'], explicit['items'], strict=True):
            self.assertEqual(old['saved'], new['saved'])
            self.assertTrue(new['prompt'].startswith(old['prompt']+'\nExperimental criterion rules v1:'))

    def test_ordered_question_or_criterion_drift_detected(self):
        database = self.runs[0].parent/'workflow.sqlite3'
        with sqlite3.connect(database) as db:
            raw = db.execute('SELECT plan FROM turns WHERE ordinal=0').fetchone()[0]
            turn = json.loads(raw)
            turn['prompt'] += ' (altered)'
            db.execute('UPDATE turns SET plan=? WHERE ordinal=0', (json.dumps(turn),))
        with self.assertRaisesRegex(ValueError, 'frozen schedule'):
            self.plan()

    def test_artifact_corruption_detected(self):
        manifest = self.runs[0]
        with WorkflowProgress(manifest.parent, json.loads(manifest.read_text())['run_key'], read_only=True) as progress:
            key = progress.db.execute('SELECT surface FROM turns WHERE surface IS NOT NULL LIMIT 1').fetchone()[0]
            artifact = progress._blob_path(key)
        artifact.unlink()
        artifact.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'artifact hash mismatch'):
            self.plan()

    def test_document_readback_hash_drift_detected(self):
        journal = self.runs[0].with_name('workflow.jsonl')
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        row = next(e for e in events if e['phase'] == 'readback')
        row['evidence']['submitted_sha256'] = '0'*64
        write_journal(journal, events)
        with self.assertRaisesRegex(ValueError, 'document readback'):
            self.plan()

    def test_cli_saved_response_import_and_resume_offline(self):
        plan = self.plan()
        records = write(self.root/'responses.json', [self.response(plan)])
        args = [str(self.manifest), '--revision', REVISION, '--output', str(self.root/'overlay'),
                '--responses', str(records)]
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.print') as out:
            main(args)
            first = json.loads(out.call_args.args[0])
            main(args)
            self.assertEqual(first, json.loads(out.call_args.args[0]))
            self.assertGreater(first['providers']['mem0']['credit_delta'], 0)

    def test_cli_defaults_to_no_transport(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.print') as out:
            main([str(self.manifest), '--revision', REVISION])
            plan = json.loads(out.call_args.args[0])
        self.assertFalse(plan['dispatch_enabled'])
        self.assertNotIn('Synthetic retained answer.', json.dumps(plan))

    def test_published_aggregate_requires_actual_retained_inputs(self):
        public_summary = SPEC.parents[1]/'results/pizza-week-v2/summary.json'
        with self.assertRaisesRegex(ValueError, 'public aggregate only'):
            snapshot(public_summary, REVISION)


if __name__ == '__main__':
    unittest.main()
