"""Exercise the two-command authoring and native entry point without traffic."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from pacbench import __main__ as cli, native_pilot, prepare
from pacbench.followups import save
from pacbench.model_calls import ModelOutput
from pacbench.providers import ACTIVE_PROVIDERS


def read(path):
    return json.loads(path.read_text())


def test_generate_and_run_one_new_author_spec(tmp_path, monkeypatch, capsys):
    note = 'I booked the Cedar Room for our chess club.'
    spec = tmp_path / 'chess.json'
    save(spec, {'id': 'chess-club-cli-test', 'timezone': 'America/New_York',
                'question_at': '2026-10-06T12:00:00-04:00',
                'scenes': [{'id': 'booking', 'channel': 'imessage', 'thread': 'chess',
                            'participants': ['Me', 'Sam'], 'at': '2026-10-05T18:00:00-04:00',
                            'test_intent': 'Recall which room was booked for chess club.',
                            'messages': [{'sender': 'Sam', 'text': note, 'at': '2026-10-05T18:00:01-04:00'}]}]})
    corpus = tmp_path / 'generated'
    calls = []

    def fail_if_called(*_, **__):
        pytest.fail('unexpected model, writer or provider call')

    def model(prompt):
        calls.append(prompt)
        if prompt.startswith('Identify independently assessable'):
            value = {'claims': [{'id': 'room', 'statement': 'The chess club has the Cedar Room.', 'evidence': ['s1']}]}
        elif 'Review each proposed claim against' in prompt:
            value = {'complete': True, 'coverage_reason': 'The message names the room.',
                     'reviews': [{'id': 'room', 'supported': True, 'relevant': True,
                                  'unambiguous': True, 'reason': 'It explicitly names Cedar Room.'}]}
        elif prompt.startswith('Write one short'):
            value = {'question': 'Which room did we book for chess club?'}
        elif prompt.startswith('Check the proposed user question'):
            value = {'safe': True, 'faithful': True, 'reason': 'It asks for the booked room.'}
        elif prompt.startswith('Independently audit this complete raw personal-message corpus'):
            value = {'consistent': True, 'questions_answerable': True, 'natural_enough': True,
                     'issues': [], 'assessments': []}
        else:
            pytest.fail(f'unexpected authoring request {prompt[:100]}')
        text = json.dumps(value)
        path = corpus / 'model-calls' / f'{len(calls)}.json'
        save(path, {'model': 'offline-fixture', 'status': 'ok', 'text': text,
                    'request': {'messages': [{'role': 'user', 'content': prompt}]}})
        return ModelOutput(text, 'offline-fixture', {}, str(path))

    driver = SimpleNamespace(model_id='offline-fixture')
    monkeypatch.setenv('META_API_KEY', 'offline-fixture')
    monkeypatch.setattr(prepare, 'load_driver', lambda: driver)
    monkeypatch.setattr(prepare, 'client_settings', lambda _: ('https://offline.invalid/v1', 'offline-fixture'))
    monkeypatch.setattr(prepare, 'ModelClient', lambda **_: model)
    monkeypatch.setattr(prepare, 'ResponsesWriter', fail_if_called)
    original_audit = prepare.audit_prepared
    audit_calls = []

    def audit(root):
        audit_calls.append(root)
        return original_audit(root)

    monkeypatch.setattr(prepare, 'audit_prepared', audit)
    assert cli.main(['generate', str(spec), '--output', str(corpus)]) == 0
    assert audit_calls == [corpus]
    assert len(calls) >= 5
    frozen = (corpus / 'week.json').read_bytes()
    manifest = json.loads(frozen)
    assert read(corpus / 'run-audit.json')['state'] == 'pass'
    assert {'bundle.json', 'contract.json', 'realism-review.json'} <= manifest['files'].keys()

    portable = tmp_path / 'portable'
    shutil.move(corpus, portable)
    monkeypatch.setattr(prepare, 'load_driver', fail_if_called)
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', fail_if_called)
    monkeypatch.setattr(native_pilot, 'provision', fail_if_called)
    planned = tmp_path / 'plan'
    assert cli.main(['run', str(portable), '--output', str(planned), '--plan']) == 0
    plan = read(planned / 'summary.json')
    assert plan['corpus_hash'] == manifest['corpus_hash']
    assert plan['state'] == 'planned' and plan['selected_providers'] == list(ACTIVE_PROVIDERS)
    assert (plan['message_count'], plan['ingestion_days'], plan['planned_questions'], plan['planned_criteria']) == (1, 1, 1, 1)
    from pacbench.workflows.spec import compile_prepared

    recording = compile_prepared(portable, 'continuing-account')
    assert [item['id'] for item in plan['questions']] == [turn.id for turn in recording.turns] == ['final']

    visited = []

    def unavailable(_, provider, *args, **kwargs):
        visited.append(provider)
        raise RuntimeError('offline provider fixture')

    monkeypatch.delenv('PACBENCH_STOP_FILE', raising=False)
    monkeypatch.delenv('PACBENCH_TRANSPORT', raising=False)
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: {})
    monkeypatch.setattr(native_pilot, 'load_driver', lambda: driver)
    monkeypatch.setattr(native_pilot, 'client_settings', lambda _: ('https://offline.invalid/v1', 'offline-fixture'))
    monkeypatch.setattr(native_pilot, 'ModelClient', lambda **_: fail_if_called)
    monkeypatch.setattr(native_pilot, 'provision', unavailable)
    run = tmp_path / 'native'
    assert cli.main(['run', str(portable / 'week.json'), '--output', str(run)]) == 1
    report = read(run / 'summary.json')
    assert report['state'] == 'incomplete' and report['corpus_hash'] == plan['corpus_hash']
    assert report['selected_providers'] == visited == list(ACTIVE_PROVIDERS)
    assert (run / 'preparation/week.json').read_bytes() == frozen
    assert (run / 'sources/2026-10-05/messages.json').read_text().count(note) == 1
    printed = capsys.readouterr().out
    for provider, result in zip(ACTIVE_PROVIDERS, report['results'], strict=True):
        assert result['provider'] == provider and result['failure_phase'] == 'setup'
        assert result['capability']['available'] is False
        assert read(run / 'repeat-1' / provider / 'status.json') == result
        assert f'{provider} invalid' in printed

    def gateway_blocked():
        raise RuntimeError('offline account unavailable')

    blocked = tmp_path / 'blocked'
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', gateway_blocked)
    monkeypatch.setattr(native_pilot, 'provision', fail_if_called)
    assert cli.main(['run', str(portable), '--output', str(blocked), '--providers', 'mem0', 'memos']) == 1
    assert read(blocked / 'summary.json')['state'] == 'blocked'
    assert 'mem0 not run offline account unavailable' in capsys.readouterr().out

    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', fail_if_called)
    with (portable / 'bundle.json').open('a') as stream:
        stream.write('\n')
    with pytest.raises(ValueError, match='file hash mismatch'):
        cli.main(['run', str(portable), '--output', str(tmp_path / 'tampered'), '--plan'])
    assert not (tmp_path / 'tampered').exists()


def test_month_plan_and_run_keep_full_corpus_and_select_subset(tmp_path, monkeypatch):
    corpus = Path(__file__).resolve().parents[1] / 'runs/pizza-month-local-20261105/preparation'
    assert cli.main(['run', str(corpus), '--output', str(tmp_path / 'plan'), '--plan',
                     '--providers', 'memcal', 'supermemory']) == 0
    report = read(tmp_path / 'plan/summary.json')
    assert (report['message_count'], report['ingestion_days'], report['planned_questions']) == (2868, 30, 15)
    assert report['selected_providers'] == ['memcal', 'supermemory']

    def offline_route():
        raise RuntimeError('offline account unavailable')

    monkeypatch.delenv('PACBENCH_TRANSPORT', raising=False)
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', offline_route)
    assert cli.main(['run', str(corpus), '--output', str(tmp_path / 'native'), '--providers', 'supermemory']) == 1
    blocked = read(tmp_path / 'native/summary.json')
    assert blocked['message_count'] == 2868 and blocked['selected_providers'] == ['supermemory']
    assert report['corpus_hash'] == blocked['corpus_hash']


def test_shared_prepared_loader_authenticates_portable_siblings_and_loads_checks_once(tmp_path, monkeypatch):
    from pacbench.corpus import evaluation
    from pacbench.corpus.audit import open_prepared

    source = Path(__file__).resolve().parents[1] / 'runs/bike-pickup-native-clock-20261105/preparation'
    portable = tmp_path / 'portable'
    shutil.copytree(source, portable)
    original = evaluation.load_evaluations
    calls = []

    def load(root, messages, resolved, **kwargs):
        calls.append(root)
        return original(root, messages, resolved, **kwargs)

    monkeypatch.setattr(evaluation, 'load_evaluations', load)
    output = tmp_path / 'execution-input'
    with open_prepared(portable, portable=True, destination=output) as corpus:
        assert corpus.root == output
        assert corpus.corpus_hash == read(portable / 'week.json')['corpus_hash']
        assert len(corpus.questions) == 5 and corpus.questions is corpus.evaluations
        assert corpus.source_basis['evaluation_plan_hash'] == corpus.evaluation_plan['plan_hash']
    assert calls == [output]
    assert (output / 'week.json').read_bytes() == (portable / 'week.json').read_bytes()

    sidecar = next((portable / 'model-calls').glob('*.json'))
    sidecar.write_bytes(sidecar.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='file hash mismatch'):
        with open_prepared(portable, portable=True, destination=tmp_path / 'tampered'):
            pytest.fail('corrupt portable corpus reached the auditor')
    assert calls == [output] and not (tmp_path / 'tampered').exists()
