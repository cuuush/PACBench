"""Exercise the two-command authoring and native entry point without traffic."""
import argparse
import json
from contextlib import contextmanager, nullcontext
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
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
    assert cli.main(['generate', str(spec), '--output', str(corpus), '--inference-route', 'muse-gateway']) == 0
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
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
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
    assert 'mem0, memos not run offline account unavailable' in capsys.readouterr().out

    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', fail_if_called)
    with (portable / 'bundle.json').open('a') as stream:
        stream.write('\n')
    with pytest.raises(SystemExit) as rejected:
        cli.main(['run', str(portable), '--output', str(tmp_path / 'tampered'), '--plan'])
    assert rejected.value.code == 2
    assert 'file hash mismatch' in capsys.readouterr().err
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

    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', offline_route)
    assert cli.main(['run', str(corpus), '--output', str(tmp_path / 'native'), '--providers', 'supermemory']) == 1
    blocked = read(tmp_path / 'native/summary.json')
    assert blocked['message_count'] == 2868 and blocked['selected_providers'] == ['supermemory']
    assert report['corpus_hash'] == blocked['corpus_hash']


def test_named_corpus_plan_and_fresh_run(tmp_path, monkeypatch, capsys):
    from pacbench import openrouter_run

    source = Path(__file__).resolve().parents[1] / 'runs/bike-pickup-native-clock-20261105/preparation'
    dataset = tmp_path / 'datasets' / 'independent-persona'
    shutil.copytree(source, dataset)
    monkeypatch.setattr(cli, 'REPO', tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    for name in ('PACBENCH_TRANSPORT', 'PACBENCH_DIRECT', 'PACBENCH_GATEWAY_STATE', 'MUSE_GATEWAY_STATE',
                 'PACBENCH_OPENROUTER_PROXY_URL', 'PACBENCH_OPENROUTER_PROXY_TOKEN', 'PACBENCH_OPENROUTER_LIMIT_USD'):
        monkeypatch.delenv(name, raising=False)

    def unexpected(*_, **__):
        pytest.fail('called live execution for an offline or incomplete invocation')

    monkeypatch.setattr(openrouter_run, 'fetch_catalog', unexpected)
    monkeypatch.setattr(native_pilot, 'run', unexpected)
    command = ['run', 'independent-persona']
    assert cli.main([*command, '--plan']) == 0
    plan_path = Path(capsys.readouterr().out.splitlines()[-1])
    assert plan_path.is_relative_to(tmp_path / 'artifacts' / 'runs')
    assert read(plan_path)['selected_providers'] == list(ACTIVE_PROVIDERS)
    for flags, expected in [([], '--budget-usd AMOUNT'), (['--budget-usd', '2'], 'Set OPENROUTER_API_KEY'),
                            (['--budget-usd', 'NaN'], 'positive finite --budget-usd')]:
        with pytest.raises(SystemExit) as rejected:
            cli.main([*command, *flags])
        assert rejected.value.code == 2 and expected in capsys.readouterr().err
    assert list((tmp_path / 'artifacts' / 'runs').glob('*/summary.json')) == [plan_path]

    active = {}

    @contextmanager
    def session(output, limit, *, before_start):
        assert before_start is None
        assert not output.exists() and output.parent == plan_path.parent.parent
        active.update(output=output, limit=limit)
        try:
            yield
        finally:
            active.clear()

    def run(manifest, output, **kwargs):
        assert manifest == dataset / 'week.json'
        assert kwargs == dict(max_messages=None, providers=list(ACTIVE_PROVIDERS),
                              inference_route='openrouter', report_blocked=True)
        assert active == dict(output=output, limit='5.25')
        return {'state': 'diagnostic', 'results': [
            {'provider': provider, 'state': 'diagnostic', 'capability': {}} for provider in ACTIVE_PROVIDERS
        ]}

    monkeypatch.setenv('OPENROUTER_API_KEY', 'fixture-key')
    monkeypatch.setattr(openrouter_run, 'public_session', session)
    monkeypatch.setattr(native_pilot, 'run', run)
    assert cli.main([*command, '--budget-usd', '5.25']) == 0
    assert not active
    run_path = Path(capsys.readouterr().out.splitlines()[-1])
    assert run_path != plan_path and run_path.parent.parent == plan_path.parent.parent


def test_bounded_generate_uses_fresh_session(tmp_path, monkeypatch, capsys):
    from pacbench import openrouter_run

    spec = tmp_path / 'persona.json'
    save(spec, {'id': 'independent-persona', 'scenes': []})
    output = tmp_path / 'corpus'
    for name in ('PACBENCH_TRANSPORT', 'PACBENCH_DIRECT', 'PACBENCH_GATEWAY_STATE', 'MUSE_GATEWAY_STATE',
                 'PACBENCH_OPENROUTER_PROXY_URL', 'PACBENCH_OPENROUTER_PROXY_TOKEN', 'PACBENCH_OPENROUTER_LIMIT_USD'):
        monkeypatch.delenv(name, raising=False)
    command = ['generate', str(spec), '--output', str(output)]
    with pytest.raises(SystemExit) as rejected:
        cli.main(command)
    assert rejected.value.code == 2
    assert 'Set OPENROUTER_API_KEY and pass --budget-usd AMOUNT' in capsys.readouterr().err
    assert not output.exists()
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fixture-key')

    @contextmanager
    def session(root, limit, *, before_start):
        assert before_start is None
        assert root == output and limit == '3'
        monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
        yield

    def generate(body, root, **kwargs):
        assert body['id'] == 'independent-persona' and root == output
        assert kwargs['inference_route'] == 'openrouter'
        assert os.environ['PACBENCH_TRANSPORT'] == 'openrouter'
        return {'state': 'prepared'}

    monkeypatch.setattr(openrouter_run, 'public_session', session)
    monkeypatch.setattr(prepare, 'prepare', generate)
    assert cli.main([*command, '--budget-usd', '3']) == 0


def test_public_live_command_selects_installed_runtime(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'REPO', tmp_path)
    command = ['run', 'pizza-week', '--budget-usd', '5']
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit) as rejected:
        cli.use_installed_runtime(command, parser)
    assert rejected.value.code == 2
    assert './scripts/setup' in capsys.readouterr().err

    runtime = tmp_path / 'vendor/venv-hermes-isolated'
    python = runtime / 'bin/python'
    python.parent.mkdir(parents=True)
    python.touch(mode=0o700)
    commands = []
    monkeypatch.setattr(os, 'execv', lambda executable, args: commands.append((executable, args)))
    cli.use_installed_runtime(command, parser)
    assert commands == [(python, [str(python), '-m', 'pacbench', *command])]
    monkeypatch.setattr(sys, 'prefix', str(runtime))
    cli.use_installed_runtime(command, parser)
    assert len(commands) == 1


