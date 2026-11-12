"""Reuse installed official tokenizers with native cache keys and hash checks."""
import hashlib
import shutil
from pathlib import Path

from pacbench.followups import save

HASHES = {
    'fb374d419588a4632f3f557e76b4b70aebbca790':'446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d',
    '9b5ad71b2ce5302211f9c61530b329a4922fc6a4':'223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7',
}


def configure(root, env):
    vendor=Path(__file__).resolve().parents[1]/'vendor'
    # Isolated servers can own the installed LiteLLM assets even when the
    # Hermes client environment does not depend on LiteLLM. Use identical
    # official bytes, never a mirror or an unverified replacement tokenizer.
    candidates = [vendor/'venv-hermes-isolated', vendor/'venv-hindsight', vendor/'venv-openviking']
    for bundled in (path for runtime in candidates for path in
                    runtime.glob('lib/python*/site-packages/litellm/litellm_core_utils/tokenizers')):
        if not all((bundled/name).exists() for name in HASHES):continue
        cache=root/'tokenizer-cache'
        cache.mkdir(exist_ok=True)
        for name,expected in HASHES.items():
            if hashlib.sha256((bundled/name).read_bytes()).hexdigest()!=expected:
                raise RuntimeError('Installed tokenizer differs from the native tiktoken hash')
            shutil.copyfile(bundled/name,cache/name)
        env['TIKTOKEN_CACHE_DIR']=str(cache.resolve())
        save(root/'native-tokenizer-setup.json',{'source':'installed LiteLLM bundled tokenizers',
             'sha256':HASHES,'native_tiktoken_cache':True})
        return True
    return False
