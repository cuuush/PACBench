"""Observe ReMe's stock watched-resource and conversation capture workflow."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pacbench.gateway.observer import PROTOCOL
from pacbench.gateway_client import local_endpoint as local_endpoint
from pacbench.providers import HERMES_TOOLS, validate_native_deployment
from pacbench.workflows.native import native_chat_closed, receipt_lineage
from pacbench.workflows.progress import process_identity
from provider_adapters.reme_resource import inside


class ReMeWitness:
    def __init__(self, root, owner, workspace, receipts, server, plugin, docs, all_docs, ledger):
        self.root,self.owner,self.workspace,self.receipts = root,owner,workspace,receipts
        self.server,self.plugin,self.ledger = server,plugin,ledger
        self.documents = tuple(docs)
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in docs))
        self.expected_names = {doc.filename for doc in all_docs}
        self.sources = {}
        self.conversations = {}
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        if self.owner.provider!='reme' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('ReMe native account or listener identity changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.workspace),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='0.4.2.2',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['reme']),native_processes={'memory':self.server,
                  'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def control_readback(self):
        resources = list((self.workspace/'resource').rglob('*.txt'))
        conversations = list((self.workspace/'session/dialog').glob('*.jsonl'))
        if resources or conversations or any(self.receipts.glob('*.json')):
            raise ValueError('ReMe native store already contains documents or conversation history')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_resource_count':0,'native_capture_count':0})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('ReMe nightly source releases must advance in order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        path = self.workspace/'resource'/datetime.fromisoformat(doc.cutoff).date().isoformat()/doc.filename
        if self.cutoff!=doc.cutoff or raw.get('resource')!=str(path) or path.is_symlink() or not path.is_file():
            raise ValueError('ReMe did not receive its current native resource file')
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'native auto_resource watcher','resource':str(path.relative_to(self.workspace))})

    def document_readback(self, doc, acceptance):
        if self.documents[len(self.sources)]!=doc:
            raise ValueError('ReMe document order differs from the frozen schedule')
        relative = acceptance['request']['resource']
        source = inside(self.workspace,relative)
        deadline = time.monotonic()+600
        while time.monotonic()<deadline:
            for receipt_file in self.receipts.glob('*.json'):
                record = json.loads(receipt_file.read_bytes())
                result = record.get('response') or {}
                matches = [item for item in (result.get('metadata') or {}).get('results',[]) if item.get('path')==relative]
                if not matches:
                    continue
                if len(matches)!=1 or record.get('processing_forced') is not False or matches[0].get('success') is not True:
                    raise ValueError('ReMe scoped resource action did not complete normally')
                item, = matches
                hook = record.get('hook_metadata') or {}
                meta = item['metadata']
                card = inside(self.workspace,meta['path'])
                link = f'[[{relative}]]'
                nodes = [node for node in hook.get('observed_index_nodes',[]) if node.get('path')==meta['path']]
                source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                if (source_hash!=doc.sha256 or not hook.get('index_observation_complete') or len(nodes)!=1
                        or not nodes[0].get('chunk_ids') or nodes[0].get('front_matter',{}).get('source_resource')!=link
                        or datetime.fromisoformat(doc.cutoff).date().isoformat() not in card.relative_to(self.workspace).parts
                        or meta.get('source_resource')!=link or not card.is_file() or link not in card.read_text()):
                    raise ValueError('ReMe native resource, card or searchable index binding differs')
                self.sources[doc.filename] = source_hash
                return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=source_hash,
                    readback={'resource':relative,'card':str(card.relative_to(self.workspace)),
                              'card_sha256':hashlib.sha256(card.read_bytes()).hexdigest(),
                              'native_observation_sha256':hashlib.sha256(receipt_file.read_bytes()).hexdigest(),
                              'indexed_chunk_count':len(nodes[0]['chunk_ids'])})
            time.sleep(.3)
        raise TimeoutError('ReMe native watcher did not expose a completed card and index')

    def source_inventory(self):
        paths = [path for path in (self.workspace/'resource').rglob('*.txt') if path.name in self.expected_names]
        raw = {path.name:path for path in paths}
        if (len(paths)!=len(raw) or set(raw)!=set(self.sources) or any(path.is_symlink() or
                hashlib.sha256(path.read_bytes()).hexdigest()!=self.sources[name] for name,path in raw.items())):
            raise ValueError('ReMe native resource inventory contains future, altered or missing source')
        return list(self.sources)

    def idle(self):
        now = time.monotonic_ns()
        deadline = time.monotonic()+90
        expected = self.owner.witness
        ident = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('ReMe native Hermes observation failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for r in sample['sessions'] for p in r['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=ident or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p['name']!='reme' for p in providers)):
                    raise RuntimeError('ReMe Hermes lifecycle evidence differs from its run-owned gateway')
                active = (sample['native_release_threads'] or any(r['pending_futures'] for r in sample['sessions'])
                          or any(p['unfinished_writes'] for p in providers))
                if (not active and sample['observed_monotonic_ns']>=now
                        and not any(not row.get('finished') and row['accounting_role']!='judge' for row in self.ledger.snapshot())):
                    return dict(gateway_identity=ident,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                readback={'gateway':sample})
            time.sleep(.1)
        raise TimeoutError('ReMe native Hermes work did not drain')

    def native_write_barrier(self, session, answer):
        self.idle()
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (session,)).fetchone()
        capture = self.workspace/'session/dialog'/(self.plugin._scoped_session_id('default',session)+'.jsonl')
        if not capture.is_file() or capture.is_symlink():
            raise ValueError('ReMe persisted capture is missing for the current Hermes session')
        content = capture.read_bytes()
        rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        observed = []
        for row in rows[-2:]:
            parts = row.get('content')
            text = parts if isinstance(parts,str) else ''.join(p.get('text','') for p in parts if p.get('type')=='text')
            observed.append((row.get('role'),text))
        if observed!=[('user',prompt.strip()),('assistant',answer.strip())]:
            raise ValueError('ReMe native conversation capture differs from this turn')
        self.conversations[session] = capture
        return dict(session_id=session,pending_writes=0,
                    readback={'native_dialog':str(capture.relative_to(self.workspace)),
                              'sha256':hashlib.sha256(content).hexdigest(),'message_count':len(rows)})

    def after_native_close(self, sid):
        sample = self.idle()['readback']['gateway']
        if not native_chat_closed(self.owner.home, sid, sample['sessions']):
            raise ValueError('ReMe selected session did not close natively')
        return dict(session_id=sid,pending_writes=0,readback={'native_dialog':str(self.conversations[sid].relative_to(self.workspace))})

    tool_boundaries = staticmethod(lambda _: {})

    def request_lineage(self, requests):
        return receipt_lineage(self.ledger.snapshot(), requests)

    def close(self):
        pass


def check_canary(root):
    """Re-audit the pinned native source, capture, shutdown, and receipts."""
    from pacbench.evidence_store import file_binding as binding
    from pacbench.judging.retained_scope import read_canary_binding as read
    from pacbench.judging.retained_week import (
        audit_document_canary,
        verify_listener_stopped,
    )

    audit = audit_document_canary(root, 'reme')
    root = audit.root
    setup = read(binding(root/'native-server-setup.json'))
    shutdown = read(binding(root/'native-service-stop.json'))
    proof = shutdown['log_slice']
    log = Path(proof['path'])
    if (setup['provider_revision'] != PINS['reme'][1]
            or setup['pid'] != audit.deployment['native_processes']['memory']['pid']
            or setup['pid'] != shutdown['pid'] or shutdown['forced_kill'] or not shutdown['native_shutdown_complete']
            or shutdown['returncode'] not in (0, -15) or log != root/'reme-native-service.private.log'
            or hashlib.sha256(log.read_bytes()[proof['offset']:proof['offset']+proof['length']]).hexdigest()
                != proof['sha256']):
        raise ValueError('ReMe canary lacks its native shutdown proof')
    verify_listener_stopped(setup['endpoint'], 'ReMe')
    return audit.bindings


PINS = {
    "reme": ("https://github.com/agentscope-ai/ReMe.git", "36c23674ad8c599dae20b0687af40acd8ecb990e"),
    "memos": ("https://registry.npmjs.org/@memtensor/memos-local-plugin/-/memos-local-plugin-2.0.21.tgz", "3a3634e4469bf0e22c520954653959e28e6db130371d1a3b171fe7186d5ea539"),
}


def settings(provider: str, root: Path, endpoint: str, model: str) -> dict:
    endpoint = local_endpoint(endpoint)
    if not model.strip():
        raise ValueError("An explicit local model name is required")
    root = root.resolve()
    if provider == "reme":
        return {
            "plugin_name": "reme",
            "plugin_config_path": "hermes-home/reme/config.json",
            "plugin_config": {"mode": "embedded", "workspace_dir": str(root / "reme-workspace"), "reme_config": "default"},
            "environment": {
                "LLM_BACKEND": "openai", "LLM_MODEL_NAME": model,
                "LLM_BASE_URL": endpoint, "LLM_API_KEY": "local-not-a-secret",
            },
        }
    if provider == "memos":
        return {
            "plugin_name": "memtensor",
            "plugin_config_path": "memos-home/config.yaml",
            "plugin_config": {
                "version": 1,
                "llm": {"provider": "openai_compatible", "endpoint": endpoint, "model": model,
                        "apiKey": "", "fallbackToHost": False},
                "embedding": {"provider": "local", "local": {"offline": True}},
                "hub": {"enabled": False}, "telemetry": {"enabled": False},
            },
            "environment": {
                "MEMOS_HOME": str(root / "memos-home"), "MEMOS_HERMES_BRIDGE_MODE": "shared",
                "LLM_PROVIDER": "openai_compatible", "LLM_ENDPOINT": endpoint,
                "LLM_MODEL": model, "LLM_API_KEY": "", "LLM_FALLBACK_TO_HOST": "false",
                "L3_LLM_PROVIDER": "openai_compatible", "L3_LLM_ENDPOINT": endpoint,
                "L3_LLM_MODEL": model, "L3_LLM_API_KEY": "",
                "SKILL_EVOLVER_PROVIDER": "openai_compatible", "SKILL_EVOLVER_ENDPOINT": endpoint,
                "SKILL_EVOLVER_MODEL": model, "SKILL_EVOLVER_API_KEY": "",
                "EMBEDDING_PROVIDER": "local", "EMBEDDING_API_KEY": "",
                "TELEMETRY_ENABLED": "false", "HUB_ENABLED": "false",
            },
        }
    raise ValueError(f"Unsupported provider: {provider}")


def prepare(provider: str, root: Path, source: Path, endpoint: str, model: str) -> dict:
    """Prepare a new run-owned home; refuse to replace existing runtime state."""
    source = source.resolve()
    pin = PINS[provider][1]
    if provider == 'memos':
        from pacbench.provider_versions import verify_memos_package
        package,release=verify_memos_package(Path(__file__).resolve().parents[2])
        if source != package.resolve():
            raise ValueError('Select the verified official MemOS local release package')
        actual=release['sha256']
        plugin=source/'adapters/hermes/memos_provider'
    else:
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        plugin=source/'integrations/hermes_agent'
    if actual != pin:
        raise ValueError(f"Provider source pin differs: expected {pin}, got {actual}")
    cfg = settings(provider, root, endpoint, model)
    if not (plugin / "__init__.py").is_file():
        raise ValueError("Native provider package is missing")
    root = root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    home = root / "hermes-home"
    home.mkdir(mode=0o700)
    plugins = home / "plugins"
    plugins.mkdir(mode=0o700)
    (plugins / cfg["plugin_name"]).symlink_to(plugin, target_is_directory=True)
    path = root / cfg["plugin_config_path"]
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg["plugin_config"], indent=2) + "\n")
    path.chmod(0o600)
    (home / "config.yaml").write_text(json.dumps({"memory": {"provider": cfg["plugin_name"]}}, indent=2) + "\n")
    (home / "config.yaml").chmod(0o600)
    env = {**cfg["environment"], "HERMES_HOME": str(home)}
    report = {"provider": provider, "provider_revision": actual, "environment": env,
              "qualification": "configuration-only", "ingestion_verified": False,
              "fresh_session_recall_verified": False}
    (root / "runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def audit_reme_resource_receipt(result, day, text, source_ids):
    """Require actual native action and indexed source-linked card witnesses."""
    errors=[]
    digest=hashlib.sha256(text.encode()).hexdigest()
    relative=f'resource/{day}/source-{digest}.txt'
    receipt=result.get('native_result',{})
    response=receipt.get('response',{})
    items=response.get('metadata',{}).get('results',[])
    matches=[i for i in items if i.get('path')==relative]
    metadata=(matches[0].get('metadata') or {}) if len(matches)==1 else {}
    nodes=receipt.get('hook_metadata',{}).get('observed_index_nodes',[])
    card=result.get('card_text')
    if (result.get('ingestion_condition')!='native-resource-document-extraction-v1' or
            result.get('resource_path')!=relative or result.get('document_sha256')!=digest or
            result.get('stored_source_sha256')!=digest or result.get('source_ids')!=source_ids or
            result.get('source_readback_exact') is not True or result.get('manual_job_invocations')!=0 or
            result.get('native_defaults') is not True):
        errors.append('ReMe native document delivery/source binding is unproven')
    if (receipt.get('protocol')!='reme-native-resource-observer-v1' or
            receipt.get('processing_forced') is not False or response.get('success') is not True or
            receipt.get('response_sha256')!=hashlib.sha256(json.dumps(response,sort_keys=True,ensure_ascii=False).encode()).hexdigest() or
            len(matches)!=1 or matches[0].get('success') is not True or
            metadata.get('action')!='added' or metadata.get('source_resource')!=f'[[{relative}]]' or
            'error' in (metadata.get('index') or {})):
        errors.append('ReMe native resource processing did not complete')
    if (not isinstance(card,str) or hashlib.sha256(card.encode()).hexdigest()!=result.get('card_sha256') or
            f'[[{relative}]]' not in (card or '') or result.get('card_path')!=metadata.get('path') or
            not str(result.get('card_path','')).startswith(f'daily/{day}/') or
            receipt.get('hook_metadata',{}).get('index_observation_complete') is not True or
            len(nodes)!=1 or nodes[0].get('path')!=result.get('card_path') or not nodes[0].get('chunk_ids') or
            nodes[0].get('front_matter',{}).get('source_resource')!=f'[[{relative}]]'):
        errors.append('ReMe generated memory card/index provenance is unproven')
    return errors
