"""Inspect Memcal trial prerequisites without inference or store initialization."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

from . import harness
from .context_isolation import outside_project
from .gateway_client import STATE, require_live_cost_boundary
from .preparation_audit import audit_prepared


def provider_inventory(repo: Path = harness.REPO) -> dict:
    """Inspect installation paths only; do not launch providers or inference."""
    from .provider_versions import memcal_source
    paths = {
        'memcal': [str(memcal_source(repo) / 'integrations/hermes/memcal/__init__.py')],
        'mem0': [],
        'hindsight': ['vendor/venv-hindsight/bin/python'],
        'openviking': ['vendor/venv-openviking/bin/openviking-server'],
        'reme': ['vendor/venv-reme/bin/python'],
        'cognee': ['vendor/venv-cognee/bin/python'],
        'memos': ['vendor/memos-local-plugin/package/dist/bridge.cjs'],
        'supermemory': ['vendor/supermemory-local/supermemory-server-linux-x64',
                        'vendor/supermemory-local/installation.json'],
        'honcho': ['vendor/honcho-local/.venv/bin/python'],
    }
    requirements = {
        'memcal': 'Native Messages and TLS IMAP fixtures; installed Memcal and Hermes client dependencies.',
        'mem0': 'Hermes Mem0 SDK, local Qdrant and Ollama nomic-embed-text.',
        'hindsight': 'Hindsight 1.2.1 server and client in separate environments.',
        'openviking': 'OpenViking server and Ollama nomic-embed-text.',
        'reme': 'Pinned ReMe runtime, Hermes resource plugin and local embeddings.',
        'cognee': 'Pinned Cognee server, native HTTP Hermes plugin and local Ollama embeddings.',
        'memos': 'Pinned local MemOS bridge, Hermes memtensor plugin and offline MiniLM assets.',
        'supermemory': 'Verified local server, SDK and Ollama nomic-embed-text.',
        'honcho': 'Honcho 3.6.1 checkout, SDK 2.9.0, Docker/pgvector and Ollama; simulated clock additionally needs pinned libfaketime.',
    }
    providers = []
    for name, files in paths.items():
        missing = [path for path in files if not (repo / path).is_file()]
        providers.append({'provider': name, 'missing_paths': missing,
                          'other_requirements': requirements[name],
                          'openrouter_pilot': 'native route implemented; installation and live readiness must be verified',
                          'installed_paths_present': not missing})
    return {'providers': providers, 'docker_executable_present': bool(shutil.which('docker')),
            'inference_calls': 0,
            'scope': 'File presence only; versions, service readiness, model compatibility, route enforcement and recall are not verified.'}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL,
        timeout=30, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat'})


def _runtime_metadata(python: Path) -> dict:
    # Inspect distributions, without importing providers or loading a real home.
    program = (
        'import importlib.metadata as m,json; '
        'print(json.dumps({n:m.version(n) for n in '
        '["hermes-agent","memcal","pytypedstream","openai","pyyaml"]}))')
    return json.loads(subprocess.check_output(
        [str(python), '-c', program], text=True, stderr=subprocess.DEVNULL, timeout=30))


def _scratch_check() -> str:
    # Actually create and remove an empty directory: access() alone can lie in
    # a managed sandbox. Never relax the same boundary used by native recall.
    with tempfile.TemporaryDirectory(prefix='pacbench-preflight-') as folder:
        path = Path(folder)
        if not outside_project(path):
            raise RuntimeError('Temporary directory is inside Git; select a writable TMPDIR outside every project')
        return str(path.parent.resolve())


def inspect(prepared: Path, *, repo: Path = harness.REPO,
            gateway_state: Path = STATE, check_gateway: bool = False) -> dict:
    """A passing setup check is not model access, corpus quality or recall proof."""
    checks = []

    def check(name, action):
        try:
            detail = action()
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            # Subprocess output and exception text can contain private values.
            detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
            checks.append({'check': name, 'state': 'blocked', 'detail': detail})
            return False
        checks.append({'check': name, 'state': 'pass', 'detail': detail})
        return True

    def preparation():
        root = prepared.parent if prepared.is_file() else prepared
        result = audit_prepared(root)
        if result['state'] != 'pass':
            raise RuntimeError('Frozen preparation integrity failed; run pacbench.preparation_audit for details')
        if not (root / 'week.json').is_file():
            raise RuntimeError('Reusable preparation manifest week.json is missing')
        return {'path': str(root), 'scope': result['scope']}

    check('frozen_preparation', preparation)

    def hermes():
        tree = repo / 'vendor/hermes-isolated'
        pin = next((line.split('=', 1)[1].strip() for line in
                   (repo / 'vendor/HERMES_PIN').read_text().splitlines()
                   if line.startswith('HERMES_PIN=')), '')
        if not pin:
            raise RuntimeError('vendor/HERMES_PIN has no pinned revision')
        if _git(tree, 'rev-parse', 'HEAD').strip() != pin:
            raise RuntimeError('Hermes checkout does not match vendor/HERMES_PIN')
        if _git(tree, 'diff', 'HEAD') != (repo / 'patches/hermes-isolated.diff').read_text():
            raise RuntimeError('Hermes tree differs from the exact tracked isolation patch')
        if _git(tree, 'ls-files', '--others', '--exclude-standard').strip():
            raise RuntimeError('Hermes tree contains untracked files; inspect before native execution')
        return {'revision': pin}

    check('pinned_hermes', hermes)
    check('client_dependencies', lambda: _runtime_metadata(repo / 'vendor/venv-hermes-isolated/bin/python'))

    def memcal():
        from .provider_versions import verify_memcal_source
        return verify_memcal_source(repo)

    check('memcal_checkout', memcal)
    check('isolated_scratch', _scratch_check)

    def driver():
        from .generation import GenerationError, load_config
        try:
            config, _ = load_config(repo / '.env')
        except GenerationError:
            raise RuntimeError('Driver configuration is missing MODEL or META; restore the authorized configuration securely') from None
        return {'configured_model': config.model, 'model_inventory': 'not requested'}

    check('driver_configuration', driver)

    def existing_state():
        required = ('ledger.sqlite3', 'client-token', 'address.json')
        missing = [name for name in required if not (gateway_state / name).is_file()]
        if missing:
            raise RuntimeError('Restore the original gateway private state; missing: ' + ', '.join(missing)
                               + '. Do not initialize a replacement ledger.')
        return {'files_present': list(required),
                'history': 'Presence alone does not prove the original ledger was recovered'}

    state_present = check('existing_gateway_state', existing_state)
    if check_gateway and state_present:
        if gateway_state.resolve() != STATE.resolve():
            checks.append({'check': 'gateway_health', 'state': 'blocked',
                           'detail': 'Set MUSE_GATEWAY_STATE before starting this process; health must inspect the same state'})
        else:
            def health():
                data = require_live_cost_boundary()  # Authenticated GET /health only.
                return {key: data.get(key) for key in ('service', 'daily_limit_usd',
                        'total_limit_usd', 'accounted_today_usd', 'budget_blocked', 'upstream_auth_blocked')}
            check('gateway_health', health)
    blocked = any(row['state'] == 'blocked' for row in checks)
    return {'state': 'blocked' if blocked else 'prerequisites-pass',
            'provider': 'memcal', 'inference_calls': 0, 'checks': checks,
            'scope': 'Setup only. Verify the recovered ledger and free model inventory before paid controls; '
                     'then run a native canary. This does not prove provider accuracy or authorize spending.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', required=True, type=Path)
    parser.add_argument('--check-gateway', action='store_true', help='Also perform free GET /health against existing state')
    parser.add_argument('--all-providers', action='store_true', help='Include a read-only inventory of seven provider installation paths')
    args = parser.parse_args()
    result = inspect(args.prepared, check_gateway=args.check_gateway)
    if args.all_providers:
        result['provider_inventory'] = provider_inventory()
    print(json.dumps(result, indent=2))
    return int(result['state'] == 'blocked')


if __name__ == '__main__':
    raise SystemExit(main())
