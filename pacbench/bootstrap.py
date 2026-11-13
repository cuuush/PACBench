"""Reproduce pinned runtimes without paid inference or task credentials.

The default is a read-only plan. Existing source revisions are never reset.
The private task ledger is deliberately outside this installer's remit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request

from .provider_versions import MEMCAL_REVISION, memcal_source, verify_memos_package
from .reme_memos_setup import PINS as REME_PINS
from .cognee_memobase_setup import PINS as COGNEE_PINS

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ('hermes', 'hindsight', 'honcho', 'openviking', 'reme', 'cognee', 'memos', 'supermemory', 'clock', 'embeddings')


def environment(root=ROOT):
    env = dict(os.environ)
    # Installation never needs model credentials. Keep inherited TLS/proxy trust.
    for key in list(env):
        if key.endswith(('API_KEY', 'API_TOKEN')) or key.startswith('PACBENCH_OPENROUTER'):
            env.pop(key, None)
    env.setdefault('UV_CACHE_DIR', str(root / '.bootstrap-cache/uv'))
    env.setdefault('UV_PYTHON_INSTALL_DIR', str(root / '.bootstrap-cache/python'))
    env.setdefault('UV_PYTHON_BIN_DIR', str(root / '.bootstrap-cache/bin'))
    env.setdefault('npm_config_cache', str(root / '.bootstrap-cache/npm'))
    env['NODE_USE_ENV_PROXY'] = '1'
    return env


def command(args, *, cwd=ROOT):
    subprocess.run([str(v) for v in args], cwd=cwd, env=environment(), check=True)


def output(args, cwd=ROOT):
    return subprocess.check_output([str(v) for v in args], cwd=cwd, text=True).strip()


def source(url, revision, destination, *, patch=None):
    if not destination.exists():
        destination.mkdir(parents=True)
        command(['git', 'init', destination])
        command(['git', '-C', destination, 'remote', 'add', 'origin', url])
        command(['git', '-C', destination, 'fetch', '--depth=1', 'origin', revision])
        command(['git', '-C', destination, 'checkout', '--detach', 'FETCH_HEAD'])
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


def verify_nomic(model_root, expected):
    manifest = model_root / 'manifests/registry.ollama.ai/library/nomic-embed-text/latest'
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
    for name, digest in pin['files'].items():
        destination = ROOT.parent / 'shared/pacbench-memos-models' / pin['repository'] / name
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


def venv(name, requirements, *, cpu_torch=False):
    path = ROOT / 'vendor' / ('venv-' + name)
    if not path.exists():
        command(['uv', 'venv', '--python', '3.12', path])
    python = path / 'bin/python'
    if cpu_torch:
        command(['uv', 'pip', 'install', '--python', python, 'torch', '--index-url', 'https://download.pytorch.org/whl/cpu'])
    command(['uv', 'pip', 'install', '--python', python, *requirements])


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
        command(['uv', 'pip', 'install', '--python', vendor / 'venv-hermes-isolated/bin/python', '-r', vendor / 'hermes-isolated-extra-requirements.txt', '-e', memcal])
    elif name == 'honcho':
        tree = vendor / 'honcho-local'
        source('https://github.com/plastic-labs/honcho.git', '70c67624169683b9e265517aec95a51b8077cabc', tree)
        command(['uv', 'sync', '--frozen', '--no-dev', '--python', '3.13'], cwd=tree)
        command(['uv', 'pip', 'install', '--python', tree / '.venv/bin/python', 'time-machine==3.6.0'])
    elif name == 'reme':
        source(*REME_PINS['reme'], vendor / 'reme')
        venv(name, ['reme-ai[core]==0.4.2.2'])
    elif name == 'cognee':
        pin = COGNEE_PINS['cognee']
        source(pin['repository'], pin['commit'], vendor / 'cognee-integrations')
        venv(name, [pin['backend_requirement']], cpu_torch=True)
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
        command(['npm', 'install', '--omit=dev', '--omit=optional', '--legacy-peer-deps'], cwd=root / 'package')
        # Transformers imports Sharp even for text embeddings. Its Linux CPU
        # binaries are optional in npm metadata but required by this runtime.
        command(['npm', 'install', '--no-save', '--package-lock=false', '--omit=dev',
                 '--omit=optional', '--legacy-peer-deps', '@img/sharp-linux-x64@0.34.5',
                 '@img/sharp-libvips-linux-x64@1.2.4'], cwd=root / 'package')
        verify_memos_package(ROOT)
    elif name == 'supermemory':
        root = vendor / 'supermemory-local'
        digest = 'd7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984'
        url = 'https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.9/supermemory-server-linux-x64'
        binary = root / 'supermemory-server-linux-x64'
        download(url, binary, digest)
        binary.chmod(0o755)
        (root / 'installation.json').write_text(json.dumps({'version': 'server-v0.0.9', 'sha256': digest, 'source': url}) + '\n')


def verify(components):
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
            elif name == 'honcho':
                if output(['git', '-C', ROOT / 'vendor/honcho-local', 'rev-parse', 'HEAD']) != '70c67624169683b9e265517aec95a51b8077cabc':
                    raise RuntimeError('Honcho source differs')
            elif name in ('reme', 'cognee'):
                tree = ROOT / 'vendor' / ('reme' if name == 'reme' else 'cognee-integrations')
                revision = REME_PINS['reme'][1] if name == 'reme' else COGNEE_PINS['cognee']['commit']
                if output(['git', '-C', tree, 'rev-parse', 'HEAD']) != revision or output(['git', '-C', tree, 'status', '--porcelain']):
                    raise RuntimeError('Provider integration source differs')
            elif name == 'memos':
                _, detail = verify_memos_package(ROOT)
            elif name == 'supermemory':
                if file_digest(ROOT / 'vendor/supermemory-local/supermemory-server-linux-x64') != 'd7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984':
                    raise RuntimeError('Supermemory binary differs')
            elif name == 'clock':
                if file_digest(ROOT / 'vendor/libfaketime/runtime-v4/libfaketime.so.1') != 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa':
                    raise RuntimeError('Clock binary differs')
            elif name == 'embeddings':
                pins = json.loads((ROOT / 'vendor/EMBEDDING_PINS.json').read_text())
                model_root = ROOT / 'vendor/ollama/models'
                if file_digest(ROOT/'vendor/ollama/bin/ollama')!=pins['ollama']['binary_sha256']:raise RuntimeError('Ollama executable differs')
                verify_nomic(model_root, pins['nomic']['manifest_sha256'])
                for filename, digest in pins['minilm']['files'].items():
                    if file_digest(ROOT.parent / 'shared/pacbench-memos-models' / pins['minilm']['repository'] / filename) != digest:
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
""".replace('CACHE', json.dumps(str(ROOT.parent / 'shared/pacbench-memos-models')))
                subprocess.run(['node', '--input-type=module', '-e', program], cwd=ROOT,
                               env=environment(), check=True, timeout=120, capture_output=True)
                detail['memos_offline_embedding_dimensions'] = 384
            checks.append({'component': name, 'state': 'pass', 'detail': detail})
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            checks.append({'component': name, 'state': 'blocked', 'error': type(exc).__name__})
    return {'state': 'pass' if all(x['state'] == 'pass' for x in checks) else 'blocked',
            'checks': checks, 'inference_calls': 0,
            'scope': 'Offline installed identity checks; no native processing or paid readiness claim.'}


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
    manifest=runtime/'models/manifests/registry.ollama.ai/library/nomic-embed-text/latest'
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install and verify; briefly starts local Ollama for a CPU embedding check, never paid inference')
    parser.add_argument('--start-services',action='store_true',help='Leave verified local Ollama running on port11435; no paid inference')
    parser.add_argument('--check', action='store_true', help='Offline verification of installed identities; no installation')
    parser.add_argument('--components', nargs='+', choices=COMPONENTS, default=list(COMPONENTS))
    args = parser.parse_args(argv)
    if args.start_services and not (args.install or args.check):parser.error('--start-services requires --install or --check')
    if args.install and args.check:
        parser.error('Choose --install or --check')
    if args.check:
        result = verify(args.components)
        if args.start_services and result['state']=='pass':result['services']=start_services()
        print(json.dumps(result, indent=2))
        return int(result['state'] != 'pass')
    if not args.install:
        print(json.dumps({'components': args.components, 'install': False, 'paid_calls': False,
            'private_accounting': 'Restore original ledger.json and authorization.json securely; never recreate from summaries.',
            'remaining_prerequisites': ['Docker daemon for clock build and Honcho', 'start installed local Ollama at 127.0.0.1:11435 before execution', 'restore original private accounting', 'native per-provider preflight and processing checks'],
            'existing_installations': 'Mismatched or modified sources are preserved and rejected; move them aside explicitly before retrying.'}, indent=2))
        return 0
    for name in dict.fromkeys(args.components):
        print(f'Installing {name}', flush=True)
        install(name)
    result = verify(args.components)
    if args.start_services and result['state']=='pass':result['services']=start_services()
    print(json.dumps(result, indent=2))
    return int(result['state'] != 'pass')


if __name__ == '__main__':
    raise SystemExit(main())
