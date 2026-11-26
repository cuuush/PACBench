from contextlib import contextmanager, nullcontext
from dataclasses import replace
import fcntl
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from pacbench.providers.supermemory import week as runner
from pacbench.providers.supermemory import week_admission as week
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.supermemory.week import StockWeekWitness
from pacbench.providers.supermemory.observation import stock_temporal_metadata
from pacbench.campaigns.pizza_week import compile_daily_week
from pacbench.workflows.delivery import submit_document
from pacbench.workflows.progress import WorkflowProgress


@pytest.mark.parametrize('transient_timeout', [False, True])
def test_stock_sdk_add_and_readback_of_one_complete_day(tmp_path, transient_timeout):
    sdk = pytest.importorskip('supermemory')
    doc = compile_daily_week()[1][0]
    namespace, native = 'week', 'D' * 22
    wire = dict(type='text', id=doc.filename, content=doc.text[:-1], metadata=stock_temporal_metadata(doc.cutoff),
                system=dict(status='done', createdAt=doc.cutoff, updatedAt=doc.cutoff))
    db_row = dict(id=native, customId=doc.filename, containerTags=[namespace], content=wire['content'],
                  status='done', createdAt=doc.cutoff, updatedAt=doc.cutoff, metadata=wire['metadata'], latestRevision=1)
    calls = []

    def respond(req):
        calls.append(req.method)
        if req.url.path == '/ns/week/document':
            assert req.method == 'POST'
            assert json.loads(req.content) == dict(id=doc.filename, content=doc.text, date=doc.cutoff)
            return httpx.Response(200, json=dict(id=doc.filename, status='queued'))
        if req.url.path == '/ns/week/list/documents':
            return httpx.Response(200, json=dict(documents=[wire], chunks=[], memories=[],
                pagination=dict(currentPage=1, totalPages=1, totalItems=1)))
        assert req.method == 'GET' and req.url.path == '/ns/week/document/' + doc.filename
        if transient_timeout and calls.count('GET') == 1:
            raise httpx.ReadTimeout('transient native status read', request=req)
        return httpx.Response(200, json=wire)

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = sdk.Supermemory(api_key='fixture', base_url='http://offline.invalid', httpx_client=transport, max_retries=0)
        (tmp_path / 'source-membership').mkdir()
        witness = StockWeekWitness.__new__(StockWeekWitness)
        witness.root, witness.namespace, witness.endpoint, witness.sdk = tmp_path, namespace, 'http://offline.invalid', client
        witness.documents, witness.batch, witness.cutoff = [doc], [doc], doc.cutoff
        witness.ready, witness.accepted, witness.captures = {}, {}, {}
        witness.explicit_writes = {}
        witness.read_native = lambda path, body=None: (dict(memories=[db_row], pagination=dict(currentPage=1, totalPages=1))
                                                        if path.endswith('/list') else db_row)
        accepted = witness.acceptance(submit_document('supermemory', client.with_raw_response, namespace, doc), doc)
        proof = witness.document_readback(doc, accepted)
        assert proof['readback']['native_id'] == native and proof['submitted_sha256'] == doc.sha256
        assert proof['ready'] and proof['readback']['native_revision'] == 1
        assert witness.source_inventory() == [doc.filename]
        assert calls == ['POST', *(['GET'] * (1 + transient_timeout)), 'POST', 'POST']


