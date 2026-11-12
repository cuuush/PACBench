import json
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest

from pacbench import openrouter_run
from pacbench.openrouter_direct import provider_plan, BASE_URL


def test_direct_plans_do_not_claim_url_changes_pin_unsupported_clients():
    plans = [provider_plan(name, Path('/offline')/name) for name in
             ('memcal','mem0','hindsight','openviking','honcho','supermemory')]
    assert all(BASE_URL in json.dumps(p['native_settings']) for p in plans)
    assert not any(p['requires_server_side_route_preset'] for p in plans)
    assert {p['provider'] for p in plans if p['native_extension_available']} == {'hindsight','honcho','openviking'}
    assert all(not p['paid_calls_enabled'] for p in plans)


def test_entrypoint_uses_direct_environment_and_never_proxy(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('OPENROUTER_API_KEY','offline-secret')
    monkeypatch.setattr('pacbench.preparation_audit.audit_prepared',lambda _: {'state':'pass'})
    cfg=dict(prompt_per_million='1',completion_per_million='2',context_tokens=100,max_completion_tokens=50)
    monkeypatch.setattr(openrouter_run,'fetch_catalog',lambda: (cfg,{}))
    monkeypatch.setattr('pacbench.direct_accounting.live_budget',lambda: SimpleNamespace(config=cfg))
    monkeypatch.setattr(openrouter_run,'transport_environment',lambda *a: pytest.fail('proxy transport'))
    output=tmp_path/'new-run'
    entered=[]
    @contextmanager
    def direct(key):
        entered.append(key);yield
    monkeypatch.setattr('pacbench.openrouter_direct.direct_environment',direct)
    def run(*args,**kwargs):
        assert entered==['offline-secret']
        assert kwargs['inference_route']=='openrouter'
        return {'state':'diagnostic'}
    monkeypatch.setattr('pacbench.native_pilot.run',run)
    assert openrouter_run.main(['--prepared',str(tmp_path),'--output',str(output),'--spend-limit-usd','20'])==0
    assert not output.exists()
