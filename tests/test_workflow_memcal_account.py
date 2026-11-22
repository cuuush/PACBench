"""Provision the real store/config against recording site and process boundaries, with no inference."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from types import SimpleNamespace

import pytest

from pacbench import workflow_memcal_account as factory
from pacbench.canary_capability import digest, sha


def test_factory_preserves_synthetic_store_and_primary_failure(tmp_path, monkeypatch):
    from pacbench import workflow_memcal, workflow_capability as cap, openrouter_route as route
    from pacbench.workflow_memcal_source import frozen_messages_digest
    from memcal import db
    root = tmp_path/'campaign/memcal'
    store = root/'memcal-home'
    store.mkdir(parents=True)
    env_file = store/'.env'
    marker = b'# existing synthetic source configuration\nIMAP_USER=synthetic@fixture.invalid\n'
    env_file.write_bytes(marker)
    db.open_db(store/'memcal.db').close()
    site = workflow_memcal.MemCalSite('synthetic-site',str(tmp_path),str(store),'bluebubbles',
                                      '127.0.0.1',9999,'synthetic@fixture.invalid','http://127.0.0.1:9000')
    rows = factory._source_messages()
    spec_sha = sha(factory.SPEC)
    canary_docs, _, frozen = cap.schedule(spec_sha, True)
    mapping = {'account':site.account,'frozen_messages_sha256':frozen_messages_digest(rows),
               'records':{r['source_id']:{'external_id':'fixture-' + r['source_id']} for r in rows}}
    path = tmp_path/'external-ids.json'
    path.write_text(json.dumps(mapping))
    import hashlib
    upstream = SimpleNamespace(bindings_path=path, bindings_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        release=lambda at:pytest.fail('unexpected source release'),observe=lambda at:pytest.fail('unexpected source read'),
        deployment=lambda:pytest.fail('fixture cannot qualify live'))
    scope = {'path':'binding','sha256':'fixture'}
    from pacbench.provider_versions import memcal_week_pin
    from pacbench.workflow_tracks import memcal_week_tools
    label, revision, version = memcal_week_pin()
    cfg = {'access':{'memcal_revision':revision}, 'root':str(root),'provider':'memcal', 'spec_sha256':spec_sha, 'frozen_inputs':frozen,'canary':True,
           'site_sha256':digest(asdict(site)),'account':site.account,'store':str(store),
           'projects':{'memcal':{'driver':'fixture-driver','memory':'fixture-memory','judge':'fixture-judge'}}}
    monkeypatch.setattr(cap,'load',lambda:cfg)
    monkeypatch.setattr(workflow_memcal,'inspect_memcal_site',lambda s:None)
    monkeypatch.setenv('PACBENCH_DIRECT','1')
    monkeypatch.setenv('PACBENCH_TRANSPORT','openrouter')
    monkeypatch.setenv(cap.ENV,json.dumps(scope))
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION',label)
    monkeypatch.setattr(route,'MODEL','openai/gpt-6-luna')
    monkeypatch.setattr(route,'ENDPOINT','amazon-bedrock/global')
    monkeypatch.delenv('OPENAI_COMPAT_API_KEY',raising=False)
    @contextmanager
    def gateway(home, python, env, **kwargs):
        assert env['MEMCAL_HOME'] == str(store) and kwargs['provider'] == 'memcal'
        try:
            yield SimpleNamespace(env=env,home=home,witness={'pid':1},pins={'hermes':'test','hermes_isolation_sha256':'test'})
        finally:
            raise RuntimeError('synthetic cleanup failure')
    monkeypatch.setattr(factory,'PersistentGateway',gateway)
    with pytest.raises(ValueError,match='primary') as caught:
        with factory.open_account(root=root,site=site,scope=scope,ledger=None,upstream=upstream) as (condition,account):
            assert account.client.config.db_path == store/'memcal.db' and account.identity == site.account
            assert account.source_witness.documents == canary_docs and len(account.source_witness.rows) == 142
            assert len({d.cutoff for d in canary_docs}) == 1
            assert condition.track == 'first-party-connector' and condition.tool_names == memcal_week_tools(revision)
            assert condition.version == version
            assert (root/'hermes-home/plugins/memcal').is_dir()
            raise ValueError('primary')
    assert caught.value.__notes__ == ['MemCal account cleanup also failed (RuntimeError)']
    assert env_file.read_bytes() == marker and 'OPENAI_COMPAT_API_KEY' not in os.environ
    with pytest.raises(ValueError,match='original warm owner'):
        with factory.open_account(root=root,site=site,scope=scope,ledger=None,upstream=upstream):
            pytest.fail('must not replace prior home')
