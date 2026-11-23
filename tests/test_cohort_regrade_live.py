import fcntl
import io
import json
from email.message import Message
from pathlib import Path
from urllib.request import OpenerDirector

import pytest

from pacbench import cohort_regrade_live as live, direct_http, openrouter_run
from pacbench.observational_receipts import replay_attempts
from pacbench.workflow_spec import CONTEXT_JUDGE_PROTOCOL
from tests.test_judge_development_live import synthetic_checkpoint


@pytest.fixture
def offline(tmp_path, monkeypatch):
    campaign = tmp_path/'campaign'
    campaign.mkdir()
    (campaign/'execution.lock').touch()
    checkpoint = tmp_path/'checkpoint'
    key, _, identity = synthetic_checkpoint(checkpoint)
    monkeypatch.setattr(live.prepaid, 'CAMPAIGN', campaign)
    monkeypatch.setattr(live.prepaid, 'CHECKPOINT', checkpoint)
    monkeypatch.setenv('OPENROUTER_API_KEY', key)
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROFILE', live.PROFILE)
    monkeypatch.setattr('pacbench.openrouter_route.PROFILE_NAME', live.PROFILE)
    monkeypatch.setattr('pacbench.openrouter_direct.PROFILE_NAME', live.PROFILE)
    monkeypatch.setattr(openrouter_run, 'fetch_catalog', lambda **_: ({'max_completion_tokens': 32768}, None))
    monkeypatch.setattr(direct_http, '_installed', False)

    status = {**identity, 'limit': 10, 'limit_remaining': 10, 'limit_reset': None}
    credits = {'total_credits': 10, 'total_usage': 0, 'auto_recharge_enabled': False}

    def read_account(endpoint, selected_key):
        assert selected_key == key
        response = {'key': status, 'credits': credits}[endpoint]
        return json.dumps({'data': response}).encode(), response

    monkeypatch.setattr(live.prepaid, 'account_data', read_account)
    monkeypatch.setattr(live, 'ARMS', ('honcho',))
    monkeypatch.setattr(live, '_verify_inventory', lambda _: None)
    at = '2026-10-06T09:00:00-04:00'
    item = {'provider': 'honcho', 'arm': 'honcho', 'input_sha256': 'a'*64, 'prompt_sha256': 'b'*64,
            'prompt': 'Synthetic retained prompt',
            'options': {'question_at': at, 'judge_protocol': CONTEXT_JUDGE_PROTOCOL}}
    items = []
    for i in range(47):
        turn = dict(id=f'question-{i}', prompt='Question', claims=[dict(id='criterion', description='Claim', evidence=[])])
        items.append({**item, 'id': f'honcho/question-{i}', 'saved': dict(
            turn=turn, surface={'answer': 'Saved answer'}, original={'criteria': None})})
    coverage = {'honcho': dict(state='verified-retained-inputs', provider='honcho', expected_turns=49,
        completed_turns=49, expected_criteria=192, completed_criteria=192, judge_requests_planned=47,
        original_condition={}, verified_pending_completion=[], execution_sealed=True, source_provenance={},
        binding=dict(input_projection='test', release_clock='test', dataset_sha256='test',
                     ordered_questions_sha256='test', canary=False))}
    plan = dict(plan_id='c'*64, manifest_sha256=live.MANIFEST_SHA, revision=live.REVISION,
                items=items, coverage=coverage, source_roots=[])
    monkeypatch.setattr(live.regrade, 'snapshot', lambda *_, **__: plan)
    return campaign, credits, plan


@pytest.mark.parametrize('mode', ('complete', 'unsettled', 'wrong-provider', 'duplicate', 'lock-lost', 'recharge', 'malformed'))
def test_production_transport_and_stop_boundaries(offline, monkeypatch, mode):
    campaign, credits, plan = offline
    wire = []

    def upstream(_self, request, *, timeout):
        assert request.full_url == live.CHAT_COMPLETIONS_URL
        body = json.loads(request.data)
        assert body == live.wire_body(plan['items'][len(wire)])
        wire.append(body)
        generation = 1 if mode == 'duplicate' else len(wire)
        provider = 'Unexpected' if mode == 'wrong-provider' else 'Amazon Bedrock'
        usage = dict(prompt_tokens=4, completion_tokens=3)
        if mode != 'unsettled':
            usage['cost'] = '0.000012'
        verdict = {} if mode == 'malformed' else {
            'verdicts': [dict(id='criterion', verdict='correct', reason='Synthetic fixture')]}
        event = dict(id=f'mock-{generation}', model=live.MODEL, provider=provider, usage=usage,
                     choices=[dict(index=0, delta=dict(content=json.dumps(verdict)), finish_reason='stop')])
        response = io.BytesIO(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
        response.status = 200
        response.headers = Message()
        response.headers['Content-Type'] = 'text/event-stream'
        return response

    monkeypatch.setattr(OpenerDirector, 'open', upstream)
    if mode == 'recharge':
        credits['auto_recharge_enabled'] = True
    if mode == 'lock-lost':
        original = live.prepaid.verify_prepaid

        def unlock(*args):
            result = original(*args)
            fd, = (int(path.name) for path in Path('/proc/self/fd').iterdir()
                   if path.is_symlink() and str(path.readlink()) == str(campaign/'execution.lock'))
            fcntl.flock(fd, fcntl.LOCK_UN)
            return result

        monkeypatch.setattr(live.prepaid, 'verify_prepaid', unlock)
    root = campaign/live.SCOPE
    if mode == 'complete':
        report = live.run(campaign/'manifest.json', plan['plan_id'])
        assert report['billing']['complete_cost_usd'] == '0.000564'
        assert report['providers']['honcho']['regrade_correct'] == 47
        assert report['receipt_verified'] is True and (root/'report.json').is_file()
        with pytest.raises(RuntimeError, match='durable run'):
            live.run(campaign/'manifest.json', plan['plan_id'])
        assert len(wire) == 47
    else:
        with pytest.raises((ValueError, RuntimeError)):
            live.run(campaign/'manifest.json', plan['plan_id'])
        expected = {'unsettled': 1, 'wrong-provider': 1, 'duplicate': 2, 'lock-lost': 0, 'recharge': 0,
                    'malformed': 1}[mode]
        assert len(wire) == expected
        assert not (root/'report.json').exists()
        if expected:
            with (root/'receipts.jsonl').open() as stream:
                attempts = list(replay_attempts(stream).values())
            assert len(attempts) == expected and all(not row.get('retry_of') for row in attempts)