@pytest.mark.parametrize(('native_failure', 'ledger_failure', 'stop_failure'), [
    (False, False, False), (False, True, False), (True, True, False), (True, True, True),
])
def test_week_terminal_result_survives_drain_and_shutdown_failures(
        monkeypatch, tmp_path, native_failure, ledger_failure, stop_failure):
    from pacbench.workflows import recording as workflow_metrics
    from provider_adapters import supermemory_local

    contract = {'turns': 1}
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline')
    for name in (runner.JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_JUDGE_OUTPUT_RECOVERY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runner, 'selected', lambda: None)
    monkeypatch.setattr(runner, 'PROFILE_NAME', runner.PROFILE)
    monkeypatch.setattr(runner, 'account_lock', lambda _: nullcontext(3))
    for name in ('check_stops', 'validate_recall_environment', 'verify_retained_catalog_provider', 'check_runtime', 'fetch_catalog'):
        monkeypatch.setattr(runner, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, 'plan', lambda: ((), (), {}, contract))
    monkeypatch.setattr(runner, 'verify_prepaid', lambda *_: ({}, b'{}', {'receipt_sha256': 'test'}))
    monkeypatch.setattr(runner, 'projects', lambda _: {runner.PROVIDER: {'driver': 'driver', 'memory': 'memory'}})
    monkeypatch.setattr(runner, 'client_settings', lambda name: ('offline', name))
    monkeypatch.setattr(runner, 'direct_environment', lambda _: nullcontext())
    monkeypatch.setattr(runner.harness, 'provision_home', lambda home, **_: (home / 'plugins').mkdir(parents=True))
    monkeypatch.setattr(workflow_metrics, 'summarize', lambda _: {'coverage': {'verified_turns': 1}})

    (tmp_path / 'plugins').mkdir()
    (tmp_path / 'plugins' / '.install-metadata.json').write_text('{}')
    root = tmp_path / runner.WEEK_RUN / runner.PROVIDER
    events = []
    native_error, ledger_error, stop_error = ValueError('native'), OSError('ledger'), RuntimeError('stop')

    def snapshot():
        events.append('snapshot')
        if ledger_failure:
            raise ledger_error
        return []

    def has_active_attempts():
        events.append('active')
        if ledger_failure:
            raise ledger_error
        return False

    def execute(*_):
        events.append('native')
        (root / 'workflow').mkdir()
        (root / 'workflow' / 'workflow.jsonl').write_text('')
        if native_failure:
            raise native_error
        return {'verified_turns': 1}

    def stop():
        events.append('stop')
        if stop_failure:
            raise stop_error
        (root / 'native-service-stop.json').write_text('{"state": "verified-inventory-stopped"}')

    monkeypatch.setattr(runner.cap, 'budget', lambda: SimpleNamespace(
        snapshot=snapshot, has_active_attempts=has_active_attempts))
    monkeypatch.setattr(runner, 'run_native', execute)
    monkeypatch.setattr(supermemory_local, 'start', lambda *_: stop)

    expected = native_error if native_failure else ledger_error if ledger_failure else None
    if expected is None:
        result = runner.run(tmp_path, tmp_path, tmp_path)
        assert result['state'] == 'stock-week-native-complete-ungraded'
        assert result['billing']['attempts'] == result['unsettled_transports'] == 0
        assert 'failure_type' not in result
        assert result == json.loads((root / 'result.json').read_text())
    else:
        with pytest.raises(type(expected)) as err:
            runner.run(tmp_path, tmp_path, tmp_path)
        assert err.value is expected
        result = json.loads((root / 'result.json').read_text())
        assert result['state'] == 'incomplete'
        assert result['failure_type'] == type(expected).__name__
        expected_secondary = []
        if native_failure:
            expected_secondary.append(dict(stage='transport_drain', failure_type='OSError'))
        if stop_failure:
            expected_secondary.append(dict(stage='native_shutdown', failure_type='RuntimeError'))
        expected_secondary.append(dict(stage='receipt_summary', failure_type='OSError'))
        assert result['secondary_failures'] == expected_secondary
        assert 'billing' not in result
    assert result['workflow']['coverage']['verified_turns'] == 1
    assert result['cleanup'] == ('unverified' if stop_failure else 'verified-inventory-stopped')
    assert events == ['native', 'active', 'stop', 'snapshot']


