import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from pacbench.judging import development_live as pilot
from pacbench.workflows import capability as cap
from pacbench.canary_capability import binding
from pacbench.direct_accounting import live_budget
from pacbench.observational_receipts import replay_attempts


def synthetic_checkpoint(checkpoint):
    checkpoint.mkdir(parents=True)
    key = 'synthetic-execution-key'
    (checkpoint/'execution-key-fingerprint.private.txt').write_text(hashlib.sha256(key.encode()).hexdigest())
    confirmation = checkpoint/'operator-confirmation.json'
    confirmation.write_text(json.dumps(dict(auto_recharge='off', confirmed_by='offline-test',
                                            confirmed_at='2026-11-22T00:00:00+00:00')))
    identity = {field: None for field in ('label','organization_id','workspace_id','creator_user_id',
                                         'is_management_key','is_provisioning_key','expires_at')}
    (checkpoint/'key.private.json').write_text(json.dumps(dict(data=identity)))
    return key, confirmation, identity


@pytest.fixture
def scope(tmp_path, monkeypatch):
    campaign = tmp_path/'campaign'
    root = campaign/pilot.NAME
    root.mkdir(parents=True)
    (campaign/'execution.lock').touch()
    checkpoint = tmp_path/'checkpoint'
    key, confirmation, identity = synthetic_checkpoint(checkpoint)
    monkeypatch.setattr(pilot, 'CAMPAIGN', campaign)
    monkeypatch.setattr(pilot, 'CHECKPOINT', checkpoint)
    status = {**identity, 'limit': '12', 'limit_remaining': '11', 'limit_reset': None}
    credits = {'total_credits': '13', 'total_usage': '1'}

    def account(endpoint, _):
        data = status if endpoint == 'key' else credits
        return json.dumps(dict(data=data)).encode(), data

    monkeypatch.setattr(pilot, 'account_data', account)
    for name, value in {
        'OPENROUTER_API_KEY': key, 'PACBENCH_OPENROUTER_PROFILE': pilot.PROFILE,
        'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
        'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
        'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768', 'PACBENCH_DISPATCH_TRACKING': pilot.CONDITION,
        'PACBENCH_RECEIPT_JOURNAL': str(root/'receipts.jsonl'),
        'PACBENCH_OBSERVATIONAL_COHORT_STOP': str(campaign/'ACCOUNT_STOP'),
        'PACBENCH_STOP_FILE': str(root/'STOP'),
    }.items():
        monkeypatch.setenv(name, value)
    proof = binding(confirmation)
    scope_path = root/'workflow-scope.json'
    prepaid = pilot.verify_prepaid(root, key, proof)
    scope_path.write_text(json.dumps(dict(root=str(root), admission=pilot.SCOPE, provider=pilot.NAME,
        run_id=campaign.name, reviewed_commit=pilot.REVISION, manifest_sha256=pilot.MANIFEST_SHA,
        projects={pilot.NAME: {'judge': pilot.PROJECT}}, controller=pilot.process_identity(),
        prepaid=prepaid, auto_recharge_evidence=proof)))
    monkeypatch.setenv(cap.ENV, json.dumps(binding(scope_path)))
    return root, status, credits, proof


def send(budget, cell, generation, *, cost=True, done=True):
    reservation = budget.reserve(pilot.PROJECT, cell['body'])
    ident = reservation['id']
    budget.mark_dispatch_possible(ident)
    event = dict(id=generation, model=cell['body']['model'], provider='Amazon Bedrock',
                 choices=[dict(index=0, delta=dict(content='{}'), finish_reason='stop')],
                 usage=dict(prompt_tokens=4, completion_tokens=3))
    if cost:
        event['usage']['cost'] = '0.000012'
    raw = 'data: '+json.dumps(event)+'\n\n'
    if done:
        raw += 'data: [DONE]\n\n'
    budget.settle(ident, 200, raw, body_complete=False)
    return ident


@contextmanager
def bound_lock(root):
    path = root/'workflow-scope.json'
    scope = json.loads(path.read_text())
    with pilot.account_lock() as fd:
        path.write_text(json.dumps({**scope, 'execution_lock_fd': fd}))
        with patch.dict(os.environ, {cap.ENV: json.dumps(binding(path))}):
            yield


