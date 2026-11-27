"""Install and verify pinned PACBench runtimes without model credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from pacbench.providers.cognee import PINS as COGNEE_PINS
from pacbench.providers.reme import PINS as REME_PINS

from .provider_versions import (
    MEMCAL_REVISION,
    hindsight_model_cache,
    huggingface_model_cache,
    memcal_source,
    memos_model_cache,
    verify_memos_package,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ('hermes', 'hindsight', 'honcho', 'openviking', 'reme', 'cognee', 'memos', 'supermemory', 'clock', 'embeddings')
INSTALL_TOOLS = {
    'hermes': ('git', 'uv'),
    'hindsight': ('uv',),
    'honcho': ('git', 'uv', 'docker'),
    'openviking': ('uv',),
    'reme': ('git', 'uv'),
    'cognee': ('git', 'uv'),
    'memos': ('node', 'npm', 'make', 'c++'),
    'embeddings': ('node', 'tar', 'zstd'),
}


def install_blockers(components):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        return ['Native bootstrap requires Linux x86-64.']
    build_clock = 'clock' in components and not (ROOT / 'vendor/libfaketime/runtime-v4/libfaketime.so.1').exists()
    commands = dict.fromkeys(tool for name in components for tool in INSTALL_TOOLS.get(name, ()))
    if build_clock:
        commands.update(dict.fromkeys(('curl', 'sha256sum', 'docker')))
    missing = [tool for tool in commands if shutil.which(tool) is None]
    if missing:
        return ['Required commands missing from PATH: ' + ', '.join(missing)]
    if build_clock or 'honcho' in components:
        docker = ['docker']
        reason = 'The clock build requires an accessible Docker daemon.'
        if 'honcho' in components:
            docker.append('--host=unix:///var/run/docker.sock')
            reason = 'Honcho requires access to the local Docker socket at /var/run/docker.sock.'
        try:
            daemon = subprocess.run([*docker, 'info'], env=environment(), timeout=10,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return [reason]
        if daemon.returncode:
            return [reason]
    return []


def environment(root=ROOT):
    env = dict(os.environ)
    # Installation never needs model credentials. Keep inherited TLS/proxy trust.
    for key in list(env):
        if (key in {'META', 'PACBENCH_DIRECT_UPSTREAM_KEY'} or
                key.endswith(('API_KEY', 'API_TOKEN')) or key.startswith('PACBENCH_OPENROUTER')):
            env.pop(key, None)
    env.setdefault('UV_CACHE_DIR', str(root / '.bootstrap-cache/uv'))
    env.setdefault('UV_PYTHON_INSTALL_DIR', str(root / '.bootstrap-cache/python'))
    env.setdefault('UV_PYTHON_BIN_DIR', str(root / '.bootstrap-cache/bin'))
    env.setdefault('npm_config_cache', str(root / '.bootstrap-cache/npm'))
    env.setdefault('XDG_CACHE_HOME', str(root / '.bootstrap-cache'))
    env['NODE_USE_ENV_PROXY'] = '1'
    return env


def command(args, *, cwd=ROOT):
    subprocess.run([str(v) for v in args], cwd=cwd, env=environment(), check=True)


def output(args, cwd=ROOT):
    return subprocess.check_output([str(v) for v in args], cwd=cwd, text=True).strip()


def catalog_plugins(*, install):
    python = ROOT / 'vendor/venv-hermes-isolated/bin/python'
    tree = ROOT / 'vendor/hermes-isolated'
    env = environment()
    env.setdefault('HERMES_RUNTIME_DIR', str(ROOT / '.bootstrap-cache/hermes-tools'))
    paths = [str(tree)]
    if install:
        paths.insert(0, str(python.with_name('hermes')))
    function = 'prefetch_catalog' if install else 'verify_catalog_cache'
    program = ('import sys; from pathlib import Path; '
               f'from pacbench.catalog_plugins import {function}; {function}(*map(Path, sys.argv[1:]))')
    subprocess.run([str(python), '-c', program, *paths], cwd=ROOT, env=env, check=True)


def source(url, revision, destination, *, patch=None):
    if os.path.lexists(destination):
        verify_source(revision, destination, patch)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{destination.name}-', dir=destination.parent) as staging:
        tree = Path(staging) / 'source'
        command(['git', 'init', tree])
        command(['git', '-C', tree, 'remote', 'add', 'origin', url])
        command(['git', '-C', tree, 'fetch', '--depth=1', 'origin', revision])
        command(['git', '-C', tree, 'checkout', '--detach', 'FETCH_HEAD'])
        verify_source(revision, tree, patch)
        if os.path.lexists(destination):
            raise FileExistsError(f'{destination}: appeared during source setup')
        tree.rename(destination)


def verify_source(revision, destination, patch):
    actual = output(['git', '-C', destination, 'rev-parse', 'HEAD'])
    if actual != revision:
        raise RuntimeError(f'{destination}: revision differs; preserve/move the existing checkout before installing')
    if output(['git', '-C', destination, 'ls-files', '--others', '--exclude-standard']):
        raise RuntimeError(f'{destination}: untracked files; refusing to alter it')
    dirty = output(['git', '-C', destination, 'status', '--porcelain'])
    if patch:
        if dirty:
            actual_diff = subprocess.check_output(['git', '-C', str(destination), 'diff', '--abbrev=8', 'HEAD'])
            if actual_diff != patch.read_bytes():
                raise RuntimeError(f'{destination}: working tree differs from the reviewed patch')
        else:
            command(['git', '-C', destination, 'apply', patch])
    elif dirty:
        raise RuntimeError(f'{destination}: uncommitted changes; refusing to alter it')


def download(url, destination, digest):
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + '.download')
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
            if file_digest(temporary) != digest:
                raise RuntimeError('Downloaded asset digest differs from pin')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if file_digest(destination) != digest:
        raise RuntimeError(f'{destination}: digest differs; refusing to replace existing file')


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def nomic_manifest(model_root):
    return model_root / 'manifests/registry.ollama.ai/library/nomic-embed-text/latest'


def verify_nomic(model_root, expected):
    manifest = nomic_manifest(model_root)
    model = json.loads(manifest.read_text())
    if file_digest(manifest) != expected:
        # Ollama 0.40 records the original registry manifest as one extra
        # provenance layer. Verify its original bytes and every source field;
        # this is native metadata, not a change to model weights or parameters.
        layers = model.get('layers', [])
        reference = layers[-1] if layers else {}
        if reference.get('mediaType') != 'application/vnd.docker.distribution.manifest.v2+json' or reference.get('digest') != 'sha256:' + expected:
            raise RuntimeError('Native embedding model differs from the registry pin')
        original = model_root / 'blobs' / ('sha256-' + expected)
        if file_digest(original) != expected or json.loads(original.read_text()) != {**model, 'layers': layers[:-1]}:
            raise RuntimeError('Native embedding provenance does not bind unchanged source')
    for layer in [model['config'], *model['layers']]:
        digest = layer['digest']
        if not digest.startswith('sha256:') or file_digest(model_root / 'blobs' / digest.replace(':', '-')) != digest.split(':')[1]:
            raise RuntimeError('Nomic model blob differs')


def embeddings():
    pins = json.loads((ROOT / 'vendor/EMBEDDING_PINS.json').read_text())
    pin = pins['minilm']
    model_cache = memos_model_cache(ROOT)
    for name, digest in pin['files'].items():
        destination = model_cache / pin['repository'] / name
        download(f"https://huggingface.co/{pin['repository']}/resolve/{pin['revision']}/{name}", destination, digest)
    runtime = ROOT / 'vendor/ollama'
    archive = runtime / ('release-' + pins['ollama']['version'] + '.tar.zst')
    download(pins['ollama']['url'], archive, pins['ollama']['sha256'])
    # CPU-only runtime; retain all native CPU libraries, omit unused CUDA payloads.
    # The official archive digest is checked above. GNU tar+zstd supports this
    # release format on Python versions before tarfile gained zstd support.
    command(['tar', '--zstd', '-xf', archive, '-C', runtime,
             '--exclude=lib/ollama/cuda*', '--exclude=lib/ollama/vulkan*',
             'bin/ollama', 'lib/ollama'])
    env = environment()
    env['OLLAMA_MODELS'] = str(runtime / 'models')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env['OLLAMA_HOST'] = f'127.0.0.1:{port}'
    binary = runtime / 'bin/ollama'
    with (runtime / 'setup.log').open('ab') as log:
        process = subprocess.Popen([str(binary), 'serve'], env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/version', timeout=1):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Temporary local embedding server failed; inspect vendor/ollama/setup.log')
                time.sleep(.2)
        if not nomic_manifest(runtime / 'models').exists():
            subprocess.run([str(binary), 'pull', pins['nomic']['name']], env=env, check=True)
        verify_nomic(runtime / 'models', pins['nomic']['manifest_sha256'])
        from provider_adapters.embedding_health import check
        check(f'http://127.0.0.1:{port}/v1')
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def runtime_lock(name):
    root=ROOT/'vendor/runtime-locks'
    manifest=json.loads((root/'manifest.json').read_text())
    row=manifest['components'][name]
    path=root/(name+'.txt')
    if path.exists() and file_digest(path)!=row['sha256']:
        raise RuntimeError('Runtime dependency constraints differ from reviewed manifest')
    if not path.exists() and 'sha256' in row:
        raise RuntimeError('Reviewed runtime dependency constraints are missing')
    return row,path


def constraint_versions(path):
    return dict(line.strip().split('==',1) for line in path.read_text().splitlines()
                if line.strip() and not line.startswith('#'))


def verify_constraints(name, python):
    row,path=runtime_lock(name)
    code="import importlib.metadata as m,json,re,sys;print(json.dumps({'python':sys.version.split()[0],'packages':{re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in m.distributions() if d.metadata['Name']}}))"
    actual=json.loads(output([python,'-c',code]))
    if actual['python']!=row['python']:raise RuntimeError('Python version differs from reviewed runtime')
    if not path.exists():return {'python':row['python'],'upstream_lock':row['upstream_lock']}
    pins=constraint_versions(path)
    wrong=[name for name,version in actual['packages'].items() if name in pins and version!=pins[name]]
    if wrong:raise RuntimeError('Runtime dependency version drift: '+','.join(sorted(wrong)))
    return {'python':row['python'],'constraints_sha256':row['sha256'],
            'installed_versions_checked':sum(name in pins for name in actual['packages']),
            'scope':'Constraints pin selected runtime dependencies without installing optional/development packages'}


def venv(name, requirements, *, cpu_torch=False):
    row,lock=runtime_lock(name)
    path = ROOT / 'vendor' / ('venv-' + name)
    if not path.exists():
        command(['uv', 'venv', '--python', row['python'], path])
    python = path / 'bin/python'
    pins=constraint_versions(lock)
    if cpu_torch:
        command(['uv', 'pip', 'install', '--python', python, 'torch=='+pins['torch'], '--no-deps',
                 '--index-url', 'https://download.pytorch.org/whl/cpu'])
    requirements=[*requirements,*(['torch=='+pins['torch']] if cpu_torch else [])]
    command(['uv', 'pip', 'install', '--python', python, '--constraint', lock, *requirements])


def cognee_tokenizer(*, offline):
    env = environment()
    env['HF_HUB_CACHE'] = str(huggingface_model_cache(ROOT))
    program = """
