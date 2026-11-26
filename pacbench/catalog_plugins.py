"""Install the reviewed Hermes catalog providers into an isolated trial home."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

# Historical entries in the pinned Hermes v0.23.1 catalog.
HERMES_CATALOG = {
    'mem0': ('mem0ai/mem0/integrations/hermes-plugin-mem0', '43849c6e9d4d773b83a8144b6373aedc67241a5d'),
    'honcho': ('plastic-labs/honcho/hermes-plugin-honcho', '894b88321872e01702a1aa638ddfdde6ad60f173'),
    'hindsight': ('vectorize-io/hindsight/hindsight-integrations/hermes', 'f14cc1eb5624b0374cb4d82768eb2e0b9c21ae24'),
    'supermemory': ('supermemoryai/hermes-supermemory', '91405dd00fdf2a0b3515b25c25557598f6793299'),
    'openviking': ('volcengine/OpenViking/examples/hermes-plugin', 'e855796c83f9eb575d9b0f3082926660730f6a63'),
}

# Explicit upstream source selection for the stable-provider upgrade cohort.
# Hermes remains unmodified: use its documented source install with exact --ref.
# Standalone Supermemory publishes no tags/releases; record its current declared
# plugin version and full revision rather than claiming a tagged stable release.
CATALOG = {
    'mem0': ('mem0ai/mem0/integrations/hermes-plugin-mem0', '94c3fe9f238f3dbf29c9ce98643bd71eb13077cd'),
    'honcho': ('plastic-labs/honcho/hermes-plugin-honcho', '70c67624169683b9e265517aec95a51b8077cabc'),
    'hindsight': ('vectorize-io/hindsight/hindsight-integrations/hermes', '0eb02d8c418eac6b489bc63883c993b2257dfac4'),
    'supermemory': ('supermemoryai/hermes-supermemory', 'a8f360166419958ce77b876c69f7be3bf0716d67'),
    'openviking': ('volcengine/OpenViking/examples/hermes-plugin', '86edb40a01733490b930266dcfcab7c2cebf94b3'),
}
SOURCE_VERSIONS = {'mem0': '1.3.0', 'honcho': '1.2.0', 'hindsight': '1.2.4',
                   'supermemory': '2.0.0', 'openviking': '2.1.4'}
SOURCE_PROFILE = 'upstream-stable-providers-20261113-v1'

# Read-only import and adapter-surface checks, run in the actual Hermes interpreter.
_PROBE = r'''
import importlib.metadata as metadata, json, pathlib, sys, tomllib
from packaging.requirements import Requirement
from plugins.memory import import_provider_module
name, root = sys.argv[1], pathlib.Path(sys.argv[2])
project = tomllib.loads((root/'pyproject.toml').read_text())['project']
for spec in project.get('dependencies', []):
    req = Requirement(spec)
    if req.marker and not req.marker.evaluate(): continue
    try: version = metadata.version(req.name)
    except metadata.PackageNotFoundError: raise RuntimeError('Missing plugin dependency: ' + req.name)
    if version not in req.specifier: raise RuntimeError('Incompatible plugin dependency: ' + spec + ' (installed ' + version + ')')
plugin = import_provider_module(name)
if name == 'mem0':
    from mem0 import Memory
    assert callable(Memory.from_config)
    assert callable(import_provider_module(name, '_setup').build_oss_config)
    assert callable(plugin.Mem0MemoryProvider.save_config)
elif name == 'honcho':
    from honcho import Honcho
    from honcho.api_types import SessionPeerConfig
    assert callable(Honcho) and SessionPeerConfig
    assert callable(import_provider_module(name, 'client').HonchoClientConfig.from_global_config)
    assert callable(import_provider_module(name, 'session_peers').sanitize_peer_id)
elif name == 'hindsight':
    from hindsight_client import Hindsight
    cls = plugin.HindsightMemoryProvider
    assert callable(Hindsight) and callable(cls._get_client) and callable(cls._run_sync)
elif name == 'openviking':
    cls = plugin.OpenVikingMemoryProvider
    assert callable(cls.initialize) and callable(plugin._VikingClient.upload_temp_file) and callable(plugin._VikingClient.post)
elif name == 'supermemory':
    from supermemory import Supermemory
    assert callable(Supermemory)
    for attr in ('_load_supermemory_config', '_sanitize_tag', '_expand_container_tag', '_clean_text_for_capture'):
        assert callable(getattr(plugin, attr))
else: raise RuntimeError('Unsupported catalog provider')
print(json.dumps({'provider': name, 'plugin_version': project.get('version'), 'dependencies': project.get('dependencies', [])}))
'''


def _expected_source(identifier: str) -> str:
    owner, repo, *subdir = identifier.split('/')
    source = f'https://github.com/{owner}/{repo}.git'
    return source + ('#' + '/'.join(subdir) if subdir else '')


def verify_retained_catalog_provider(name: str, home: Path) -> None:
    """Check the pinned existing installation without modifying the home."""
    if name not in CATALOG:
        raise ValueError('No retained catalog recovery protocol')
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    identifier, sha = CATALOG[name]
    plugins = home / 'plugins'
    directory = plugins / name
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f'Expected an installed {name} catalog plugin directory')
    try:
        index = json.loads((plugins / '.install-metadata.json').read_text())
        manifest = YAML(typ='safe').load((directory / 'plugin.yaml').read_text())
    except (OSError, ValueError, YAMLError) as exc:
        raise ValueError(f'{name} catalog plugin metadata or manifest is missing or invalid') from exc
    metadata = index.get(name) if isinstance(index, dict) else None
    if (not isinstance(metadata, dict) or metadata.get('pinned') is not True or
            metadata.get('revision') != sha or metadata.get('source') != _expected_source(identifier) or
            not isinstance(manifest, dict) or manifest.get('name') != name or
            str(manifest.get('version')) != SOURCE_VERSIONS[name]):
        raise ValueError('Retained catalog source differs')


def _verify_catalog_pin(name: str, tree: Path) -> None:
    identifier, _ = CATALOG[name]
    from ruamel.yaml import YAML
    parser = YAML(typ="safe")
    entry = parser.load((tree / 'plugin-catalog' / f'{name}.yaml').read_text())
    if (entry.get('name') != name or entry.get('sha') != HERMES_CATALOG[name][1] or
        entry.get('repo', '').removesuffix('/') != 'https://github.com/' + '/'.join(identifier.split('/')[:2]) or
        entry.get('subdir', '') != '/'.join(identifier.split('/')[2:])):
        raise RuntimeError(f'Pinned Hermes catalog mismatch for {name}')


def _plugin_environment(home: Path, fallback: Path) -> dict:
    env = {**os.environ, 'HOME': str(fallback.resolve()), 'HERMES_HOME': str(home.resolve()),
           'HERMES_ENABLE_PROJECT_PLUGINS': '0', 'GIT_TERMINAL_PROMPT': '0',
           'PYTHONDONTWRITEBYTECODE': '1'}
    if env.get('PACBENCH_R2_LIVE_SCOPE') or env.get('PACBENCH_WORKFLOW_SCOPE'):
        # Package setup is offline from the inference transport. Its child
        # Python bootstrap must not load the request-bound benchmark hooks.
        for key in ('PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PYTHONPATH', 'NODE_OPTIONS'):
            env.pop(key, None)
    return env


def _install_upstream_provider(name: str, home: Path, fallback: Path, hermes_bin: Path, tree: Path) -> dict:
    """Run Hermes' native pinned installer once, under setup."""
    _verify_catalog_pin(name, tree)
    identifier, sha = CATALOG[name]
    env = _plugin_environment(home, fallback)
    command = [str(hermes_bin), 'plugins', 'install', identifier, '--ref', sha, '--no-deps', '--no-enable']
    try:
        result = subprocess.run(command, cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Hermes catalog install for {name} did not complete: {type(exc).__name__}') from exc
    if result.returncode:
        if os.environ.get('PACBENCH_R2_LIVE_SCOPE') or os.environ.get('PACBENCH_WORKFLOW_SCOPE'):
            (home.parent/'catalog-install-error.private.log').write_text(result.stdout + '\n' + result.stderr)
        raise RuntimeError(f'Hermes catalog install for {name} failed (exit {result.returncode})')
    return _probe_installed_provider(name, home, hermes_bin, tree, env)


def _probe_installed_provider(name: str, home: Path, hermes_bin: Path, tree: Path, env: dict) -> dict:
    identifier, sha = CATALOG[name]
    directory = home / 'plugins' / name
    try:
        verify_retained_catalog_provider(name, home)
    except ValueError as exc:
        raise RuntimeError(f'Hermes catalog install for {name} failed source or manifest verification') from exc
    python = hermes_bin.parent / 'python'
    try:
        probe = subprocess.run([str(python), '-c', _PROBE, name, str(directory.resolve())],
                               cwd=tree, env=env, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Hermes plugin dependency probe for {name} did not complete: {type(exc).__name__}') from exc
    if probe.returncode:
        # Tracebacks can contain credentials; retain only fixed context and status.
        raise RuntimeError(f'Hermes plugin dependency or API probe for {name} failed (exit {probe.returncode})')
    try:
        checked = json.loads(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f'Hermes plugin dependency probe for {name} returned invalid output') from exc
    if checked.get('provider') != name or checked.get('plugin_version') != SOURCE_VERSIONS[name]:
        raise RuntimeError(f'Hermes plugin dependency probe for {name} returned mismatched identity')
    receipt = {'source': identifier, 'revision': sha, 'version': checked['plugin_version'],
               'dependencies': checked['dependencies'], 'install': 'hermes-plugins-ref-no-deps-v1',
               'source_profile': SOURCE_PROFILE, 'hermes_catalog_revision': HERMES_CATALOG[name][1],
               'source_selection': ('upstream untagged plugin revision; no release tags available' if name == 'supermemory' else 'plugin shipped in selected stable backend release')}
    if name == 'hindsight':
        receipt['native_recall_types_default'] = 'observation'
    return receipt


def _cache_entry(name: str, tree: Path) -> Path:
    return tree.parent / 'hermes-catalog' / name / CATALOG[name][1]


def _plugin_files_digest(directory: Path) -> str:
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise RuntimeError(f'Unexpected symbolic link in cached Hermes plugin: {path.name}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError(f'Unexpected file type in cached Hermes plugin: {path.name}')
        with path.open('rb') as stream:
            files[path.relative_to(directory).as_posix()] = [
                path.stat().st_mode & 0o111, hashlib.file_digest(stream, 'sha256').hexdigest()]
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _verified_cache_entry(name: str, tree: Path) -> Path:
    _verify_catalog_pin(name, tree)
    entry = _cache_entry(name, tree)
    try:
        verify_retained_catalog_provider(name, entry)
        recorded = (entry / 'files.sha256').read_text().strip()
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'{name} Hermes plugin cache is unavailable. Run ./scripts/setup --install --components hermes.') from exc
    if recorded != _plugin_files_digest(entry / 'plugins' / name):
        raise RuntimeError(f'Cached Hermes plugin files differ for {name}: {entry}')
    return entry


def verify_catalog_cache(tree: Path) -> None:
    for name in CATALOG:
        _verified_cache_entry(name, tree)


def prefetch_catalog(hermes_bin: Path, tree: Path) -> None:
    """Cache each native installer result and its original install metadata."""
    for name in CATALOG:
        entry = _cache_entry(name, tree)
        if entry.exists():
            _verified_cache_entry(name, tree)
            continue
        entry.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.plugin-', dir=entry.parent) as stage:
            stage = Path(stage)
            home, fallback = stage / 'home', stage / 'fallback'
            home.mkdir()
            fallback.mkdir()
            _install_upstream_provider(name, home, fallback, hermes_bin, tree)
            plugins = home / 'plugins'
            cache = stage / 'cache'
            cache.mkdir()
            (cache / 'plugins').mkdir()
            (plugins / name).rename(cache / 'plugins' / name)
            (plugins / '.install-metadata.json').rename(cache / 'plugins' / '.install-metadata.json')
            digest = _plugin_files_digest(cache / 'plugins' / name)
            (cache / 'files.sha256').write_text(digest + '\n')
            try:
                cache.rename(entry)
            except OSError:
                if not entry.exists():
                    raise
                _verified_cache_entry(name, tree)


def install_catalog_provider(name: str, home: Path, fallback: Path, hermes_bin: Path, tree: Path) -> dict:
    """Provision the selected plugin locally without reusing any prior home."""
    entry = _verified_cache_entry(name, tree)
    plugins = home / 'plugins'
    plugins.mkdir(exist_ok=True)
    shutil.copytree(entry / 'plugins' / name, plugins / name, symlinks=True)
    shutil.copyfile(entry / 'plugins' / '.install-metadata.json', plugins / '.install-metadata.json')
    receipt = _probe_installed_provider(name, home, hermes_bin, tree, _plugin_environment(home, fallback))
    receipt['source_cached'] = True
    return receipt
