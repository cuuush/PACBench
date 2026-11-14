"""Bounded document ingestion followed by ordinary native Hermes recall."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import harness
from .creds import load_driver
from .frozen import load_bundle, messages_for
from .model_calls import ModelClient
from .semantic_contract import load_contract
from .semantic_grade import Claim, grade_native_semantic
from .followups import save
from .gateway_client import client_settings, require_live_cost_boundary, ModelAccessBlocked, SpendingBlocked
from .followups import freeze_policy, run_followups
from .evaluation import load_evaluations
from .native_checks import run_checks
from .tool_policy import VERSION as TOOL_POLICY, policy_record
from .usage import tracked_turn
from .stop import OperatorStop, check_stop
from .provider_lineup import RETIRED_PROVIDERS, require_not_retired

from .provider_versions import memcal_source
MEMCAL_SRC = memcal_source(harness.REPO)
# These use profile-local stores without requiring a shared remote namespace.
ISOLATED_PROVIDERS = {"holographic", "memcal", "mem0", "hindsight", "openviking", "supermemory", "honcho", "reme", "cognee", "memos"}
_ARM_SHUTDOWNS = {}


def native_provider_name(provider):
    return 'memtensor' if provider=='memos' else provider


def memcal_model_settings(model: str, base: str, key: str) -> dict:
    """Native store-scoped model settings; pure and usable in offline plans."""
    return {'MEMCAL_LLM_PROVIDER': 'openai-compatible', 'MEMCAL_OPENAI_BASE_URL': base,
            'OPENAI_COMPAT_API_KEY': key, 'MEMCAL_PROPOSE_MODEL': model,
            'MEMCAL_SWEEP_MODEL': model, 'MEMCAL_MATCH_MODEL': model}


def hindsight_model_settings(run_id: str, model: str, base: str, key: str) -> tuple[dict, dict]:
    """Use Hindsight's native OpenAI transport for DeepSeek, Meta for Spark."""
    from .role_routes import memory_model
    OPENROUTER_MODEL=memory_model()
    provider = 'openai' if model == OPENROUTER_MODEL else 'meta'
    config = {'mode': 'local_external', 'profile': 'pacbench-' + run_id,
              'bank_id': 'pacbench-' + run_id, 'llm_provider': provider,
              'llm_model': model, 'llm_base_url': base, 'llm_api_key': key}
    env = {'HINDSIGHT_LLM_API_KEY': key, 'HINDSIGHT_API_LLM_API_KEY': key,
           'HINDSIGHT_API_LLM_BASE_URL': base, 'HINDSIGHT_API_LLM_MODEL': model,
           'HINDSIGHT_API_LLM_PROVIDER': provider, 'HINDSIGHT_API_ENABLE_API_KEY_AUTH': 'false'}
    if model == OPENROUTER_MODEL:
        env['HINDSIGHT_API_LLM_TIMEOUT']='300'
    return config, env


def mem0_model_settings(model: str, base: str, *, openrouter: bool = False) -> dict:
    """Fields applied to the native Mem0 SDK config after its setup builder."""
    settings = {'openai_base_url': base}
    if openrouter:
        # Native Mem0 gives this setting precedence over openai_base_url.
        settings['openrouter_base_url'] = base
    from .role_routes import memory_model
    OPENROUTER_MODEL=memory_model()
    if model in {'muse-spark-1.3-contributor', OPENROUTER_MODEL}:
        settings['is_reasoning_model'] = True
    return settings


def openrouter_provider_plan(provider: str, root: Path) -> dict:
    """Illustrate native settings using inert placeholders; never contact services.

    These configurations are not readiness or compatibility evidence. Real
    execution separately requires the authorized task transport and reviewed inputs.
    """
    require_not_retired(provider)
    from .openrouter_route import MODEL, ENDPOINT
    from provider_adapters import (honcho_local, openviking_local,
                                   supermemory_local)
    root = Path(root)
    base, key = 'http://127.0.0.1:1/p/provider/v1', '<local-token-placeholder>'
    if provider == 'memcal':
        native = memcal_model_settings(MODEL, base, key)
    elif provider == 'mem0':
        native = {'model': MODEL, **mem0_model_settings(MODEL, base, openrouter=True)}
    elif provider == 'hindsight':
        config, env = hindsight_model_settings('plan', MODEL, base, key)
        native = {'config': config, 'environment': env}
    elif provider == 'openviking':
        native = openviking_local.plan_config(root, MODEL, base, key, port=1)
    elif provider == 'supermemory':
        native = supermemory_local.plan_config(root, MODEL, base, key, port=1)
    elif provider == 'honcho':
        native = honcho_local.plan_config(root, MODEL, base, key, db_port=1)
    else:
        raise ValueError('unsupported provider for OpenRouter plan')
    return {'provider': provider, 'model': MODEL, 'endpoint': ENDPOINT,
            'native_settings': native, 'paid_calls_enabled': False,
            'status': 'offline configuration only; transport and native compatibility unverified'}


def next_checkpoint(rows, at):
    """Order instants, including the repeated hour when Eastern DST ends."""
    later = [r['at'] for r in rows if datetime.fromisoformat(r['at']) > datetime.fromisoformat(at)]
    return min(later, key=datetime.fromisoformat) if later else None


def copy_prepared_files(source, output):
    """Copy frozen run inputs together with their exact shared evidence store."""
    for name in ('bundle.json','contract.json','resolved-spec.json','shared-text.json'):
        if name=='shared-text.json' and not (source/name).exists():continue
        shutil.copyfile(source/name,output/name)


def ingest_pending(needed, ingested, failed_batches, output, root, provider, env,
                   timezone, stop_file, ingest_day, check_model_health):
    """Persist each native receipt before honoring an operator stop."""
    for batch in needed:
        if batch['id'] in ingested:
            continue
        check_stop(stop_file)
        if batch['id'] in failed_batches:
            raise RuntimeError('Earlier native import failed; source state unavailable')
        record = ingest_day(provider, output/'sources'/batch['id'], root, env, batch['at'], timezone)
        save(root/batch['file'], record)
        if record['state'] == 'ready':
            ingested.append(batch['id'])
        else:
            failed_batches.add(batch['id'])
        error=(record.get('result') or {}).get('native_error') or record.get('native_error') or {}
        if error.get('authentication_blocked') is True or error.get('http_status') in (401,403):
            raise ModelAccessBlocked('Native provider authentication blocked; live run stopped')
        check_stop(stop_file)
        if record['state'] != 'ready':
            check_model_health()
            raise RuntimeError('Native source ingestion/processing failed; inspect saved record')
        check_model_health()


