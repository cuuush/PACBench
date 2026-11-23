"""Synthetic answers in the real retained Week formats. No provider or transport."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pacbench.canary_capability import binding, digest as wire_digest
from pacbench.cohort_regrade import Overlay, main, snapshot
from pacbench.cohort_regrade_journal import FORMAT, SCHEMA, load as load_judge_journal
from pacbench.cohort_regrade_retained import ADMISSIONS, DAILY_FILES, DOCUMENT_PROGRESS, MEMCAL_PROGRESS, THREAD_FILES, WEEK
from pacbench.workflow_capability import _memcal_schedule
from pacbench.workflow_memcal_tools import TOOLS, manifest as memcal_product
from pacbench.workflow_progress import WorkflowProgress
from pacbench.workflow_source_plan import SourcePlan
from pacbench.workflow_spec import Document, SPEC, compile_week, instant
from pacbench.workflow_stream import fingerprint

REVISION = 'luna-context-v4-v1'
NEUTRAL_REVISION = 'explicit-criteria-v1'
SPEC_SHA = hashlib.sha256(SPEC.read_bytes()).hexdigest()


@lru_cache
def week(canary=False):
    _, full_docs, turns, basis = compile_week('continuing-account')
    if canary:
        turns = turns[:4]
    docs = [d for d in full_docs if not canary or instant(d.cutoff) <= instant(turns[-1].at)]
    return docs, turns, basis, full_docs


def seven_daily(*, original_clock=False):
    documents, _, _, _ = week()
    nightly = {d.cutoff[:10]: d.cutoff for d in documents}
    dataset = SPEC.parents[1]/'datasets/pizza-week/r2'
    manifest = json.loads((dataset/'manifest.json').read_text())
    return [
        Document('daily-'+batch['id']+'.txt', batch['at'] if original_clock else nightly[batch['id']],
                 'mixed', (dataset/batch['source_file']).read_bytes().decode('utf-8'), tuple(batch['source_ids']))
        for batch in manifest['source_batches']
    ]


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
                 historical_memcal=False, journal=True, seal=False, daily=False):
        arm = self.root/provider
        directory = arm/'workflow'
        directory.mkdir(parents=True)
        docs, turns, basis, full_docs = week(canary)
        if daily:
            docs = seven_daily()
        is_memcal = current_memcal or historical_memcal
        cond = condition(provider, '0.8.12' if is_memcal else None,
                         'first-party-connector' if is_memcal else 'nightly-document')
        identity = dict(protocol=MEMCAL_PROGRESS if is_memcal else DOCUMENT_PROGRESS, condition=cond,
                        account=str(arm), spec_sha256=SPEC_SHA, source_basis=basis,
                        evidence_kind='native', canary=canary)
        scope = dict(protocol=WEEK, provider=provider, root=str(arm), run_id=self.root.name, canary=canary,
                     admission=ADMISSIONS.get(provider, 'recorded-'+provider+'-daily-v1'),
                     spec_sha256=SPEC_SHA, frozen_inputs=basis)
        if daily:
            identity['input_projection'] = scope['input_projection'] = DAILY_FILES
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

    def legacy_complete(self, directory, provider, *, documents=None, skip_original=False):
        directory.mkdir(parents=True)
        default, turns, basis, _ = week()
        docs = default if documents is None else documents
        cond = condition(provider, 'historical-fixture')
        events = journal_header(cond, turns, basis, False)
        remaining = iter(docs)
        next_doc = next(remaining, None)
        prior = {}
        for index, turn in enumerate(turns):
            while next_doc is not None and instant(next_doc.cutoff) <= instant(turn.at):
                events.extend(journal_documents([next_doc]))
                next_doc = next(remaining, None)
            surface = saved_surface(turn, docs, prior.get(turn.session, {}))
            prior[turn.session] = surface
            events.extend([
                dict(phase='dispatch', question=turn.id, prompt=turn.prompt, at=turn.at),
                dict(phase='answer-visible', question=turn.id, surface=surface),
                dict(phase='verified-turn', question=turn.id, activity={'synthetic': True}),
            ])
            if turn.claims and not (skip_original and index == 0):
                events.append(dict(phase='grade', question=turn.id, criteria=original_criteria(turn), judge=REVISION))
        if next_doc is not None:
            events.extend(journal_documents([next_doc, *remaining]))
        events.append(dict(phase='completion'))
        path = directory/'workflow.jsonl'
        write_journal(path, events)
        return path

    def judge_records(self, plan, *, other=False):
        judges = {
            'first': {'model': 'fixture/judge-one', 'route': 'fixture/first',
                      'endpoint': 'https://judges.example.invalid/v1/rubric', 'fallback': False, 'response_format': FORMAT},
        }
        if other:
            judges['second'] = {**judges['first'], 'model': 'fixture/judge-two', 'route': 'fixture/second'}
        if plan['judge'].get('model'):
            judges['first'].update(model=plan['judge']['model'], route=plan['judge']['route'])
        rows = [dict(schema=SCHEMA, plan_id=plan['plan_id'], revision=plan['revision'], judges=judges)]
        for selector, identity in judges.items():
            route = {name: identity[name] for name in ('model', 'route', 'endpoint')}
            for item in plan['items']:
                cell = item['id']
                response = json.dumps({'verdicts': [
                    {'id': c['id'], 'verdict': 'correct' if selector == 'first' else 'misrepresented',
                     'reason': 'synthetic judgment'} for c in item['saved']['turn']['claims']]})
                rows.append({'judge': selector, 'cell': cell, 'attempt_id': selector+'-'+cell,
                             'input_sha256': item['input_sha256'], 'prompt_sha256': item['prompt_sha256'],
                             'request': {**route, 'fallback': identity['fallback'], 'prompt': item['prompt']},
                             'receipt': {**route, 'request_id': 'synthetic-request-'+selector+'-'+cell,
                                         'status': 'complete', 'response_sha256': hashlib.sha256(response.encode()).hexdigest()},
                             'response': response})
        return rows

    def judge_cli(self, manifest, journal, output, *, selector='first', revision=NEUTRAL_REVISION):
        return [str(manifest), '--revision', revision, '--response-journal', str(journal),
                '--journal-sha256', hash_file(journal), '--judge', selector, '--output', str(output)]

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

    def test_complete_selected_judge_comparisons_keep_conditions_and_originals_separate(self):
        historical = self.legacy_complete(self.root/'old-supermemory', 'supermemory', skip_original=True)
        daily = self.make_run('supermemory', count=49, seal=True, daily=True)
        manifest = write(self.root/'cohort.json', {
            'schema': 'pacbench-regrade-cohort-v1',
            'runs': [
                {'arm': 'historical', 'provider': 'supermemory', 'manifest': str(historical.relative_to(self.root)),
                 'sha256': hash_file(historical)},
                {'arm': 'daily', 'provider': 'supermemory', 'manifest': str(daily.relative_to(self.root)),
                 'sha256': hash_file(daily), 'input_projection': DAILY_FILES,
                 'ingestion_protocol': 'recorded-supermemory-daily-v1'},
            ]})
        plan = snapshot(manifest, NEUTRAL_REVISION)
        self.assertEqual(len(plan['items']), 94)
        self.assertEqual(plan['coverage']['historical']['source_provenance']['ready_documents'], 48)
        self.assertEqual(plan['coverage']['daily']['source_provenance']['ready_documents'], 7)
        journal = self.root/'responses.jsonl'
        write_journal(journal, self.judge_records(plan, other=True))
        before = {path: hash_file(path) for path in (manifest, journal, historical, *daily.parent.rglob('*.json'),
                                                    daily.parent/'workflow.sqlite3', daily.with_name('workflow.jsonl'))}
        output = self.root/'first-overlay'
        args = self.judge_cli(manifest, journal, output)
        subprocess.run([sys.executable, '-m', 'pacbench.cohort_regrade', *args], check=True, capture_output=True, text=True)
        result = json.loads((output/'report.json').read_text())
        old, new = result['arms']['historical'], result['arms']['daily']
        self.assertEqual({g['input_projection']: g['arms'] for g in result['conditions']},
                         {THREAD_FILES: ['historical'], DAILY_FILES: ['daily']})
        self.assertEqual((new['original_correct'], new['regrade_correct'], new['compared_criteria']), (0, 192, 192))
        self.assertGreater(old['original_ungraded_criteria'], 0)
        self.assertEqual(old['regrade_correct'], 192)
        self.assertTrue(any(row['original'] is None and row['regrade'] == 'correct' and row['credit_delta'] is None
                            for row in result['criterion_deltas']))
        self.assertEqual(result['provenance']['model'], 'fixture/judge-one')
        self.assertNotIn('Synthetic retained answer.', json.dumps(result))
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('builtins.print'):
            main(args)
            other = self.root/'second-overlay'
            main(self.judge_cli(manifest, journal, other, selector='second'))
            rival = json.loads((other/'report.json').read_text())
            self.assertEqual(rival['provenance']['judge_id'], 'second')
            self.assertEqual(rival['arms']['daily']['regrade_correct'], 0)
            with patch('sys.stderr'), self.assertRaises(SystemExit):
                main(self.judge_cli(manifest, journal, output, selector='second'))
        self.assertEqual(json.loads((output/'report.json').read_text()), result)
        self.assertEqual(before, {path: hash_file(path) for path in before})

    def test_daily_projection_uses_original_clock_if_recorder_used_it(self):
        journal = self.legacy_complete(self.root/'honcho', 'honcho', documents=seven_daily(original_clock=True))
        manifest = write(self.root/'daily-only.json', {'schema': 'pacbench-regrade-cohort-v1', 'runs': [
            {'provider': 'honcho', 'manifest': str(journal.relative_to(self.root)),
             'sha256': hash_file(journal), 'input_projection': DAILY_FILES}]})
        plan = snapshot(manifest, NEUTRAL_REVISION)
        self.assertEqual(plan['coverage']['honcho']['binding']['release_clock'], 'r2-original-batch-cutoff')
        self.assertEqual(plan['coverage']['honcho']['completed_turns'], 49)
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        submit = next(row for row in rows if row['phase'] == 'submit')
        submit['content'] = 'changed daily content'
        write_journal(journal, rows)
        configured = json.loads(manifest.read_text())
        configured['runs'][0]['sha256'] = hash_file(journal)
        write(manifest, configured)
        with self.assertRaisesRegex(ValueError, 'frozen daily source'):
            snapshot(manifest, NEUTRAL_REVISION)

    def test_daily_projection_cannot_relabel_historical_run(self):
        configured = json.loads(self.manifest.read_text())
        configured['runs'][0]['input_projection'] = DAILY_FILES
        write(self.manifest, configured)
        with self.assertRaisesRegex(ValueError, 'seven original submit witnesses'):
            snapshot(self.manifest, NEUTRAL_REVISION)

    def test_recorded_result_rejects_wrong_provenance_and_incomplete_judgments(self):
        run = self.make_run('cognee', count=4, canary=True, seal=True)
        plan = snapshot(run, NEUTRAL_REVISION)
        rows = self.judge_records(plan)
        journal = self.root/'selected.jsonl'
        write_journal(journal, rows)
        with self.assertRaisesRegex(ValueError, 'journal SHA-256'):
            load_judge_journal(journal, plan, 'first', '0'*64)
        with self.assertRaisesRegex(ValueError, 'no recorded identity'):
            load_judge_journal(journal, plan, 'other', hash_file(journal))

        def update_record(rows, section, field, value):
            if section:
                rows[1][section][field] = value
            else:
                rows[1][field] = value

        for section, field, value, error in [
            ('request', 'prompt', 'foreign prompt', 'wrong judge request'),
            ('request', 'fallback', True, 'wrong judge request'),
            ('receipt', 'route', 'different/route', 'wrong judge request'),
            (None, 'input_sha256', '0'*64, 'frozen input'),
            ('receipt', 'status', 'truncated', 'incomplete recorded judge response'),
            ('receipt', 'response_sha256', '0'*64, 'response hash'),
            (None, 'cell', 'cognee/not-a-turn', 'absent or ungradable'),
        ]:
            with self.subTest(field=field):
                changed = deepcopy(rows)
                update_record(changed, section, field, value)
                write_journal(journal, changed)
                with self.assertRaisesRegex(ValueError, error):
                    load_judge_journal(journal, plan, 'first', hash_file(journal))

        for body in ['{"verdicts":[]}', '{"verdicts":[],"verdicts":[]}', '{"answers":{}}']:
            changed = deepcopy(rows)
            changed[1]['response'] = body
            changed[1]['receipt']['response_sha256'] = hashlib.sha256(body.encode()).hexdigest()
            write_journal(journal, changed)
            with self.assertRaisesRegex(ValueError, 'unsupported or incomplete semantic verdicts'):
                load_judge_journal(journal, plan, 'first', hash_file(journal))

        write_journal(journal, rows[:-1])
        output = self.root/'should-not-exist'
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main(self.judge_cli(run, journal, output))
        self.assertFalse(output.exists())

        write_journal(journal, rows)
        with journal.open('ab') as stream:
            stream.write(b'{"torn":')
        with self.assertRaisesRegex(ValueError, 'incomplete response journal tail'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))

    def test_jsonl_selected_judge_refuses_incomplete_original_arm(self):
        run = self.runs[0]
        plan = snapshot(run, NEUTRAL_REVISION)
        journal = self.root/'incomplete-arm.jsonl'
        write_journal(journal, self.judge_records(plan))
        with self.assertRaisesRegex(ValueError, 'incomplete retained answers'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))

    def test_complete_turns_without_intake_evidence_are_not_accepted_for_comparison(self):
        run = self.make_run('hindsight', count=4, canary=True, seal=True, journal=False)
        plan = snapshot(run, NEUTRAL_REVISION)
        journal = self.root/'missing-intake.jsonl'
        write_journal(journal, self.judge_records(plan))
        with self.assertRaisesRegex(ValueError, 'missing original intake evidence'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))

    def test_selected_judge_rejects_unsupported_format_and_changed_luna_pin(self):
        run = self.make_run('openviking', count=4, canary=True, seal=True)
        plan = snapshot(run, REVISION)
        journal = self.root/'unsupported.jsonl'
        rows = self.judge_records(plan)
        rows[0]['judges']['first']['response_format'] = 'decisions-alpha'
        write_journal(journal, rows)
        with self.assertRaisesRegex(ValueError, 'unsupported selected judge'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))
        rows[0]['judges']['first'].update(response_format=FORMAT, model='not/luna')
        write_journal(journal, rows)
        with self.assertRaisesRegex(ValueError, 'model/route revision'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))

    def test_selected_judge_rejects_duplicate_attempt_and_request(self):
        run = self.make_run('hindsight', count=4, canary=True, seal=True)
        plan = snapshot(run, NEUTRAL_REVISION)
        journal = self.root/'duplicate.jsonl'
        rows = self.judge_records(plan)
        duplicate = deepcopy(rows)
        duplicate.append(duplicate[1])
        write_journal(journal, duplicate)
        with self.assertRaisesRegex(ValueError, 'duplicate cell or attempt'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))
        rows[2]['receipt']['request_id'] = rows[1]['receipt']['request_id']
        write_journal(journal, rows)
        with self.assertRaisesRegex(ValueError, 'duplicate recorded request'):
            load_judge_journal(journal, plan, 'first', hash_file(journal))

    def test_published_aggregate_requires_actual_retained_inputs(self):
        public_summary = SPEC.parents[1]/'results/pizza-week-v2/summary.json'
        with self.assertRaisesRegex(ValueError, 'public aggregate only'):
            snapshot(public_summary, REVISION)


if __name__ == '__main__':
    unittest.main()
