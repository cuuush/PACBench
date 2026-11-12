"""Text-only benchmark calls through a locally authenticated Codex app-server.

Codex owns authentication: this module never opens, copies, or exports tokens.
It is not an OpenAI-compatible proxy and does not run Hermes/provider tools.
Protocol fields are checked against the installed CLI's generated schema before
starting a server. Config, model, and instruction mismatches fail before a turn.
See https://learn.chatgpt.com/docs/app-server and config-file/config-reference.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context_isolation import outside_project
from .model_calls import ModelOutput, _read_record, _write_record
from .stop import OperatorStop, check_stop

PROTOCOL = 'codex-subscription-text-v1'
DEFAULT_MODEL = 'gpt-6-luna'
BASE_INSTRUCTIONS = ('You perform a text-only benchmark authoring or evaluation task. '
                     'Use only the supplied text. Do not call tools, access files, browse, '
                     'delegate, or perform external actions. Return the requested answer.')
DISABLED_FEATURES = (
    'shell_tool', 'shell_snapshot', 'apps', 'plugins', 'remote_plugin',
    'browser_use', 'browser_use_external', 'computer_use', 'image_generation',
    'view_image', 'multi_agent', 'multi_agent_v2', 'code_mode', 'code_mode_host',
    'memories', 'hooks', 'skill_search', 'skill_mcp_dependency_install',
    'tool_suggest', 'sleep_tool', 'goals',
)
CONFIG = {
    'model_provider': 'openai', 'forced_login_method': 'chatgpt',
    'approval_policy': 'never', 'sandbox_mode': 'read-only',
    'web_search': 'disabled', 'mcp_servers': {}, 'model_providers': {},
    'project_doc_max_bytes': 0,
    'features': {key: False for key in DISABLED_FEATURES},
}
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_STREAM_BYTES = 64 * 1024 * 1024


class CodexCallError(RuntimeError):
    """Safe, fixed failure code; raw upstream diagnostics are not published."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _child_env() -> dict[str, str]:
    # The native CLI may read its own existing login; do not pass API-key or
    # externally supplied token overrides that could silently change billing.
    excluded = {'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN',
                'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'CODEX_THREAD_ID',
                'CODEX_EXEC_SERVER_URL', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'}
    return {key: value for key, value in os.environ.items() if key not in excluded}


def _config_args(workspace: Path) -> list[str]:
    values = {**CONFIG, 'model_instructions_file': str(workspace / 'instructions.txt'),
              'sqlite_home': str(workspace / 'state')}
    args: list[str] = []
    for key, value in values.items():
        if key == 'features':
            for name, enabled in value.items():
                args += ['-c', f'features.{name}={str(enabled).lower()}']
        else:
            # JSON strings, bools, empty objects and integers are also TOML.
            args += ['-c', f'{key}={json.dumps(value)}']
    return args


def _verify_schema(codex_bin: str, workspace: Path, timeout_s: float) -> dict:
    """Offline compatibility check. Never launch a partially protected old CLI."""
    schema_dir = workspace / 'schema'
    try:
        version = subprocess.run([codex_bin, '--version'], cwd=workspace,
            env=_child_env(), capture_output=True, timeout=min(timeout_s, 15), check=True)
        subprocess.run([codex_bin, 'app-server', 'generate-json-schema', '--experimental',
                        '--out', str(schema_dir)], cwd=workspace, env=_child_env(),
                       capture_output=True, timeout=min(timeout_s, 30), check=True)
        required = {
            'ThreadStartParams': {'allowProviderModelFallback', 'environments', 'ephemeral',
                                  'baseInstructions', 'dynamicTools', 'selectedCapabilityRoots'},
            'ThreadStartResponse': {'model', 'modelProvider', 'instructionSources', 'cwd'},
            'TurnStartParams': {'environments', 'model', 'effort'},
        }
        digests = {}
        for name, fields in required.items():
            raw = (schema_dir / 'v2' / (name + '.json')).read_bytes()
            if not fields <= json.loads(raw).get('properties', {}).keys():
                raise CodexCallError('unsupported_codex_protocol')
            digests[name] = hashlib.sha256(raw).hexdigest()
        version_text = version.stdout.decode('utf-8', errors='replace').strip()
        # Only save an ordinary version string, never arbitrary subprocess output.
        if not version_text.startswith('codex-cli ') or len(version_text) > 100:
            raise CodexCallError('invalid_codex_version')
        return {'version': version_text, 'schema_sha256': digests}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise CodexCallError('codex_protocol_check_failed') from exc


class _AppServer:
    """Bounded JSONL transport with cancellation and process-group cleanup."""

    def __init__(self, codex_bin: str, workspace: Path, deadline: float):
        self.deadline = deadline
        self.next_id = 0
        self.events: queue.Queue = queue.Queue()
        self.stderr_sha = hashlib.sha256()
        self.stderr_bytes = 0
        self.process = subprocess.Popen(
            [codex_bin, 'app-server', '--listen', 'stdio://', *_config_args(workspace)],
            cwd=workspace, env=_child_env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read_stdout(self):
        total = 0
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    self.events.put(CodexCallError('codex_server_closed'))
                    return
                total += len(line)
                if len(line) > MAX_LINE_BYTES or total > MAX_STREAM_BYTES:
                    self.events.put(CodexCallError('codex_stream_limit'))
                    return
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError()
                    self.events.put(event)
                except ValueError:
                    self.events.put(CodexCallError('invalid_codex_json'))
                    return
        except (OSError, ValueError):
            self.events.put(CodexCallError('codex_read_failed'))

    def _read_stderr(self):
        try:
            while chunk := self.process.stderr.read(8192):
                self.stderr_sha.update(chunk)
                self.stderr_bytes += len(chunk)
        except (OSError, ValueError):
            pass

    def send(self, method: str, params: dict, *, notification=False) -> int | None:
        message = {'method': method, 'params': params}
        if not notification:
            self.next_id += 1
            message['id'] = self.next_id
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexCallError('codex_write_failed') from exc
        return message.get('id')

    def receive(self) -> dict:
        while True:
            check_stop()
            if time.monotonic() >= self.deadline:
                raise CodexCallError('codex_timeout')
            try:
                event = self.events.get(timeout=min(.1, max(.001, self.deadline-time.monotonic())))
            except queue.Empty:
                continue
            if isinstance(event, Exception):
                raise event
            # Never grant approvals, execute dynamic tools, supply tokens, or
            # answer any new server request. Even unfamiliar requests stop work.
            if 'method' in event and 'id' in event:
                try:
                    self.process.stdin.write((json.dumps({'id': event['id'], 'error': {
                        'code': -32601, 'message': 'Benchmark client does not execute tools'}})+'\n').encode())
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    pass
                raise CodexCallError('unexpected_codex_server_request')
            return event

    def call(self, method: str, params: dict, consume) -> dict:
        request_id = self.send(method, params)
        while True:
            event = self.receive()
            if event.get('id') == request_id:
                if 'error' in event:
                    raise CodexCallError('codex_rpc_failed_' + method.replace('/', '_'))
                result = event.get('result')
                if not isinstance(result, dict):
                    raise CodexCallError('invalid_codex_rpc_result')
                return result
            consume(event)

    def close(self, thread_id: str | None, turn_id: str | None) -> dict:
        interrupted = False
        if thread_id and turn_id and self.process.poll() is None:
            try:
                self.send('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                interrupted = True
            except CodexCallError:
                pass
        # Interrupt submission is not confirmation of stopped inference. Reap
        # the owned process group; remote usage after a disconnect remains unknown.
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
            except ProcessLookupError:
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()
        for reader in self.readers:
            reader.join(timeout=1)
        return {'interrupt_requested': interrupted, 'process_returncode': self.process.returncode,
                'stderr_bytes': self.stderr_bytes, 'stderr_sha256': self.stderr_sha.hexdigest()}


def _check_config(result: dict, workspace: Path) -> dict:
    config = result.get('config')
    if not isinstance(config, dict):
        raise CodexCallError('missing_codex_effective_config')
    for key, expected in CONFIG.items():
        if key in ('mcp_servers', 'model_providers'):
            if config.get(key, {}) != {}:
                raise CodexCallError('codex_external_configuration_present')
        elif key == 'features':
            if any(config.get(key, {}).get(name) is not False for name in DISABLED_FEATURES):
                raise CodexCallError('codex_tools_not_disabled')
        elif config.get(key) != expected:
            raise CodexCallError('codex_configuration_mismatch')
    if config.get('model_instructions_file') != str(workspace / 'instructions.txt'):
        raise CodexCallError('codex_instruction_configuration_mismatch')
    if config.get('sqlite_home') != str(workspace / 'state'):
        raise CodexCallError('codex_state_configuration_mismatch')
    # Publish only the known overrides. Never persist the raw config: it can
    # contain user-defined headers, credentials, or unrelated personal settings.
    return {'verified': True, 'overrides': CONFIG}


def _usage(raw: dict | None) -> dict:
    if not raw:
        return {}
    total = raw.get('total')
    if not isinstance(total, dict):
        return {}
    names = {'inputTokens': 'input_tokens', 'cachedInputTokens': 'cached_input_tokens',
             'outputTokens': 'output_tokens', 'reasoningOutputTokens': 'reasoning_tokens',
             'totalTokens': 'total_tokens', 'cacheWriteInputTokens': 'cache_write_input_tokens'}
    result = {}
    for original, normalized in names.items():
        value = total.get(original)
        if type(value) is int and value >= 0:
            result[normalized] = value
    return result


class CodexModelClient:
    """Audited author/judge adapter; use the user's native ChatGPT login.

    Every uncached request gets a fresh ephemeral thread. Conversation replay is
    explicit text, not hidden Codex state. Temperature is intentionally unsupported;
    no undocumented API parameters, USD estimates, or replacement model are used.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, record_dir: Path,
                 reasoning_effort: str | None = None, json_mode: bool = False,
                 cache_dir: Path | None = None, codex_bin: str = 'codex',
                 timeout_s: float = 300, scratch_root: Path | None = None):
        if not model or model.strip() != model:
            raise ValueError('An exact nonempty Codex model slug is required')
        if timeout_s <= 0:
            raise ValueError('timeout_s must be positive')
        self.model, self.record_dir = model, Path(record_dir)
        self.reasoning_effort, self.json_mode = reasoning_effort, json_mode
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.codex_bin, self.timeout_s = codex_bin, timeout_s
        self.scratch_root = Path(scratch_root) if scratch_root else None

    def __call__(self, prompt: str) -> ModelOutput:
        return self.complete([{'role': 'user', 'content': prompt}])

    def continue_from(self, prior: ModelOutput, initial_prompt: str, feedback: str) -> ModelOutput:
        path = Path(prior.record_path).resolve()
        if not path.is_relative_to(self.record_dir.resolve()):
            raise ValueError('Codex continuation requires this run\'s model record')
        saved = _read_record(path)
        if (saved.get('status') != 'ok' or saved.get('protocol') != PROTOCOL or
                saved.get('model') != self.model or saved.get('text') != prior.text or
                saved.get('request', {}).get('messages', [{}])[0] !=
                {'role': 'user', 'content': initial_prompt}):
            raise ValueError('Codex continuation does not match its original request')
        return self.complete([*saved['request']['messages'],
                              {'role': 'assistant', 'content': prior.text},
                              {'role': 'user', 'content': feedback}])

    def complete(self, messages: list[dict[str, str]]) -> ModelOutput:
        if not messages or any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
                m['role'] not in ('user', 'assistant', 'system', 'developer') or
                not isinstance(m['content'], str) for m in messages):
            raise ValueError('Only explicit text messages are supported')
        request = {'model': self.model, 'messages': messages, 'reasoning_effort': self.reasoning_effort,
                   'json_mode': self.json_mode, 'base_instructions': BASE_INSTRUCTIONS,
                   'tool_policy': CONFIG, 'continuation': 'explicit-text-replay-v1'}
        key = _sha({'protocol': PROTOCOL, 'request': request})
        self.record_dir.mkdir(parents=True, exist_ok=True)
        for directory in (self.record_dir, self.cache_dir):
            if directory:
                for path in sorted(directory.glob(f'{key}*.json')):
                    saved = _read_record(path)
                    if (saved.get('status') == 'ok' and saved.get('request') == request and
                            saved.get('protocol') == PROTOCOL and saved.get('model') == self.model):
                        if directory != self.record_dir:
                            # Reused historical tokens are not newly incurred usage.
                            destination = self._record_path(key)
                            _write_record(destination, {**saved, 'cache_hit': True,
                                'cache_source_record': str(path), 'inference_this_run': False,
                                'cost_this_run_usd': None})
                            path = destination
                        return ModelOutput(saved['text'], self.model, saved.get('usage') or {}, str(path), key)
        path = self._record_path(key)
        started = time.monotonic()
        record = {'created_at': datetime.now(timezone.utc).isoformat(), 'protocol': PROTOCOL,
                  'request': request, 'request_sha': key, 'model': self.model, 'status': 'failed',
                  'text': '', 'usage': {}, 'cost_usd': None, 'cost_status': 'subscription-unpriced',
                  'inference_this_run': False, 'events': [], 'response': None}
        server = None
        thread_id = turn_id = None
        finished = False
        failure: BaseException | None = None
        temporary = None
        try:
            check_stop()
            temporary = tempfile.TemporaryDirectory(prefix='pacbench-codex-', dir=self.scratch_root)
            tmp = temporary.name
            workspace = Path(tmp).resolve()
            if not outside_project(workspace):
                raise CodexCallError('codex_workspace_not_isolated')
            record['workspace'] = str(workspace)
            (workspace / 'instructions.txt').write_text(BASE_INSTRUCTIONS, encoding='utf-8')
            record['codex'] = _verify_schema(self.codex_bin, workspace, self.timeout_s)
            server = _AppServer(self.codex_bin, workspace, started+self.timeout_s)
            state = {'thread_id': None, 'turn_id': None, 'completed': None, 'items': {}, 'usage': None}

            def consume(event):
                method, params = event.get('method'), event.get('params', {})
                if not isinstance(params, dict):
                    raise CodexCallError('invalid_codex_event')
                # Never record unknown payloads or reasoning deltas.
                if method == 'model/rerouted':
                    raise CodexCallError('codex_model_rerouted')
                if method == 'error':
                    raise CodexCallError('codex_turn_error')
                if method in ('item/started', 'item/completed'):
                    item = params.get('item', {})
                    if item.get('type') not in ('userMessage', 'agentMessage', 'reasoning'):
                        raise CodexCallError('codex_tool_or_context_item_forbidden')
                    if method == 'item/completed' and item.get('type') == 'agentMessage':
                        state['items'][item['id']] = item
                if method in ('turn/started', 'turn/completed'):
                    turn = params.get('turn', {})
                    if state['turn_id'] and state['turn_id'] != turn.get('id'):
                        raise CodexCallError('codex_turn_identity_mismatch')
                    state['turn_id'] = turn.get('id')
                    if method == 'turn/completed':
                        state['completed'] = turn
                if method == 'thread/tokenUsage/updated':
                    state['usage'] = params.get('tokenUsage')
                if method in ('turn/started', 'turn/completed', 'thread/tokenUsage/updated',
                              'item/started', 'item/completed'):
                    if params.get('threadId') != state['thread_id']:
                        raise CodexCallError('codex_thread_identity_mismatch')
                    if params.get('turnId') and state['turn_id'] and params['turnId'] != state['turn_id']:
                        raise CodexCallError('codex_turn_identity_mismatch')
                    record['events'].append({'method': method, 'item_type': params.get('item', {}).get('type')})

            server.call('initialize', {'clientInfo': {'name': 'pacbench', 'version': '0.1.0'},
                        'capabilities': {'experimentalApi': True}}, consume)
            server.send('initialized', {}, notification=True)
            record['configuration'] = _check_config(server.call('config/read', {
                'cwd': str(workspace), 'includeLayers': False}, consume), workspace)
            account = server.call('account/read', {'refreshToken': False}, consume).get('account')
            if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                raise CodexCallError('codex_chatgpt_login_required')
            record['auth_type'] = 'chatgpt'  # Never save email, workspace, or tokens.
            cursor = None
            seen = set()
            model = None
            for _ in range(100):
                inventory = server.call('model/list', {'includeHidden': True, 'limit': 100,
                                                      'cursor': cursor}, consume)
                model = next((row for row in inventory.get('data', [])
                              if row.get('model') == self.model), None)
                if model:
                    break
                cursor = inventory.get('nextCursor')
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
            if model is None:
                raise CodexCallError('codex_exact_model_unavailable')
            if self.reasoning_effort is not None and self.reasoning_effort not in {
                    row.get('reasoningEffort') for row in model.get('supportedReasoningEfforts', [])}:
                raise CodexCallError('codex_reasoning_effort_unavailable')
            record['catalog'] = {'model': self.model, 'access_verified_by_catalog': False}
            thread_params = {'model': self.model, 'modelProvider': 'openai', 'cwd': str(workspace),
                'ephemeral': True, 'allowProviderModelFallback': False, 'environments': [],
                'selectedCapabilityRoots': [], 'dynamicTools': [], 'approvalPolicy': 'never',
                'sandbox': 'read-only', 'baseInstructions': BASE_INSTRUCTIONS,
                'developerInstructions': '', 'experimentalRawEvents': False}
            thread = server.call('thread/start', thread_params, consume)
            if thread.get('model') != self.model or thread.get('modelProvider') != 'openai':
                raise CodexCallError('codex_resolved_model_mismatch')
            if (thread.get('cwd') != str(workspace) or thread.get('instructionSources') != [] or
                    thread.get('approvalPolicy') != 'never' or
                    thread.get('sandbox', {}).get('type') != 'readOnly' or
                    thread.get('sandbox', {}).get('networkAccess', False)):
                raise CodexCallError('codex_thread_configuration_mismatch')
            thread_id = state['thread_id'] = thread['thread']['id']
            record['thread_id'] = thread_id
            prompt = (messages[0]['content'] if len(messages) == 1 and messages[0]['role'] == 'user'
                      else 'The following is the complete text conversation. Continue with the final answer.\n'
                           + json.dumps(messages, ensure_ascii=False))
            if self.json_mode:
                prompt += '\nReturn one valid JSON object only, with no Markdown fences.'
            turn_params = {'threadId': thread_id, 'model': self.model, 'environments': [],
                           'input': [{'type': 'text', 'text': prompt}]}
            if self.reasoning_effort is not None:
                turn_params['effort'] = self.reasoning_effort
            record['native_request'] = {'thread': thread_params, 'turn': turn_params}
            record['inference_this_run'] = True
            response = server.call('turn/start', turn_params, consume)
            turn_id = response['turn']['id']
            if state['turn_id'] and state['turn_id'] != turn_id:
                raise CodexCallError('codex_turn_identity_mismatch')
            state['turn_id'] = turn_id
            record['turn_id'] = turn_id
            while state['completed'] is None:
                consume(server.receive())
            completed = state['completed']
            record['usage'] = _usage(state['usage'])
            record['native_token_usage'] = state['usage']
            record['response'] = {'status': completed.get('status'), 'id': completed.get('id')}
            if completed.get('status') != 'completed':
                raise CodexCallError('codex_turn_not_completed')
            for item in completed.get('items', []):
                if item.get('type') not in ('userMessage', 'agentMessage', 'reasoning'):
                    raise CodexCallError('codex_tool_or_context_item_forbidden')
                if item.get('type') == 'agentMessage':
                    state['items'][item['id']] = item
            finals = [item for item in state['items'].values()
                      if item.get('phase') in (None, 'final_answer')]
            text = '\n'.join(item.get('text', '') for item in finals)
            record['text'] = text
            if not text.strip():
                raise CodexCallError('codex_empty_final_answer')
            if self.json_mode:
                try:
                    if not isinstance(json.loads(text), dict):
                        raise ValueError()
                except ValueError as exc:
                    raise CodexCallError('codex_invalid_json_answer') from exc
            record['status'] = 'ok'
            finished = True
            record['cleanup'] = server.close(None, None)
            server = None
        except BaseException as exc:
            failure = exc
            record['error'] = exc.code if isinstance(exc, CodexCallError) else type(exc).__name__
            record['status'] = 'cancelled' if isinstance(exc, (KeyboardInterrupt, OperatorStop)) else 'failed'
            if 'state' in locals():
                record['usage'] = _usage(state['usage'])
                record['native_token_usage'] = state['usage']
                turn_id = turn_id or state['turn_id']
        finally:
            if server:
                try:
                    record['cleanup'] = server.close(thread_id, turn_id if not finished else None)
                except Exception as exc:
                    record['cleanup'] = {'state': 'unconfirmed', 'error_class': type(exc).__name__}
                    failure = failure or CodexCallError('codex_cleanup_failed')
                    record['status'] = 'failed'
                    record.setdefault('error', 'codex_cleanup_failed')
            if temporary:
                try:
                    temporary.cleanup()
                except OSError as exc:
                    record['workspace_cleanup_error'] = type(exc).__name__
            record['latency_s'] = round(time.monotonic()-started, 6)
            record['usage_status'] = 'observed' if record['usage'] else 'unreported'
            if failure and record['inference_this_run']:
                record['remote_completion'] = 'uncertain; no automatic retry'
            _write_record(path, record)
        if failure:
            if isinstance(failure, (KeyboardInterrupt, SystemExit, OperatorStop)):
                raise failure
            raise CodexCallError(f"{record['error']}; audit record {path}") from failure
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached = self.cache_dir / (key + '.json')
            if not cached.exists():
                _write_record(cached, record)
        return ModelOutput(record['text'], self.model, record['usage'], str(path), key)

    def _record_path(self, key: str) -> Path:
        path = self.record_dir / (key + '.json')
        suffix = 2
        while path.exists():
            path = self.record_dir / f'{key}-{suffix}.json'
            suffix += 1
        return path
