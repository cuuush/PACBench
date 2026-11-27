"""Reserve a bounded request before dispatch and settle it using recorded upstream usage."""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .openrouter_route import ENDPOINT, MODEL, PROVIDER, with_route
from .receipt_costs import read_frames


def response_validation(events, complete):
    pieces, reasons, errors = [], [], []
    for event in events:
        if event.get('error') is not None:
            errors.append('upstream_error')
        choices = event.get('choices', [])
        if not isinstance(choices, list):
            errors.append('malformed_choices')
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                errors.append('malformed_choice')
                continue
            if choice.get('error') is not None:
                errors.append('choice_error')
            reason = choice.get('finish_reason')
            if reason is not None:
                reasons.append(reason if reason in ('stop', 'length', 'tool_calls', 'content_filter',
                                                     'function_call', 'error') else 'unknown')
            message = choice.get('message', choice.get('delta', {}))
            if not isinstance(message, dict):
                errors.append('malformed_message')
                continue
            content = message.get('content')
            if isinstance(content, str):
                pieces.append(content)
    return {'body_complete': complete, 'envelope_errors': errors, 'finish_reasons': reasons,
            'assistant_nonwhitespace_characters': len(''.join(pieces).strip())}


class BudgetRefused(ValueError):
    pass

class AuthBlocked(ValueError):
    pass


def _money(value):
    if isinstance(value, bool):
        raise ValueError('invalid monetary value')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid monetary value') from None
    if not result.is_finite() or result < 0:
        raise ValueError('invalid monetary value')
    return result


def _price_float(value):
    exact = _money(value)
    result = float(exact)
    if not math.isfinite(result):
        raise ValueError('invalid price')
    if Decimal(str(result)) > exact:
        result = math.nextafter(result, 0.0)
    return result


