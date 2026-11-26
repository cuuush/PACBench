"""Offline verification of exact plugin installation and fail-closed preflight."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pacbench import catalog_plugins as catalog
from pacbench import harness


@pytest.fixture
def catalog_checkout(tmp_path):
    if harness.ISOLATED_TREE.exists():
        return harness.ISOLATED_TREE
    # Exercise the offline installer and its dependency probe even in a
    # source-only checkout. A provisioned checkout exercises its real catalog.
    tree = tmp_path/'hermes'
    manifests = tree/'plugin-catalog'
    manifests.mkdir(parents=True)
    for name, (repo, revision) in catalog.HERMES_CATALOG.items():
        (manifests/(name+'.yaml')).write_text(yaml.safe_dump(dict(name=name,repo='https://github.com/'+'/'.join(repo.split('/')[:2]),
                                                               subdir='/'.join(repo.split('/')[2:]),sha=revision)))
    return tree


@pytest.mark.parametrize('name', sorted(catalog.CATALOG))
def test_exact_ref_install_and_dependency_probe(name, tmp_path, monkeypatch, catalog_checkout):
    home, fallback = tmp_path/'home', tmp_path/'fallback'
    (home/'plugins').mkdir(parents=True)
    fallback.mkdir()
    source, sha = catalog.CATALOG[name]
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            plugin = home/'plugins'/name
            plugin.mkdir()
            (plugin/'plugin.yaml').write_text(f"name: {name}\nversion: {catalog.SOURCE_VERSIONS[name]}\n")
            (home/'plugins'/'.install-metadata.json').write_text(json.dumps({name: {
                'source': catalog._expected_source(source), 'revision': sha, 'pinned': True}}))
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout=json.dumps({'provider': name,
            'plugin_version': str(catalog.SOURCE_VERSIONS[name]), 'dependencies': ['example>=1']}), stderr='')
    monkeypatch.setattr(catalog.subprocess, 'run', run)
    receipt = catalog.install_catalog_provider(name, home, fallback, Path('/fake/bin/hermes'), catalog_checkout)
    assert calls[0][1:] == ['plugins', 'install', source, '--ref', sha, '--no-deps', '--no-enable']
    assert calls[1][0] == '/fake/bin/python'
    assert receipt['revision'] == sha and receipt['dependencies'] == ['example>=1']


def test_wrong_installed_revision_fails_before_probe(tmp_path, monkeypatch, catalog_checkout):
    home, fallback = tmp_path/'home', tmp_path/'fallback'
    plugin = home/'plugins'/'mem0'
    plugin.mkdir(parents=True)
    fallback.mkdir()
    (plugin/'plugin.yaml').write_text('name: mem0\nversion: 1.3.0\n')
    (home/'plugins'/'.install-metadata.json').write_text(json.dumps({'mem0': {
        'source': catalog._expected_source(catalog.CATALOG['mem0'][0]), 'revision': '0'*40, 'pinned': True}}))
    calls = []
    monkeypatch.setattr(catalog.subprocess, 'run', lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match='source or manifest'):
        catalog.install_catalog_provider('mem0', home, fallback, Path('/fake/bin/hermes'), catalog_checkout)
    assert len(calls) == 1


def test_declared_dependency_failure_is_not_accepted(tmp_path, monkeypatch, catalog_checkout):
    home, fallback = tmp_path/'home', tmp_path/'fallback'
    plugin = home/'plugins'/'mem0'
    plugin.mkdir(parents=True)
    fallback.mkdir()
    (plugin/'plugin.yaml').write_text('name: mem0\nversion: 1.3.0\n')
    source, sha = catalog.CATALOG['mem0']
    (home/'plugins'/'.install-metadata.json').write_text(json.dumps({'mem0': {
        'source': catalog._expected_source(source), 'revision': sha, 'pinned': True}}))
    results = iter([SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)])
    monkeypatch.setattr(catalog.subprocess, 'run', lambda *a, **k: next(results))
    with pytest.raises(RuntimeError, match='dependency or API probe'):
        catalog.install_catalog_provider('mem0', home, fallback, Path('/fake/bin/hermes'), catalog_checkout)


def test_failed_preflight_leaves_provider_inactive_before_native_setup(tmp_path, monkeypatch):
    from pacbench import native_pilot
    monkeypatch.setattr('pacbench.providers.require_live_trials_ready', lambda: None)
    monkeypatch.setattr(native_pilot, 'client_settings', lambda *_: ('http://127.0.0.1:1234/v1', 'test'))
    def home_setup(home, *, provider_name, **kwargs):
        assert provider_name == ''
        home.mkdir(parents=True)
        (home/'config.yaml').write_text('memory:\n  provider: ""\n')
    monkeypatch.setattr(native_pilot.harness, 'provision_home', home_setup)
    def failed_install(name, home, *args):
        assert yaml.safe_load((home/'config.yaml').read_text())['memory']['provider'] == ''
        raise RuntimeError('dependency missing')
    monkeypatch.setattr(catalog, 'install_catalog_provider', failed_install)
    with pytest.raises(RuntimeError, match='dependency missing'):
        native_pilot.provision(tmp_path/'arm', 'mem0')
    config = yaml.safe_load((tmp_path/'arm/hermes-home/config.yaml').read_text())
    assert config['memory']['provider'] == ''
    assert not (tmp_path/'arm/catalog-plugin.json').exists()