def test_twenty_ordered_attempts_and_cost(scope):
    root, *_ = scope
    cells = pilot.frozen_requests()
    with bound_lock(root):
        budget = live_budget(profile=pilot.PROFILE)
        for i, cell in enumerate(cells):
            send(budget, cell, f'gen-{i}')
        summary = pilot.prefix_summary(root, budget.snapshot(), cells)
        assert summary['generations'] == 20 and summary['complete_cost_usd'] == '0.000240'
        with pytest.raises(RuntimeError, match='next unused'):
            budget.reserve(pilot.PROJECT, cells[-1]['body'])


def test_route_body_order_lock_and_account_stop(scope):
    root, *_ = scope
    cells = pilot.frozen_requests()
    with pytest.raises(RuntimeError, match='shared execution lock'):
        live_budget()
    with bound_lock(root):
        budget = cap.budget()
        with pytest.raises(BlockingIOError), pilot.account_lock():
            pytest.fail('second owner acquired the lock')
        with pytest.raises(ValueError, match='billing'):
            cap.budget(profile='deepseek-flex')
        body = copy.deepcopy(cells[0]['body'])
        body['provider']['allow_fallbacks'] = True
        for request in (body, {**cells[0]['body'], 'temperature': 0}):
            with pytest.raises(ValueError, match='Unreviewed'):
                budget.reserve(pilot.PROJECT, request)
        with pytest.raises(RuntimeError, match='next unused'):
            budget.reserve(pilot.PROJECT, cells[1]['body'])
        res = budget.reserve(pilot.PROJECT, cells[0]['body'])
        (root.parent/'ACCOUNT_STOP').touch()
        with pytest.raises(RuntimeError, match='stop is active'):
            budget.mark_dispatch_possible(res['id'])


@pytest.mark.parametrize('problem', ['unknown', 'missing-cost', 'incomplete-stream', 'duplicate-generation'])
def test_ambiguous_or_invalid_receipt_stops_next_cell(scope, problem):
    root, *_ = scope
    cells = pilot.frozen_requests()
    with bound_lock(root):
        budget = cap.budget()
        index = 1
        if problem == 'unknown':
            first = budget.reserve(pilot.PROJECT, cells[0]['body'])
            budget.mark_dispatch_possible(first['id'])
        else:
            send(budget, cells[0], 'shared', cost=problem != 'missing-cost', done=problem != 'incomplete-stream')
            if problem == 'duplicate-generation':
                send(budget, cells[1], 'shared')
                index = 2
        with pytest.raises((RuntimeError, ValueError), match='receipt|generation|terminator|DONE'):
            budget.reserve(pilot.PROJECT, cells[index]['body'])
        assert len(budget.snapshot()) == index
        assert budget.retry_delay(budget.snapshot()[-1]['id']) is None


@pytest.mark.parametrize('problem', ['wrong-key', 'wrong-identity', 'no-credit', 'recharge', 'resetting-limit', 'exhausted-limit'])
def test_prepaid_controls_fail_closed(scope, problem):
    root, status, credits, proof = scope
    key = os.environ['OPENROUTER_API_KEY']
    if problem == 'wrong-key':
        key += 'unknown'
    elif problem == 'wrong-identity':
        status['workspace_id'] = 'different'
    elif problem == 'no-credit':
        credits['total_usage'] = credits['total_credits']
    elif problem == 'recharge':
        credits['auto_recharge_enabled'] = True
    elif problem == 'resetting-limit':
        status['limit_reset'] = 'daily'
    elif problem == 'exhausted-limit':
        status['limit_remaining'] = 0
    with pytest.raises(ValueError) as error:
        pilot.verify_prepaid(root, key, proof)
    assert not isinstance(error.value, FileExistsError)
    assert not (root/'receipts.jsonl').exists()