@pytest.mark.parametrize('entry', [[], ['generate']])
def test_generate_uses_proxy_chat_writer_and_reports_known_error(entry, tmp_path, monkeypatch, capsys):
    from pacbench.generation import GenerationError

    path = Path(__file__).resolve().parents[1] / 'specs/pizza_week.json'
    output = tmp_path / 'preparation'
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.delenv('PACBENCH_DIRECT', raising=False)
    monkeypatch.setenv('META_API_KEY', 'offline-proxy-token')
    monkeypatch.setattr(prepare, 'load_driver', lambda: SimpleNamespace(model_id='proxy-model'))
    monkeypatch.setattr(prepare, 'client_settings', lambda project: (f'http://127.0.0.1:1/p/{project}/v1', 'offline-proxy-token'))

    def legacy_writer(**_):
        pytest.fail('proxy preparation must use the chat writer')

    def chat_writer(**settings):
        assert settings['url'] == 'http://127.0.0.1:1/p/pacbench-authoring/v1/chat/completions'
        assert settings['reasoning_effort'] == 'low'
        raise GenerationError('offline setup failure')

    monkeypatch.setattr(prepare, 'ResponsesWriter', legacy_writer)
    monkeypatch.setattr(prepare, 'ModelClient', chat_writer)
    assert cli.main([*entry, str(path), '--output', str(output)]) == 1
    assert read(output / 'experiment-status.json')['inference_route'] == 'openrouter'
    assert read(output / 'summary.json')['state'] == 'incomplete'
    assert not (output / 'week.json').exists()
    assert 'Preparation stopped. offline setup failure' in capsys.readouterr().err


