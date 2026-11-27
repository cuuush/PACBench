"""Native Mem0 OSS readback for the continuing-account document workflow."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pacbench import harness
from pacbench.evidence_store import save
from pacbench.gateway.observer import PROTOCOL
from pacbench.providers import HERMES_TOOLS, validate_native_deployment
from pacbench.workflows.native import receipt_lineage
from pacbench.workflows.progress import process_identity

# Verified against the installed 2.2.1 SQLiteManager.save_messages.
HISTORY_WINDOW = 10


def append_history(previous, current, expected):
    """Match newly persisted native IDs and the remainder of the rolling suffix."""
    count = len(expected)
    new = current[-count:]
    if ([(role, text) for _,role,text in new] != expected
            or len({row[0] for row in (*previous, *new)}) != len(previous)+count
            or current != [*previous, *new][-HISTORY_WINDOW:]):
        raise RuntimeError('Mem0 native history differs from the expected next write')
    return new


class Mem0Documents:
    """Use the plugin's shipped SDK configuration, recording native extraction."""

    def __init__(self, config):
        self.config = config
        self.backend = None
        self.calls = []

    def open(self):
        from copy import deepcopy

        from ..providers import provider_module

        config = deepcopy(self.config)
        config['llm']['config']['response_callback'] = self.record_call
        self.backend = provider_module('mem0', '_backend').OSSBackend(config)
        return self

    def record_call(self, llm, response, request):
        self.calls.append((request, response.model_dump(mode='json')))

    def add(self, text, **kwargs):
        from provider_adapters.document_connector import ExtractionLog

        self.calls.clear()
        errors = ExtractionLog()
        logger = logging.getLogger('mem0.memory.main')
        old_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(errors)
        try:
            results = self.backend._memory.add(text, **kwargs)['results']
        finally:
            logger.removeHandler(errors)
            logger.setLevel(old_level)
        if len(self.calls) != 1 or errors.errors:
            raise RuntimeError('Mem0 extraction did not complete cleanly')
        request, response = self.calls[0]
        if (not any(text in item['content'] for item in request['messages']
                    if item['role'] == 'user' and isinstance(item.get('content'), str))
                or not response['choices'] or any(c['finish_reason'] != 'stop' for c in response['choices'])):
            raise RuntimeError('Mem0 did not process the exact released document to completion')
        return dict(sha256=hashlib.sha256(text.encode()).hexdigest(), native_results=results,
                    response_id=response['id'])

    def close(self):
        self.backend.close()