import json
from pathlib import Path
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

repo = 'nomic-ai/nomic-embed-text-v1.5'
Tokenizer.from_file(hf_hub_download(repo, 'tokenizer.json', local_files_only=OFFLINE))
json.loads(Path(hf_hub_download(repo, 'tokenizer_config.json', local_files_only=OFFLINE)).read_text())
""".replace('OFFLINE', repr(offline))
    try:
        subprocess.run([str(ROOT / 'vendor/venv-cognee/bin/python'), '-c', program],
                       cwd=ROOT, env=env, check=True, timeout=120, capture_output=offline)
    except subprocess.CalledProcessError:
        if offline:
            raise RuntimeError('Cognee tokenizer unavailable in the local Hugging Face cache') from None
        raise


def hindsight_models(*, offline):
    cache = str(hindsight_model_cache(ROOT))
    env = environment()
    env.update(SENTENCE_TRANSFORMERS_HOME=cache, HF_HUB_CACHE=cache)
    if offline:
        env['HF_HUB_OFFLINE'] = '1'
    program = """
import asyncio
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
from hindsight_api.engine.embeddings import LocalSTEmbeddings

async def check_defaults():
    await LocalSTEmbeddings().initialize()
    await LocalSTCrossEncoder().initialize()

asyncio.run(check_defaults())
"""
    try:
        subprocess.run([str(ROOT / 'vendor/venv-hindsight/bin/python'), '-c', program],
                       cwd=ROOT, env=env, check=True, timeout=180 if offline else 1800,
                       capture_output=offline)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            'Hindsight could not load its native BAAI/bge-small-en-v1.5 and '
            f'hindsight-ai/hs-rerank-1 models from {cache}. '
            'Run ./scripts/setup --install --components hindsight with Hugging Face access.'
        ) from exc


def verify_memos_dependencies(package):
    folder=ROOT/'vendor/runtime-locks'
    row=json.loads((folder/'manifest.json').read_text())['components']['memos']
    lock=folder/'memos-package-lock.json'
    if file_digest(lock)!=row['sha256']:raise RuntimeError('MemOS dependency lock differs')
    checked=0
    for relative,entry in json.loads(lock.read_text())['packages'].items():
        if not relative:continue
        path=Path(package)/relative/'package.json'
        if path.is_file():
            if json.loads(path.read_text())['version']!=entry['version']:
                raise RuntimeError('Installed MemOS npm dependency differs from lock: '+relative)
            checked+=1
    for name,version in [('@img/sharp-linux-x64','0.34.5'),('@img/sharp-libvips-linux-x64','1.2.4')]:
        if json.loads((Path(package)/'node_modules'/name/'package.json').read_text())['version']!=version:
            raise RuntimeError('Native text-embedding addon differs')
    return {'lock_sha256':row['sha256'],'installed_versions_checked':checked}


def install(name):
    vendor = ROOT / 'vendor'
    if name == 'embeddings':
        embeddings()
    elif name == 'clock':
        binary = vendor / 'libfaketime/runtime-v4/libfaketime.so.1'
        if not binary.exists():
            command(['sh', vendor / 'libfaketime/install-runtime-v4.sh'])
        if file_digest(binary) != 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa':
            raise RuntimeError('Clock binary differs from the reproducible build')
    elif name == 'hermes':
        pin = dict(line.split('=', 1) for line in (vendor / 'HERMES_PIN').read_text().splitlines() if '=' in line)
        tree = vendor / 'hermes-isolated'
        source(pin['HERMES_REMOTE'], pin['HERMES_PIN'], tree, patch=ROOT / 'patches/hermes-isolated.diff')
        memcal = memcal_source(ROOT)
        source('https://github.com/cuuush/memcal.git', MEMCAL_REVISION, memcal)
        command(['uv', 'python', 'install', '3.14.9'])
        env = environment()
        env['UV_PROJECT_ENVIRONMENT'] = str(vendor / 'venv-hermes-isolated')
        subprocess.run(['uv', 'sync', '--locked', '--no-default-groups', '--extra', 'all', '--python', '3.14.9'], cwd=tree, env=env, check=True)
        _,lock=runtime_lock('hermes')
        command(['uv', 'pip', 'install', '--python', vendor / 'venv-hermes-isolated/bin/python',
                 '--constraint',lock,'-r', vendor / 'hermes-isolated-extra-requirements.txt', '-e', memcal])
        catalog_plugins(install=True)
    elif name == 'honcho':
        from provider_adapters.honcho_local import PGVECTOR_IMAGE

        tree = vendor / 'honcho-local'
        source('https://github.com/plastic-labs/honcho.git', '70c67624169683b9e265517aec95a51b8077cabc', tree)
        row,_=runtime_lock('honcho')
        command(['uv', 'sync', '--frozen', '--no-dev', '--python', row['python']], cwd=tree)
        command(['uv', 'pip', 'install', '--python', tree / '.venv/bin/python', 'time-machine==3.6.0'])
        docker = ['docker', '--host=unix:///var/run/docker.sock']
        cached = subprocess.run([*docker, 'image', 'inspect', PGVECTOR_IMAGE], env=environment(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if cached.returncode:
            command([*docker, 'pull', PGVECTOR_IMAGE])
    elif name == 'reme':
        source(*REME_PINS['reme'], vendor / 'reme')
        venv(name, ['reme-ai[core]==0.4.2.2'])
    elif name == 'cognee':
        pin = COGNEE_PINS['cognee']
        source(pin['repository'], pin['commit'], vendor / 'cognee-integrations')
        venv(name, [pin['backend_requirement']], cpu_torch=True)
        cognee_tokenizer(offline=False)
    elif name == 'hindsight':
        venv(name, ['hindsight-all==1.2.1'], cpu_torch=True)
    elif name == 'openviking':
        venv(name, ['openviking==0.5.4.1'])
    elif name == 'memos':
        pin = json.loads((vendor / 'MEMOS_LOCAL_PIN.json').read_text())
        root = vendor / 'memos-local-plugin'
        archive = root / 'release.tgz'
        download(pin['tarball'], archive, pin['sha256'])
        if not (root / 'package').exists():
            with tarfile.open(archive) as stream:
                members = stream.getmembers()
                if any(m.issym() or m.islnk() or '..' in Path(m.name).parts or Path(m.name).parts[0] != 'package' for m in members):
                    raise RuntimeError('Unsafe package archive')
                stream.extractall(root, filter='data')
        verify_memos_package(ROOT)
        locks=ROOT/'vendor/runtime-locks';manifest=json.loads((locks/'manifest.json').read_text())
        lock=locks/'memos-package-lock.json'
        if file_digest(lock)!=manifest['components']['memos']['sha256']:
            raise RuntimeError('Native MemOS npm dependency lock differs')
        shutil.copyfile(lock,root/'package/package-lock.json')
        # Transformers needs the platform's Sharp addons already pinned in this lock.
        command(['npm', 'ci', '--omit=dev', '--include=optional', '--legacy-peer-deps'], cwd=root / 'package')
        verify_memos_package(ROOT)
    elif name == 'supermemory':
        root = vendor / 'supermemory-local'
        digest = 'd7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984'
        url = 'https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.9/supermemory-server-linux-x64'
        binary = root / 'supermemory-server-linux-x64'
        download(url, binary, digest)
        binary.chmod(0o755)
        (root / 'installation.json').write_text(json.dumps({'version': 'server-v0.0.9', 'sha256': digest, 'source': url}) + '\n')


def verify(components, *, warm_models=False):
    """Offline installation evidence, deliberately separate from paid readiness."""
    expected = {
        'hermes': ('vendor/venv-hermes-isolated/bin/python', {'memcal': '0.8.11', 'mem0ai': '2.2.1', 'honcho-ai': '2.9.0', 'supermemory': '5.0.1', 'hindsight-client': '1.2.1', 'time-machine': '3.6.0'}),
        'hindsight': ('vendor/venv-hindsight/bin/python', {'hindsight-all': '1.2.1'}),
        'openviking': ('vendor/venv-openviking/bin/python', {'openviking': '0.5.4.1'}),
        'reme': ('vendor/venv-reme/bin/python', {'reme-ai': '0.4.2.2'}),
        'cognee': ('vendor/venv-cognee/bin/python', {'cognee': '1.6.8'}),
        'honcho': ('vendor/honcho-local/.venv/bin/python', {'time-machine': '3.6.0'}),
    }
    checks = []
    for name in dict.fromkeys(components):
        try:
            detail = {}
            if name in expected:
                python, versions = expected[name]
                code = 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ' + repr(list(versions)) + '}))'
                detail = json.loads(output([ROOT / python, '-c', code]))
                if detail != versions:
                    raise RuntimeError('Installed distribution versions differ from pins')
                detail['dependency_constraints']=verify_constraints(name,ROOT/python)
                if name in ('hermes', 'honcho'):
                    detail['clock'] = json.loads(output([ROOT / python, '-c', 'from pacbench.python_provider_clock import package_metadata; import json; print(json.dumps(package_metadata()))']))
            if name == 'hermes':
                from .preflight import _git
                tree = ROOT / 'vendor/hermes-isolated'
                pin = next(x.split('=', 1)[1] for x in (ROOT / 'vendor/HERMES_PIN').read_text().splitlines() if x.startswith('HERMES_PIN='))
                if _git(tree, 'rev-parse', 'HEAD').strip() != pin or _git(tree, 'diff', '--abbrev=8', 'HEAD') != (ROOT / 'patches/hermes-isolated.diff').read_text() or _git(tree, 'ls-files', '--others', '--exclude-standard').strip():
                    raise RuntimeError('Hermes source or isolation patch differs')
                from .provider_versions import verify_memcal_source
                detail['memcal_source'] = verify_memcal_source(ROOT)
                catalog_plugins(install=False)
                detail['catalog_plugins_cached'] = True
            elif name == 'hindsight':
                hindsight_models(offline=not warm_models)
                detail['local_models_verified'] = True
            elif name == 'honcho':
                if output(['git', '-C', ROOT / 'vendor/honcho-local', 'rev-parse', 'HEAD']) != '70c67624169683b9e265517aec95a51b8077cabc':
                    raise RuntimeError('Honcho source differs')
            elif name in ('reme', 'cognee'):
                tree = ROOT / 'vendor' / ('reme' if name == 'reme' else 'cognee-integrations')
                revision = REME_PINS['reme'][1] if name == 'reme' else COGNEE_PINS['cognee']['commit']
                if output(['git', '-C', tree, 'rev-parse', 'HEAD']) != revision or output(['git', '-C', tree, 'status', '--porcelain']):
                    raise RuntimeError('Provider integration source differs')
                if name == 'cognee':
                    cognee_tokenizer(offline=True)
                    detail['embedding_tokenizer_cached'] = True
            elif name == 'memos':
                _, detail = verify_memos_package(ROOT)
                detail['dependency_lock']=verify_memos_dependencies(ROOT/'vendor/memos-local-plugin/package')
            elif name == 'supermemory':
                if file_digest(ROOT / 'vendor/supermemory-local/supermemory-server-linux-x64') != 'd7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984':
                    raise RuntimeError('Supermemory binary differs')
            elif name == 'clock':
                if file_digest(ROOT / 'vendor/libfaketime/runtime-v4/libfaketime.so.1') != 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa':
                    raise RuntimeError('Clock binary differs')
            elif name == 'embeddings':
                pins = json.loads((ROOT / 'vendor/EMBEDDING_PINS.json').read_text())
                model_root = ROOT / 'vendor/ollama/models'
                model_cache = memos_model_cache(ROOT)
                if file_digest(ROOT/'vendor/ollama/bin/ollama')!=pins['ollama']['binary_sha256']:raise RuntimeError('Ollama executable differs')
                verify_nomic(model_root, pins['nomic']['manifest_sha256'])
                for filename, digest in pins['minilm']['files'].items():
                    if file_digest(model_cache / pins['minilm']['repository'] / filename) != digest:
                        raise RuntimeError('MiniLM asset differs')
                # Exercise the actual native text embedder, with remote loading
                # disabled, rather than treating file presence as readiness.
                program = """