def test_generate_checks_explicit_route_before_writing(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / 'specs/pizza_week.json'
    output = tmp_path / 'preparation'
    command = ['generate', str(path), '--output', str(output), '--inference-route', 'codex-subscription']
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.delenv('PACBENCH_DIRECT', raising=False)
    with pytest.raises(SystemExit) as conflict:
        cli.main(command)
    assert conflict.value.code == 2 and not output.exists()
    assert 'conflicts with PACBENCH_TRANSPORT=openrouter' in capsys.readouterr().err

    monkeypatch.setenv('PACBENCH_DIRECT', '1')
    with pytest.raises(SystemExit) as direct:
        cli.main(command)
    assert direct.value.code == 2 and not output.exists()
    assert 'pacbench.openrouter_run --help' in capsys.readouterr().err

    monkeypatch.delenv('PACBENCH_TRANSPORT')
    monkeypatch.delenv('PACBENCH_DIRECT')

    def subscription_prepare(*_, inference_route, **__):
        assert inference_route == 'codex-subscription'
        return {'state': 'prepared'}

    monkeypatch.setattr(prepare, 'prepare', subscription_prepare)
    assert cli.main(command) == 0


def test_run_records_driver_failure_after_gateway_health(tmp_path, monkeypatch, capsys):
    from pacbench.generation import GenerationError

    corpus = Path(__file__).resolve().parents[1] / 'runs/bike-pickup-native-clock-20261105/preparation'
    output = tmp_path / 'run'
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
    monkeypatch.delenv('PACBENCH_DIRECT', raising=False)
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: {'service': 'test-gateway'})

    def driver_failure():
        raise GenerationError('test model inventory unavailable')

    def unexpected(*_, **__):
        pytest.fail('driver failure must stop before provider setup')

    monkeypatch.setattr(native_pilot, 'load_driver', driver_failure)
    monkeypatch.setattr(native_pilot, 'provision', unexpected)
    assert cli.main(['run', str(corpus), '--output', str(output), '--providers', 'mem0']) == 1
    report = read(output / 'summary.json')
    assert report['gateway_before'] == {'service': 'test-gateway'}
    assert report['state'] == 'blocked' and report['results'] == []
    assert report['reason'] == 'test model inventory unavailable'
    assert 'mem0 not run test model inventory unavailable' in capsys.readouterr().out


def test_run_reports_missing_muse_history_without_creating_it(tmp_path, monkeypatch, capsys):
    from pacbench import gateway_client

    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
    monkeypatch.delenv('PACBENCH_DIRECT', raising=False)
    state = tmp_path / 'private-state'
    state.mkdir()
    (state / 'client-token').write_text('fixture-secret')
    save(state / 'address.json', {'base': 'http://127.0.0.1:18999'})
    monkeypatch.setattr(gateway_client, 'STATE', state)
    monkeypatch.setattr(gateway_client.transport, 'open_without_redirect',
                        lambda *_a, **_kw: pytest.fail('unexpected health request'))
    corpus = Path(__file__).resolve().parents[1] / 'runs/bike-pickup-native-clock-20261105/preparation'
    output = tmp_path / 'blocked'

    assert cli.main(['run', str(corpus), '--output', str(output)]) == 1
    report = read(output / 'summary.json')
    assert report['state'] == 'blocked'
    with pytest.raises(gateway_client.GatewayConfigurationError) as missing:
        gateway_client.require_muse_state(state)
    assert report['reason'] == str(missing.value)
    assert 'ledger.sqlite3' in report['reason'] and 'README.md' in report['reason']
    printed = capsys.readouterr().out
    assert ', '.join(ACTIVE_PROVIDERS) + ' not run ' + report['reason'] in printed
    assert printed.count(report['reason']) == 1
    assert 'fixture-secret' not in report['reason']
    assert not (state / 'ledger.sqlite3').exists()