def test_normal_direct_model_client_uses_frozen_wire_and_receipt(scope, monkeypatch):
    import io
    import urllib.request
    from email.message import Message
    from pacbench import direct_http
    from pacbench.model_calls import ModelClient
    from pacbench.openrouter_route import CHAT_COMPLETIONS_URL

    root, *_ = scope
    cell = pilot.frozen_requests()[0]
    headers = Message()
    headers['Content-Type'] = 'text/event-stream'
    event = dict(id='mock-generation', model=cell['body']['model'], provider='Amazon Bedrock',
                 choices=[dict(index=0, delta=dict(content='{}'), finish_reason='stop')],
                 usage=dict(prompt_tokens=4, completion_tokens=3, cost='0.000012'))
    sends = []

    def mock_open(self, request, *, timeout):
        assert request.full_url == CHAT_COMPLETIONS_URL
        sends.append(json.loads(request.data))
        response = io.BytesIO(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
        response.status = 200
        response.headers = headers
        return response

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', mock_open)
    monkeypatch.setattr(direct_http, '_installed', False)
    monkeypatch.setenv('PACBENCH_DIRECT', '1')
    monkeypatch.setenv('PACBENCH_TRANSPORT', 'openrouter')
    monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY', os.environ['OPENROUTER_API_KEY'])
    direct_http.install()
    with bound_lock(root):
        budget = live_budget(project=pilot.PROJECT)
        client = ModelClient(key='pbdirect-'+pilot.PROJECT, model=cell['body']['model'],
                             url=CHAT_COMPLETIONS_URL, record_dir=root/'model-calls', max_attempts=1)
        output = client._request(cell['body'], protocol='chat')
        assert output.text == '{}'
        assert sends == [cell['body']]
        assert pilot.prefix_summary(root, budget.snapshot(), [cell])['complete_cost_usd'] == '0.000012'


@pytest.mark.parametrize('mode', ['owned', 'unlocked', 'unsettled'])
def test_production_cli_checks_the_real_shared_lock_and_receipts(tmp_path, mode):
    campaign = tmp_path/'campaign'
    prior = campaign/'luna-judge-development-v1'
    prior.mkdir(parents=True)
    previous_scope = prior/'workflow-scope.json'
    previous_scope.write_text('{}')
    lock_path = campaign/'execution.lock'
    lock_path.touch()
    key, _, _ = synthetic_checkpoint(tmp_path/'pizza-v2-handoff/billing-resume')
    shim = Path(__file__).resolve().parent/'fixtures/judge_development_cli'
    repo = Path(pilot.__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, '-m', 'pacbench.judge_development_live'],
                          cwd=repo, capture_output=True, text=True, timeout=20,
                          env={**{k: os.environ[k] for k in ('PATH', 'HOME') if k in os.environ},
                               'PYTHONPATH': str(shim), 'PYTHONDONTWRITEBYTECODE': '1',
                               'PACBENCH_TEST_CAMPAIGN': str(campaign), 'PACBENCH_TEST_RESPONSE': mode,
                               'PACBENCH_OPENROUTER_PROFILE': pilot.PROFILE, 'OPENROUTER_API_KEY': key})
    root = campaign/pilot.NAME
    assert previous_scope.read_text() == '{}'
    with lock_path.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    wire = campaign/'wire.jsonl'
    if mode == 'unlocked':
        assert proc.returncode != 0 and 'A/B requires the shared execution lock' in proc.stderr
        assert (root/'workflow-scope.json').is_file()
        assert not wire.exists() and not (root/'receipts.jsonl').exists()
    elif mode == 'unsettled':
        assert proc.returncode != 0 and not (root/'result.json').exists()
        assert [json.loads(line) for line in wire.read_text().splitlines()] == [pilot.frozen_requests()[0]['body_sha256']]
        with (root/'receipts.jsonl').open() as stream:
            only, = replay_attempts(stream).values()
        assert only['state'] != 'settled' and only['dispatch_context']['attempt_number'] == 1
    else:
        assert proc.returncode == 0, proc.stderr
        assert [json.loads(line) for line in wire.read_text().splitlines()] == [r['body_sha256'] for r in pilot.frozen_requests()]
        result = json.loads((root/'result.json').read_text())
        assert result['billing']['generations'] == 20 and result['billing']['complete_cost_usd'] == '0.000240'
        assert result['missing_cells'] == []