import {LocalEmbeddingProvider} from './vendor/memos-local-plugin/package/dist/core/embedding/providers/local.js';
const p = new LocalEmbeddingProvider();
const log = {info(){},warn(){},debug(){},error(){}};
const v = await p.embed(['Local offline embedding readiness check.'], 'document', {
  config: {model:'Xenova/all-MiniLM-L6-v2', local:{cacheDir:CACHE, offline:true}}, log});
if(v.length !== 1 || v[0].length !== 384 || !v[0].every(Number.isFinite)) throw Error('Bad local embedding');
await p.close();
""".replace('CACHE', json.dumps(str(model_cache)))
                subprocess.run(['node', '--input-type=module', '-e', program], cwd=ROOT,
                               env=environment(), check=True, timeout=120, capture_output=True)
                detail['memos_offline_embedding_dimensions'] = 384
            checks.append({'component': name, 'state': 'pass', 'detail': detail})
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            blocked = {'component': name, 'state': 'blocked', 'error': type(exc).__name__}
            if isinstance(exc, FileNotFoundError) and exc.filename:
                blocked['missing_path'] = os.fsdecode(exc.filename)
            if name == 'embeddings':
                blocked['memos_model_cache'] = str(memos_model_cache(ROOT))
                blocked['asset_pins'] = 'vendor/EMBEDDING_PINS.json'
            if isinstance(exc, RuntimeError):
                blocked['detail'] = str(exc)
            checks.append(blocked)
    return {'state': 'pass' if all(x['state'] == 'pass' for x in checks) else 'blocked',
            'checks': checks, 'inference_calls': 0,
            'scope': 'Installed identity and local model checks. No paid inference.'}


def start_services():
    """Leave the verified local embedding daemon ready; never load credentials."""
    runtime=ROOT/'vendor/ollama';pins=json.loads((ROOT/'vendor/EMBEDDING_PINS.json').read_text())
    if file_digest(runtime/'bin/ollama')!=pins['ollama']['binary_sha256']:
        raise RuntimeError('Ollama executable differs from official archive pin')
    verify_nomic(runtime/'models',pins['nomic']['manifest_sha256'])
    base='http://127.0.0.1:11435'
    def read(path):
        with urllib.request.urlopen(base+path,timeout=2) as response:return json.load(response)
    process=None
    try:read('/api/version')
    except OSError:
        env=environment();env.update(OLLAMA_HOST='127.0.0.1:11435',OLLAMA_MODELS=str(runtime/'models'))
        logpath=runtime/'runtime.private.log'
        with logpath.open('ab') as log:
            logpath.chmod(0o600)
            process=subprocess.Popen([str(runtime/'bin/ollama'),'serve'],env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        deadline=time.monotonic()+30
        while True:
            try:read('/api/version');break
            except OSError:
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Local embedding daemon failed to start')
                time.sleep(.2)
    if read('/api/version').get('version')!=pins['ollama']['version']:
        raise RuntimeError('Existing endpoint is not the pinned Ollama version')
    manifest=nomic_manifest(runtime/'models')
    models=read('/api/tags')['models']
    if not any(m.get('name')=='nomic-embed-text:latest' and m.get('digest') in {file_digest(manifest),pins['nomic']['manifest_sha256']} for m in models):
        raise RuntimeError('Existing embedding endpoint model differs from verified local manifest')
    from provider_adapters.embedding_health import check
    health=check(base+'/v1')
    result={'state':'ready','endpoint':base,'model':'nomic-embed-text','paid_calls':0,
        'started_pid':process.pid if process else None,'embedding_health':health}
    (runtime/'runtime.private.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='./scripts/setup', description=__doc__,
        epilog='With no options, install every runtime and start local Ollama.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--install', action='store_true', help='Install and verify without leaving a service running')
    mode.add_argument('--check', action='store_true', help='Verify the installed runtimes offline')
    parser.add_argument('--start-services', action='store_true',
                        help='Leave verified local Ollama running on port 11435')
    parser.add_argument('--components', nargs='+', choices=COMPONENTS, default=list(COMPONENTS))
    args = parser.parse_args(argv)
    has_embeddings = 'embeddings' in args.components
    if args.start_services and not has_embeddings:
        parser.error('--start-services requires the embeddings component')
    run_services = args.start_services or (has_embeddings and not (args.install or args.check))
    if not args.check:
        blockers = install_blockers(args.components)
        if blockers:
            print(json.dumps({'state': 'blocked', 'components': args.components, 'blockers': blockers,
                              'inference_calls': 0}, indent=2))
            return 1
        for name in dict.fromkeys(args.components):
            print(f'Installing {name}', flush=True)
            install(name)
    result = verify(args.components) if args.check else verify(args.components, warm_models=True)
    if result['state'] == 'pass':
        if run_services:
            result['services'] = start_services()
    elif args.check:
        blocked = [row['component'] for row in result.get('checks', ()) if row['state'] == 'blocked']
        repair = './scripts/setup --install'
        if blocked:
            repair += ' --components ' + ' '.join(blocked)
        result['next_step'] = f'Install or repair the blocked runtimes with `{repair}`, then rerun the check.'
    print(json.dumps(result, indent=2))
    return int(result['state'] != 'pass')


if __name__ == '__main__':
    raise SystemExit(main())
