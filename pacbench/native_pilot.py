"""Bounded document ingestion followed by ordinary native Hermes recall."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import harness
from .creds import load_driver
from .frozen import load_bundle, messages_for
from .model_calls import ModelClient
from .semantic_contract import load_contract
from .semantic_grade import Claim, grade_semantic
from .followups import save
from .gateway_client import client_settings, require_live_cost_boundary
from .followups import freeze_policy, run_followups
from .evaluation import load_evaluations
from .native_checks import run_checks
from .tool_policy import VERSION as TOOL_POLICY, policy_record
from .usage import tracked_turn

MEMCAL_SRC = harness.REPO.parent / "memcal"
# These use profile-local stores without requiring a shared remote namespace.
ISOLATED_PROVIDERS = {"holographic", "memcal", "mem0", "hindsight", "openviking", "byterover", "supermemory", "honcho"}

PROBE = '''
import json, sys
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
    tool = {"holographic": "fact_store", "memcal": "memcal_list_days", "mem0": "mem0_search", "hindsight":"hindsight_recall", "openviking":"viking_search", "byterover":"brv_status", "supermemory":"supermemory-profile", "honcho":"honcho_profile"}[p.name]
    args = ({"action": "list"} if p.name == "holographic" else
            {"query": "native read-only capability check"} if p.name in {"mem0","hindsight","openviking"} else {})
    output = p.handle_tool_call(tool, args)
    print(json.dumps({"tool": tool, "arguments": args, "output": output}))
finally:
    p.shutdown()
'''


def provider_names() -> list[str]:
    """Inventory the pinned checkout, rather than maintaining a vendor wish list."""
    root = harness.ISOLATED_TREE / "plugins" / "memory"
    names = sorted(p.name for p in root.iterdir()
                   if p.is_dir() and (p / "plugin.yaml").is_file())
    return names + ["memori", "memcal"]


def provision(root: Path, provider: str, model: str = "unconfigured", *,
              local_timezone: str = "") -> dict:
    home = root / "hermes-home"
    run_id = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    driver_project = f"pacbench-{run_id}-{provider}-driver"
    memory_project = f"pacbench-{run_id}-{provider}-memory"
    driver_url, client_key = client_settings(driver_project)
    harness.provision_home(
        home, provider_plugin_src=(MEMCAL_SRC / "integrations/hermes/memcal"
                                   if provider == "memcal" else None),
        provider_name=provider, model=model, model_provider="meta-ai",
        model_base_url=driver_url)
    # Isolate HOME fallbacks; only the selected provider supplies memory.
    fallback = root / "fallback-home"
    fallback.mkdir()
    env = {"HOME": str(fallback.resolve()), "PACBENCH_NO_BUILTIN_MEMORY": "1", "PACBENCH_PROVIDER_ONLY": "1",
           "MEMCAL_HOME": str((root / "memcal-home").resolve()),
           "MEMCAL_SRC": str(MEMCAL_SRC),
           "PACBENCH_USAGE_ROLES": json.dumps({driver_project:"hermes",memory_project:"memory_provider"}), "HERMES_ENABLE_PROJECT_PLUGINS": "0"}
    if local_timezone:
        from zoneinfo import ZoneInfo
        ZoneInfo(local_timezone)  # Reject invalid simulation zones before any live calls.
        env["TZ"] = local_timezone
    env.update({"META": "", "META_API_KEY": client_key, "MODEL_API_KEY": client_key,
                "META_MODEL_API_KEY": client_key, "META_BASE_URL": driver_url,
                "OPENAI_API_KEY": client_key, "OPENROUTER_API_KEY": "", "OPENROUTER_API_BASE": "", "MEM0_API_KEY": "",
                "MEM0_MODE": "oss", "MEM0_HOST": "", "MEM0_TELEMETRY": "false",
                "MEM0_DIR": str((root / "mem0-state").resolve())})
    if provider == "mem0":
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
from plugins.memory.mem0._setup import build_oss_config
from plugins.memory.mem0 import Mem0MemoryProvider
flags=json.loads(sys.argv[1])
oss,_=build_oss_config(flags)
Mem0MemoryProvider().save_config({"mode":"oss","user_id":sys.argv[2],"oss":oss},sys.argv[3])
'''
            flags = {"oss_llm": "openai", "oss_llm_model": configured_model,
                     "oss_llm_url": extraction_url, "oss_embedder": "ollama",
                     "oss_embedder_model": "nomic-embed-text", "oss_embedder_url": "http://127.0.0.1:11435",
                     "oss_vector": "qdrant", "oss_vector_path": str((root / "qdrant").resolve())}
            proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c", builder,
                                   json.dumps(flags), identity, str(home.resolve())],
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
        config["sync_max_chars"] = 6000
        # Spark's reasoning counts against the output budget. The SDK default
        # 2,000 exhausted that budget before emitting extraction JSON. Use the
        # documented model config, not a prompt or extraction workaround.
        config['oss']['llm']['config']['max_tokens']=16384
        save(home / "mem0.json", config)
        os.chmod(home / "mem0.json", 0o600)
        (home / ".env").write_text("OPENAI_API_KEY=" + client_key + "\n")
        os.chmod(home / ".env", 0o600)
    if provider == "memcal":
        memcal_url, _ = client_settings(memory_project)
        configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
        env.update({"MEMCAL_LLM_PROVIDER": "openai-compatible", "MEMCAL_OPENAI_BASE_URL": memcal_url,
                    "OPENAI_COMPAT_API_KEY": client_key, "MEMCAL_PROPOSE_MODEL": configured_model,
                    "MEMCAL_SWEEP_MODEL": configured_model, "MEMCAL_MATCH_MODEL": configured_model})
        # Recommended store-scoped configuration overrides project .env fallbacks.
        memcal_home = root / "memcal-home"
        memcal_home.mkdir(parents=True, exist_ok=True)
        routed = {key:value for key,value in env.items() if key.startswith('MEMCAL_') and key not in {'MEMCAL_HOME','MEMCAL_SRC'}}
        routed['OPENAI_COMPAT_API_KEY'] = client_key
        (memcal_home / '.env').write_text(''.join(f'{key}={value}\n' for key,value in routed.items()))
        os.chmod(memcal_home / '.env', 0o600)
        # Native first-install bootstrap, with no facts seeded into the store.
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from memcal import config, db, brief; "
            "import os; cfg=config.load(); "
            "assert cfg.llm_provider == 'openai-compatible' and cfg.openai_base_url == os.environ['MEMCAL_OPENAI_BASE_URL'] and cfg.openai_api_key == os.environ['OPENAI_COMPAT_API_KEY'], 'Memcal model routing must use the shared gateway'; cfg.ensure_dirs(); "
            "conn=db.open_db(cfg.db_path); brief.write(conn,cfg); conn.close()")
        proc = subprocess.run([str(harness.ISOLATED_BIN.parent / "python"), "-c",
                               bootstrap, str(MEMCAL_SRC)],
                              env={**os.environ, **env}, capture_output=True, timeout=30)
        if proc.returncode:
            raise RuntimeError("Memcal native scratch-store bootstrap failed")
    if provider == 'hindsight':
        # README local_external setup with a run-owned self-hosted native server.
        # Preserve failed embedded trials: the pinned plugin repeatedly restarts
        # the newer SDK daemon when it adds native profile environment fields.
        from .gateway_client import validate_route
        base,key=client_settings(memory_project)
        validate_route(base,key)
        configured_model=model if model!='unconfigured' else 'muse-spark-1.3-contributor'
        folder=home/'hindsight'
        folder.mkdir()
        save(folder/'config.json',{'mode':'local_external','profile':'pacbench-'+run_id,
            'bank_id':'pacbench-'+run_id,'llm_provider':'meta',
            'llm_model':configured_model,'llm_base_url':base,'llm_api_key':key})
        os.chmod(folder/'config.json',0o600)
        env.update(HINDSIGHT_LLM_API_KEY=key,HINDSIGHT_API_LLM_API_KEY=key,
                   HINDSIGHT_API_LLM_BASE_URL=base,HINDSIGHT_API_LLM_MODEL=configured_model,
                   HINDSIGHT_API_LLM_PROVIDER='meta',HINDSIGHT_API_ENABLE_API_KEY_AUTH='false')
        # Ordinary provisioning creates an empty bank, not starter memories or a
        # benchmark mission. Recall of a nonexistent bank is otherwise a 404.
        bootstrap='''
import json,sys
from pathlib import Path
from hindsight import HindsightEmbedded
cfg=json.loads((Path(sys.argv[1])/'hindsight/config.json').read_text())
p=HindsightEmbedded(profile=cfg['profile'],llm_provider=cfg['llm_provider'],llm_model=cfg['llm_model'],
    llm_api_key=cfg['llm_api_key'],llm_base_url=cfg['llm_base_url'])
try:
    result=p.create_bank(bank_id=sys.argv[2])
    print(json.dumps({'endpoint':p._manager.get_url(cfg['profile']),'bank':result.model_dump(mode='json')}))
finally:p.close()
'''
        proc=subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',bootstrap,
                             str(home.resolve()),'pacbench-'+run_id],
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
        atexit.register(lambda:subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',stop_script,'pacbench-'+run_id],
            env={**os.environ,**env},cwd=harness.ISOLATED_TREE,capture_output=True,timeout=30))
    if provider=='openviking':
        from provider_adapters.openviking_local import start
        base,key=client_settings(memory_project)
        start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key)
    if provider=='byterover':
        from provider_adapters.byterover_local import configure
        base,key=client_settings(memory_project)
        configure(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key)
    if provider=='honcho':
        from provider_adapters.honcho_local import start
        base,key=client_settings(memory_project)
        start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key)
    if provider=='supermemory':
        from provider_adapters.supermemory_local import start
        base,key=client_settings(memory_project)
        start(root.resolve(),env,model if model!='unconfigured' else 'muse-spark-1.3-contributor',base,key)
    return env


def probe(root: Path, provider: str, env: dict, *, smoke: bool = False, smoke_timeout_s: int = 30) -> dict:
    child_env = harness.turn_env("2026-09-11T09:00:00-04:00",
                                 root / "hermes-home", "", env)
    try:
        proc = subprocess.run(
            [str(harness.ISOLATED_BIN.parent / "python"), "-c", PROBE, provider],
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
                [str(harness.ISOLATED_BIN.parent / "python"), "-c", READ_ONLY_SMOKE, provider],
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
        repetitions: int = 1, *, smoke: bool = False, providers: list[str] | None = None) -> dict:
    from .document_ingestion import BLOCKERS, ingest_day, prepare_sources, record_recall_isolation, validate_evaluation_clocks
    if not 1 <= repetitions <= 3 or not 1 <= max_messages <= 100000:
        raise ValueError("pilot limits: 1–3 repetitions, 1–100000 messages; increase gradually")
    selected = providers if providers is not None else provider_names()
    if not selected or len(set(selected)) != len(selected) or any(p not in provider_names() for p in selected):
        raise ValueError("invalid or duplicate provider selection")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Registered first, so native server shutdown hooks run before this final
    # fallback for CLI grandchildren left behind by timed-out provider tools.
    import atexit
    from .process_cleanup import cleanup_run_processes
    atexit.register(cleanup_run_processes, output)
    results = []
    report = {"state": "inventory", "results": results, "mode": "native-hermes",
              "builtin_memory": False, "tool_policy": TOOL_POLICY, "session_search": False,'ingestion_mode':'documents'}
    save(output / "summary.json", report)
    driver = None
    judge = plan = None
    messages = contract = resolved = None
    evaluations = []
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
        for name in ("bundle.json", "contract.json", "resolved-spec.json"):
            shutil.copyfile(source / name, output / name)
        report.update(input_hash=hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False).encode()).hexdigest(), contract_hash=contract.contract_hash,
                      bundle_hash=json.loads((source / "summary.json").read_text())["bundle_hash"],
                      message_count=len(messages), source=os.path.relpath(source.resolve(), output))
        report["process_timezone"] = resolved.get("timezone", "")
        report.update(ingestion_version='daily-source-documents-v1',
                      document_provider_blockers=dict(BLOCKERS),
                      ingestion_schedule=prepare_sources(output,messages,resolved.get('timezone') or 'America/New_York'))
        validate_evaluation_clocks(report['ingestion_schedule'],[p for p in selected if p not in BLOCKERS],
            [row['at'] for row,_,_ in evaluations] if evaluations else [resolved['question_at']],
            report['process_timezone'] or 'America/New_York', chronological=bool(evaluations))
        if evaluation_plan:
            shutil.copyfile(source / 'evaluation-plan.json', output / 'evaluation-plan.json')
            shutil.copytree(source / 'evaluations', output / 'evaluations')
            report.update(evaluation_plan_hash=evaluation_plan['plan_hash'], evaluation_context=evaluation_plan['context'])
        judge = ModelClient(key=os.environ["META_API_KEY"], model=driver.model_id,
                            url=client_settings("pacbench-judge")[0] + "/chat/completions",
                            record_dir=output / "model-calls")
        plan = freeze_policy(contract, output / "followup-plan.json")
        report["followup_plan_hash"] = plan["plan_hash"]
        report["followup_policy"] = "one combined message for implicit omissions only; wrong answers, explicit misses and admitted retrieval failures are terminal; half recovery credit; source fallback fails retention"
        report["state"] = "running"
        save(output / "summary.json", report)
    for repetition in range(1, repetitions + 1):
        for provider in selected:
            root = output / f"repeat-{repetition}" / provider
            if source and provider in BLOCKERS:
                result={'provider':provider,'repetition':repetition,'state':'blocked','reason':BLOCKERS[provider],
                        'input_hash':report['input_hash'],'capability':{'available':False,'reason':BLOCKERS[provider],
                        'execution_blocker':BLOCKERS[provider],'wiring_verified':False,'tools':[]}}
                results.append(result)
                save(root/'status.json',result)
                save(output/'summary.json',report)
                continue
            try:
                env = provision(root, provider, driver.model_id if driver else "unconfigured",
                                local_timezone=resolved.get("timezone", "") if resolved else "")
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
            results.append(result)
            if capability.get("wiring_verified") is False:
                result.update(state="invalid", reason="native read-only smoke failed")
            if (not source or not capability["available"] or capability.get("execution_blocker")
                    or result["state"] == "invalid"):
                save(output / "summary.json", report)
                continue
            try:
                policy = policy_record(provider, capability["tools"])
            except ValueError as exc:
                result.update(state="invalid", reason=str(exc))
                save(output / "summary.json", report)
                continue
            allowlist = ",".join(policy["allowed_tools"])
            save(root / "tool-policy.json", policy)
            ingested=[]
            failed_batches=set()
            from .document_ingestion import ready_sources
            def import_before_check(row, directory):
                needed=ready_sources(report['ingestion_schedule'],provider,row['at'],report['process_timezone'] or 'America/New_York')
                for batch in needed:
                    if batch['id'] in ingested:continue
                    if batch['id'] in failed_batches:raise RuntimeError('Earlier native import failed; source state unavailable')
                    record=ingest_day(provider,output/'sources'/batch['id'],root,env,batch['at'],report['process_timezone'] or 'America/New_York')
                    save(root/batch['file'],record)
                    if record['state']!='ready':
                        failed_batches.add(batch['id'])
                        raise RuntimeError('Native source ingestion/processing failed; inspect saved record')
                    ingested.append(batch['id'])
                if not (root/'pre-recall-isolation.json').exists():record_recall_isolation(root)
                save(directory/'source-state.json',{'at':row['at'],'ingested_batches':list(ingested),
                    'source_ids':[sid for batch in needed for sid in batch['source_ids']]})
            if evaluations:
                try:
                    sequence = run_checks(evaluations, evaluation_plan['context'], root, judge, allowlist, env,
                                          question_dir=output / 'combined-questions', before_check=import_before_check)
                    result.update(state=sequence['state'], checks=sequence['checks'], input_hash=report['input_hash'],
                                  reason='' if sequence['state'] == 'diagnostic' else 'evaluation sequence incomplete')
                    capability['live_verified'] = sequence['state'] == 'diagnostic'
                    result['behavior'] = {'evaluation_context': evaluation_plan['context']}
                except RuntimeError as exc:
                    result.update(state='invalid',reason=str(exc))
            else:
                try:
                    import_before_check({'at':resolved['question_at']},root)
                except RuntimeError as exc:
                    result.update(state='invalid',reason=str(exc))
                    save(root/'status.json',result);save(output/'summary.json',report)
                    continue
                # Deliberately omit --resume: recall must cross a real session boundary.
                turn = tracked_turn(harness.run_turn, contract.question, usage_path=root / "usage.json", at=resolved["question_at"],
                                        home=root / "hermes-home", allowlist=allowlist,
                                        extra_env=env, timeout_s=180)
                save(root / "recall.json", vars(turn))
                result["behavior"] = {
                    "provider_tool_calls": [c.get("name") for c in turn.tool_calls
                                            if c.get("name") in capability["tools"]],
                    "prefetch_context_present": bool(turn.prefetch_context)}
                result.update(state="invalid", reason="recall turn failed or policy blocked")
                if not turn.returncode and turn.answer.strip() and not turn.policy_blocks:
                    grade = grade_semantic(resolved["id"], turn.prompt, turn.answer,
                                           tuple(Claim(c.id, c.statement, c.evidence)
                                                 for c in contract.claims), judge,
                                           record_path=root / "judgment.json", assess_recovery=True)
                    save(root / "grade.json", {"correct": grade.correct, "missed": grade.missed,
                                              "misrepresented": grade.misrepresented,
                                              "claims": [vars(s) for s in grade.slots], "recovery": grade.recovery})
                    result.update(state="diagnostic", reason="", input_hash=report['input_hash'])
                    capability["live_verified"] = True
                    recovery = run_followups(contract=contract, plan=plan, grade=grade,
                        initial_turn=turn, output=root / "followups", judge=judge,
                        home=root / "hermes-home", allowlist=allowlist, extra_env=env,
                        question_dir=output / "combined-questions")
                    result["recovery"] = {"state": recovery["state"], "score": recovery["score"],
                        "attempts": [{"claim_ids": a["claim_ids"], "verdicts": a["verdicts"],
                                      "metrics": a["metrics"]} for a in recovery["attempts"]]}
                    if recovery["state"] != "complete":
                        result.update(state="invalid", reason="follow-up diagnostics incomplete")
            from .usage import snapshot
            save(root / "model-usage.json", snapshot(json.loads(env["PACBENCH_USAGE_ROLES"])))
            save(root / "status.json", result)
            save(output / "summary.json", report)
    report["state"] = ("diagnostic" if all(r["state"] == "diagnostic" for r in results)
                       else "incomplete") if source else "inventory"
    if source:
        report["gateway_after"] = require_live_cost_boundary()
    save(output / "summary.json", report)
    if source:
        from .native_audit import audit
        report["audit"] = audit(output)
        if report["audit"]["state"] != "pass":
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
    args = parser.parse_args()
    report = run(args.prepared, args.output, args.max_messages, args.repetitions, smoke=args.smoke, providers=args.providers)
    for r in report["results"]:
        print(r["provider"], r["state"], r["capability"].get("execution_blocker") or
              r["capability"].get("reason", ""))
    return 1 if (report["state"] == "incomplete" or any(r["state"] == "invalid" for r in report["results"]) or
                 args.prepared and any(r["state"] != "diagnostic" for r in report["results"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
