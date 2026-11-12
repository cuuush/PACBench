"""Offline protocol controls; no Codex process, login, or model call is needed."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from pacbench import codex_model_calls as c
from pacbench.stop import OperatorStop

REAL_SERVER = c._AppServer
REAL_VERIFY_SCHEMA = c._verify_schema


class FakeServer:
    instances = []
    scenario = 'ok'

    def __init__(self, binary, workspace, deadline):
        self.workspace, self.calls, self.events = workspace, [], []
        self.closed = None
        self.instances.append(self)

    def call(self, method, params, consume):
        self.calls.append((method, params))
        scenario = type(self).scenario
        if method == 'initialize':
            return {}
        if method == 'config/read':
            config = json.loads(json.dumps(c.CONFIG))
            config['model_instructions_file'] = str(self.workspace / 'instructions.txt')
            config['sqlite_home'] = str(self.workspace / 'state')
            if scenario == 'mcp':
                config['mcp_servers'] = {'secret': {'env': {'TOKEN': 'never-save-this-secret'}}}
            if scenario == 'tools_enabled':
                config['features']['shell_tool'] = True
            return {'config': config}
        if method == 'account/read':
            return {'account': {'type': 'apiKey' if scenario == 'api_key' else 'chatgpt',
                                'email': 'private@example.org'}}
        if method == 'model/list':
            if scenario == 'pagination' and not params['cursor']:
                return {'data': [], 'nextCursor': 'next'}
            return {'data': [] if scenario == 'model_missing' else [
                {'model': c.DEFAULT_MODEL, 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]}
        if method == 'thread/start':
            return {'model': 'other' if scenario == 'fallback' else params['model'],
                    'modelProvider': 'openai', 'cwd': str(self.workspace),
                    'instructionSources': ['/project/AGENTS.md'] if scenario == 'instructions' else [],
                    'approvalPolicy': 'never', 'sandbox': {'type': 'readOnly'},
                    'thread': {'id': 'thread-1'}}
        if method == 'turn/start':
            item = {'id': 'answer-1', 'type': 'agentMessage', 'text': '{"approved":true}',
                    'phase': 'final_answer'}
            common = {'threadId': 'thread-1', 'turnId': 'turn-1'}
            consume({'method': 'turn/started', 'params': {
                'threadId': 'thread-1', 'turn': {'id': 'turn-1', 'status': 'inProgress'}}})
            self.events = [
                {'method': 'item/completed', 'params': {**common, 'item': item}},
                {'method': 'thread/tokenUsage/updated', 'params': {**common, 'tokenUsage': {
                    'total': {'inputTokens': 12, 'outputTokens': 8, 'cachedInputTokens': 2,
                              'reasoningOutputTokens': 3, 'totalTokens': 20},
                    'last': {'inputTokens': 12, 'outputTokens': 8, 'totalTokens': 20}}}},
                {'method': 'turn/completed', 'params': {'threadId': 'thread-1', 'turn': {
                    'id': 'turn-1', 'status': 'completed', 'items': [item]}}}]
            if scenario == 'missing_usage':
                del self.events[1]
            elif scenario == 'tool':
                self.events.insert(0, {'method': 'item/started', 'params': {
                    **common, 'item': {'id': 'tool-1', 'type': 'commandExecution'}}})
            elif scenario == 'rerouted':
                self.events.insert(0, {'method': 'model/rerouted', 'params': {
                    **common, 'toModel': 'another-model'}})
            elif scenario == 'other_turn':
                self.events[-1]['params']['turn']['id'] = 'another-turn'
            elif scenario == 'other_thread':
                self.events[0]['params']['threadId'] = 'unrelated'
            elif scenario == 'failure':
                self.events[-1]['params']['turn']['status'] = 'failed'
            elif scenario == 'final_tool':
                self.events[-1]['params']['turn']['items'].append({'type': 'webSearch'})
            elif scenario == 'invalid_json':
                item['text'] = 'not json'
            elif scenario == 'commentary':
                item['phase'] = 'commentary'
            return {'turn': {'id': 'turn-1'}}
        raise AssertionError(method)

    def send(self, method, params, **kwargs):
        self.calls.append((method, params))

    def receive(self):
        if type(self).scenario == 'timeout':
            raise c.CodexCallError('codex_timeout')
        if type(self).scenario == 'cancelled':
            raise OperatorStop('operator stop requested')
        return self.events.pop(0)

    def close(self, thread_id, turn_id):
        assert self.workspace.exists(), 'Stop process before removing workspace'
        self.closed = (thread_id, turn_id)
        return {'process_returncode': 0, 'interrupt_requested': bool(turn_id)}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeServer.instances, FakeServer.scenario = [], 'ok'
    monkeypatch.setattr(c, '_AppServer', FakeServer)
    monkeypatch.setattr(c, '_verify_schema', lambda *args: {'version': 'codex-cli fake'})
    monkeypatch.setattr(c, 'outside_project', lambda _: True)
    monkeypatch.delenv('PACBENCH_STOP_FILE', raising=False)
    # Tripwire: all controls in this file must remain offline.
    monkeypatch.setattr(c.subprocess, 'Popen', Mock(side_effect=AssertionError('No live subprocess')))
    monkeypatch.setattr(c.subprocess, 'run', Mock(side_effect=AssertionError('No live subprocess')))


def client(tmp_path, **kwargs):
    return c.CodexModelClient(record_dir=tmp_path / 'records', scratch_root=tmp_path, **kwargs)


def saved(tmp_path):
    return [json.loads(p.read_text()) for p in sorted((tmp_path / 'records').glob('*.json'))]


def test_success_is_native_exact_model_and_unpriced(tmp_path):
    output = client(tmp_path, reasoning_effort='low', json_mode=True)('Review this')
    server = FakeServer.instances[0]
    calls = dict(server.calls)
    assert output.text == '{"approved":true}'
    assert output.model == 'gpt-6-luna'
    assert output.usage['total_tokens'] == 20
    assert output.usage['cached_input_tokens'] == 2
    assert calls['thread/start']['ephemeral'] is True
    assert calls['thread/start']['allowProviderModelFallback'] is False
    assert calls['thread/start']['environments'] == []
    assert calls['thread/start']['dynamicTools'] == []
    assert calls['turn/start']['environments'] == []
    assert calls['turn/start']['model'] == output.model
    assert calls['turn/start']['effort'] == 'low'
    row = saved(tmp_path)[0]
    assert row['cost_usd'] is None and row['cost_status'] == 'subscription-unpriced'
    assert row['usage_status'] == 'observed' and row['status'] == 'ok'
    assert 'private@example.org' not in json.dumps(row)
    assert not Path(row['workspace']).exists()
    assert Path(output.record_path).stat().st_mode & 0o077 == 0
    assert server.closed == (None, None)


@pytest.mark.parametrize('scenario,error', [
    ('mcp', 'external_configuration'), ('tools_enabled', 'tools_not_disabled'),
    ('api_key', 'chatgpt_login_required'), ('model_missing', 'exact_model_unavailable'),
    ('fallback', 'resolved_model_mismatch'), ('instructions', 'thread_configuration_mismatch')])
def test_configuration_failures_stop_before_inference(tmp_path, scenario, error):
    FakeServer.scenario = scenario
    with pytest.raises(c.CodexCallError, match=error):
        client(tmp_path)('Review')
    assert 'turn/start' not in dict(FakeServer.instances[0].calls)
    row = saved(tmp_path)[0]
    assert row['status'] == 'failed' and not row['inference_this_run']
    assert 'never-save-this-secret' not in json.dumps(row)


@pytest.mark.parametrize('scenario,error', [
    ('tool', 'tool_or_context_item'), ('final_tool', 'tool_or_context_item'),
    ('rerouted', 'model_rerouted'), ('other_turn', 'turn_identity'),
    ('other_thread', 'thread_identity'), ('failure', 'turn_not_completed'),
    ('timeout', 'codex_timeout'), ('commentary', 'empty_final_answer')])
def test_bad_turns_remain_failed_without_retry(tmp_path, scenario, error):
    FakeServer.scenario = scenario
    with pytest.raises(c.CodexCallError, match=error):
        client(tmp_path)('Review')
    assert len(FakeServer.instances) == 1
    row = saved(tmp_path)[0]
    assert row['status'] == 'failed'
    assert row['remote_completion'] == 'uncertain; no automatic retry'
    assert FakeServer.instances[0].closed == ('thread-1', 'turn-1')


def test_cancellation_is_preserved_and_cleans_process(tmp_path):
    FakeServer.scenario = 'cancelled'
    with pytest.raises(OperatorStop):
        client(tmp_path)('Review')
    assert saved(tmp_path)[0]['status'] == 'cancelled'
    assert FakeServer.instances[0].closed == ('thread-1', 'turn-1')


def test_missing_tokens_are_unknown_not_zero(tmp_path):
    FakeServer.scenario = 'missing_usage'
    output = client(tmp_path)('Review')
    assert output.usage == {}
    row = saved(tmp_path)[0]
    assert row['usage_status'] == 'unreported' and row['cost_usd'] is None


def test_failed_call_retains_observed_usage(tmp_path):
    FakeServer.scenario = 'failure'
    with pytest.raises(c.CodexCallError):
        client(tmp_path)('Review')
    assert saved(tmp_path)[0]['usage']['total_tokens'] == 20


def test_exact_success_cache_prevents_duplicate_inference(tmp_path):
    model = client(tmp_path)
    first = model('Review')
    second = model('Review')
    assert first == second and len(FakeServer.instances) == 1


def test_cache_copy_is_reused_historical_not_free_inference(tmp_path):
    cache = tmp_path / 'cache'
    first = client(tmp_path, cache_dir=cache)('Review')
    second = c.CodexModelClient(record_dir=tmp_path / 'second', cache_dir=cache)('Review')
    assert first.text == second.text and len(FakeServer.instances) == 1
    row = json.loads(Path(second.record_path).read_text())
    assert row['cache_hit'] and row['inference_this_run'] is False
    assert row['cost_this_run_usd'] is None


def test_failure_receipts_are_not_overwritten(tmp_path):
    FakeServer.scenario = 'failure'
    for _ in range(2):
        with pytest.raises(c.CodexCallError):
            client(tmp_path)('Review')
    assert len(saved(tmp_path)) == 2


def test_writer_continuation_replays_only_verified_text(tmp_path):
    model = client(tmp_path)
    first = model('Write scene')
    model.continue_from(first, 'Write scene', 'Correct the date')
    replay = dict(FakeServer.instances[-1].calls)['turn/start']['input'][0]['text']
    assert 'Write scene' in replay and first.text.replace('"', '\\"') in replay
    assert 'Correct the date' in replay
    with pytest.raises(ValueError, match='original request'):
        model.continue_from(first, 'Different source', 'Try again')


def test_unsupported_reasoning_stops_before_turn(tmp_path):
    with pytest.raises(c.CodexCallError, match='reasoning_effort_unavailable'):
        client(tmp_path, reasoning_effort='max')('Review')
    assert 'turn/start' not in dict(FakeServer.instances[0].calls)


def test_paginated_exact_catalog(tmp_path):
    FakeServer.scenario = 'pagination'
    client(tmp_path)('Review')
    assert len([x for x in FakeServer.instances[0].calls if x[0] == 'model/list']) == 2


def test_nonisolated_workspace_never_starts_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(c, 'outside_project', lambda _: False)
    with pytest.raises(c.CodexCallError, match='workspace_not_isolated'):
        client(tmp_path)('Review')
    assert not FakeServer.instances


def test_api_key_environment_never_passed_to_native_process(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    monkeypatch.setenv('CODEX_ACCESS_TOKEN', 'secret')
    monkeypatch.setenv('CODEX_HOME', '/native/codex/home')
    env = c._child_env()
    assert 'OPENAI_API_KEY' not in env and 'CODEX_ACCESS_TOKEN' not in env
    assert env['CODEX_HOME'] == '/native/codex/home'


def test_config_invocation_disables_tools_and_does_not_change_auth_home(tmp_path):
    args = c._config_args(tmp_path)
    assert 'features.shell_tool=false' in args
    assert 'features.hooks=false' in args
    assert 'mcp_servers={}' in args
    assert 'web_search="disabled"' in args
    assert 'sqlite_home=' + json.dumps(str(tmp_path / 'state')) in args
    assert not any('auth' in x or 'CODEX_HOME' in x for x in args)


def test_invalid_json_is_inspectable(tmp_path):
    FakeServer.scenario = 'invalid_json'
    with pytest.raises(c.CodexCallError, match='invalid_json_answer'):
        client(tmp_path, json_mode=True)('Review')
    assert saved(tmp_path)[0]['text'] == 'not json'


def bare_transport():
    import io
    import queue
    import time
    server = REAL_SERVER.__new__(REAL_SERVER)
    server.deadline = time.monotonic() + 2
    server.next_id = 0
    server.events = queue.Queue()
    server.process = Mock()
    server.process.stdin = io.BytesIO()
    return server


def test_transport_refuses_all_server_requests_before_external_execution():
    server = bare_transport()
    server.events.put({'method': 'item/tool/call', 'id': 'external',
                       'params': {'tool': 'send_email'}})
    with pytest.raises(c.CodexCallError, match='unexpected_codex_server_request'):
        server.receive()
    reply = json.loads(server.process.stdin.getvalue())
    assert reply['id'] == 'external' and reply['error']['code'] == -32601
    assert 'result' not in reply


def test_transport_deadline_does_not_wait_for_process_output():
    server = bare_transport()
    server.deadline = 0
    with pytest.raises(c.CodexCallError, match='codex_timeout'):
        server.receive()


def test_transport_rejects_malformed_json_without_storing_raw_diagnostics():
    import io
    server = bare_transport()
    server.process.stdout = io.BytesIO(b'private diagnostic is not json\n')
    server._read_stdout()
    with pytest.raises(c.CodexCallError, match='invalid_codex_json') as error:
        server.receive()
    assert 'private diagnostic' not in str(error.value)


def test_transport_rpc_errors_omit_private_error_details():
    server = bare_transport()
    server.events.put({'id': 1, 'error': {'message': 'secret raw error'}})
    with pytest.raises(c.CodexCallError, match='codex_rpc_failed_account_read') as error:
        server.call('account/read', {}, lambda _: None)
    assert 'secret' not in str(error.value)


def test_schema_gate_is_offline_and_requires_environment_and_fallback_fields(tmp_path, monkeypatch):
    def run(command, **kwargs):
        if '--version' in command:
            return Mock(stdout=b'codex-cli 0.167.0-alpha.4')
        directory = tmp_path / 'schema' / 'v2'
        directory.mkdir(parents=True, exist_ok=True)
        fields = {
            'ThreadStartParams': ['allowProviderModelFallback', 'environments', 'ephemeral',
                                 'baseInstructions', 'dynamicTools', 'selectedCapabilityRoots'],
            'ThreadStartResponse': ['model', 'modelProvider', 'instructionSources', 'cwd'],
            'TurnStartParams': ['environments', 'model', 'effort']}
        for name, keys in fields.items():
            (directory / (name + '.json')).write_text(json.dumps({'properties': dict.fromkeys(keys, {})}))
        return Mock()
    runner = Mock(side_effect=run)
    monkeypatch.setattr(c.subprocess, 'run', runner)
    result = REAL_VERIFY_SCHEMA('codex', tmp_path, 20)
    assert result['version'] == 'codex-cli 0.167.0-alpha.4'
    assert len(result['schema_sha256']) == 3
    assert all('turn/start' not in call.args[0] for call in runner.call_args_list)


def test_old_codex_schema_cannot_silently_ignore_no_tools_settings(tmp_path, monkeypatch):
    def run(command, **kwargs):
        if '--version' in command:
            return Mock(stdout=b'codex-cli old')
        directory = tmp_path / 'schema' / 'v2'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'ThreadStartParams.json').write_text('{"properties":{}}')
        return Mock()
    monkeypatch.setattr(c.subprocess, 'run', run)
    with pytest.raises(c.CodexCallError, match='unsupported_codex_protocol'):
        REAL_VERIFY_SCHEMA('codex', tmp_path, 20)


def test_cleanup_failure_does_not_erase_original_failure_receipt(tmp_path, monkeypatch):
    FakeServer.scenario = 'timeout'
    monkeypatch.setattr(FakeServer, 'close', Mock(side_effect=OSError('private cleanup details')))
    with pytest.raises(c.CodexCallError, match='codex_timeout'):
        client(tmp_path)('Review')
    row = saved(tmp_path)[0]
    assert row['error'] == 'codex_timeout'
    assert row['cleanup']['state'] == 'unconfirmed'
    assert 'private cleanup details' not in json.dumps(row)


def test_effective_state_path_must_remain_in_job_workspace(tmp_path):
    config = json.loads(json.dumps(c.CONFIG))
    config['model_instructions_file'] = str(tmp_path / 'instructions.txt')
    config['sqlite_home'] = '/unexpected/shared/state'
    with pytest.raises(c.CodexCallError, match='state_configuration_mismatch'):
        c._check_config({'config': config}, tmp_path)
