"""Run the scoped judge launcher through its real transport with harness-owned stubs."""
import io
import json
from email.message import Message
from urllib.request import OpenerDirector

import pytest

from pacbench.providers.honcho import regrade_live as v4
from pacbench.observational_receipts import replay_attempts
from tests.test_cohort_regrade_live import offline


@pytest.mark.parametrize('fault', ('complete', 'malformed-third', 'unsettled', 'wrong-model', 'duplicate', 'recharge'))
def test_fixed_honcho_v4_send_limit_and_verified_reuse(offline, monkeypatch, fault):
    campaign, credits, plan = offline
    plan['revision'] = v4.REVISION
    for i, item in enumerate(plan['items']):
        item['prompt'] += f' {i}'
        item['saved']['turn']['claims'] = [
            dict(id=f'criterion-{j}', description='Claim', evidence=[]) for j in range(5 if i < 4 else 4)]
    wire = v4.transport._wire_plan(plan)
    monkeypatch.setattr(v4, 'PLAN_ID', plan['plan_id'])
    monkeypatch.setattr(v4, 'INPUT_SHA', plan['manifest_sha256'])
    monkeypatch.setattr(v4, 'WIRE_SHA', v4.regrade.digest(wire['cells']))
    monkeypatch.setattr(v4, 'frozen_plan', lambda: (plan, wire))
    if fault == 'recharge':
        credits['auto_recharge_enabled'] = True
    received = []

    def respond(_self, request, *, timeout):
        assert request.full_url == v4.transport.CHAT_COMPLETIONS_URL
        item = plan['items'][len(received)]
        body = json.loads(request.data)
        assert body == v4.transport.wire_body(item)
        received.append(body)
        ordinal = len(received)
        claims = item['saved']['turn']['claims']
        verdict = {'verdicts': [dict(id=c['id'], verdict='correct', reason='Synthetic') for c in claims]}
        if fault == 'malformed-third' and ordinal == 3:
            verdict = {'verdicts': []}
        usage = dict(prompt_tokens=10, completion_tokens=5)
        if fault != 'unsettled':
            usage['cost'] = '0.00001'
        event = dict(id=f'mock-{1 if fault == "duplicate" else ordinal}', provider='Amazon Bedrock', usage=usage,
                     model='wrong' if fault == 'wrong-model' else v4.transport.MODEL,
                     choices=[dict(index=0, delta={'content': json.dumps(verdict)}, finish_reason='stop')])
        response = io.BytesIO(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
        response.status = 200
        response.headers = Message()
        response.headers['Content-Type'] = 'text/event-stream'
        return response

    monkeypatch.setattr(OpenerDirector, 'open', respond)
    root = campaign/v4.SCOPE
    if fault == 'complete':
        report = v4.run()
        assert report['condition'] == v4.SCOPE
        row = report['providers']['honcho']
        assert (row['regrade_correct'], row['regraded_criteria'], row['expected_criteria']) == (192, 192, 192)
        assert report['billing']['complete_cost_usd'] == '0.00047'
        assert report == v4.run()
        assert len(received) == 47
    else:
        with pytest.raises((ValueError, RuntimeError)):
            v4.run()
        assert not (root/'report.json').exists()
        expected = {'malformed-third': 3, 'unsettled': 1, 'wrong-model': 1, 'duplicate': 2, 'recharge': 0}[fault]
        assert len(received) == expected
        with pytest.raises(RuntimeError, match='Preserve it without replay'):
            v4.run()
        assert len(received) == expected
        if expected:
            with (root/'receipts.jsonl').open() as stream:
                rows = list(replay_attempts(stream).values())
            assert len(rows) == expected and all(not row.get('retry_of') for row in rows)
        if fault == 'malformed-third':
            partial = json.loads((root/'partial-report.json').read_text())['providers']['honcho']
            assert (partial['regraded_criteria'], partial['expected_criteria']) == (10, 192)