def test_run_uses_configured_openrouter_proxy_and_blocks_unbound_transport(tmp_path, monkeypatch, capsys):
    from pacbench import gateway_client
    from pacbench.openrouter_route import ENDPOINT, MODEL

    corpus = Path(__file__).resolve().parents[1] / 'runs/bike-pickup-native-clock-20261105/preparation'
    command = ['run', str(corpus), '--providers', 'mem0']
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setenv('PACBENCH_DIRECT', '0')
    monkeypatch.delenv('PACBENCH_OPENROUTER_PROXY_URL', raising=False)
    monkeypatch.delenv('PACBENCH_OPENROUTER_PROXY_TOKEN', raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-upstream-key')

    checks = []
    health = {'service': 'pacbench-openrouter', 'model': MODEL, 'endpoint': ENDPOINT,
              'limit_usd': '1.25', 'budget_blocked': False, 'auth_blocked': False}

    def health_check(request, *, timeout):
        assert request.full_url == 'http://127.0.0.1:18999/health'
        assert request.get_header('Authorization') == 'Bearer offline-proxy-token'
        checks.append(request.full_url)
        return BytesIO(json.dumps(health).encode())

    class PassedAdmission(Exception):
        pass

    def stop_before_driver():
        raise PassedAdmission

    monkeypatch.setattr(gateway_client.transport, 'open_without_redirect', health_check)
    monkeypatch.setattr(native_pilot, 'load_driver', stop_before_driver)
    assert cli.main([*command, '--output', str(tmp_path / 'plan'), '--plan']) == 0
    blocked = tmp_path / 'blocked'
    assert cli.main([*command, '--output', str(blocked)]) == 1
    report = read(blocked / 'summary.json')
    assert report['inference_route'] == 'openrouter' and report['state'] == 'blocked' and report['results'] == []
    assert report['reason'].startswith('run-owned OpenRouter transport is not configured.')
    assert 'README.md' in report['reason']
    assert report['reason'] in capsys.readouterr().out
    assert not checks

    monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_URL', 'http://127.0.0.1:18999')
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROXY_TOKEN', 'offline-proxy-token')
    monkeypatch.setenv('PACBENCH_OPENROUTER_LIMIT_USD', '1.25')
    with pytest.raises(PassedAdmission):
        cli.main([*command, '--output', str(tmp_path / 'admitted')])
    assert checks == ['http://127.0.0.1:18999/health']

    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
    monkeypatch.setattr(gateway_client, 'STATE', tmp_path / 'missing-shared-state')
    assert cli.main([*command, '--output', str(tmp_path / 'muse')]) == 1
    assert read(tmp_path / 'muse/summary.json')['inference_route'] == 'muse-gateway'
    assert len(checks) == 1

    monkeypatch.setenv('PACBENCH_DIRECT', '1')
    with pytest.raises(SystemExit) as rejected:
        cli.main([*command, '--output', str(tmp_path / 'unsupported-direct')])
    assert rejected.value.code == 2
    assert 'pacbench.openrouter_run --help' in capsys.readouterr().err
    assert not (tmp_path / 'unsupported-direct').exists()
    assert len(checks) == 1


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


@pytest.mark.parametrize(('command', 'corpus_text', 'existing', 'reason'), [
    (['run'], '{"id":"author-spec","scenes":[]}', False, 'Run `python -m pacbench generate'),
    (['run', '--plan'], '{"version":2,"corpus_hash":"x","files":[]}', False, 'Expected a frozen corpus manifest'),
    (['run', '--plan'], '{', False, 'Cannot read frozen corpus manifest'),
    (['run'], '{}', True, 'output already exists'),
    (['generate'], '{}', True, 'output already exists'),
    (['generate'], '{"version":2,"files":{},"corpus_hash":"x"}', False, 'generate expects an author spec'),
])
def test_cli_invalid_inputs_report_a_short_error(tmp_path, command, corpus_text, existing, reason):
    source = tmp_path / 'input.json'
    source.write_text(corpus_text)
    output = tmp_path / 'output'
    if existing:
        output.mkdir()
        (output / 'existing').touch()
    result = subprocess.run([sys.executable, '-m', 'pacbench', *command, str(source), '--output', str(output)],
                            env={**os.environ, 'PACBENCH_TRANSPORT': 'muse-gateway'},
                            capture_output=True, text=True)
    assert result.returncode == 2 and reason in result.stderr
    assert 'Traceback' not in result.stderr and 'KeyError' not in result.stderr
    if existing:
        assert list(output.iterdir()) == [output / 'existing']
    elif command == ['run']:
        report = read(output / 'summary.json')
        assert report['state'] == 'incomplete' and report['failure_phase'] == 'input'
        assert report['results'] == [] and reason in report['reason']


def test_cli_preserves_unexpected_native_errors(tmp_path, monkeypatch):
    source = tmp_path / 'week.json'
    source.touch()

    def unexpected(*_, **__):
        raise ValueError('unexpected provider failure')

    monkeypatch.setattr(native_pilot, 'run', unexpected)
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'muse-gateway')
    with pytest.raises(ValueError, match='unexpected provider failure'):
        cli.main(['run', str(source), '--output', str(tmp_path / 'native')])


