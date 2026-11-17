import hashlib
import json

import pytest

from pacbench import bootstrap


def test_plan_has_no_side_effects(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, 'install', lambda *_: pytest.fail('plan installed software'))
    bootstrap.main([])
    result = json.loads(capsys.readouterr().out)
    assert not result['install'] and not result['paid_calls']
    assert 'hermes' in result['components']


def test_installer_environment_removes_model_keys_preserves_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'secret')
    monkeypatch.setenv('PACBENCH_OPENROUTER_DIRECT_TOKEN', 'private')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy:8080')
    env = bootstrap.environment(tmp_path)
    assert 'OPENROUTER_API_KEY' not in env
    assert 'PACBENCH_OPENROUTER_DIRECT_TOKEN' not in env
    assert env['HTTPS_PROXY'] == 'http://proxy:8080'


def test_existing_source_mismatch_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, 'output', lambda *_: 'different-revision')
    monkeypatch.setattr(bootstrap, 'command', lambda *_: pytest.fail('modified existing source'))
    with pytest.raises(RuntimeError, match='preserve/move'):
        bootstrap.source('unused', 'expected', tmp_path)


def test_existing_download_mismatch_is_preserved(tmp_path):
    path = tmp_path / 'asset'
    path.write_bytes(b'original')
    with pytest.raises(RuntimeError, match='refusing to replace'):
        bootstrap.download('https://unused', path, 'wrong')
    assert path.read_bytes() == b'original'


def test_valid_download_reuses_offline_file(tmp_path, monkeypatch):
    path = tmp_path / 'asset'
    path.write_bytes(b'verified')
    monkeypatch.setattr(bootstrap.urllib.request, 'urlopen', lambda *_: pytest.fail('unexpected download'))
    bootstrap.download('https://unused', path, hashlib.sha256(b'verified').hexdigest())


def test_source_patch_comparison_uses_fixed_abbreviation(tmp_path, monkeypatch):
    patch = tmp_path / 'reviewed.diff'
    patch.write_bytes(b'exact patch')
    monkeypatch.setattr(bootstrap, 'output', lambda args: 'revision' if 'rev-parse' in args else '' if 'ls-files' in args else ' M file')
    def diff(args):
        assert '--abbrev=8' in args
        return b'exact patch'
    monkeypatch.setattr(bootstrap.subprocess, 'check_output', diff)
    bootstrap.source('unused', 'revision', tmp_path, patch=patch)


@pytest.mark.parametrize('damage', [None, 'weights', 'parameters'])
def test_native_nomic_provenance_requires_unchanged_original(tmp_path, damage):
    blobs = tmp_path / 'blobs'
    blobs.mkdir()
    def blob(data):
        digest = hashlib.sha256(data).hexdigest()
        (blobs / ('sha256-' + digest)).write_bytes(data)
        return {'digest': 'sha256:' + digest, 'size': len(data), 'mediaType': 'application/vnd.ollama.image.model'}
    original = {'config': blob(b'config'), 'layers': [blob(b'weights')]}
    data = json.dumps(original).encode()
    reference = blob(data)
    reference['mediaType'] = 'application/vnd.docker.distribution.manifest.v2+json'
    expected = reference['digest'].split(':')[1]
    native = {**original, 'layers': [*original['layers'], reference]}
    if damage == 'weights':
        (blobs / original['layers'][0]['digest'].replace(':', '-')).write_bytes(b'changed')
    elif damage == 'parameters':
        native['config'] = blob(b'changed')
    manifest = tmp_path / 'manifests/registry.ollama.ai/library/nomic-embed-text/latest'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(native))
    if damage:
        with pytest.raises(RuntimeError):
            bootstrap.verify_nomic(tmp_path, expected)
    else:
        bootstrap.verify_nomic(tmp_path, expected)


def test_start_services_is_explicit_and_does_not_run_after_failed_identity_check(monkeypatch,capsys):
    monkeypatch.setattr(bootstrap,'verify',lambda _:dict(state='pass'))
    monkeypatch.setattr(bootstrap,'start_services',lambda:dict(state='ready',paid_calls=0))
    assert bootstrap.main(['--check','--start-services'])==0
    assert json.loads(capsys.readouterr().out)['services']['paid_calls']==0
    monkeypatch.setattr(bootstrap,'verify',lambda _:dict(state='blocked'))
    monkeypatch.setattr(bootstrap,'start_services',lambda:pytest.fail('started with failed identities'))
    assert bootstrap.main(['--check','--start-services'])==1


def test_backend_install_uses_reviewed_python_cpu_wheel_and_constraints(tmp_path,monkeypatch):
    folder=tmp_path/'vendor/runtime-locks';folder.mkdir(parents=True)
    lock=folder/'cognee.txt';lock.write_text('torch==2.15.0+cpu\nlitellm==1.109.1\n')
    (folder/'manifest.json').write_text(json.dumps({'components':{'cognee':{'python':'3.12.14','sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}}}))
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path);calls=[]
    monkeypatch.setattr(bootstrap,'command',lambda args,**kw:calls.append(args))
    bootstrap.venv('cognee',['cognee==1.6.8'],cpu_torch=True)
    assert calls[0][-2:] == ['3.12.14',tmp_path/'vendor/venv-cognee']
    assert 'torch==2.15.0+cpu' in calls[1] and '--no-deps' in calls[1]
    assert '--constraint' in calls[2] and lock in calls[2] and 'torch==2.15.0+cpu' in calls[2]


def test_dependency_drift_is_blocked_optional_packages_are_not_required(tmp_path,monkeypatch):
    folder=tmp_path/'vendor/runtime-locks';folder.mkdir(parents=True)
    lock=folder/'cognee.txt';lock.write_text('litellm==1.109.1\npytest==9.1.1\n')
    (folder/'manifest.json').write_text(json.dumps({'components':{'cognee':{'python':'3.12.14','sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}}}))
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    monkeypatch.setattr(bootstrap,'output',lambda _:json.dumps({'python':'3.12.14','packages':{'litellm':'1.109.1'}}))
    assert bootstrap.verify_constraints('cognee','unused')['installed_versions_checked']==1
    monkeypatch.setattr(bootstrap,'output',lambda _:json.dumps({'python':'3.12.14','packages':{'litellm':'1.110.0'}}))
    with pytest.raises(RuntimeError,match='version drift'):bootstrap.verify_constraints('cognee','unused')
    lock.write_text('litellm==changed\n')
    with pytest.raises(RuntimeError,match='reviewed manifest'):bootstrap.runtime_lock('cognee')


def test_memos_lock_checks_selected_dependencies_and_native_addons(tmp_path,monkeypatch):
    folder=tmp_path/'vendor/runtime-locks';folder.mkdir(parents=True)
    lock=folder/'memos-package-lock.json';lock.write_text(json.dumps({'packages':{'node_modules/selected':{'version':'1.2.3'},'node_modules/optional':{'version':'2.0.0'}}}))
    (folder/'manifest.json').write_text(json.dumps({'components':{'memos':{'sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}}}))
    package=tmp_path/'package'
    for name,version in [('selected','1.2.3'),('@img/sharp-linux-x64','0.34.5'),('@img/sharp-libvips-linux-x64','1.2.4')]:
        path=package/'node_modules'/name/'package.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'version':version}))
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    assert bootstrap.verify_memos_dependencies(package)['installed_versions_checked']==1
    (package/'node_modules/selected/package.json').write_text('{"version":"1.2.4"}')
    with pytest.raises(RuntimeError,match='differs from lock'):bootstrap.verify_memos_dependencies(package)