class Mem0Witness:
    def __init__(self, root, user, agent, documents, owner, transport, ledger):
        from mem0.memory.main import _build_session_scope

        self.root, self.user, self.owner = root, user, owner
        self.transport, self.ledger = transport, ledger
        self.import_scope = _build_session_scope({'user_id':user})
        self.chat_scope = _build_session_scope({'user_id':user, 'agent_id':agent})
        self.history = root/'mem0-state'/'history.db'
        actual_history = self.transport.backend._memory.db.connection.execute('PRAGMA database_list').fetchone()[2]
        if Path(actual_history).resolve() != self.history:
            raise RuntimeError('Mem0 SQLite history differs from the shared run-owned native path')
        self.cutoff = None
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in documents))
        self.imported = []
        self.source_history = []
        self.chat_history = []

    def rows(self, scope):
        with closing(sqlite3.connect(self.history.as_uri()+'?mode=ro', uri=True)) as connection:
            return list(connection.execute(
                'SELECT id,role,content FROM messages WHERE session_scope=? ORDER BY rowid', (scope,)))

    def verify_deployment(self, condition, identity):
        from importlib.metadata import version

        from ..catalog_plugins import verify_retained_catalog_provider

        verify_retained_catalog_provider('mem0', self.owner.home)
        if self.owner.provider != 'mem0' or identity != str(self.root) or version('mem0ai') != '2.2.1':
            raise ValueError('unverified stock Mem0 OSS account')
        result = dict(account=identity, condition=vars(condition), store_identity=str(self.history.parent),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='OSS 2.2.1', frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'], effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['mem0']), native_processes={
                'driver':process_identity(self.owner.witness['pid']), 'memory':process_identity()})
        validate_native_deployment(condition, result)
        return result

    def control_readback(self):
        native = self.transport.backend._memory.get_all(filters={'user_id':self.user})['results']
        chat = self.rows(self.chat_scope)
        if native or chat or self.rows(self.import_scope):
            raise RuntimeError('Mem0 account or collection is not empty')
        return dict(account=str(self.root), chat_count=0, conversation_writes=0, source_files=[],
                    readback={'native_vector_records':len(native), 'native_history_rows':len(chat)})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index >= len(self.cutoffs) or cutoff != self.cutoffs[index]:
            raise ValueError('Mem0 releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff, as_of=cutoff, service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        if doc.cutoff != self.cutoff or raw['sha256'] != doc.sha256:
            raise ValueError('Mem0 accepted a different source or release')
        return dict(native_id=doc.filename, accepted=True, at=doc.cutoff,
                    request={'interface':'Memory.add(infer=True)', **raw})

    def document_readback(self, doc, acceptance):
        generation = acceptance['request']['response_id']
        candidates = [row for row in self.ledger.snapshot() if row['accounting_role']=='memory_provider'
                      and row['dispatch_context']['phase']=='ingest' and row['workflow_slot']['batch_id']==doc.filename
                      and row.get('http_status')==200]
        if len(candidates)!=1 or candidates[0].get('upstream_request_id') not in (None,generation):
            raise RuntimeError('Mem0 extraction response does not belong to the released document attempt')
        attempt, = candidates
        rows = self.rows(self.import_scope)
        results = acceptance['request']['native_results']
        stored = []
        for result in results:
            memory = self.transport.backend._memory.get(result['id'])
            if memory is None or memory.get('memory') != result.get('memory'):
                raise RuntimeError('Mem0 extracted record was not visible in the native vector store')
            stored.append(result['id'])
        new, = append_history(self.source_history, rows, [('user', doc.text)])
        self.source_history = rows
        self.imported.append(doc.filename)
        return dict(native_id=doc.filename, ready=True, at=doc.cutoff, submitted_sha256=doc.sha256,
                    readback={'native_input_id':new[0], 'native_vector_ids':stored,
                              'native_source_history_window':len(rows),
                              'extraction_attempt_id':attempt['id'],
                              'billing_state':attempt['state'],
                              'service_at':datetime.now(timezone.utc).isoformat()})

    def source_inventory(self):
        if self.rows(self.import_scope) != self.source_history:
            raise RuntimeError('Mem0 native source history changed outside its verified release')
        return list(self.imported)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic() + 60
        expected = self.owner.witness
        identity = [expected['pid'], expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic() < deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('Mem0 gateway observer failed')
            if observer.exists():
                sample = json.loads(observer.read_bytes())
                if ([sample['pid'], sample['start_time']] != identity or sample['protocol'] != PROTOCOL
                        or sample['observer_sha256'] != self.owner.pins['reuse_observer_sha256']
                        or sample['native_failures']):
                    raise RuntimeError('Mem0 native lifecycle observation failed')
                providers = [p for row in sample['sessions'] for p in row['providers']]
                if any('error_type' in p or p['name'] not in ('mem0','builtin')
                       or (p['name'] == 'mem0' and type(p.get('workers_alive')) is not int) for p in providers):
                    raise RuntimeError('incomplete Mem0 native provider observation')
                pending = (sample['native_release_threads'] or any(row['pending_futures'] for row in sample['sessions'])
                           or any(p['workers_alive'] for p in providers if p['name'] == 'mem0'))
                if not pending and sample['observed_monotonic_ns'] >= since:
                    active = any(not row.get('finished') and row['accounting_role'] != 'judge'
                                 for row in self.ledger.snapshot())
                    if not active:
                        return dict(gateway_identity=identity, inflight_requests=0, pending_native_writes=0,
                                    native_errors=[], readback=sample)
            time.sleep(.05)
        raise RuntimeError('Mem0 native open sessions did not become idle')

    def native_write_barrier(self, session, answer):
        from ..providers import provider_module

        sample = self.idle()['readback']
        managers = [r for r in sample['sessions'] if r['session_id'] == session and not r['shutdown_returned']]
        if not any(p['name'] == 'mem0' and p['backend_attached'] for r in managers for p in r['providers']):
            raise RuntimeError('Mem0 did not observe the live Hermes session')
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
            prompt, = connection.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (session,)).fetchone()
        truncate = provider_module('mem0')._truncate_for_sync
        limit = json.loads((self.owner.home/'mem0.json').read_text())['sync_max_chars']
        expected = [('user', truncate(prompt, limit)), ('assistant', truncate(answer, limit))]
        rows = self.rows(self.chat_scope)
        new = append_history(self.chat_history, rows, expected)
        self.chat_history = rows
        return dict(session_id=session, pending_writes=0, readback={'native_history_ids':[r[0] for r in new]})

    def after_native_close(self, session):
        deadline = time.monotonic()+60
        path = self.owner.home/'state.db'
        while time.monotonic() < deadline:
            sample = self.idle()['readback']
            managers = [r for r in sample['sessions'] if r['session_id'] == session]
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
                end = connection.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?',(session,)).fetchone()
            if end and end[0] is not None and end[1]=='cli_close' and managers and all(
                    row['shutdown_returned'] and row['clean'] for row in managers):
                if self.rows(self.chat_scope) != self.chat_history:
                    raise RuntimeError('Mem0 history has an unwitnessed write during native close')
                return dict(session_id=session, pending_writes=0,
                            readback={'closed_managers':len(managers),'observer_sample_ns':sample['observed_monotonic_ns']})
            time.sleep(.05)
        raise RuntimeError('Mem0 did not close the selected native session')

    def tool_boundaries(self, results):
        # Mem0 does not expose source-document reads. Native tool output is
        # retained but SDK-to-wire equivalence is not inferred.
        return {}

    def request_lineage(self, requests):
        return receipt_lineage(self.ledger.snapshot(), requests)

    def close(self):
        pass


def check_canary(root):
    """Re-audit the saved canary without opening the retired provider account."""
    from pacbench.evidence_store import file_sha as sha
    from pacbench.gateway_reuse import MEM0_PLUGIN_SHA256
    from pacbench.judging.retained_week import audit_document_canary

    audit = audit_document_canary(root, 'mem0')
    installed = audit.root/'hermes-home/plugins/mem0'
    plugin_files = {p.name: sha(p) for p in sorted(installed.glob('*.py'))}
    installed_sha = hashlib.sha256(json.dumps(plugin_files, sort_keys=True).encode()).hexdigest()
    if (audit.condition['version'] != 'OSS 2.2.1'
            or audit.condition['tool_names'] != list(HERMES_TOOLS['mem0'])
            or installed_sha != MEM0_PLUGIN_SHA256):
        raise ValueError('Mem0 canary static runtime differs from the Week condition')
    return audit.bindings


def mem0_model_settings(model: str, base: str, *, openrouter: bool = False) -> dict:
    """Fields applied to the native Mem0 SDK config after its setup builder."""
    settings = {'openai_base_url': base}
    if openrouter:
        # Native Mem0 gives this setting precedence over openai_base_url.
        settings['openrouter_base_url'] = base
    from pacbench.openrouter_route import MODEL as OPENROUTER_MODEL
    if model in {'muse-spark-1.3-contributor', OPENROUTER_MODEL}:
        settings['is_reasoning_model'] = True
    return settings


def _mem0_plan(root, model, base, key):
    return {'model': model, **mem0_model_settings(model, base, openrouter=True)}


def _check_mem0_resume(arm):
    home, model, memory_key = arm.home, arm.model, arm.memory_key
    config=json.loads((home/'mem0.json').read_text())
    expected = 'pacbench-' + arm.root_hash[:24]
    if (config.get('mode')!='oss' or config.get('user_id')!=expected or
        config['oss']['llm']['config']['model']!=model or config['oss'].get('graph_store') or
        config['oss']['llm']['config']['api_key']!=memory_key):raise ValueError('Retained Mem0 native configuration differs')
    from pacbench.gateway_client import validate_route
    validate_route(config['oss']['llm']['config']['openai_base_url'],memory_key)


def _provision_mem0(arm):
    if arm.resume:
        _check_mem0_resume(arm)
        return
    root, home, env, model = arm.root, arm.home, arm.env, arm.model
    client_key, memory_key = arm.driver_key, arm.memory_key
    inference_route = arm.inference_route
    identity = "pacbench-" + arm.root_hash[:24]
    extraction_url = arm.memory_url
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
from pacbench.providers import provider_module
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
    from pacbench.gateway_client import validate_route
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
