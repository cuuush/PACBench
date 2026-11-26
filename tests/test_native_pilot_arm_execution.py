"""Native pilot arm boundaries exercised with local, deterministic substitutes."""
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from pacbench import clock_context, context_isolation, document_ingestion, providers as live_trials
from pacbench import native_audit, native_checks, native_pilot, native_writes, preparation_audit, usage
from pacbench.gateway_client import ModelAccessBlocked, SpendingBlocked
from pacbench.stop import OperatorStop


@pytest.mark.parametrize('phased,fault', [
    (False, None), (True, None), (True, 'setup'), (True, 'probe'),
    (False, 'access'), (False, 'spending'), (False, 'operator'), (False, 'verify'), (True, 'shutdown'),
])
def test_arm_records_failure_and_releases_its_backend(tmp_path, monkeypatch, phased, fault):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'evaluation-plan.json').write_text('{}')
    (source / 'evaluations').mkdir()
    output = tmp_path / 'trial'
    at = '2026-10-01T12:00:00-04:00'
    row = {'id': 'check', 'at': at}
    evaluation_plan = {'context': 'shared', 'plan_hash': 'plan'}
    contract = SimpleNamespace(question='What happened?', claims=[])
    corpus = SimpleNamespace(root=source, messages=[], contract=contract, corpus_hash=None,
                             resolved={'id': 'query', 'question_at': at, 'timezone': 'America/New_York'},
                             evaluation_plan=evaluation_plan if phased else None,
                             evaluations=[(row, contract, {})] if phased else [],
                             input_hash='inputs', summary={'contract_hash': 'contract', 'bundle_hash': 'bundle'})
    events = []

    monkeypatch.setattr(live_trials, 'require_live_trials_ready', lambda: None)
    monkeypatch.setattr(context_isolation, 'validate_recall_environment', lambda: None)
    monkeypatch.setattr(preparation_audit, 'open_prepared', lambda *a, **kw: nullcontext(corpus))
    monkeypatch.setattr(native_pilot, 'copy_prepared_files', lambda *a: None)
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: {})
    monkeypatch.setattr(native_pilot, 'load_driver', lambda: SimpleNamespace(model_id='offline'))
    monkeypatch.setattr(native_pilot, 'client_settings', lambda *_: ('http://offline.invalid', 'offline'))
    monkeypatch.setattr(native_pilot, 'ModelClient', lambda **_: None)
    monkeypatch.setattr(native_pilot, 'freeze_policy', lambda *a: {'plan_hash': 'followup'})
    monkeypatch.setattr(native_pilot, 'smoke_succeeded', lambda *_: True)
    monkeypatch.setattr(native_pilot, 'policy_record', lambda *_: {'allowed_tools': [], 'source_argument_rules': {}})
    if phased and fault is None:
        schedule = [{'id': 'day', 'at': '2026-09-30T20:00:00-04:00', 'file': 'ingestion/day.json'}]
        monkeypatch.setattr(document_ingestion, 'prepare_sources', lambda *a, **kw: schedule)
        monkeypatch.setattr(document_ingestion, 'ingest_day', lambda *a, **kw: {'state': 'ready'})
        monkeypatch.setattr(document_ingestion, 'ready_sources',
                            lambda *a: pytest.fail('native recall reparsed a prepared timeline'))
    else:
        monkeypatch.setattr(document_ingestion, 'prepare_sources', lambda *a, **kw: [])
    monkeypatch.setattr(document_ingestion, 'record_recall_isolation', lambda *a: None)
    monkeypatch.setattr(native_audit, 'audit', lambda *_: {'state': 'pass'})
    monkeypatch.setattr(usage, 'snapshot', lambda *_: {})
    monkeypatch.setattr(usage, 'provider_model_health', lambda *_: {'state': 'captured'})

    def provision(root, provider, *_a, **_kw):
        events.append(('provision', provider))
        if fault == 'access':
            raise ModelAccessBlocked('access refused')
        if fault == 'spending':
            raise SpendingBlocked('budget refused')
        if fault == 'operator':
            raise OperatorStop('operator requested stop')
        if fault == 'setup' and provider == 'memcal':
            raise OSError('provider setup failure')
        return {'PACBENCH_USAGE_ROLES': '{}'}

    def probe(root, *_args, **_kwargs):
        if fault == 'probe' and root.name == 'memcal':
            raise RuntimeError('provider probe failure')
        return {'available': fault == 'verify' or root.name == 'mem0', 'tools': []}

    def shutdown(root):
        events.append(('shutdown', root.name))
        if fault == 'shutdown':
            raise RuntimeError('native shutdown incomplete')

    def checks(_rows, _context, root, _judge, _allowlist, _env, **kwargs):
        events.append(('checks', root.name))
        kwargs['before_check'](row, root / 'check')
        return {'state': 'diagnostic', 'checks': [{'id': row['id'], 'state': 'graded'}]}

    def turn(_run_turn, question, **kwargs):
        assert question == contract.question and kwargs['at'] == at
        events.append(('recall', kwargs['home'].parent.name))
        return SimpleNamespace(tool_calls=[], prefetch_context=None, returncode=0, answer='answer', policy_blocks=[])

    def grade(*_args, **_kwargs):
        if fault == 'verify':
            raise KeyError('malformed verification evidence')
        return SimpleNamespace(correct=0, missed=0, misrepresented=0, slots=[], recovery={}, evidence_diagnostic={})

    monkeypatch.setattr(native_pilot, 'provision', provision)
    monkeypatch.setattr(native_pilot, 'probe', probe)
    monkeypatch.setattr(native_pilot, 'stop_arm_model_work', shutdown)
    monkeypatch.setattr(native_pilot, 'run_checks', checks)
    monkeypatch.setattr(native_writes, 'ensure_ready', lambda *_: events.append(('watermark', 'mem0')))
    monkeypatch.setattr(native_pilot, 'tracked_turn', turn)
    monkeypatch.setattr(native_checks, 'diagnostic_inputs', lambda *a, **kw: {})
    monkeypatch.setattr(clock_context, 'judge_question', lambda *_: contract.question)
    monkeypatch.setattr(native_pilot, 'grade_native_semantic', grade)
    monkeypatch.setattr(native_pilot, 'run_followups', lambda **kw: {'state': 'complete', 'score': 0, 'attempts': []})

    if fault in ('verify', 'shutdown'):
        with pytest.raises(KeyError if fault == 'verify' else RuntimeError):
            native_pilot.run(source, output, providers=['memcal', 'mem0'])
        report = json.loads((output / 'summary.json').read_text())
        assert report['state'] == 'incomplete'
        assert len(report['results']) == 1 and report['results'][0]['state'] == 'invalid'
        expected_recall = [('recall', 'memcal')] if fault == 'verify' else []
        assert events == [('provision', 'memcal'), *expected_recall, ('shutdown', 'memcal')]
        failure = report['results'][0]
        if fault == 'verify':
            assert failure['failure_phase'] == 'evaluation' and failure['reason'] == 'KeyError'
        else:
            assert failure['cleanup_error'] == 'RuntimeError'
        return

    report = native_pilot.run(source, output, providers=['memcal', 'mem0'])
    assert report['state'] == ('interrupted' if fault == 'operator' else 'incomplete')
    if fault in ('access', 'spending', 'operator'):
        if fault == 'operator':
            assert report['interruption'] == 'operator stop requested'
            assert [result['state'] for result in report['results']] == ['interrupted']
        else:
            assert report[f'{fault}_stop']
            assert [result['state'] for result in report['results']] == ['blocked']
        assert events == [('provision', 'memcal'), ('shutdown', 'memcal')]
        assert not (output / 'repeat-1' / 'mem0').exists()
        return
    assert [result['state'] for result in report['results']] == [
        'invalid' if fault in ('setup', 'probe') else 'blocked', 'diagnostic']
    if fault in ('setup', 'probe'):
        assert report['results'][0]['failure_phase'] == fault
    expected_recall = [('checks', 'mem0'), ('watermark', 'mem0')] if phased else [('recall', 'mem0')]
    assert events == [('provision', 'memcal'), ('shutdown', 'memcal'), ('provision', 'mem0'),
                      *expected_recall, ('shutdown', 'mem0')]
    root = output / 'repeat-1' / 'mem0'
    assert (root / 'pre-shutdown-model-usage.json').is_file()
    assert (root / 'model-usage.json').is_file()
    assert (root / 'grade.json').is_file() is not phased
    assert (root / 'check' / 'source-state.json' if phased else root / 'source-state.json').is_file()
    if phased and fault is None:
        state = json.loads((root/'check/source-state.json').read_text())
        assert state['ingested_batches'] == ['day']