class Budget:
    """Exclusive file lock covers reservation, inference and settlement."""
    def __init__(self, path, *, cap, context_tokens, prompt_per_million,
                 completion_per_million, max_completion_tokens, new_run=False):
        self.path = Path(path)
        if not isinstance(context_tokens, int) or isinstance(context_tokens, bool) or context_tokens <= 0:
            raise ValueError('invalid context length')
        if not isinstance(max_completion_tokens, int) or isinstance(max_completion_tokens, bool) or max_completion_tokens <= 0:
            raise ValueError('invalid completion limit')
        self.config = {'cap': str(_money(cap)), 'context_tokens': context_tokens,
                       'prompt_per_million': str(_money(prompt_per_million)),
                       'completion_per_million': str(_money(completion_per_million)),
                       'max_completion_tokens': max_completion_tokens,
                       'model': MODEL, 'endpoint': ENDPOINT}
        if _money(cap) <= 0:
            raise ValueError('positive explicit spending cap required')
        if _money(prompt_per_million) <= 0 or _money(completion_per_million) <= 0:
            raise ValueError('positive explicit input and output price ceilings required')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._run_owned = new_run
        self._input_traces = {}
        self._trace_lock = threading.Lock()
        self._dispatch = threading.local()
        if new_run:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as ledger:
                self._persist_state(ledger, {'version': 1, 'config': self.config, 'calls': []})

    @contextlib.contextmanager
    def input_trace(self, project, arm):
        if not self._run_owned or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,56}-driver', project):
            raise ValueError('API input observer requires a run-owned Hermes driver project')
        fd = os.open(arm, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self._trace_lock:
                if project in self._input_traces:
                    raise ValueError('API input observer is already bound for this driver project')
                self._input_traces[project] = fd
            try:
                yield
            finally:
                with self._trace_lock:
                    del self._input_traces[project]
        finally:
            os.close(fd)

    def claim_dispatch(self, request):
        """Consume the sending thread's reservation for these exact request bytes."""
        if not self._run_owned or getattr(self._dispatch, 'request', None) is not request:
            raise RuntimeError('A durable run-owned reservation is required before inference')
        del self._dispatch.request

    def request(self, project, body, send):
        if body.get('n', 1) not in (None, 1) or body.get('modalities') not in (None, ['text']):
            raise ValueError('unsupported paid feature')
        if any(k in body for k in ('audio', 'prediction', 'service_tier', 'web_search_options', 'max_output_tokens')):
            raise ValueError('unsupported paid feature')
        if any(not isinstance(tool, dict) or tool.get('type') != 'function' for tool in body.get('tools', [])):
            raise ValueError('only function tools are supported')
        for msg in body.get('messages', []):
            if not isinstance(msg, dict):
                raise ValueError('invalid messages')
            content = msg.get('content')
            if content is not None and not isinstance(content, str):
                if not isinstance(content, list) or any(not isinstance(x, dict) or x.get('type') != 'text' or not isinstance(x.get('text'), str) for x in content):
                    raise ValueError('only text content is supported')
        maximum = body.get('max_completion_tokens', body.get('max_tokens', self.config['max_completion_tokens']))
        if ('max_completion_tokens' in body and 'max_tokens' in body or
                not isinstance(maximum, int) or isinstance(maximum, bool) or
                not 0 < maximum <= self.config['max_completion_tokens']):
            raise ValueError('explicit bounded completion limit required')
        bound = (Decimal(self.config['context_tokens']) * _money(self.config['prompt_per_million']) +
                 Decimal(maximum) * _money(self.config['completion_per_million'])) / Decimal(1000000)
        request = dict(body)
        supplied = request.get('provider')
        if supplied is not None:
            if not isinstance(supplied, dict) or any(k not in PROVIDER or PROVIDER[k] != v for k, v in supplied.items()):
                raise ValueError('conflicting provider route')
            request['provider'] = dict(PROVIDER)
        request = with_route(request)
        request['provider']['max_price'] = {'prompt': _price_float(self.config['prompt_per_million']),
                                            'completion': _price_float(self.config['completion_per_million']), 'request': 0}
        if request.get('stream'):
            request['stream_options'] = {**(request.get('stream_options') or {}), 'include_usage': True}
        created = False
        if self._run_owned:
            fd = os.open(self.path, os.O_RDWR)
        else:
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(self.path, os.O_RDWR)
        with os.fdopen(fd, 'r+', encoding='utf-8') as ledger:
            fcntl.flock(ledger, fcntl.LOCK_EX)
            raw = ledger.read()
            if not raw and not created:
                raise ValueError('empty existing ledger; refusing reset')
            state = json.loads(raw) if raw else {'version': 1, 'config': self.config, 'calls': []}
            if created:
                self._persist_state(ledger, state)
            if state.get('version') != 1 or state.get('config') != self.config or not isinstance(state.get('calls'), list):
                raise ValueError('ledger configuration mismatch')
            if any(row.get('http_status') in (401, 403) for row in state['calls']):
                raise AuthBlocked('upstream authentication blocked')
            total = sum((_money(row['accounted']) for row in state['calls']), Decimal(0))
            if state.get('spending_stop'):
                raise BudgetRefused(state['spending_stop']['reason'])
            if total + bound > _money(self.config['cap']):
                if self._run_owned:
                    state['spending_stop'] = {'reason': 'budget_refused', 'project': project,
                                              'accounted_usd': str(total), 'requested_bound_usd': str(bound),
                                              'observed_at': datetime.now(timezone.utc).isoformat()}
                    self._persist_state(ledger, state)
                raise BudgetRefused('budget_refused')
            row = {'id': str(uuid.uuid4()), 'project': project, 'model': MODEL,
                   'started': datetime.now(timezone.utc).isoformat(), 'finished': None,
                   'reserved': str(bound), 'accounted': str(bound), 'state': 'uncertain',
                   'provider_request': request['provider'], 'completion_bound_tokens': maximum,
                   'input_tokens': None, 'output_tokens': None, 'http_status': None, 'error_label': None}
            with self._trace_lock:
                if project in self._input_traces:
                    from pacbench.context_isolation import record_proxy
                    record_proxy(project, {'request': request, 'id': row['id']}, self._input_traces[project])
            state['calls'].append(row)
            self._persist_state(ledger, state)
            try:
                self._dispatch.request = request
                status, data = send(request)
            except Exception:
                row.update(finished=datetime.now(timezone.utc).isoformat(), error_label='upstream_unavailable')
                self._persist_state(ledger, state)
                raise
            finally:
                self._dispatch.request = None
            row.update(finished=datetime.now(timezone.utc).isoformat(), http_status=status,
                       error_label=None if status == 200 else 'upstream_request_failed')
            if status == 402 and self._run_owned:
                row['error_label'] = 'upstream_payment_required'
                state['spending_stop'] = {'reason': row['error_label'], 'project': project,
                                          'request_id': row['id'], 'observed_at': row['finished']}
                self._persist_state(ledger, state)
                raise BudgetRefused(row['error_label'])
            if status == 200:
                frames = read_frames(data)
                events = frames.events
                complete = bool(events) and not frames.errors and (
                    frames.sse and frames.sse_done_observed if request.get('stream') else not frames.sse)
                row['response_validation'] = response_validation(events, complete)
                if any(event.get('model') not in (None, MODEL) for event in events):
                    row['error_label'] = 'model_mismatch'
                    self._persist_state(ledger, state)
                    return 502, b'{"error":{"type":"model_mismatch"}}'
                for event in events:
                    for key, field in (('id', 'upstream_request_id'), ('model', 'served_model'),
                                       ('provider', 'served_provider')):
                        if isinstance(event.get(key), str):
                            row[field] = event[key][:256]
                reasons = row['response_validation']['finish_reasons']
                if reasons:
                    row['finish_reason'] = reasons[-1]
                # Financial evidence is independent of answer usability. An incomplete
                # response retains the full reservation even if a partial usage frame arrived.
                usage = next((e['usage'] for e in reversed(events) if isinstance(e.get('usage'), dict)), {})
                if complete:
                    try:
                        cost = _money(usage['cost'])
                        inp, out = usage['prompt_tokens'], usage['completion_tokens']
                    except (KeyError, ValueError):
                        pass
                    else:
                        if (type(inp) is int and 0 <= inp <= self.config['context_tokens']
                                and type(out) is int and 0 <= out <= maximum and cost <= bound):
                            row.update(accounted=str(cost), state='settled', input_tokens=inp, output_tokens=out)
            self._persist_state(ledger, state)
            return status, data

    def _persist_state(self, ledger, state):
        _persist(ledger, state)
        self._last_snapshot = json.loads(json.dumps(state))

    def _snapshot_state(self):
        if not self._run_owned and not self.path.exists():
            return {'calls': []}
        with self.path.open('r') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                if not hasattr(self, '_last_snapshot'):
                    raise RuntimeError('Task ledger is busy and no durable local snapshot is available') from None
                return self._last_snapshot
            state = json.load(file)
            if state.get('config') != self.config:
                raise ValueError('ledger configuration mismatch')
            return state

    def health(self):
        state = self._snapshot_state()
        calls = state['calls']
        used = sum(_money(row['accounted']) for row in calls)
        stop = state.get('spending_stop')
        completed = used
        if self._run_owned and calls and calls[-1]['finished'] is None:
            # A pending reservation can settle below its bound. Admission waits
            # for that settlement under the lock before making a terminal decision.
            completed -= _money(calls[-1]['accounted'])
        return {'service': 'pacbench-openrouter', 'model': MODEL, 'endpoint': ENDPOINT,
                'limit_usd': self.config['cap'], 'accounted_usd': float(used),
                'budget_blocked': bool(stop) or completed >= _money(self.config['cap']),
                'spending_stop_reason': stop['reason'] if stop else None,
                'auth_blocked': any(row.get('http_status') in (401, 403) for row in calls)}

    def snapshot(self):
        rows = []
        for call in self._snapshot_state()['calls']:
            amount = _money(call['accounted'])
            row = {**call, 'accounted_upper_bound_usd': float(amount)}
            if call['state'] == 'settled':
                row.update(actual_cost_usd=float(amount), actual_cost_usd_decimal=format(amount, 'f'),
                           actual_cost_source='openrouter.usage.cost')
            rows.append(row)
        return rows


def _persist(file, state):
    file.seek(0)
    file.write(json.dumps(state, separators=(',', ':')))
    file.truncate()
    file.flush()
    os.fsync(file.fileno())
