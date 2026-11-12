import hashlib
from pathlib import Path

import pytest

from provider_adapters import tokenizer_cache as cache


def test_server_bundled_tokenizers_require_exact_hashes(tmp_path, monkeypatch):
    repo = tmp_path/'repo'
    monkeypatch.setattr(cache, '__file__', str(repo/'provider_adapters/tokenizer_cache.py'))
    hashes = {'one': hashlib.sha256(b'official-one').hexdigest(),
              'two': hashlib.sha256(b'official-two').hexdigest()}
    monkeypatch.setattr(cache, 'HASHES', hashes)
    package = repo/'vendor/venv-hindsight/lib/python3.12/site-packages/litellm/litellm_core_utils/tokenizers'
    package.mkdir(parents=True)
    (package/'one').write_bytes(b'official-one')
    (package/'two').write_bytes(b'official-two')
    root = tmp_path/'arm'; root.mkdir()
    env = {}
    assert cache.configure(root, env)
    assert Path(env['TIKTOKEN_CACHE_DIR']) == root/'tokenizer-cache'
    assert (root/'tokenizer-cache/one').read_bytes() == b'official-one'
    (package/'two').write_bytes(b'changed')
    other = tmp_path/'other'; other.mkdir()
    with pytest.raises(RuntimeError, match='native tiktoken hash'):
        cache.configure(other, {})


def test_missing_bundled_assets_leave_native_cache_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, '__file__', str(tmp_path/'provider_adapters/tokenizer_cache.py'))
    env = {'TIKTOKEN_CACHE_DIR': 'existing-native-cache'}
    assert cache.configure(tmp_path, env) is False
    assert env == {'TIKTOKEN_CACHE_DIR': 'existing-native-cache'}