def stop_arm_model_work(root):
    """Use the native run-owned shutdown hook when a cost barrier trips."""
    stop = _ARM_SHUTDOWNS.pop(root.resolve(), None)
    try:
        if stop is not None:
            stop()
    finally:
        from .process_cleanup import cleanup_run_processes
        cleanup_run_processes(root)

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
    return sorted((names | catalog_migrated_providers() | ISOLATED_PROVIDERS | {'memori','memcal'})-RETIRED_PROVIDERS.keys())


def require_trial_provider(provider: str, *, resume: bool = False) -> None:
    """Refuse moved providers without a reviewed provisioning route."""
    from .catalog_plugins import CATALOG
    bundled = harness.ISOLATED_TREE / "plugins" / "memory" / provider
    if provider in catalog_migrated_providers() and not (bundled / "plugin.yaml").is_file():
        if provider not in CATALOG:
            raise RuntimeError(f"No reviewed trial-home catalog provisioning route for {provider!r}")
        if resume and provider not in {'supermemory','openviking','hindsight','mem0'}:
            raise RuntimeError("Catalog provider resume requires a verified installed-plugin recovery protocol")


def provision(root: Path, provider: str, model: str = "unconfigured", *,
              local_timezone: str = "", resume: bool = False,
              honcho_simulated_at: str | None = None,
              inference_route: str = 'muse-gateway') -> dict:
    require_not_retired(provider)
    if provider == 'memcal':
        from .provider_versions import verify_memcal_source
        verify_memcal_source(harness.REPO)
    if inference_route not in ('muse-gateway', 'codex-subscription', 'openrouter'):
        raise ValueError('Unknown inference route')
    if inference_route == 'openrouter':
        if provider not in {'memcal','mem0','hindsight','supermemory','honcho','openviking','reme','cognee','memos'}:
            raise ValueError('Unsupported OpenRouter memory provider')
        from .openrouter_route import MODEL
        if model != MODEL or os.environ.get('PACBENCH_TRANSPORT') != 'openrouter':
            raise ValueError('OpenRouter pilot requires its exact model and run-owned transport')
        require_live_cost_boundary()
    from .live_trials import require_live_trials_ready
    require_live_trials_ready()
    require_trial_provider(provider, resume=resume)
    home = root / "hermes-home"
    run_id = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    driver_project = f"pacbench-{run_id}-{provider}-driver"
    memory_project = f"pacbench-{run_id}-{provider}-memory"
    driver_url, client_key = client_settings(driver_project)
    _, memory_key = client_settings(memory_project)
    if resume and (provider not in {'openviking','memos','supermemory','hindsight','mem0','cognee','memcal'} or not (home/'config.yaml').exists()):
        raise ValueError('Provider lacks a verified retained-store resume protocol')
    from .catalog_plugins import CATALOG, install_catalog_provider
    external = provider in CATALOG and not (harness.ISOLATED_TREE / "plugins" / "memory" / provider / "plugin.yaml").is_file()
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
        from .role_routes import memory_model, record as role_record
        model=memory_model()
        if role_record():save(root/'role-routes.json',role_record())
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
    if external and resume:
        from .catalog_plugins import verify_retained_catalog_provider
        verify_retained_catalog_provider(provider,home)
    env = {"HOME": str(fallback.resolve()), "PACBENCH_NO_BUILTIN_MEMORY": "1", "PACBENCH_PROVIDER_ONLY": "1",
           "MEMCAL_HOME": str((root / "memcal-home").resolve()),
           "MEMCAL_SRC": str(MEMCAL_SRC),
           "PACBENCH_USAGE_ROLES": json.dumps({driver_project:"hermes",memory_project:"memory_provider"}), "HERMES_ENABLE_PROJECT_PLUGINS": "0"}
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
    from .role_routes import split_enabled
    if inference_route=='openrouter' and split_enabled():
        if provider=='hindsight':env['HINDSIGHT_API_LLM_MAX_CONCURRENT']='1'
        if provider=='memcal':env['MEMCAL_MAX_PARALLEL']='1'
    if provider == 'mem0' and resume:
        config=json.loads((home/'mem0.json').read_text())
        expected='pacbench-'+hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
        if (config.get('mode')!='oss' or config.get('user_id')!=expected or
            config['oss']['llm']['config']['model']!=model or config['oss'].get('graph_store') or
            config['oss']['llm']['config']['api_key']!=memory_key):raise ValueError('Retained Mem0 native configuration differs')
        from .gateway_client import validate_route
        validate_route(config['oss']['llm']['config']['openai_base_url'],memory_key)
    if provider == "mem0" and not resume:
        identity = "pacbench-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
        extraction_url, _ = client_settings(memory_project)
        configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
        # Invoke the documented agent-driven OSS setup, not a custom SDK shim.
        command = [str(harness.ISOLATED_BIN), "memory", "setup", "mem0", "--mode", "oss",
                   "--oss-llm", "openai", "--oss-llm-model", configured_model,
                   "--oss-llm-url", extraction_url, "--oss-embedder", "ollama",
                   "--oss-embedder-model", "nomic-embed-text", "--oss-embedder-url", "http://127.0.0.1:11435",
                   "--oss-vector", "qdrant", "--oss-vector-path", str((root / "qdrant").resolve()),
                   "--user-id", identity]
        proc = subprocess.run(command, env={**os.environ, **env, "HERMES_HOME": str(home.resolve())},
                              capture_output=True, text=True, timeout=90)
        save(root / "native-setup.json", {"command": command, "returncode": proc.returncode,
                                         "stdout": proc.stdout, "stderr": proc.stderr})
        if proc.returncode or not (home / "mem0.json").exists():
            # The pinned top-level argparse rejects flags documented by its plugin.
            # Use the README's manual mem0.json path and the plugin's own config builder.
            builder = '''
import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[4])
from pacbench.provider_loader import provider_module
build_oss_config = provider_module("mem0", "_setup").build_oss_config
Mem0MemoryProvider = provider_module("mem0").Mem0MemoryProvider
flags=json.loads(sys.argv[1])
oss,_=build_oss_config(flags)
Mem0MemoryProvider().save_config({"mode":"oss","user_id":sys.argv[2],"oss":oss},sys.argv[3])
'''
            flags = {"oss_llm": "openai", "oss_llm_model": configured_model,
                     "oss_llm_url": extraction_url, "oss_embedder": "ollama",
                     "oss_embedder_model": "nomic-embed-text", "oss_embedder_url": "http://127.0.0.1:11435",
                     "oss_vector": "qdrant", "oss_vector_path": str((root / "qdrant").resolve())}
            proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c", builder,
                                   json.dumps(flags), identity, str(home.resolve()), str(harness.REPO)],
                                  env={**os.environ, **env, "HERMES_HOME": str(home.resolve())},
                                  cwd=harness.ISOLATED_TREE, capture_output=True, text=True, timeout=30)
            save(root / "native-manual-setup.json", {"route": "README manual OSS mem0.json",
                 "flags": flags, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            if proc.returncode or not (home / "mem0.json").exists():
                raise RuntimeError("documented manual Mem0 setup failed; see native-manual-setup.json")
        config = json.loads((home / "mem0.json").read_text())
        # README recommends 6000 characters for 8k-context embedders.
        from .gateway_client import validate_route
        actual_url = config["oss"]["llm"]["config"]["openai_base_url"]
        validate_route(actual_url, client_key)
        if actual_url != extraction_url:
            raise RuntimeError("Mem0 model endpoint differs from the isolated gateway project")
        config['oss']['llm']['config'].update(mem0_model_settings(
            configured_model, extraction_url, openrouter=inference_route == 'openrouter'))
        config['oss']['llm']['config']['api_key'] = memory_key
        config["sync_max_chars"] = 6000
        # The README's manual setup documents this SDK flag for reasoning
        # models. Its name heuristic knows GPT/o-series, but not Spark/DeepSeek. Let
        # the native SDK choose its reasoning-model request parameters rather
        # than treating Spark as a legacy chat model with a 2,000-token cap.
        if config['oss']['llm']['config'].get('is_reasoning_model') is True:
            save(root/'model-compatibility.json',{
                'model':configured_model,'is_reasoning_model':True,
                'route':'Hermes Mem0 README manual OSS configuration; native SDK explicit model classification',
                'output_budget_override':False,'reasoning_effort_override':False})
        # Leave output budgets and reasoning effort to the native SDK.
        save(home / "mem0.json", config)
        os.chmod(home / "mem0.json", 0o600)
        (home / ".env").write_text("OPENAI_API_KEY=" + memory_key + "\n")
        os.chmod(home / ".env", 0o600)
    if provider == "memcal":
        memcal_url, _ = client_settings(memory_project)
        configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
        env.update(memcal_model_settings(configured_model, memcal_url, memory_key))
        # Recommended store-scoped configuration overrides project .env fallbacks.
        memcal_home = root / "memcal-home"
        memcal_home.mkdir(parents=True, exist_ok=True)
        routed = {key:value for key,value in env.items() if key.startswith('MEMCAL_') and key not in {'MEMCAL_HOME','MEMCAL_SRC'}}
        routed['OPENAI_COMPAT_API_KEY'] = memory_key
        if resume:
            retained=dict(line.split('=',1) for line in (memcal_home/'.env').read_text().splitlines() if '=' in line)
            if retained!=routed:raise ValueError('Retained Memcal model configuration differs')
        else:(memcal_home / '.env').write_text(''.join(f'{key}={value}\n' for key,value in routed.items()))
        os.chmod(memcal_home / '.env', 0o600)
        # Native first-install bootstrap, with no facts seeded into the store.
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from memcal import config, db, brief; "
            "import os; cfg=config.load(); "
            "assert cfg.llm_provider == 'openai-compatible' and cfg.openai_base_url == os.environ['MEMCAL_OPENAI_BASE_URL'] and cfg.openai_api_key == os.environ['OPENAI_COMPAT_API_KEY'], 'Memcal model routing must use the shared gateway'; cfg.ensure_dirs(); "
            "conn=db.open_db(cfg.db_path); brief.write(conn,cfg); conn.close()")
        if not resume:
            proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c",
                                   bootstrap, str(MEMCAL_SRC)],
                                  env={**os.environ, **env}, capture_output=True, timeout=30)
            if proc.returncode:
                raise RuntimeError("Memcal native scratch-store bootstrap failed")
    if provider == 'hindsight':
        env['PACBENCH_HINDSIGHT_TRACE_DIR']=str((root/'private/hindsight-native-traces').resolve())
        save(root/'native-trace-observer.json',{'version':'hindsight-native-reflection-traces-v3',
            'route':'Read-only native /llm-requests API; observe the default trace writer',
            'native_writer_flush_s':0.5,'observation_timeout_s':3,'processing_forced':False,
            'schema_tools':'Recognize native 1.2 declarations, request schemas, outputs and ordered result replay',
            'private_raw_snapshots':True,
            'scope':'Reflection tools and original source chunks seen by the native model. Gateway remains authoritative for costs; trace tokens are separate native telemetry.'})
        # README local_external setup with a run-owned self-hosted native server.
        # Preserve failed embedded trials: the pinned plugin repeatedly restarts
        # the newer SDK daemon when it adds native profile environment fields.
        from .gateway_client import validate_route
        backend_python = harness.REPO / 'vendor/venv-hindsight/bin/python'
        if not backend_python.exists():
            raise RuntimeError('Install the isolated Hindsight server runtime in vendor/venv-hindsight; see vendor/README')
        from provider_adapters.tokenizer_cache import configure as configure_tokenizers
        configure_tokenizers(root,env)
        base,key=client_settings(memory_project)
        validate_route(base,key)
        configured_model=model if model!='unconfigured' else 'muse-spark-1.3-contributor'
        folder=home/'hindsight'
        folder.mkdir(exist_ok=resume)
        hindsight_config, hindsight_env = hindsight_model_settings(run_id, configured_model, base, key)
        if resume:
            retained=json.loads((folder/'config.json').read_text())
            if any(retained.get(k)!=v for k,v in hindsight_config.items()):raise ValueError('Retained Hindsight profile/model differs')
        else:save(folder/'config.json', hindsight_config)
        os.chmod(folder/'config.json',0o600)
        env.update(hindsight_env)
        # Ordinary provisioning creates an empty bank, not starter memories or a
        # benchmark mission. Recall of a nonexistent bank is otherwise a 404.
        bootstrap='''
import json,sys
from importlib.metadata import version
from pathlib import Path
from hindsight import HindsightEmbedded
cfg=json.loads((Path(sys.argv[1])/'hindsight/config.json').read_text())
p=HindsightEmbedded(profile=cfg['profile'],llm_provider=cfg['llm_provider'],llm_model=cfg['llm_model'],
    llm_api_key=cfg['llm_api_key'],llm_base_url=cfg['llm_base_url'])
try:
    if sys.argv[3]=='resume':
        import urllib.request
        p._ensure_started()
        with urllib.request.urlopen(p._manager.get_url(cfg['profile'])+'/v1/default/banks',timeout=10) as response:
            result=json.load(response)
        assert sys.argv[2] in json.dumps(result), 'Retained Hindsight bank missing'
    else:result=p.create_bank(bank_id=sys.argv[2]).model_dump(mode='json')
    print(json.dumps({'endpoint':p._manager.get_url(cfg['profile']),'bank':result,
        'runtime_versions':{name:version(name) for name in ('hindsight-api-slim','hindsight-client','hindsight-embed')}}))
finally:p.close()
'''
        proc=subprocess.run([str(backend_python),'-c',bootstrap,
                             str(home.resolve()),'pacbench-'+run_id,'resume' if resume else 'fresh'],
                            env={**os.environ,**env,'HERMES_HOME':str(home.resolve())},
                            cwd=harness.ISOLATED_TREE,capture_output=True,text=True,timeout=120)
        save(root/'native-bank-setup.json',{'route':'Self-hosted Hindsight native server; create_bank defaults; no starter facts',
             'returncode':proc.returncode,'stdout':proc.stdout.replace(key,'<gateway-token>'),
             'stderr':proc.stderr.replace(key,'<gateway-token>')})
        if proc.returncode:raise RuntimeError('Hindsight empty-bank setup failed; inspect native-bank-setup.json')
        endpoint=json.loads(proc.stdout.splitlines()[-1])['endpoint']
        cfg=json.loads((folder/'config.json').read_text())
        cfg['api_url']=endpoint
        save(folder/'config.json',cfg)
        env.update(HINDSIGHT_MODE='local_external',HINDSIGHT_API_URL=endpoint,
                   HINDSIGHT_BANK_ID='pacbench-'+run_id,HINDSIGHT_API_KEY='')
        import atexit
        stop_script="from hindsight_embed import get_embed_manager; get_embed_manager().stop(__import__('sys').argv[1])"
        # Shutdown only this run-owned service, through the native manager.
        stop = lambda:subprocess.run([str(backend_python),'-c',stop_script,'pacbench-'+run_id],
            env={**os.environ,**env},cwd=harness.ISOLATED_TREE,capture_output=True,timeout=30)
        atexit.register(stop)
        _ARM_SHUTDOWNS[root.resolve()] = stop
    if provider=='openviking':
        from provider_adapters.openviking_local import start
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key)
    if provider=='honcho':
        from provider_adapters.honcho_local import start
        from provider_adapters.honcho_enrollment import ENVIRONMENT, PROTOCOL
        env[ENVIRONMENT] = PROTOCOL
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key,
            **({'simulated_at':honcho_simulated_at} if honcho_simulated_at else {}))
    if provider=='reme':
        from provider_adapters.reme_local import start
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model,base,key)
    if provider=='cognee':
        from provider_adapters.cognee_runtime import start
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model,base,key)
    if provider=='memos':
        from provider_adapters.memos_local import start
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model,base,key,resume=resume)
    if provider=='supermemory':
        from provider_adapters.supermemory_local import start
        base,key=client_settings(memory_project)
        _ARM_SHUTDOWNS[root.resolve()] = start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key,resume=resume)
    return env


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