def test_plan_and_native_release_times_agree_for_unordered_mixed_offsets(tmp_path, monkeypatch):
    from pacbench.corpus import audit
    from pacbench.document_ingestion import prepare_sources

    messages = [
        {'source_id': 'latest', 'collected_at': '2026-10-05T18:00:00-04:00', 'written_at': '2026-10-05T18:00:00Z'},
        {'source_id': 'tomorrow', 'collected_at': '2026-10-06T17:00:00-04:00', 'written_at': '2026-10-06T16:00:00-04:00'},
        {'source_id': 'earlier', 'collected_at': '2026-10-05T23:30:00+02:00', 'written_at': '2026-10-05T20:00:00Z'},
    ]
    for row in messages:
        row.update(channel='imessage', thread='a', sender='Sam', text='A message.', from_owner=False)
    question = {'id': 'after', 'at': '2026-10-07T10:00:00-04:00'}
    monkeypatch.setattr(audit, 'open_prepared', lambda *_, **__: nullcontext(SimpleNamespace(
        messages=messages, resolved={'timezone': 'America/New_York'}, corpus_hash='fixture', evaluations=[],
        questions=[(question, SimpleNamespace(claims=[]), {})],
    )))
    plan = tmp_path / 'plan'
    assert cli.plan_run(tmp_path / 'week.json', plan, ['mem0']) == 0
    planned = read(plan / 'summary.json')['ingestion_schedule']
    native = prepare_sources(tmp_path / 'native', messages, 'America/New_York', native_fixtures=False)
    expected = [('2026-10-05', '2026-10-05T18:00:00-04:00'), ('2026-10-06', '2026-10-06T17:00:00-04:00')]
    assert [(row['id'], row['at']) for row in planned] == [(row['id'], row['at']) for row in native] == expected
