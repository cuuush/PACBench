"""Install the reviewed Hermes catalog providers into an isolated trial home."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

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


def install_catalog_provider(name: str, home: Path, fallback: Path, hermes_bin: Path, tree: Path) -> dict:
    """Install reviewed upstream source without changing the shared client environment.

    Hermes uses a separate PM runtime even with --no-deps. The memory provider is
    left inactive until its declared dependencies and adapter imports are checked.
    """
    if name not in CATALOG:
        raise RuntimeError(f'Unsupported external catalog provider: {name}')
    identifier, sha = CATALOG[name]
    # Verify the unchanged Hermes catalog separately from the explicit upstream selection.
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError
    parser = YAML(typ="safe")
    entry = parser.load((tree / 'plugin-catalog' / f'{name}.yaml').read_text())
    if (entry.get('name') != name or entry.get('sha') != HERMES_CATALOG[name][1] or
        entry.get('repo', '').removesuffix('/') != 'https://github.com/' + '/'.join(identifier.split('/')[:2]) or
        entry.get('subdir', '') != '/'.join(identifier.split('/')[2:])):
        raise RuntimeError(f'Pinned Hermes catalog mismatch for {name}')
    env = {**os.environ, 'HOME': str(fallback.resolve()), 'HERMES_HOME': str(home.resolve()),
           'HERMES_ENABLE_PROJECT_PLUGINS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    command = [str(hermes_bin), 'plugins', 'install', identifier, '--ref', sha, '--no-deps', '--no-enable']
    try:
        result = subprocess.run(command, cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Hermes catalog install for {name} did not complete: {type(exc).__name__}') from exc
    if result.returncode:
        raise RuntimeError(f'Hermes catalog install for {name} failed (exit {result.returncode})')
    directory = home / 'plugins' / name
    try:
        metadata = json.loads((home / 'plugins' / '.install-metadata.json').read_text())[name]
        manifest = parser.load((directory / 'plugin.yaml').read_text())
    except (OSError, ValueError, KeyError, YAMLError) as exc:
        raise RuntimeError(f'Hermes catalog install for {name} has invalid metadata') from exc
    if (not directory.is_dir() or directory.is_symlink() or metadata.get('pinned') is not True or
        metadata.get('revision') != sha or metadata.get('source') != _expected_source(identifier) or
        not isinstance(manifest, dict) or manifest.get('name') != name or
        str(manifest.get('version')) != SOURCE_VERSIONS[name]):
        raise RuntimeError(f'Hermes catalog install for {name} failed source or manifest verification')
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