def read_prepared(source: Path, max_messages: int) -> tuple[list[dict], object, dict]:
    summary = json.loads((source / "summary.json").read_text())
    bundle = load_bundle(source / "bundle.json", summary["bundle_hash"])
    if len(bundle["lives"]) != 1:
        raise ValueError("native pilot requires one frozen life")
    messages = messages_for(bundle, next(iter(bundle["lives"])))
    if not messages or len(messages) > max_messages:
        raise ValueError(f"message budget exceeded or empty: {len(messages)} / {max_messages}")
    contract = load_contract(source / "contract.json")
    evidence_hash = hashlib.sha256(json.dumps(messages, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    if (not (source / "evaluation-plan.json").exists() and evidence_hash != contract.evidence_hash or contract.contract_hash != summary["contract_hash"]):
        raise ValueError("frozen contract does not match exposure")
    resolved = json.loads((source / "resolved-spec.json").read_text())
    from .preparation_audit import audit_prepared
    checked=audit_prepared(source)
    if checked["state"]!="pass":raise ValueError("Preparation failed integrity audit: "+str(checked["errors"]))
    return messages, contract, resolved


def run(source: Path | None, output: Path, max_messages: int = 150,
        repetitions: int = 1, *, smoke: bool = False, providers: list[str] | None = None,
        question_dir: Path | None = None, honcho_clock: str = 'real',
        inference_route: str = 'muse-gateway', judge_reasoning_effort: str | None = None,
        judge_timeout_s: int = 180, recall_timeout_s: int = 180) -> dict:
    for provider in providers or []:require_not_retired(provider)
    from .live_trials import require_live_trials_ready
    require_live_trials_ready()
    from .document_ingestion import BLOCKERS, ingest_day, prepare_sources, record_recall_isolation, validate_evaluation_clocks
    if inference_route not in ('muse-gateway', 'codex-subscription', 'openrouter'):
        raise ValueError('Unknown inference route')
    if type(judge_timeout_s) is not int or not 0<judge_timeout_s<=900:
        raise ValueError('Judge deadline must be 1–900 real seconds')
    if type(recall_timeout_s) is not int or not 0<recall_timeout_s<=900:
        raise ValueError('Recall deadline must be 1–900 real seconds')
    if judge_reasoning_effort is not None and (inference_route!='openrouter' or
            judge_reasoning_effort not in {'none','low','medium','high'}):
        raise ValueError('Explicit judge reasoning requires the direct OpenRouter condition')
    if not 1 <= repetitions <= 3 or not 1 <= max_messages <= 100000:
        raise ValueError("pilot limits: 1–3 repetitions, 1–100000 messages; increase gradually")
    selected = providers if providers is not None else provider_names()
    if not selected or len(set(selected)) != len(selected) or any(p not in provider_names() for p in selected):
        raise ValueError("invalid or duplicate provider selection")
    if honcho_clock not in ('real', 'simulated') or (honcho_clock == 'simulated' and (not source or 'honcho' not in selected)):
        raise ValueError('A simulated Honcho clock requires prepared source and the Honcho provider')
    if inference_route == 'openrouter':
        if any(p not in {'memcal','mem0','hindsight','supermemory','honcho','openviking','reme','cognee','memos'} for p in selected):
            raise ValueError('Unsupported OpenRouter memory provider')
        if os.environ.get('PACBENCH_TRANSPORT') != 'openrouter':
            raise RuntimeError('OpenRouter requires the run-local transport; key and spending limit must be configured first')
        require_live_cost_boundary()
    elif os.environ.get('PACBENCH_TRANSPORT') == 'openrouter':
        raise ValueError('Transport and inference route differ')
    if source and inference_route == 'codex-subscription':
        raise RuntimeError('Codex native pilot is blocked: an isolated Hermes OAuth provision/login/resume workflow is not implemented; no ingestion or inference was started')
    if source:
        from .context_isolation import validate_recall_environment
        validate_recall_environment()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stop_file = Path(os.environ.get('PACBENCH_STOP_FILE') or output / 'STOP').resolve()
    question_dir=(question_dir or output/'combined-questions').resolve()
    # Registered first, so native server shutdown hooks run before this final
    # fallback for CLI grandchildren left behind by timed-out provider tools.
    import atexit
    from .process_cleanup import cleanup_run_processes
    atexit.register(cleanup_run_processes, output)
    results = []
    report = {"state": "inventory", "results": results, "mode": "native-hermes",
              "builtin_memory": False, "tool_policy": TOOL_POLICY, "session_search": False,'ingestion_mode':'documents'}
    report['inference_route'] = inference_route
    if judge_reasoning_effort is not None:
        report['judge_generation_conditions']={'reasoning_effort':judge_reasoning_effort,
            'scope':'Benchmark judge only; native provider and Hermes settings unchanged'}
    report.setdefault('judge_generation_conditions',{})['wall_deadline_s']=judge_timeout_s
    report['recall_wall_deadline_s']=recall_timeout_s
    if 'honcho' in selected and os.environ.get('DERIVER_MODEL_CONFIG__THINKING_EFFORT'):
        report['provider_generation_conditions'] = {'honcho': {
            'deriver_thinking_effort': os.environ['DERIVER_MODEL_CONFIG__THINKING_EFFORT'],
            'scope': 'Documented native model setting; output limits and batching remain unchanged'}}
    if inference_route == 'openrouter':
        from .openrouter_route import MODEL, ENDPOINT
        from .openrouter_direct import active
        if active():
            from .direct_accounting import VERSION as TRANSPORT_VERSION
        else:
            from .openrouter_proxy import VERSION as TRANSPORT_VERSION
        from .role_routes import memory_model, record as role_record
        if role_record():report['role_routes']=role_record()
        report.update(hermes_model=MODEL, judge_model=MODEL, extraction_model=memory_model(),
                      openrouter_endpoint=ENDPOINT, transport=TRANSPORT_VERSION)
    if inference_route == 'codex-subscription':
        report['hermes_model'] = report['judge_model'] = 'gpt-6-luna'
        report['subscription_cost_status'] = 'unpriced'
    report['provenance_version'] = ('source-provenance-v3' if any(p in {'cognee','memos','supermemory'} for p in selected) else 'source-provenance-v2')
    report['provider_loader_protocol']='native-loaded-source-v1'
    if source and 'openviking' in selected:
        from .terminal_processing import VERSION as TERMINAL_PROTOCOL
        report['terminal_processing_protocols']={'openviking':TERMINAL_PROTOCOL}
    from .clock_context import NATIVE as CLOCK_CONTEXT
    from .openrouter_direct import active as direct_active
    report['clock_context_protocol']=CLOCK_CONTEXT
    report['question_prompt_protocol']=harness.PROMPT_PROTOCOL
    if source and 'honcho' in selected:
        from provider_adapters.honcho_enrollment import PROTOCOL as HONCHO_MEMBERSHIP
        report['provider_membership_protocols'] = {'honcho': HONCHO_MEMBERSHIP}
    if honcho_clock == 'simulated':
        from provider_adapters.honcho_clock import VERSION as HONCHO_CLOCK
        report['provider_clocks'] = {'honcho': HONCHO_CLOCK}
    from .semantic_grade import DIAGNOSTIC_VERSION, DATE_SCOPED_JUDGE_PROTOCOL
    report['evidence_diagnostic_protocol'] = DIAGNOSTIC_VERSION
    report['judge_protocol'] = DATE_SCOPED_JUDGE_PROTOCOL
    save(output / "summary.json", report)
    driver = None
    judge = plan = None
    messages = contract = resolved = None
    evaluations = []
    active_clock_root = None
    if source:
        if source.is_file():
            from .prepare import import_week
            source=import_week(source,output / "preparation")
        messages, contract, resolved = read_prepared(source, max_messages)
        evaluation_plan = None
        if (source / 'evaluation-plan.json').exists():
            evaluation_plan, evaluations = load_evaluations(source, messages, resolved)
        try:
            report["gateway_before"] = require_live_cost_boundary()
        except RuntimeError as exc:
            report.update(state="blocked", reason=str(exc))
            save(output / "summary.json", report)
            raise
        driver = load_driver()
        copy_prepared_files(source,output)
        report.update(input_hash=hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest(), contract_hash=contract.contract_hash,
                      bundle_hash=json.loads((source / "summary.json").read_text())["bundle_hash"],
                      message_count=len(messages), source=os.path.relpath(source.resolve(), output))
        report["process_timezone"] = resolved.get("timezone", "")
        from .document_ingestion import VERSION as INGESTION_VERSION
        report.update(ingestion_version=INGESTION_VERSION,
                      document_provider_blockers=dict(BLOCKERS),
                      ingestion_schedule=prepare_sources(output,messages,resolved.get('timezone') or 'America/New_York'))
        validate_evaluation_clocks(report['ingestion_schedule'],[p for p in selected if p not in BLOCKERS],
            [row['at'] for row,_,_ in evaluations] if evaluations else [resolved['question_at']],
            report['process_timezone'] or 'America/New_York', chronological=bool(evaluations))
        if evaluation_plan:
            from .native_writes import VERSION as WRITE_READINESS
            report['write_readiness_protocol']=WRITE_READINESS
            shutil.copyfile(source / 'evaluation-plan.json', output / 'evaluation-plan.json')
            shutil.copytree(source / 'evaluations', output / 'evaluations')
            report.update(evaluation_plan_hash=evaluation_plan['plan_hash'], evaluation_context=evaluation_plan['context'])
        if inference_route == 'codex-subscription':
            from .codex_model_calls import CodexModelClient
            model_judge = CodexModelClient(model='gpt-6-luna', record_dir=output / 'model-calls')
        else:
            judge_base, judge_token = client_settings("pacbench-judge")
            model_judge = ModelClient(key=judge_token, model=driver.model_id,
                                url=judge_base + "/chat/completions",
                                record_dir=output / "model-calls", reasoning_effort=judge_reasoning_effort,
                                json_mode=inference_route=='openrouter',timeout_s=judge_timeout_s)
        def judge(*args, **kwargs):
            check_stop(stop_file)
            if active_clock_root is not None:
                from provider_adapters.honcho_clock import benchmark_overhead
                with benchmark_overhead(active_clock_root,json.loads(env['PACBENCH_USAGE_ROLES'])):
                    return model_judge(*args, **kwargs)
            return model_judge(*args, **kwargs)
        plan = freeze_policy(contract, output / "followup-plan.json")
        report["followup_plan_hash"] = plan["plan_hash"]
        report["followup_policy"] = "one combined message for implicit omissions only; wrong answers, explicit misses and admitted retrieval failures are terminal; half recovery credit; source fallback is permitted but earns no retention credit"
        report["state"] = "running"
        save(output / "summary.json", report)
    interrupted = False
    live_stopped = False
    for repetition in range(1, repetitions + 1):
        for provider in selected:
            active_clock_root = None
            try:
                check_stop(stop_file)
            except OperatorStop:
                interrupted = True
                break
            root = output / f"repeat-{repetition}" / provider
            if source and provider in BLOCKERS:
                result={'provider':provider,'repetition':repetition,'state':'blocked','reason':BLOCKERS[provider],
                        'input_hash':report['input_hash'],'capability':{'available':False,'reason':BLOCKERS[provider],
                        'execution_blocker':BLOCKERS[provider],'wiring_verified':False,'tools':[]}}
                results.append(result)
                save(root/'status.json',result)
                save(output/'summary.json',report)
                stop_arm_model_work(root)
                continue
            if source:
                try:require_live_cost_boundary()
                except RuntimeError as exc:
                    result={'provider':provider,'repetition':repetition,'state':'blocked',
                            'reason':str(exc),'input_hash':report['input_hash'],
                            'capability':{'available':False,'reason':str(exc),'tools':[],
                                          'wiring_verified':False,'execution_blocker':str(exc)}}
                    results.append(result);save(root/'status.json',result)
                    save(output/'summary.json',report)
                    continue
            try:
                clock_options = {}
                if provider == 'honcho' and honcho_clock == 'simulated':
                    from provider_adapters.honcho_enrollment import initial_at
                    clock_options['honcho_simulated_at'] = initial_at(messages)
                env = provision(root, provider, driver.model_id if driver else "unconfigured",
                                local_timezone=resolved.get("timezone", "") if resolved else "",
                                inference_route=inference_route, **clock_options)
                env['PACBENCH_STOP_FILE'] = str(stop_file)
                env['PACBENCH_CLOCK_CONTEXT_PROTOCOL']=report['clock_context_protocol']
                if inference_route=='openrouter' and direct_active():
                    env['PACBENCH_API_INPUT_TRACE']=str(root/'api-inputs.jsonl')
                if provider == 'honcho' and honcho_clock == 'simulated':
                    active_clock_root = root
                def guarded_turn(*args, **kwargs):
                    check_stop(stop_file)
                    return harness.run_turn(*args, **kwargs)
                check_stop(stop_file)
            except OperatorStop:
                result={'provider':provider, 'repetition':repetition, 'state':'interrupted',
                        'reason':'operator stop requested', 'capability':{'available':False, 'tools':[]}}
                results.append(result)
                save(root/'status.json', result)
                stop_arm_model_work(root)
                interrupted = True
                break
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
                # Preserve native setup failures and continue independent arms.
                _,key=client_settings('setup-check')
                reason=f'Native setup failed: {type(exc).__name__}: {exc}'.replace(key,'<gateway-token>')
                result={'provider':provider,'repetition':repetition,'state':'invalid','reason':reason,
                        'failure_phase':'setup','capability':{'available':False,'reason':reason,
                        'tools':[],'wiring_verified':False}}
                results.append(result)
                save(root/'status.json',result)
                save(root/'simulation-clock.json',{'process_timezone':resolved.get('timezone','') if resolved else '',
                     'pin':'setup attempt; no evaluation'})
                save(output/'summary.json',report)
                stop_arm_model_work(root)
                continue
            save(root / "simulation-clock.json", {"process_timezone": env.get("TZ", ""),
                 "pin": "public simulated clock supplied to every turn"})
            capability = probe(root, provider, env, smoke=smoke or source is not None,
                               smoke_timeout_s=120)
            if (smoke or source is not None) and provider in ISOLATED_PROVIDERS:
                capability["wiring_verified"] = smoke_succeeded(capability)
            save(root / "preflight.json", capability)
            result = {"provider": provider, "repetition": repetition,
                      'input_hash':report['input_hash'] if source else None,
                      "capability": capability, "state": (
                          "available-unverified" if capability["available"] and
                          not capability.get("execution_blocker") else "blocked")}
            if report.get('write_readiness_protocol'):
                result['write_readiness_protocol']=report['write_readiness_protocol']
            results.append(result)
            save(root / 'status.json', result)
            save(output / 'summary.json', report)
            if capability.get("wiring_verified") is False:
                result.update(state="invalid", reason="native read-only smoke failed")
            if (not source or not capability["available"] or capability.get("execution_blocker")
                    or result["state"] == "invalid"):
                save(output / "summary.json", report)
                stop_arm_model_work(root)
                continue
            try:
                policy = policy_record(provider, capability["tools"])
            except ValueError as exc:
                result.update(state="invalid", reason=str(exc))
                save(output / "summary.json", report)
                stop_arm_model_work(root)
                continue
            allowlist = ",".join(policy["allowed_tools"])
            env['PACBENCH_SOURCE_ARGUMENT_RULES'] = json.dumps(policy['source_argument_rules'])
            save(root / "tool-policy.json", policy)
            ingested=[]
            failed_batches=set()
            from .document_ingestion import ready_sources
            from .usage import snapshot, provider_model_health
            model_work_stopped = False
            def check_model_health():
                nonlocal model_work_stopped
                try:require_live_cost_boundary()
                except RuntimeError:
                    if not model_work_stopped:
                        model_work_stopped = True
                        stop_arm_model_work(root)
                    raise
                health = provider_model_health(snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
                save(root/'provider-model-health.json', health)
                if health['state'] == 'stop':
                    if not model_work_stopped:
                        model_work_stopped = True
                        stop_arm_model_work(root)
                    raise RuntimeError(health['reason'])
            def import_before_check(row, directory):
                check_stop(stop_file)
                check_model_health()
                needed=ready_sources(report['ingestion_schedule'],provider,row['at'],report['process_timezone'] or 'America/New_York')
                if provider == 'honcho' and honcho_clock == 'simulated':
                    env['PACBENCH_CLOCK_HORIZON'] = row['at']
                    later = next_checkpoint((r for r,_,_ in evaluations), row['at'])
                    if later: env['PACBENCH_CLOCK_NEXT_CHECKPOINT'] = later
                    else: env.pop('PACBENCH_CLOCK_NEXT_CHECKPOINT', None)
                ingest_pending(needed, ingested, failed_batches, output, root, provider, env,
                               report['process_timezone'] or 'America/New_York', stop_file,
                               ingest_day, check_model_health)
                if not (root/'pre-recall-isolation.json').exists():record_recall_isolation(root)
                if provider == 'honcho' and honcho_clock == 'simulated':
                    from provider_adapters.honcho_clock import stage_turn
                    # Age any genuine capture work before checking a later
                    # session's dependency; never flush or backdate that work.
                    stage_turn(root, row['at'], env)
                if evaluations:
                    from .native_writes import ensure_ready
                    ensure_ready(provider,row,evaluations,evaluation_plan['context'],root,env,directory)
                from .document_ingestion import source_state
                save(directory/'source-state.json',source_state(row['at'],ingested))
            if evaluations:
                try:
                    from .evaluation import judge_evidence
                    def evidence_for_check(row):
                        return judge_evidence(output, messages, resolved, row, evaluation_plan['context'], diagnostic_version=report['evidence_diagnostic_protocol'])
                    sequence = run_checks(evaluations, evaluation_plan['context'], root, judge, allowlist, env,
                                          question_dir=question_dir, before_check=import_before_check,
                                          evidence_by_id=evidence_for_check,
                                          turn_timeout_s=recall_timeout_s,
                                          provenance_version=report['provenance_version'], diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
                    result.update(state=sequence['state'], checks=sequence['checks'], input_hash=report['input_hash'],
                                  reason='' if sequence['state'] == 'diagnostic' else 'evaluation sequence incomplete')
                    capability['live_verified'] = sequence['state'] == 'diagnostic'
                    result['behavior'] = {'evaluation_context': evaluation_plan['context']}
                    if sequence['state'] == 'interrupted':
                        result['reason'] = 'operator stop requested'
                        interrupted = True
                    if sequence['state'] in ('access_blocked','budget_blocked'):
                        live_stopped=True
                        report['access_stop' if sequence['state']=='access_blocked' else 'spending_stop']=sequence['reason']
                except RuntimeError as exc:
                    result.update(state='invalid',reason=str(exc))
            else:
                try:
                    import_before_check({'at':resolved['question_at']},root)
                except (ModelAccessBlocked,SpendingBlocked) as exc:
                    result.update(state='blocked',reason=str(exc));live_stopped=True
                    report['access_stop' if isinstance(exc,ModelAccessBlocked) else 'spending_stop']=str(exc)
                    stop_arm_model_work(root)
                    save(root/'status.json',result);save(output/'summary.json',report)
                    break
                except OperatorStop:
                    result.update(state='interrupted', reason='operator stop requested')
                    interrupted = True
                    stop_arm_model_work(root)
                    save(root/'status.json',result);save(output/'summary.json',report)
                    break
                except RuntimeError as exc:
                    result.update(state='invalid',reason=str(exc))
                    save(root/'status.json',result);save(output/'summary.json',report)
                    stop_arm_model_work(root)
                    continue
                # Deliberately omit --resume: recall must cross a real session boundary.
                try:
                    check_stop(stop_file)
                except OperatorStop:
                    result.update(state='interrupted', reason='operator stop requested')
                    interrupted = True
                    stop_arm_model_work(root)
                    save(root/'status.json',result);save(output/'summary.json',report)
                    break
                turn = tracked_turn(harness.run_turn, contract.question, usage_path=root / "usage.json", at=resolved["question_at"],
                                        home=root / "hermes-home", allowlist=allowlist,
                                        extra_env=env, timeout_s=recall_timeout_s)
                save(root / "recall.json", vars(turn))
                try:
                    check_stop(stop_file)
                except OperatorStop:
                    result.update(state='interrupted', reason='operator stop requested')
                    interrupted = True
                    stop_arm_model_work(root)
                    save(root/'status.json',result);save(output/'summary.json',report)
                    break
                try:
                    result["behavior"] = {
                        "provider_tool_calls": [c.get("name") for c in turn.tool_calls
                                                if c.get("name") in capability["tools"]],
                        "prefetch_context_present": bool(turn.prefetch_context)}
                    result.update(state="invalid", reason="recall turn failed or policy blocked")
                    if not turn.returncode and turn.answer.strip() and not turn.policy_blocks:
                        from .native_checks import diagnostic_inputs
                        from .evaluation import source_evidence as build_source_evidence
                        source_evidence = build_source_evidence(messages, report["evidence_diagnostic_protocol"])
                        from .clock_context import judge_question
                        grade = grade_native_semantic(resolved["id"], judge_question(turn), turn.answer,
                                               tuple(Claim(c.id, c.statement, c.evidence)
                                                     for c in contract.claims), judge,
                                               record_path=root / "judgment.json", assess_recovery=True,
                                               diagnostic_inputs=diagnostic_inputs(turn, contract, source_evidence, diagnostic_version=report['evidence_diagnostic_protocol']), diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
                        save(root / "grade.json", {"correct": grade.correct, "missed": grade.missed,
                                                  "misrepresented": grade.misrepresented,
                                                  "claims": [vars(s) for s in grade.slots], "recovery": grade.recovery,
                                                  "evidence_diagnostic": grade.evidence_diagnostic})
                        result.update(state="diagnostic", reason="", input_hash=report['input_hash'])
                        capability["live_verified"] = True
                        recovery = run_followups(contract=contract, plan=plan, grade=grade,
                            initial_turn=turn, output=root / "followups", judge=judge,
                            home=root / "hermes-home", allowlist=allowlist, extra_env=env,
                            question_dir=question_dir, evidence_by_id=source_evidence, run_turn=guarded_turn,
                            provenance_version=report['provenance_version'], diagnostic_version=report['evidence_diagnostic_protocol'], judge_protocol=report['judge_protocol'])
                        result["recovery"] = {"state": recovery["state"], "score": recovery["score"],
                            "attempts": [{"claim_ids": a["claim_ids"], "verdicts": a["verdicts"],
                                          "metrics": a["metrics"]} for a in recovery["attempts"]]}
                        if recovery["state"] != "complete":
                            result.update(state="invalid", reason="follow-up diagnostics incomplete")
                except (ModelAccessBlocked,SpendingBlocked) as exc:
                    result.update(state='blocked',reason=str(exc));live_stopped=True
                    report['access_stop' if isinstance(exc,ModelAccessBlocked) else 'spending_stop']=str(exc)
                except OperatorStop:
                    result.update(state="interrupted", reason="operator stop requested")
                    interrupted = True
            from .usage import snapshot
            try:
                if provider=='openviking' and report.get('terminal_processing_protocols'):
                    from .terminal_processing import observe_before_stop
                    def terminal_boundary():
                        check_stop(stop_file)
                        check_model_health()
                    terminal=observe_before_stop(root,env,boundary=terminal_boundary)
                    result['terminal_processing']={'protocol':terminal['protocol'],'state':terminal['state'],
                        'scope_hash':terminal['scope_hash']}
                    if terminal['state']!='complete':
                        capability['recall_verified']=capability.get('live_verified',False)
                        capability['live_verified']=False
                        if result['state']=='diagnostic':
                            result.update(state='partial',failure_phase='terminal_processing',
                                reason='Post-answer native processing '+terminal['state']+'; semantic grades preserved')
                    if terminal['state']=='stopped_by_boundary':
                        if terminal.get('error_type')=='OperatorStop':interrupted=True
                        else:live_stopped=True
                save(root/'pre-shutdown-model-usage.json',snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
            finally:
                # Even a failed observer or ledger snapshot must stop owned work.
                stop_arm_model_work(root)
            save(root / "model-usage.json", snapshot(json.loads(env["PACBENCH_USAGE_ROLES"])))
            save(root / "status.json", result)
            save(output / "summary.json", report)
            if interrupted or live_stopped:
                break
        if interrupted or live_stopped:
            break
    report["state"] = ("diagnostic" if all(r["state"] == "diagnostic" for r in results)
                       else "incomplete") if source else "inventory"
    if interrupted:
        report.update(state='interrupted', interruption='operator stop requested')
    if source:
        try:report["gateway_after"] = require_live_cost_boundary()
        except RuntimeError as exc:
            # Budget stopping is a completed execution outcome, not a reason
            # to leave the public run permanently marked "running".
            report['state']='interrupted' if interrupted else 'incomplete'
            key=('access_stop' if isinstance(exc,ModelAccessBlocked) else
                 'spending_stop' if isinstance(exc,SpendingBlocked) else 'gateway_stop')
            report[key]=str(exc)
    save(output / "summary.json", report)
    if source:
        from .native_audit import audit
        report["audit"] = audit(output)
        if report["audit"]["state"] != "pass" and not interrupted:
            report["state"] = "incomplete"
        save(output / "native-audit.json", report["audit"])
        save(output / "summary.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-messages", type=int, default=150)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--smoke", action="store_true", help="bounded native read-only tool checks")
    parser.add_argument("--providers", nargs="+", help="select native arms; omitted inventories every provider")
    parser.add_argument('--question-dir',type=Path,help='Shared reviewed recovery cache for concurrent source-identical arms')
    parser.add_argument('--honcho-clock', choices=('real','simulated'), default='real',
                        help='Isolated native Honcho wall clock; gateway and harness stay on real time')
    parser.add_argument('--inference-route', choices=('muse-gateway', 'codex-subscription', 'openrouter'),
                        default='muse-gateway', help='Model route; openrouter requires the capped openrouter_run entrypoint')
    args = parser.parse_args()
    report = run(args.prepared, args.output, args.max_messages, args.repetitions, smoke=args.smoke, providers=args.providers,
                 question_dir=args.question_dir, honcho_clock=args.honcho_clock, inference_route=args.inference_route)
    for r in report["results"]:
        print(r["provider"], r["state"], r["capability"].get("execution_blocker") or
              r["capability"].get("reason", ""))
    return 1 if (report["state"] in ("incomplete", "interrupted") or any(r["state"] == "invalid" for r in report["results"]) or
                 args.prepared and any(r["state"] != "diagnostic" for r in report["results"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
