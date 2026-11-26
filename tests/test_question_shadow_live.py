"""Exercise the exact local development manifest through the normal prepaid entry point."""
import copy
import json
import os
from pathlib import Path
import urllib.request

import pytest

from pacbench import direct_http
from pacbench import openrouter_direct
from pacbench.judging import shadow_live as live
from pacbench.judging import development_live as prepaid
from pacbench.workflows import capability as cap
from pacbench.judging.paired import BASELINE, PROTOCOL
from pacbench.judging.shadow import REFERENCE, render, wire
from tests.test_judge_calibration_live import Upstream
from pacbench.judging import calibration_live as calibration
from tests.test_judge_development_live import synthetic_checkpoint


@pytest.fixture
def exports():
    path = os.getenv('PACBENCH_LOCAL_SHADOW_TEST_EXPORTS')
    if not path:
        pytest.skip('requires the two reviewed runner-local answer exports')
    return Path(path)


@pytest.mark.parametrize('fault', [None, 'missing-cost', 'duplicate', 'account-stop'])
@pytest.mark.parametrize('scope_id', [live.SCOPE, live.SPAN_SCOPE])
def test_shadow_entry(tmp_path, monkeypatch, exports, fault, scope_id):
    spec = live.FROZEN[scope_id]
    argv = [str(exports)] if scope_id == live.SCOPE else ['--spans-v2', str(exports)]
    campaign = tmp_path / 'campaign'
    campaign.mkdir()
    (campaign / 'execution.lock').touch()
    checkpoint = tmp_path / 'checkpoint'
    key, _, identity = synthetic_checkpoint(checkpoint)
    monkeypatch.setattr(prepaid, 'CAMPAIGN', campaign)
    monkeypatch.setattr(prepaid, 'CHECKPOINT', checkpoint)
    monkeypatch.setenv('OPENROUTER_API_KEY', key)
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROFILE', prepaid.PROFILE)
    monkeypatch.setattr(live, 'PROFILE_NAME', prepaid.PROFILE)
    monkeypatch.setattr(live, 'MODEL', 'openai/gpt-6-luna')
    monkeypatch.setattr(calibration, 'MODEL', 'openai/gpt-6-luna')
    monkeypatch.setattr(openrouter_direct, 'PROFILE_NAME', prepaid.PROFILE)
    monkeypatch.setattr(direct_http, '_installed', False)
    monkeypatch.setattr(direct_http, '_install_httpx', lambda _: None)
    metadata = Upstream.__new__(Upstream)
    metadata.identity, metadata.key = identity, key
    manifest, cells, pairs = render(exports, candidate=spec.candidate)
    assert live.fingerprint(manifest) == spec.manifest
    attempts = []

    def synthetic_http(opener, req, data=None, timeout=None):
        from urllib.parse import urlsplit
        if isinstance(req, str):
            req = urllib.request.Request(req, data=data)
        if req.get_method() == 'GET':
            assert urlsplit(req.full_url).netloc == 'openrouter.ai'
            return metadata._metadata(req, urlsplit(req.full_url))
        assert req.full_url == live.CHAT_COMPLETIONS_URL
        assert req.get_header('Authorization') == 'Bearer ' + key
        scope = cap.load()
        prepaid.check_lock(scope['execution_lock_fd'])
        cell = cells[len(attempts)]
        assert req.data == wire(cell['body'])
        tampered = copy.deepcopy(cell['body'])
        tampered['provider']['allow_fallbacks'] = True
        with pytest.raises(ValueError):
            live.admit(scope, spec.project, tampered)
        with pytest.raises(ValueError):
            live.admit(scope, 'other-scope', cell['body'])
        attempts.append(cell['id'])
        rules = pairs[cell['case']].rules
        response = [{'id': r['id'], 'reason': 'offline rehearsal', 'verdict': 'missed'}
                    if cell['arm'] == BASELINE else
                    {'id': r['id'], 'relation': 'absent', 'answer_quote': None, 'reason': 'offline rehearsal'}
                    for r in rules]
        if cell['arm'] == live.spans.PROTOCOL:
            for r in response:
                del r['answer_quote']
                r['answer_spans'] = []
            if cell['case'] == 'memcal/assistant-friday-plans':
                response[0].update(relation='entailed', answer_spans=['invented composite'])
        gen = 1 if fault == 'duplicate' else len(attempts)
        event = {'id': f'gen-synthetic-{gen}', 'model': live.MODEL, 'provider': 'Amazon Bedrock',
                 'choices': [{'index': 0, 'delta': {'content': json.dumps({'verdicts': response})},
                              'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 17, 'completion_tokens': 12, 'cost': 0.000012}}
        if fault == 'missing-cost':
            del event['usage']['cost']
        raw = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
        return Upstream._reply(req, raw, mime='text/event-stream')

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', synthetic_http)
    if fault == 'account-stop':
        (campaign/'ACCOUNT_STOP').touch()
    if fault is None:
        assert live.main(argv) == 0
        result = json.loads((campaign / spec.scope / 'result.json').read_bytes())
        assert len(attempts) == result['billing']['generations'] == len(cells)
        assert result['billing']['complete_cost_usd'] == ('0.000192' if scope_id == live.SCOPE else '0.000168')
        assert result['billing']['retried_attempts'] == 0
        for case in pairs:
            for arm in (BASELINE, spec.candidate):
                stats = result['results'][case][arm]
                if case == 'memcal/assistant-friday-plans' and arm == live.spans.PROTOCOL:
                    assert stats['state'] == 'partial_judge_response'
                    assert stats['planned'] == stats['graded'] + 1 == 12
                    assert len(stats['quarantined']) == 1
                else:
                    assert stats['state'] == 'graded'
    else:
        with pytest.raises((ValueError, RuntimeError)):
            live.main(argv)
        assert len(attempts) == {'account-stop': 0, 'missing-cost': 1, 'duplicate': 2}[fault]
    previous = len(attempts)
    with pytest.raises(RuntimeError, match='exists|stop is active'):
        live.main(argv)
    assert len(attempts) == previous
