"""Rehearse the real calibration entry point below the guarded HTTP layer."""
from email.message import Message
from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
import urllib.request

import pytest

from pacbench.judging import decision_admission as decisions_admission
from pacbench import direct_http
from pacbench.judging import calibration_live as live
from pacbench import judge_development_challenge as challenge
from pacbench.judging import development_live as prepaid
from pacbench import openrouter_direct
from pacbench.workflows import capability as workflow_capability
from pacbench.judging import decision_matrix as matrix
from tests.test_judge_development_live import synthetic_checkpoint


class Upstream:
    def __init__(self, campaign, key, identity):
        self.campaign = campaign
        self.key = key
        self.identity = identity
        self.calls = []
        self.inventory_after_chat = []
        self.fault = None
        plan = live.frozen_plan()
        self.chat = [cell for cell in plan.cells if cell.arm not in matrix.ROUTES]
        self.decisions = [cell for cell in plan.cells if cell.arm in matrix.ROUTES]
        reference = challenge._frozen(challenge.REFERENCE, challenge.REFERENCE_SHA256)['families']
        self.gold = {cell.id: reference[cell.family['id']]['cases'][cell.key]['verdict']
                     for cell in challenge._blinded_cells(live.SEED)}
        self.miss = next(ident for ident, verdict in self.gold.items() if verdict == 'correct')

    def __call__(self, opener, target, data=None, timeout=None):
        request = target if isinstance(target, urllib.request.Request) else urllib.request.Request(target, data=data)
        url = urlsplit(request.full_url)
        assert url.scheme == 'https' and url.netloc == 'openrouter.ai'
        if request.get_method() == 'GET':
            return self._metadata(request, url)
        assert request.get_method() == 'POST'
        assert request.get_header('Authorization') == 'Bearer ' + self.key
        scope = workflow_capability.load()
        prepaid.check_lock(scope['execution_lock_fd'])
        index = len(self.calls)
        cell = (self.chat + self.decisions)[index]
        assert request.full_url == cell.url and request.data == cell.body
        self.calls.append(cell)
        generation = 'gen-stub-' + str(index)
        if cell.arm not in matrix.ROUTES:
            return self._chat(cell, generation)
        if self.fault == 'decisions-denied':
            return self._reply(request, {'error': {'code': 403}}, status=403)
        if self.fault == 'decisions-duplicate':
            generation = 'gen-stub-0'
        route = matrix.ROUTES[cell.arm]
        value = {
            'id': generation, 'model': route.served_model, 'provider': route.served_provider,
            'usage': {'input_tokens': 7, 'output_tokens': 1, 'cost': 0.000014, 'is_byok': False},
            'answers': {'criterion': {'type': 'choice', 'choice': self.gold[cell.case_id]}},
        }
        if self.fault == 'decisions-no-cost':
            del value['usage']['cost']
        return self._reply(request, value)

    def _metadata(self, request, url):
        path = url.path
        cat = '/api/v1/models/openai/gpt-6-luna/endpoints'
        if path != cat:
            assert request.get_header('Authorization') == 'Bearer ' + self.key
        if path == '/api/v1/key':
            value = {**self.identity, 'limit': 12, 'limit_remaining': 11, 'limit_reset': None}
        elif path == '/api/v1/credits':
            value = {'total_credits': 13, 'total_usage': 1}
        elif path == cat:
            value = {'id': live.MODEL, 'endpoints': [{
                'tag': 'amazon-bedrock/global', 'model_id': live.MODEL, 'quantization': 'unknown',
                'service_tier': None, 'context_length': 1050000, 'max_completion_tokens': 32768,
                'supported_parameters': ['tools', 'tool_choice', 'response_format'],
                'pricing': {'prompt': '0.0000002', 'completion': '0.0000005'},
            }]}
        elif path == '/api/v1/models/user':
            assert url.query == 'output_modalities=decisions'
            self.inventory_after_chat.append(len(self.calls))
            value = [{'id': route.model, 'endpoints': ['/api/alpha/decisions']}
                     for route in matrix.ROUTES.values()]
            if self.fault == 'inventory-denied':
                value = value[1:]
        else:
            route = next(route for route in matrix.ROUTES.values()
                         if path == '/api/v1/models/' + route.model + '/endpoints')
            value = {'id': route.model, 'endpoints': [{'tag': route.endpoint,
                'name': route.served_provider + ' | ' + route.served_model,
                'provider_name': route.served_provider}]}
        return self._reply(request, {'data': value})

    def _chat(self, cell, generation):
        choice = 'missed' if cell.case_id == self.miss and cell.arm == 'v4' else self.gold[cell.case_id]
        judge = {'verdicts': [{'id': 'criterion', 'verdict': choice, 'reason': 'synthetic fixture reply'}]}
        value = {'id': generation, 'model': live.MODEL, 'provider': 'Amazon Bedrock',
                 'choices': [{'index': 0, 'delta': {'content': json.dumps(judge)}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 11, 'completion_tokens': 5, 'cost': 0.000012}}
        if self.fault == 'chat-no-cost':
            del value['usage']['cost']
        raw = ('data: ' + json.dumps(value) + '\n\ndata: [DONE]\n\n').encode()
        return self._reply(None, raw, mime='text/event-stream')

    @staticmethod
    def _reply(request, value, *, mime='application/json', status=200):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        reply = BytesIO(raw)
        head = Message()
        head['Content-Type'] = mime
        head['Content-Length'] = str(len(raw))
        reply.status, reply.headers = status, head
        if status != 200:
            raise HTTPError(request.full_url, status, 'synthetic upstream denial', head, reply)
        return reply


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    campaign = tmp_path/'campaign'
    campaign.mkdir()
    (campaign/'execution.lock').touch()
    checkpoint = tmp_path/'checkpoint'
    key, _, identity = synthetic_checkpoint(checkpoint)
    fake = Upstream(campaign, key, identity)
    monkeypatch.setattr(prepaid, 'CAMPAIGN', campaign)
    monkeypatch.setattr(prepaid, 'CHECKPOINT', checkpoint)
    monkeypatch.setattr(live, 'MODEL', 'openai/gpt-6-luna')
    monkeypatch.setattr(live, 'PROFILE_NAME', prepaid.PROFILE)
    monkeypatch.setattr(openrouter_direct, 'PROFILE_NAME', prepaid.PROFILE)
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROFILE', prepaid.PROFILE)
    monkeypatch.setenv('OPENROUTER_API_KEY', key)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', lambda opener, *a, **k: fake(opener, *a, **k))
    monkeypatch.setattr(direct_http, '_installed', False)
    monkeypatch.setattr(direct_http, '_install_httpx', lambda _: None)
    return fake


@pytest.mark.parametrize(('fault', 'attempts', 'chat', 'decisions', 'phase'), [
    (None, 96, 48, 48, None),
    ('inventory-denied', 48, 48, 0, 'decisions-preflight'),
    ('decisions-denied', 49, 48, 0, 'decisions'),
    ('decisions-no-cost', 49, 48, 0, 'decisions'),
    ('decisions-duplicate', 49, 48, 0, 'decisions'),
    ('chat-no-cost', 1, 0, 0, 'chat'),
])
def test_real_launcher(upstream, fault, attempts, chat, decisions, phase):
    upstream.fault = fault
    assert live.main([]) == (2 if fault else 0)
    root = upstream.campaign/live.NAME
    report = json.loads((root/'result.json').read_bytes())
    assert len(upstream.calls) == attempts
    assert report['chat_billing']['verified_verdicts'] == chat
    assert (report.get('decisions_billing') or {}).get('generations', 0) == decisions
    assert upstream.inventory_after_chat == ([48] if chat else [])
    assert (root/'chat-result.json').exists()
    if not fault:
        assert report['chat_billing']['complete_cost_usd'] == '0.000576'
        assert report['decisions_billing']['known_cost_usd'] == '0.000672'
        assert report['comparisons']['jev-decisions_vs_v4']['comparison'] == 'candidate-development-improvement'
        assert report['comparisons']['jev-decisions_vs_explicit']['comparison'] == 'no-winner'
    else:
        assert report['stop']['phase'] == phase
        assert report['comparisons']['jev-decisions_vs_v4']['comparison'] == 'incomplete'
    if fault == 'decisions-denied':
        assert report['stop']['reason'] == 'HTTP 403'
        assert (upstream.campaign/'ACCOUNT_STOP').is_file()
    if phase == 'decisions':
        assert len(report['decisions_billing']['unknown']) == 1
    assert not (upstream.campaign/decisions_admission.NAME/'STOP').exists()
    with pytest.raises(RuntimeError, match='exists|stop is active'):
        live.run()
    assert len(upstream.calls) == attempts
