"""Provision."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from pacbench import harness
from pacbench.evidence_store import save
from pacbench.gateway_client import client_settings, require_live_cost_boundary
from pacbench.provider_versions import memcal_source
from pacbench.providers import ACTIVE_PROVIDERS, RETIRED_PROVIDERS, require_not_retired

MEMCAL_SRC = memcal_source(harness.REPO)


# These use profile-local stores without requiring a shared remote namespace.
ISOLATED_PROVIDERS = frozenset(ACTIVE_PROVIDERS) | {'holographic'}


# Active trial selection alone does not qualify retained-store recovery.
_RESUME_PROVIDERS = frozenset(('memcal', 'mem0', 'hindsight', 'supermemory', 'honcho', 'openviking', 'cognee', 'memos'))


_ARM_SHUTDOWNS = {}


def native_provider_name(provider):
    return 'memtensor' if provider=='memos' else provider


@dataclass(frozen=True)
class _NativeService:
    module: str
    plan_port: str | None = None
    spark_when_unconfigured: bool = False
    pass_resume: bool = False


_NATIVE_SERVICES = {
    'openviking': _NativeService('provider_adapters.openviking_local', plan_port='port', spark_when_unconfigured=True),
    'honcho': _NativeService('provider_adapters.honcho_local', plan_port='db_port', spark_when_unconfigured=True, pass_resume=True),
    'reme': _NativeService('provider_adapters.reme_local'),
    'cognee': _NativeService('provider_adapters.cognee_runtime'),
    'memos': _NativeService('provider_adapters.memos_local', pass_resume=True),
    'supermemory': _NativeService('provider_adapters.supermemory_local', plan_port='port', spark_when_unconfigured=True, pass_resume=True),
}


def openrouter_provider_plan(provider: str, root: Path) -> dict:
    """Illustrate native settings using inert placeholders; never contact services.

    These configurations are not readiness or compatibility evidence. Real
    execution separately requires the authorized task transport and reviewed inputs.
    """
    require_not_retired(provider)
    from pacbench.openrouter_route import ENDPOINT, MODEL
    root = Path(root)
    base, key = 'http://127.0.0.1:1/p/provider/v1', '<local-token-placeholder>'
    if provider in _NATIVE_SERVICES:
        service = _NATIVE_SERVICES[provider]
        options = {service.plan_port: 1} if service.plan_port else {}
        native = import_module(service.module).plan_config(root, MODEL, base, key, **options)
    elif provider in _CUSTOM_SETUPS:
        setup = import_module(_CUSTOM_SETUPS[provider])
        native = getattr(setup, f'_{provider}_plan')(root, MODEL, base, key)
    else:
        raise ValueError('unsupported provider for OpenRouter plan')
    return {'provider': provider, 'model': MODEL, 'endpoint': ENDPOINT,
            'native_settings': native, 'paid_calls_enabled': False,
            'status': 'offline configuration only; transport and native compatibility unverified'}


def stop_arm_model_work(root):
    """Keep independent native shutdown and fallback cleanup observations."""
    import sys

    from pacbench.process_cleanup import cleanup_run_processes

    stop = _ARM_SHUTDOWNS.pop(root.resolve(), None)
    prior = sys.exc_info()[1]
    receipt = root/'shutdown-observation.json'
    record = {'native_hook':'absent', 'original_error_type':type(prior).__name__ if prior else None}
    if stop is None and receipt.exists():
        record = json.loads(receipt.read_text())
        record.setdefault('earlier_cleanup', []).append(record['fallback_cleanup'])
    try:
        if stop is not None:
            proof = stop()
            record['native_hook'] = 'returned'
            if isinstance(proof, dict):
                record['native_outcome'] = {key:proof[key] for key in (
                    'state', 'native_shutdown_complete', 'forced_kill', 'returncode') if key in proof}
                if proof.get('pid') is not None:
                    from pacbench.hermes_gateway import _live
                    record['native_outcome']['process_stopped'] = not _live(proof['pid'])
    except Exception as exc:
        record.update(native_hook='failed', native_error_type=type(exc).__name__)
    try:
        cleaned = cleanup_run_processes(root)
        record['fallback_cleanup'] = {'state':'returned', 'processes_signalled':len(cleaned)}
    except Exception as exc:
        record['fallback_cleanup'] = {'state':'failed', 'error_type':type(exc).__name__}
    outcome = record.get('native_outcome', {})
    incomplete = (record['native_hook'] == 'failed' or record['fallback_cleanup']['state'] == 'failed'
                  or outcome.get('native_shutdown_complete') is False or outcome.get('forced_kill') is True
                  or outcome.get('process_stopped') is False or outcome.get('returncode', 0) not in (0, -15)
                  or outcome.get('state') == 'cleanup-unverified')
    save(receipt, record)
    if incomplete and prior is None:
        raise RuntimeError('Run-owned native shutdown incomplete; inspect shutdown-observation.json')
    return record


PROBE = '''
import json, sys, inspect, hashlib
from pathlib import Path
from plugins.memory import load_memory_provider
p = load_memory_provider(sys.argv[1], register_skills=False)
if p is None:
    result = {"available": False, "reason": "native provider failed to load", "tools": []}
else:
    available = p.is_available()
    result = {"available": available,
              "reason": "" if available else (p.unavailable_reason() or "native setup incomplete"),
              "tools": [s.get("name", s.get("function", {}).get("name"))
                        for s in p.get_tool_schemas()]}
    location = inspect.getsourcefile(type(p))
    result['loaded_native_provider'] = p.name
    result['loaded_provider_module'] = location
    result['loaded_source_sha256'] = hashlib.sha256(Path(location).read_bytes()).hexdigest() if location else None
print(json.dumps(result))
'''


READ_ONLY_SMOKE = '''
import json, sys
from hermes_constants import get_hermes_home
from plugins.memory import load_memory_provider
p = load_memory_provider(sys.argv[1], register_skills=False)
p.initialize("pacbench-read-only-smoke", hermes_home=str(get_hermes_home()), platform="cli")
# A native async initializer must finish before the capability smoke dispatch.
# Same bounded readiness check for any provider exposing an initialization thread.
thread = getattr(p, "_init_thread", None)
if thread is not None:
    thread.join(timeout=30)
try:
    if p.name == "memtensor":
        tool, args = "memos_skill_list", {}
        output = p.handle_tool_call(tool,args)
    elif p.name == "cognee":
        tool, args = None, {}
        output = json.dumps({"success": p._is_usable(),
            "mode": "native read-only dataset inventory",
            "datasets": p._backend.list_datasets(timeout=10)})
    elif p.name == "reme":
        tool, args = None, {}
        # Native initialize completed the HTTP health check. This provider
        # intentionally offers no model tools; do not invent a smoke tool.
        output = json.dumps({"success": p._backend_available is True,
                             "mode": "native health; automatic-prefetch-only"})
    else:
        tool = {"holographic": "fact_store", "memcal": "memcal_list_days", "mem0": "mem0_search", "hindsight":"hindsight_recall", "openviking":"viking_search", "supermemory":"supermemory-profile", "honcho":"honcho_profile"}[p.name]
        args = ({"action": "list"} if p.name == "holographic" else
                {"query": "native read-only capability check"} if p.name in {"mem0","hindsight","openviking"} else {})
        output = p.handle_tool_call(tool, args)
    print(json.dumps({"tool": tool, "arguments": args, "output": output}))
finally:
    p.shutdown()
'''


def catalog_migrated_providers() -> set[str]:
    """Read Hermes' own migration inventory and the pinned checkout's catalog."""
    migration = harness.ISOLATED_TREE / "hermes_cli" / "memory_provider_migration.py"
    if not migration.exists():
        return set()  # Older bundled-provider Hermes releases.
    tree = ast.parse(migration.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_LEFT_CORE" for t in node.targets):
            value = node.value
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "frozenset" and len(value.args) == 1):
                raise RuntimeError("Unrecognized Hermes provider migration inventory")
            names = ast.literal_eval(value.args[0])
            if not isinstance(names, (set, frozenset)) or not all(isinstance(n, str) for n in names):
                raise RuntimeError("Invalid Hermes provider migration inventory")
            catalog = harness.ISOLATED_TREE / "plugin-catalog"
            return {n for n in names if (catalog / f"{n}.yaml").is_file()}
    raise RuntimeError("Hermes provider migration inventory is missing")


def provider_names() -> list[str]:
    """Offline selection catalog; presence does not establish provider readiness."""
    root=harness.ISOLATED_TREE/'plugins/memory'
    names={p.name for p in root.iterdir() if p.is_dir() and (p/'plugin.yaml').is_file()} if root.is_dir() else set()
    return sorted((names | catalog_migrated_providers() | ISOLATED_PROVIDERS | {'memori'})-RETIRED_PROVIDERS.keys())


def require_trial_provider(provider: str, *, resume: bool = False) -> None:
    """Refuse moved providers without a reviewed provisioning route."""
    from pacbench.catalog_plugins import CATALOG
    bundled = harness.ISOLATED_TREE / "plugins" / "memory" / provider
    if provider in catalog_migrated_providers() and not (bundled / "plugin.yaml").is_file():
        if provider not in CATALOG:
            raise RuntimeError(f"No reviewed trial-home catalog provisioning route for {provider!r}")
    if resume and provider not in _RESUME_PROVIDERS:
        raise ValueError('Provider lacks a verified retained-store resume protocol')


def uses_catalog_plugin(provider: str) -> bool:
    from pacbench.catalog_plugins import CATALOG
    bundled = harness.ISOLATED_TREE / 'plugins' / 'memory' / provider / 'plugin.yaml'
    return provider in CATALOG and not bundled.is_file()


def verify_retained_trial_home(provider: str, home: Path) -> None:
    require_trial_provider(provider, resume=True)
    if not (home / 'config.yaml').is_file():
        raise ValueError('Retained Hermes config.yaml is missing')
    if uses_catalog_plugin(provider):
        from pacbench.catalog_plugins import verify_retained_catalog_provider
        verify_retained_catalog_provider(provider, home)


def provision(root: Path, provider: str, model: str = "unconfigured", *,
              local_timezone: str = "", resume: bool = False,
              honcho_simulated_at: str | None = None, honcho_checkpoint: dict | None = None,
              inference_route: str = 'muse-gateway') -> dict:
    require_not_retired(provider)
    if inference_route not in ('muse-gateway', 'codex-subscription', 'openrouter'):
        raise ValueError('Unknown inference route')
    home = root / 'hermes-home'
    if resume:
        verify_retained_trial_home(provider, home)
    if inference_route == 'openrouter':
        if provider not in ACTIVE_PROVIDERS:
            raise ValueError('Unsupported OpenRouter memory provider')
        from pacbench.openrouter_route import MODEL
        if model != MODEL or os.environ.get('PACBENCH_TRANSPORT') != 'openrouter':
            raise ValueError('OpenRouter pilot requires its exact model and run-owned transport')
        require_live_cost_boundary()
    from pacbench.providers import require_live_trials_ready
    require_live_trials_ready()
    if not resume:
        require_trial_provider(provider)
    if provider == 'memcal':
        from pacbench.provider_versions import verify_memcal_source

        try:
            verify_memcal_source(harness.REPO)
        except subprocess.CalledProcessError as error:
            raise RuntimeError('Cannot verify the pinned MemCal checkout') from error
    root_hash = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    run_id = root_hash[:12]
    driver_project = f"pacbench-{run_id}-{provider}-driver"
    memory_project = f"pacbench-{run_id}-{provider}-memory"
    driver_url, client_key = client_settings(driver_project)
    memory_url, memory_key = client_settings(memory_project)
    from pacbench.catalog_plugins import install_catalog_provider
    external = uses_catalog_plugin(provider)
    if not resume:harness.provision_home(
        home, provider_plugin_src=(MEMCAL_SRC / "integrations/hermes/memcal"
                                   if provider == "memcal" else None),
        provider_name="" if external else native_provider_name(provider), model=('gpt-6-luna' if inference_route == 'codex-subscription' else model),
        model_provider=('openai-codex' if inference_route == 'codex-subscription' else
                        'openrouter' if inference_route == 'openrouter' else 'meta-ai'),
        model_base_url=(None if inference_route == 'codex-subscription' else driver_url))
    # Native services and in-process tools use their persistent memory capability.
    # Hermes home above retains the separately selected answer model.
    if inference_route == 'openrouter':
        from pacbench.openrouter_route import MODEL
        model = MODEL
    # Isolate HOME fallbacks; only the selected provider supplies memory.
    fallback = root / "fallback-home"
    fallback.mkdir(exist_ok=resume)
    if external and not resume:
        receipt = install_catalog_provider(provider, home, fallback, harness.ISOLATED_BIN, harness.ISOLATED_TREE)
        save(root / "catalog-plugin.json", receipt)
        # A memory provider is activated by memory.provider, not plugins.enabled.
        # Do this only after the exact source and dependencies pass preflight.
        from ruamel.yaml import YAML
        config = YAML(typ="safe").load((home / "config.yaml").read_text())
        config["memory"]["provider"] = provider
        config.setdefault("security", {})["allow_lazy_installs"] = False
        harness._write_yaml(home / "config.yaml", config)
    env = {"HOME": str(fallback.resolve()), "PACBENCH_NO_BUILTIN_MEMORY": "1", "PACBENCH_PROVIDER_ONLY": "1",
           "MEMCAL_HOME": str((root / "memcal-home").resolve()),
           "MEMCAL_SRC": str(MEMCAL_SRC),
           "PACBENCH_USAGE_ROLES": json.dumps({driver_project:"hermes",memory_project:"memory_provider"}), "HERMES_ENABLE_PROJECT_PLUGINS": "0"}
    if provider=='memcal':
        from pacbench.source_policy import PREFETCH_PROTOCOL as PREFETCH_PROTOCOL
        env['PACBENCH_PREFETCH_PROVENANCE']=json.dumps({'protocol':PREFETCH_PROTOCOL,
            'provider':'memcal','state':'unverified',
            'reason':'Actual native brief and query-driven wiki injection retained; no typed per-block source-free witness.'})
    if inference_route == 'codex-subscription':
        env['PACBENCH_INFERENCE_ROUTE'] = inference_route
        # Only provider-internal Spark calls belong to the gateway project.
        env['PACBENCH_USAGE_ROLES'] = json.dumps({memory_project: 'memory_provider'})
    if local_timezone:
        from zoneinfo import ZoneInfo
        ZoneInfo(local_timezone)  # Reject invalid simulation zones before any live calls.
        env["TZ"] = local_timezone
    env.update({"META": "", "META_API_KEY": client_key, "MODEL_API_KEY": client_key,
                "META_MODEL_API_KEY": client_key, "META_BASE_URL": driver_url,
                "OPENAI_API_KEY": memory_key, "OPENROUTER_API_KEY": "", "OPENROUTER_API_BASE": "", "MEM0_API_KEY": "",
                "MEM0_MODE": "oss", "MEM0_HOST": "", "MEM0_TELEMETRY": "false",
                "MEM0_DIR": str((root / "mem0-state").resolve())})
    if inference_route == 'openrouter':
        env['OPENROUTER_API_KEY'] = client_key  # Local run token, never the upstream key.
        env['OPENROUTER_API_BASE'] = driver_url
        env['CUSTOM_BASE_URL'] = ''
        env['OPENROUTER_BASE_URL'] = driver_url
    arm = _NativeArm(root, root_hash, model, env, memory_url, memory_key, client_key,
                     inference_route, resume)
    if provider in _CUSTOM_SETUPS:
        setup = import_module(_CUSTOM_SETUPS[provider])
        stop = getattr(setup, f'_provision_{provider}')(arm)
    elif provider in _NATIVE_SERVICES:
        stop = _start_native_service(arm, provider, honcho_simulated_at, honcho_checkpoint)
    else:
        stop = None
    if stop is not None:
        _ARM_SHUTDOWNS[root.resolve()] = stop
    return env


@dataclass(frozen=True)
class _NativeArm:
    root: Path
    root_hash: str
    model: str
    env: dict
    memory_url: str
    memory_key: str
    driver_key: str
    inference_route: str
    resume: bool

    @property
    def home(self):
        return self.root / 'hermes-home'


def _start_native_service(arm, provider, simulated_at, checkpoint):
    service = _NATIVE_SERVICES[provider]
    start = import_module(service.module).start
    options = {'resume': arm.resume} if service.pass_resume else {}
    if provider == 'honcho':
        from provider_adapters.honcho_enrollment import ENVIRONMENT, PROTOCOL

        arm.env[ENVIRONMENT] = PROTOCOL
        if simulated_at:
            options['simulated_at'] = simulated_at
        if checkpoint:
            options['checkpoint'] = checkpoint
    model = ('muse-spark-1.3-contributor'
             if service.spark_when_unconfigured and arm.model == 'unconfigured' else arm.model)
    return start(arm.root.resolve(), arm.env, model, arm.memory_url, arm.memory_key, **options)


_CUSTOM_SETUPS = {
    'mem0': 'pacbench.providers.mem0',
    'memcal': 'pacbench.providers.memcal.workflow',
    'hindsight': 'pacbench.providers.hindsight',
}


def probe(root: Path, provider: str, env: dict, *, smoke: bool = False, smoke_timeout_s: int = 30) -> dict:
    require_not_retired(provider)
    child_env = harness.turn_env("2026-09-11T09:00:00-04:00",
                                 root / "hermes-home", "", env)
    if provider == 'honcho' and (root/'provider-clock.json').exists():
        from provider_adapters.honcho_clock import load
        clock, _ = load(root)
        child_env['PACBENCH_NOW'] = clock.now().isoformat()
        child_env = clock.python_environment(child_env)
        child_env['PACBENCH_PYTHON_CLOCK_ROLE']='probe'
    try:
        proc = subprocess.run(
            [str(harness.ISOLATED_BIN.parent / "python"), "-c", PROBE, native_provider_name(provider)],
            cwd=harness.ISOLATED_TREE, env=child_env, text=True,
            capture_output=True, timeout=60)
        result = json.loads(proc.stdout.splitlines()[-1])
        if proc.returncode:
            raise ValueError("probe process failed")
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        result = {"available": False, "reason": f"native probe failed: {type(exc).__name__}",
                  "tools": []}
    result["provider"] = provider
    result["live_verified"] = False
    if provider not in ISOLATED_PROVIDERS:
        result["execution_blocker"] = "run-owned backend/remote namespace not configured"
    if smoke and result["available"] and provider in ISOLATED_PROVIDERS:
        try:
            proc = subprocess.run(
                [str(harness.ISOLATED_BIN.parent / "python"), "-c", READ_ONLY_SMOKE, native_provider_name(provider)],
                cwd=harness.ISOLATED_TREE, env=child_env, text=True,
                capture_output=True, timeout=smoke_timeout_s)
            if proc.returncode:
                raise ValueError("native smoke failed")
            result["read_only_smoke"] = json.loads(proc.stdout.splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            result["read_only_smoke"] = {"error": type(exc).__name__}
    return result


def smoke_succeeded(capability: dict) -> bool:
    try:
        output = json.loads(capability["read_only_smoke"]["output"])
    except (KeyError, ValueError, TypeError):
        return False
    return isinstance(output, dict) and "error" not in output and output.get("success") is not False
