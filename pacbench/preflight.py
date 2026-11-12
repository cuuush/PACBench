"""Inspect Memcal trial prerequisites without inference or store initialization."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import harness
from .context_isolation import outside_project
from .gateway_client import STATE, require_live_cost_boundary
from .preparation_audit import audit_prepared


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
        root = repo.parent / 'memcal'
        if not (root / 'integrations/hermes/memcal/__init__.py').is_file():
            raise RuntimeError('Sibling Memcal checkout with its shipped Hermes integration is missing')
        return {'revision': _git(root, 'rev-parse', 'HEAD').strip(),
                'working_tree_changes': bool(_git(root, 'status', '--porcelain').strip())}

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
    args = parser.parse_args()
    result = inspect(args.prepared, check_gateway=args.check_gateway)
    print(json.dumps(result, indent=2))
    return int(result['state'] == 'blocked')


if __name__ == '__main__':
    raise SystemExit(main())
