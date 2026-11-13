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