def test_run_native_binds_the_daily_layout_for_retained_verification(monkeypatch, tmp_path):
    sdk = pytest.importorskip('supermemory')
    from pacbench.workflows import document_progress as workflow_document_progress
    from pacbench.canary_capability import binding, sha
    from pacbench.workflows.runner import run_current_week

    _, docs, turns, basis, manifest = compile_daily_week()
    root = tmp_path / runner.WEEK_RUN / runner.PROVIDER
    root.mkdir(parents=True)
    owner = SimpleNamespace(home=root / 'hermes-home', env={'PACBENCH_WORKFLOW_CAPTURE': runner.PROTOCOL},
                            witness=dict(pid=runner.process_identity()['pid'], start_time='fixture', process_adoption={}),
                            pins=dict(hermes='fixture', hermes_isolation_sha256='fixture'))
    monkeypatch.setattr(sdk, 'Supermemory', lambda **_: SimpleNamespace(close=lambda: None, with_raw_response=object()))
    monkeypatch.setattr(runner, 'PersistentGateway', lambda *_, **__: nullcontext(owner))
    monkeypatch.setattr(runner, 'CheckpointClock', lambda *_: object())
    monkeypatch.setattr(runner.harness, 'turn_env', lambda *args: args[-1])
    def init_witness(self, witness_root, scope, gateway, *_):
        self.root, self.scope, self.owner = witness_root, scope, gateway
        self.namespace, self.documents = scope['namespace'], docs

    monkeypatch.setattr(StockWeekWitness, '__init__', init_witness)
    bound_accounts = []

    def run_progress(condition, account, source, scheduled, directory, spec, spec_hash, frozen, all_source, judge,
                     canary, *, resume, daily_manifest):
        assert condition == runner._stock_week_condition(owner)
        assert isinstance(account, runner.StockWeekAccount)
        assert directory == root / 'workflow'
        assert source == all_source == docs
        assert scheduled == turns and frozen == basis and spec_hash == sha(runner.SPEC)
        assert daily_manifest == manifest
        assert judge is None and not canary and not resume
        bound_accounts.append(account)
        return {'verified_turns': len(scheduled)}

    monkeypatch.setattr(workflow_document_progress, 'run', run_progress)
    settings = {
        runner.explicit.ENV: runner.explicit.PROTOCOL, 'PACBENCH_SUPERMEMORY_STOCK_SOURCE': '1',
        'PACBENCH_SUPERMEMORY_STOCK_ROUNDS': '1', 'PACBENCH_SUPERMEMORY_STOCK_CHUNKS': '1',
        'PACBENCH_STOP_FILE': str(root / 'STOP'), runner.COHORT_STOP: str(tmp_path / 'ACCOUNT_STOP'),
        'PACBENCH_DISPATCH_TRACKING': runner.CONDITION,
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ('PACBENCH_OFFLINE_CONFORMANCE', 'PACBENCH_JUDGE_OUTPUT_RECOVERY'):
        monkeypatch.delenv(name, raising=False)

    with (tmp_path / 'execution.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        scope = dict(admission=runner.SCOPE, protocol=runner.PROTOCOL, root=str(root), provider=runner.PROVIDER,
                     run_id=runner.WEEK_RUN, canary=False, namespace=f'supermemory-data-{runner.WEEK_RUN}-{runner.PROVIDER}',
                     contract=manifest, frozen_inputs=basis, native_effect=runner.BOUNDARY,
                     worker_round_protocol=runner.WORKER_ROUND_PROTOCOL, chunk_read_protocol=runner.CHUNK_READ_PROTOCOL,
                     explicit_store_protocol=runner.explicit.PROTOCOL, controller=runner.process_identity(),
                     lock_fd=lock.fileno(), projects=runner.projects(root), spec_sha256=sha(runner.SPEC))
        scope_path = root / 'workflow-scope.json'
        scope_path.write_text(json.dumps(scope))
        monkeypatch.setenv(runner.cap.ENV, json.dumps(binding(scope_path)))
        assert runner.run_native(root, scope,
                                 dict(SUPERMEMORY_BASE_URL='http://offline.invalid', SUPERMEMORY_API_KEY='fixture'),
                                 None, 'fixture')['verified_turns'] == 49

        account, = bound_accounts
        condition = runner._stock_week_condition(owner)
        for changed_condition, changed_path in [
            (condition, root / 'foreign'), (replace(condition, version='foreign'), root / 'workflow'),
        ]:
            with pytest.raises(ValueError, match='differ from the bound stock Supermemory Week'):
                run_current_week(changed_condition, account, changed_path, seven_daily_documents=True)
        scope_path.write_text(json.dumps({**scope, 'contract': {'source_layout': 'nightly-thread-files-v2'}}))
        monkeypatch.setenv(runner.cap.ENV, json.dumps(binding(scope_path)))
        with pytest.raises(ValueError, match='Unbound Supermemory Week scope'):
            run_current_week(condition, account, root / 'workflow', seven_daily_documents=True)
    assert not (root / 'workflow').exists()


@pytest.mark.parametrize('provider', ['mem0', 'supermemory'])
def test_native_daily_entry_rejects_unqualified_generic_account(tmp_path, provider):
    from pacbench.workflows.runner import run_current_week

    owner = SimpleNamespace(pins=dict(hermes='offline', hermes_isolation_sha256='offline'))
    condition = replace(runner._stock_week_condition(owner), provider=provider)
    account = runner.NativeAccount('offline identity', provider, 'offline namespace', None, None, None)
    with pytest.raises(ValueError, match='native seven daily files require .*qualified stock Supermemory Week'):
        run_current_week(condition, account, tmp_path / 'workflow', seven_daily_documents=True)
    assert not list(tmp_path.iterdir())


def test_next_followup_binds_durable_chat_and_exact_day(monkeypatch, tmp_path):
    docs, turns, _, _ = week.plan()
    first, _, _, followup = turns[:4]
    assert first.session == followup.session
    scope = dict(root=str(tmp_path), controller=week.process_identity(), namespace='native-week')
    (tmp_path / 'answer-admissions').mkdir()
    (tmp_path / 'ready-inventory.json').write_text(json.dumps(dict(namespace='native-week', sources=[
        dict(name=docs[0].filename, sha256=docs[0].sha256, cutoff=docs[0].cutoff)])))
    with WorkflowProgress(tmp_path / 'workflow', 'test', create=True) as progress:
        for idx, turn in enumerate((first, followup)):
            progress.plan_turn(0 if idx == 0 else 3, turn)
        progress.effect('answer', first.id, dict(at=first.at, prompt=first.prompt, session_id=None), lambda: {'native': True}, role='driver')
        surface = dict(session_id='same-hermes', gateway_identity=[42, 123], history=[], conversation_times=[first.at], model_requests=[])
        progress.verified_turn(first, surface, {})
        progress.mark_turn_done(first.id)

        @contextmanager
        def reader(_):
            with WorkflowProgress(tmp_path / 'workflow', 'test', read_only=True) as observer:
                yield observer

        monkeypatch.setattr(week, 'progress_reader', reader)

        def continuation():
            with pytest.raises(OwnershipConflict, match='native session'):
                week.prepare_driver(scope, followup.prompt, followup.at, 'different-hermes')
            week.prepare_driver(scope, followup.prompt, followup.at, 'same-hermes')
            with pytest.raises(FileExistsError):
                week.prepare_driver(scope, followup.prompt, followup.at, 'same-hermes')
            return {'native': True}

        result = progress.effect('answer', followup.id, dict(at=followup.at, prompt=followup.prompt,
                                 session_id='same-hermes'), continuation, role='driver')
        assert result == {'native': True}
        with pytest.raises(OwnershipConflict, match='active durable'):
            week.active_answer(scope)


def test_final_exact_answer_is_independent_of_a_missing_terminal_marker():
    response = dict(http_status=200, finished='now', response_validation=dict(finish_reasons=['stop'], envelope_errors=[],
        wire_parse_errors=0, assistant_text_sha256=sha256(b'Synthetic answer.').hexdigest(), sse_done_observed=False))
    assert week.verified_final_response(response, 'Synthetic answer.')
    assert not week.verified_final_response(response, 'Different answer.')
