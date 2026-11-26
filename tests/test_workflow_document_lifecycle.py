import json
import os
from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pacbench.workflows import capability as cap
from pacbench.workflows import lifecycle as lifecycle


@pytest.fixture(params=['mem0', 'reme', 'cognee', 'hindsight', 'openviking', 'memos_oss'])
def launcher(request, tmp_path, monkeypatch):
    name = request.param
    provider = 'memos' if name == 'memos_oss' else name
    module = import_module(f'pacbench.providers.{provider}' + ('.workflow' if provider == 'hindsight' else ''))
    root = tmp_path/'canary'/provider
    args = {
        'mem0': ('offline-qdrant', 'offline-confirmation'),
        'memos_oss': ('offline-confirmation', 'offline-source', 'offline-qualification'),
    }.get(name, ('offline-confirmation',))

    rows = [
        {'accounting_role': role, 'upstream_request_id': role}
        for role in ('hermes', 'judge', 'memory_provider')
    ]
    ledger = Mock()
    ledger.snapshot.return_value = rows
    report = {'coverage': {'verified_turns': 1, 'planned_turns': 1, 'graded_criteria': 2, 'planned_criteria': 2},
              'turns': [{'driver_attempt_coverage': {'coverage': 'complete'}}]}
    catalog = Mock()
    credit = Mock()
    summarize = Mock(return_value=report)
    billing = Mock(return_value={'complete_cost_usd': '0.001'})
    transport = SimpleNamespace(exit_error=None)
    events = []
    environment = {}

    @contextmanager
    def direct(key):
        assert key == 'offline'
        events.append('direct_enter')
        yield
        events.append('direct_exit')
        if transport.exit_error is not None:
            raise transport.exit_error

    @contextmanager
    def open_fixture_account(*_):
        events.append('account_enter')
        environment.update(os.environ)
        yield 'condition', 'account'
        events.append('account_exit')

    account = Mock(side_effect=open_fixture_account)
    judge = Mock(return_value='judge-instance')
    execute = Mock()
    prior = {'result': 'canary-result', 'scope': 'canary-scope'}
    qualify = Mock(side_effect=lambda _: events.append('qualify') or prior)

    monkeypatch.delenv(cap.ENV, raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline')
    for target, value in {
        'canary_capability.binding': lambda _: events.append('confirmation') or {},
        'canary_capability.read': lambda _: {},
        'context_isolation.validate_recall_environment': lambda: None,
        'gateway_client.client_settings': lambda _: ('url', 'key'),
        'model_calls.ModelClient': judge,
        'observational_receipts.credit_snapshot': credit,
        'openrouter_direct.direct_environment': direct,
        'openrouter_route.PROFILE_NAME': cap.PROFILE,
        'openrouter_run.fetch_catalog': catalog,
        'providers.memcal.contract.verify_contract': Mock(),
        'workflows.recording.summarize': summarize,
        'workflows.recording.receipt_summary': billing,
        'workflows.runner.run_compiled_workflow': execute,
    }.items():
        monkeypatch.setattr(f'pacbench.{target}', value)
    monkeypatch.setattr(cap, 'confirm_setting', Mock())
    create = Mock(return_value={'bound': True})
    scope = {'run_id': 'canary', 'projects': {provider: {'judge': 'offline'}}}
    monkeypatch.setattr(cap, 'create', create)
    monkeypatch.setattr(cap, 'load', lambda: scope)
    monkeypatch.setattr(cap, 'budget', lambda: ledger)
    monkeypatch.setattr(module, 'open_account', account)
    monkeypatch.setattr(module, 'check_canary', qualify)

    def run(*, canary=True, after_canary=None):
        return module.run(root, *args, canary=canary, after_canary=after_canary)

    return SimpleNamespace(run=run, root=root, catalog=catalog, credit=credit, execute=execute, ledger=ledger,
                           summarize=summarize, billing=billing, events=events, prior=prior, qualify=qualify,
                           account=account, judge=judge, transport=transport, environment=environment,
                           create=create, scope=scope, provider=provider)


def test_document_launch_retries_read_only_preflight_and_claims_once(launcher):
    run, root, catalog = launcher.run, launcher.root, launcher.catalog
    stop = cap.campaign_stop(root, nested_run=True)
    stop.touch()
    with pytest.raises(RuntimeError, match='Shared OpenRouter account stopped'):
        run()
    assert not root.exists()
    catalog.assert_not_called()
    stop.unlink()

    catalog.side_effect = TimeoutError('inventory unavailable')
    with pytest.raises(TimeoutError, match='inventory unavailable'):
        run()
    assert not root.exists()
    launcher.credit.assert_not_called()
    launcher.execute.assert_not_called()

    catalog.side_effect = None
    result = run()
    assert result['state'] == 'live-canary-complete'
    assert result['cleanup'] == 'complete'
    assert result['provider'] == root.name
    assert result == json.loads((root/'result.json').read_text())
    with pytest.raises(FileExistsError):
        run()
    assert catalog.call_count == 2
    launcher.execute.assert_called_once()
    launcher.ledger.snapshot.assert_called_once()
    launcher.summarize.assert_called_once()
    launcher.billing.assert_called_once()


def test_document_launch_retains_primary_when_summary_fails(launcher):
    run, root, execute = launcher.run, launcher.root, launcher.execute
    primary = TimeoutError('private execution detail')
    execute.side_effect = primary
    launcher.summarize.side_effect = ValueError('private workflow detail')
    with pytest.raises(TimeoutError) as observed:
        run()
    assert observed.value is primary
    result = json.loads((root/'result.json').read_text())
    assert result['state'] == 'incomplete'
    assert result['cleanup'] == 'unverified'
    assert result['error'] == 'TimeoutError'
    assert result['report_error'] == 'ValueError'
    assert result['workflow'] is None
    assert result['billing'] == launcher.billing.return_value
    assert 'private' not in json.dumps(result)
    launcher.ledger.snapshot.assert_called_once()
    launcher.summarize.assert_called_once()
    launcher.billing.assert_called_once()
    with pytest.raises(FileExistsError):
        run()
    execute.assert_called_once()


def test_week_launch_keeps_qualification_route_and_native_account_inputs(launcher, tmp_path):
    from pacbench.openrouter_route import CHAT_COMPLETIONS_URL, MODEL

    root, provider = launcher.root, launcher.provider
    previous = tmp_path / 'qualified-canary'
    inherited_environment = dict(os.environ)
    result = launcher.run(canary=False, after_canary=previous)
    assert dict(os.environ) == inherited_environment
    assert launcher.events == ['qualify', 'confirmation', 'direct_enter', 'account_enter', 'account_exit', 'direct_exit']
    launcher.qualify.assert_called_once_with(previous)
    launcher.create.assert_called_once_with(root, provider, canary=False, prepaid=launcher.credit.return_value,
                                            auto_recharge_evidence={}, qualification=launcher.prior)
    account_args = (root, launcher.scope, launcher.ledger)
    if provider == 'mem0':
        account_args = (root, 'offline-qdrant', launcher.scope, launcher.ledger)
    if provider == 'memos':
        account_args += ('offline-source', 'offline-qualification')
    launcher.account.assert_called_once_with(*account_args)
    assert launcher.environment[cap.ENV] == json.dumps({'bound': True})
    assert launcher.environment['PACBENCH_ROLE_ROUTES'] == 'luna-all-roles-v1'
    assert launcher.environment['PACBENCH_IMPORT_ROOT'] == str(root)
    assert launcher.environment['PACBENCH_STOP_FILE'] == str(root / 'STOP')
    if provider == 'openviking':
        assert launcher.environment['PACBENCH_OPENVIKING_IMPORT_CONDITION'] == 'native-defaults'
    if provider == 'memos':
        from pacbench.workflows.tracks import MEMOS_OSS_LUNA_COMPAT

        assert launcher.environment['PACBENCH_MEMOS_OSS_LUNA_COMPAT'] == MEMOS_OSS_LUNA_COMPAT
    launcher.judge.assert_called_once_with(key='key', model=MODEL, record_dir=root/'workflow/model-calls',
                                          url=CHAT_COMPLETIONS_URL, json_mode=True, max_attempts=1)
    from pacbench.campaigns.pizza_week import compile_pizza_week

    launcher.execute.assert_called_once_with(compile_pizza_week(), 'condition', 'account', root/'workflow',
                                            judge='judge-instance')
    assert result['state'] == 'live-week-complete'


def test_cleanup_evidence_keeps_its_provider_boundary(launcher):
    launcher.transport.exit_error = OSError('direct teardown failed')
    with pytest.raises(OSError, match='direct teardown failed'):
        launcher.run()
    result = json.loads((launcher.root/'result.json').read_text())
    assert result['state'] == 'incomplete'
    assert result['cleanup'] == ('unverified' if launcher.provider == 'memos' else 'complete')
    assert launcher.events[-2:] == ['account_exit', 'direct_exit']


def test_stop_arriving_during_catalog_does_not_claim_run(tmp_path, monkeypatch):
    root = tmp_path/'canary/mem0'
    stop = cap.campaign_stop(root, nested_run=True)
    monkeypatch.setenv('PACBENCH_OBSERVATIONAL_COHORT_STOP', str(stop))
    monkeypatch.setattr('pacbench.openrouter_run.fetch_catalog', lambda **kwargs: stop.touch())
    with pytest.raises(RuntimeError, match='Shared OpenRouter account stopped'):
        lifecycle.claim_run(root, cap.PROFILE)
    assert not root.exists()
