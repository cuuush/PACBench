"""The same execution engine accepts the pinned and generally prepared scenarios."""
from dataclasses import asdict
import json
from hashlib import sha256
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pacbench.workflows.conformance import RecordingAccount
from pacbench.corpus.r2 import compile_daily_week
from pacbench.workflows.runner import Condition, run_compiled_workflow, run_current_week
from pacbench.corpus.r2 import ROOT, SPEC, compile_pizza_week, compile_week
from pacbench.workflows.spec import compile_prepared, instant
from pacbench.workflows.progress import workflow_fingerprint as fingerprint

BIKE = ROOT / 'runs/bike-pickup-native-clock-20261105/preparation'
MONTH = ROOT / 'runs/pizza-month-local-20261105/preparation'


def _cli(*args):
    return subprocess.run([sys.executable, '-m', 'pacbench', 'workflow', *map(str, args)],
                          cwd=ROOT, check=True, text=True, capture_output=True)


def _journal(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _condition(provider='mem0', track='nightly-document', state='continuing-account', channels=('email', 'imessage')):
    return Condition(provider, track, state, 'recording frontend', 'recording sentinel', 'in-process',
                     'offline recording fake', 'version', 'no Hermes process', 'synthetic event clock', (), channels)


def test_prepared_bike_uses_existing_author_spec_and_compiled_runner(tmp_path):
    result = json.loads(_cli('conform', '--prepared', BIKE / 'week.json', '--track', 'nightly-document',
                             '--provider', 'mem0', '--output', tmp_path / 'bike').stdout)
    arm = result['providers']['mem0']
    assert result['spec'] == 'bike-pickup-and-coffee'
    assert result['spec_sha256'] == sha256((BIKE / 'spec.json').read_bytes()).hexdigest()
    assert (arm['offline_recorded_turns'], arm['recorded_files']) == (5, result['source_documents'])
    root = tmp_path / 'bike/mem0'
    assert not (root / 'workflow.sqlite3').exists()
    events = _journal(root / 'workflow.jsonl')
    reported = events[0]
    assert reported['format'] == 'prepared-recording-workflow-v1'
    assert reported['source_basis'] == result['source_basis']
    checks = json.loads((BIKE / 'resolved-spec.json').read_text())['checks']
    dispatched = [event for event in events if event['phase'] == 'dispatch']
    assert [(event['question'], event['prompt'], event['at']) for event in dispatched] == [
        (check['id'], check['prompt'], check['at']) for check in checks]
    assert [item['chat'] for item in reported['schedule']] == [check['session'] for check in checks]
    collected = {instant(row['at']) for row in events if row['phase'] == 'submit'}
    assert collected == {instant(at) for at in result['cutoffs']}
    assert not any(event['phase'] == 'grade' for event in events)

    month = json.loads(_cli('plan', '--prepared', MONTH, '--track', 'nightly-document', '--provider', 'mem0').stdout)
    assert result['spec_sha256'] != month['spec_sha256']
    assert result['source_basis']['corpus_hash'] != month['source_basis']['corpus_hash']
    assert sum(month['turns_by_measure'].values()) == 15


def test_custom_plan_cannot_enter_native_execution(tmp_path):
    plan = compile_prepared(BIKE, 'continuing-account')
    native = SimpleNamespace(evidence_kind='native')
    with pytest.raises(ValueError, match='offline conformance only'):
        run_compiled_workflow(plan, _condition(channels=plan.channels), native, tmp_path / 'workflow')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('canary,daily', [(False, False), (True, False), (False, True)])
def test_pizza_compatibility_wrapper_preserves_checkpoint_and_journal_identity(tmp_path, canary, daily):
    if daily:
        spec, documents, turns, source_basis, layout = compile_daily_week()
    else:
        spec, documents, turns, source_basis = compile_week('continuing-account')
        layout = None
    all_documents = documents
    if canary:
        turns = turns[:4]
        documents = [doc for doc in documents if instant(doc.cutoff) <= instant(turns[-1].at)]
    plan = compile_pizza_week(canary=canary, seven_daily_documents=daily)
    assert plan.documents == tuple(documents) and plan.all_documents == tuple(all_documents)
    assert plan.turns == tuple(turns) and plan.source_basis == source_basis

    condition = _condition()
    account = RecordingAccount(condition, documents)
    try:
        run_current_week(condition, account, tmp_path / 'workflow', canary=canary, seven_daily_documents=daily)
    finally:
        account.close()
    digest = sha256(SPEC.read_bytes()).hexdigest()
    journal = _journal(tmp_path / 'workflow/workflow.jsonl')
    if daily:
        layout_record = journal.pop(0)
        assert layout_record['layout'] == layout['source_layout']
        assert layout_record['document_index_sha256'] == layout['normalized_index_sha256']
    assert {key: value for key, value in journal[0].items() if key != 'elapsed_s'} == {
        'phase': 'spec', 'version': spec['version'], 'canary': canary, 'sha256': digest,
        'source_basis': source_basis,
        'schedule': [dict(id=turn.id, at=turn.at, chat=turn.session, measure=turn.measure) for turn in turns],
    }


@pytest.mark.parametrize('provider,track,state,channels', [
    ('mem0', 'nightly-document', 'isolated-control', ('email', 'imessage')),
    ('honcho', 'first-party-connector', 'continuing-account', ('email',)),
])
def test_pizza_compatibility_wrapper_preserves_legacy_jsonl_schedule(tmp_path, provider, track, state, channels):
    _, documents, original, basis = compile_week(state)
    condition = _condition(provider, track, state, channels)
    selected = [doc for doc in documents if doc.channel in channels]
    account = RecordingAccount(condition, selected)
    try:
        counts = run_current_week(condition, account, tmp_path / 'workflow')
    finally:
        account.close()
    expected = original if len(channels) == 2 else []
    spec = _journal(tmp_path / 'workflow/workflow.jsonl')[0]
    assert spec['sha256'] == sha256(SPEC.read_bytes()).hexdigest()
    assert spec['source_basis'] == basis
    assert spec['schedule'] == [dict(id=turn.id, at=turn.at, chat=turn.session, measure=turn.measure)
                                for turn in expected]
    assert sum(row['answered'] for row in counts.values()) == len(expected)
    assert len(account.files) == len(selected)


def test_independent_pizza_compile_rechecks_frozen_siblings(tmp_path, monkeypatch):
    import shutil

    from pacbench.corpus import r2 as pizza_week
    from pacbench.judging import retained_week as retained

    monkeypatch.delenv('PACBENCH_WORKFLOW_SCOPE', raising=False)
    shutil.copytree(ROOT/'specs', tmp_path/'specs')
    dataset = tmp_path/'datasets/pizza-week/r2'
    shutil.copytree(ROOT/'datasets/pizza-week/r2', dataset)
    spec = tmp_path/'specs'/SPEC.name
    monkeypatch.setattr(pizza_week, 'ROOT', tmp_path)
    monkeypatch.setattr(pizza_week, 'SPEC', spec)
    monkeypatch.setattr(retained, 'SPEC', spec)

    compiled = pizza_week.compile_pizza_week()
    original_basis = dict(compiled.source_basis)
    compiled.source_basis['sha256'] = 'mutated in caller'
    compiled.spec['session_groups'].clear()
    fresh = pizza_week.compile_pizza_week()
    assert fresh.source_basis == original_basis and fresh.spec['session_groups']
    condition = dict(channels=['email', 'imessage'], state='continuing-account', track='nightly-document')
    assert retained.week_plan(fresh.spec_sha256, original_basis, condition, False)[0] == list(fresh.documents)

    from pacbench.judging import retained_scope as capability

    with monkeypatch.context() as native:
        native.setenv(capability.ENV, 'bound-native-scope-a')
        cached_documents, _, cached_basis = capability.retained_schedule(fresh.spec_sha256, False)
        cached_documents.clear()
        cached_basis.clear()
        assert capability.retained_schedule(fresh.spec_sha256, False) == (
            list(fresh.documents), list(fresh.turns), original_basis)

    manifest = json.loads((dataset/'manifest.json').read_text())
    sibling = dataset/manifest['evaluation_questions'][0]
    with sibling.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='Dataset checksum differs'):
        pizza_week.compile_pizza_week()
    with pytest.raises(ValueError, match='Dataset checksum differs'):
        retained.week_plan(fresh.spec_sha256, original_basis, condition, False)

    with monkeypatch.context() as native:
        native.setenv(capability.ENV, 'bound-native-scope-b')
        with pytest.raises(ValueError, match='Dataset checksum differs'):
            capability.retained_schedule(fresh.spec_sha256, False)
