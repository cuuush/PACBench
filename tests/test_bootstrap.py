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
