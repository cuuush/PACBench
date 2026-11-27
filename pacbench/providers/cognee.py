"""Stock Cognee native files and warm conversation provenance for Pizza Week v2."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID

from pacbench.gateway.observer import PROTOCOL
from pacbench.gateway_client import loopback_url as loopback_url
from pacbench.providers import HERMES_TOOLS, validate_native_deployment
from pacbench.workflows.native import NativeAccount, native_chat_closed, receipt_lineage
from pacbench.workflows.progress import process_identity
from provider_adapters.cognee_checkpoint import verify_turn
from provider_adapters.cognee_completion import completed, pipeline_rows, require_idle
from provider_adapters.cognee_local import (
    QualificationError,
    qualify_incremental_document,
)


class CogneeAccount(NativeAccount):
    def submit(self, doc):
        return self.witness.submit(doc)

    def ask(self, prompt, *, at, session_id, on_return=None, on_trace=None):
        surface = super().ask(prompt,at=at,session_id=session_id,on_return=on_return,on_trace=on_trace)
        self.witness.observe_explicit_captures(surface)
        return surface

    def end_chat(self, sid):
        self.witness.prior_close[sid] = [row['id'] for row in pipeline_rows(self.witness.root)]
        return super().end_chat(sid)


class CogneeWitness:
    def __init__(self, root, owner, backend, endpoint, dataset, server, docs, all_docs, ledger):
        self.root,self.owner,self.backend,self.endpoint,self.dataset = root,owner,backend,endpoint,dataset
        self.server,self.ledger = server,ledger
        self.documents = tuple(docs)
        self.cutoffs = list(dict.fromkeys(doc.cutoff for doc in docs))
        self.known_sources = {doc.sha256:doc.filename for doc in all_docs}
        self.sources = {}
        self.turns = {}
        self.explicit_captures = {}
        self.prior_close = {}
        self.pending = {}
        self.cutoff = None

    def verify_deployment(self, condition, identity):
        if self.owner.provider!='cognee' or identity!=str(self.root) or process_identity(self.server['pid'])!=self.server:
            raise ValueError('Cognee native owner or listener changed')
        result = dict(account=identity,condition=vars(condition),store_identity=str(self.root/'cognee-data'),
            synthetic_source_provenance='Committed Pizza Week v2 continuing-account nightly documents',
            observed_version='1.6.8 / Hermes 1.4.3',frontend_identity='Hermes '+self.owner.pins['hermes'],
            observed_patch=self.owner.pins['hermes_isolation_sha256'],effective_model_route=condition.model_route,
            granted_tools=list(HERMES_TOOLS['cognee']),native_processes={'memory':self.server,
                'driver':process_identity(self.owner.witness['pid'])})
        validate_native_deployment(condition,result)
        return result

    def native_documents(self):
        root = self.root/'cognee-data'
        path = self.root/'cognee-system/databases/cognee_db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            rows = db.execute('SELECT id,external_metadata,raw_data_location FROM data').fetchall()
        observed = {}
        for identifier,attribute,location in rows:
            target = Path(urlparse(location).path).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError('Cognee document original lies outside the run-owned store')
            native = str(UUID(identifier))
            if native in observed:
                raise ValueError('Cognee native duplicate document identity')
            observed[native] = (hashlib.sha256(target.read_bytes()).hexdigest(),json.loads(attribute) if attribute else {})
        return observed

    def control_readback(self):
        if self.native_documents():
            raise ValueError('Cognee native store has pre-existing documents or captured conversation')
        return dict(account=str(self.root),chat_count=0,conversation_writes=0,source_files=[],
                    readback={'native_dataset':self.dataset,'native_document_count':0})

    def collection_clock(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index>=len(self.cutoffs) or self.cutoffs[index]!=cutoff:
            raise ValueError('Cognee nightly release is out of chronological order')
        self.idle()
        self.cutoff = cutoff
        return dict(cutoff=cutoff,as_of=cutoff,service_at=datetime.now(timezone.utc).isoformat())

    def prior_native_captures(self):
        by_hash = {}
        for sid, rows in self.turns.items():
            native_session, = {row['native_session'] for row in rows}
            header = f'Session ID: {native_session}\n\n'
            entries = [f"Question: {row['prompt']}\n\nAnswer: {row['answer']}\n\n" for row in rows]
            # Stock session extraction writes the contiguous Q&A after its persist
            # watermark. A single native promotion can contain multiple verified turns.
            for start in range(len(entries)):
                body = header
                for index in range(start, len(entries)):
                    body += entries[index]
                    digest = hashlib.sha256(body.encode()).hexdigest()
                    candidates = by_hash.setdefault(digest, [])
                    candidates.append({'session_id':sid,'native_session':native_session,
                                       'qa_ids':[r['qa_id'] for r in rows[start:index+1]]})
        source_ids = {rec['data_id'] for rec in self.sources.values()}
        native = self.native_documents()
        output = []
        for native_id,(source_hash,meta) in native.items():
            if native_id in source_ids:
                continue
            explicit = self.explicit_captures.get(native_id)
            if explicit and not meta and explicit['source_sha256']==source_hash:
                output.append(explicit)
                continue
            matches = by_hash.get(source_hash, [])
            if meta.get('node_set')!=['user_sessions_from_cache'] or len(matches)!=1:
                raise ValueError('Cognee prior dataset contains a document without attributable native capture')
            output.append({'data_id':native_id,'source_sha256':source_hash,'native_capture_kind':'promoted_qas',**matches[0]})
        return output

    def observe_explicit_captures(self, surface):
        """Bind stock tool-originated documents to their verified stored bytes."""
        for call,result in zip(surface['tool_calls'],surface['tool_results'],strict=True):
            if call['name']!='cognee_remember' or result.get('success') is not True:
                continue
            body = json.loads(result['text'])
            args = call['input']
            text = str(args.get('content') or '').strip()
            if (body.get('result')!='Content stored in Cognee.' or body.get('status') not in {'running','completed'}
                    or (args.get('dataset') or self.dataset)!=self.dataset or not text
                    or call['tool_call_id']!=result['tool_call_id']):
                raise ValueError('Cognee explicit permanent write lacks an exact native tool receipt')
            digest = hashlib.sha256(text.encode()).hexdigest()
            native_ids = [identifier for identifier,(stored,meta) in self.native_documents().items()
                          if stored==digest and not meta and digest not in self.known_sources]
            if len(native_ids)!=1:
                raise ValueError('Cognee explicit tool write lacks a unique native stored document')
            identifier, = native_ids
            path = self.root/'cognee-system/databases/cognee_db'
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                membership = db.execute('SELECT datasets.name FROM data JOIN datasets ON datasets.id=data.dataset_id '
                                        'WHERE data.id=?',(UUID(identifier).hex,)).fetchall()
            if membership!=[(self.dataset,)]:
                raise ValueError('Cognee explicit tool write is outside the bound native dataset')
            self.explicit_captures.setdefault(identifier,{'data_id':identifier,'source_sha256':digest,
                'native_capture_kind':'explicit_remember','session_id':surface['session_id'],
                'tool_call_id':call['tool_call_id']})

    def submit(self, doc):
        if self.cutoff!=doc.cutoff or self.pending or self.documents[len(self.sources)]!=doc:
            raise ValueError('Cognee native file submission is outside the frozen release order')
        before = {r['id'] for r in pipeline_rows(self.root)}
        require_idle(pipeline_rows(self.root))

        def status(*,dataset_id,timeout):
            identifier = str(UUID(dataset_id))
            return self.backend._request('GET',f'/api/v1/datasets/{identifier}/processing-status?pipeline=cognify_pipeline',timeout=timeout)

        source = qualify_incremental_document(self.backend,service_url=self.endpoint,dataset=self.dataset,
            text=doc.text,prior=list(self.sources.values()),processing_status=status,timeout=1800,
            native_captures=self.prior_native_captures())
        started = time.monotonic()
        deadline = started + 1800
        while True:
            operations = pipeline_rows(self.root)
            if completed(operations,before):
                break
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('Cognee native automatic improve did not finish')
            # After per-item qualification, catch an imminent local terminal row
            # without increasing the sustained query rate on a long-running improve.
            time.sleep(.2 if now - started < 1 else 1)
        self.pending[doc.filename] = {**source,'native_operation_completion':[r for r in operations if r['id'] not in before]}
        return dict(native_id=doc.filename,accepted=True,at=doc.cutoff,
                    request={'interface':'stock remember_permanent','dataset':self.dataset,'data_id':source['data_id']})

    def document_readback(self, doc, acceptance):
        observed = self.pending.pop(doc.filename)
        if (observed['data_id']!=acceptance['request']['data_id'] or observed['source_sha256']!=doc.sha256
                or not observed['stored_source_verified'] or not observed['processing_complete']):
            raise ValueError('Cognee native completion differs from the frozen source')
        self.sources[doc.filename] = {'data_id':observed['data_id'],'source_sha256':doc.sha256}
        return dict(native_id=doc.filename,ready=True,at=doc.cutoff,submitted_sha256=doc.sha256,readback=observed)

    def source_inventory(self):
        native = self.native_documents()
        originals = {ident:hashed for ident,(hashed,_) in native.items() if hashed in self.known_sources}
        if originals!={r['data_id']:r['source_sha256'] for r in self.sources.values()}:
            raise ValueError('Cognee native inventory includes a missing, changed or unreleased source')
        return list(self.sources)

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic()+180
        expected = self.owner.witness
        ident = [expected['pid'],expected['start_time']]
        observer = self.owner.home/'reuse-observer.json'
        while time.monotonic()<deadline:
            if (self.owner.home/'reuse-observer-error.json').exists():
                raise RuntimeError('Cognee native Hermes observation failed')
            if observer.is_file():
                sample = json.loads(observer.read_bytes())
                providers = [p for r in sample['sessions'] for p in r['providers'] if p['name']!='builtin']
                if ([sample['pid'],sample['start_time']]!=ident or sample['protocol']!=PROTOCOL
                        or sample['observer_sha256']!=self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p['name']!='cognee' or p['endpoint']!=self.endpoint or p['dataset']!=self.dataset
                               for p in providers)):
                    raise RuntimeError('Cognee native lifecycle observation differs from its run-owned dataset')
                active = (sample['native_release_threads'] or any(r['pending_futures'] for r in sample['sessions'])
                          or any(not p['clean'] for p in providers))
                pending = any(not r.get('finished') and r['accounting_role']!='judge' for r in self.ledger.snapshot())
                if not active and not pending and sample['observed_monotonic_ns']>=since:
                    rows = pipeline_rows(self.root)
                    try:require_idle(rows)
                    except QualificationError:pass
                    else:
                        if any(r.get('outcome') not in (None,'succeeded') for r in rows):
                            raise RuntimeError('Cognee native pipeline failed')
                        return dict(gateway_identity=ident,inflight_requests=0,pending_native_writes=0,native_errors=[],
                                    readback={'gateway':sample})
            time.sleep(.2)
        raise TimeoutError('Cognee native lifecycle did not drain')

    def native_write_barrier(self, sid, answer):
        from cognee_integration_hermes import memory_credit, recall_notice

        sample = self.idle()['readback']['gateway']
        identities = {p['native_session'] for r in sample['sessions'] if r['session_id']==sid
                      for p in r['providers'] if p['name']=='cognee' and p.get('native_session')}
        if len(identities)!=1:
            raise RuntimeError('Cognee native session is not uniquely bound to the Hermes chat')
        native_session, = identities
        path = self.owner.home/'state.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            prompt, = db.execute("SELECT content FROM messages WHERE role='user' AND session_id=? ORDER BY id DESC LIMIT 1",
                                 (sid,)).fetchone()
        text = memory_credit.strip_credit_lines(recall_notice.strip_notice(answer))
        cached = self.backend.get_session(session_id=native_session,timeout=20) or {}
        entries = [q['qa_id'] for q in cached.get('qas',[]) if q.get('question')==prompt and q.get('answer')==text]
        if len(entries)!=1:
            raise RuntimeError('Cognee did not expose the exact persisted native turn')
        self.turns.setdefault(sid,[]).append({'prompt':prompt,'answer':text,'native_session':native_session,'qa_id':entries[0]})
        return dict(session_id=sid,pending_writes=0,readback={'native_session':native_session,'qa_id':entries[0]})

    def after_native_close(self, sid):
        sample = self.idle()['readback']['gateway']
        if not native_chat_closed(self.owner.home, sid, sample['sessions']):
            raise RuntimeError('Cognee selected chat did not close through the native provider')
        outcome = verify_turn({'native_root':str(self.root),'endpoint':self.endpoint,'dataset':self.dataset,
            'verified_qa_history':self.turns[sid],'prior_pipeline_ids':self.prior_close[sid],'source_documents':{rec['data_id']:rec['source_sha256'] for rec in self.sources.values()}},
            SimpleNamespace(session_id=sid, **self.turns[sid][-1]),sample)
        return dict(session_id=sid,pending_writes=0,readback=outcome)

    tool_boundaries = staticmethod(lambda _: {})

    def request_lineage(self, requests):
        return receipt_lineage(self.ledger.snapshot(), requests)

    def close(self):
        pass


def check_canary(root):
    """Re-audit the retained native source, session and receipt bindings."""
    from pacbench.evidence_store import file_binding as binding
    from pacbench.evidence_store import file_sha as sha
    from pacbench.judging.retained_scope import read_canary_binding as read
    from pacbench.judging.retained_week import (
        audit_document_canary,
        verify_listener_stopped,
    )

    audit = audit_document_canary(root, 'cognee')
    root = audit.root
    setup = read(binding(root/'native-server-setup.json'))
    shutdown = read(binding(root/'native-service-stop.json'))
    server = audit.deployment['native_processes']['memory']
    plugin = root/'hermes-home/plugins/cognee/cognee_integration_hermes/provider.py'
    if ('inline session promotion' not in audit.condition['deployment']
            or setup['provider_revision'] != PINS['cognee']['commit']
            or setup['health']['version'] != '1.6.8' or setup['pid'] != server['pid']
            or setup['plugin_source_sha256'] != sha(plugin)
            or shutdown != {'server': server, 'state': 'verified-after-exit'}):
        raise ValueError('Cognee canary lacks its native installation or shutdown proof')
    verify_listener_stopped(setup['endpoint'], 'Cognee')
    return audit.bindings


PINS = {
    'holographic': {
        'repository': 'https://github.com/itpartypattaya/hermes-plugin-holographic',
        'commit': '05e890b461dc3e6390982cb21c5b471a6ed3d8b4',
        'capture': 'agent-tools-or-opt-in-user-sentence-extraction',
        'document_ingestion': False,
    },
    'cognee': {
        'repository': 'https://github.com/topoteretes/cognee-integrations',
        'commit': 'a176248914fdb32a78354854b300174203b15a5c',
        'plugin_subdirectory': 'integrations/hermes-agent',
        'backend_requirement': 'cognee==1.6.8',
        'capture': 'typed-session-entries-and-session-end-graph-promotion',
        'document_ingestion': True,
    },
    'memobase': {
        'repository': 'https://github.com/fred0m/hermes-memobase-provider',
        'commit': '4e8e447966acefaea372395e2db96ca0d0127f18',
        'capture': 'paired-turn-chat-buffer-and-session-end-flush',
        'document_ingestion': False,
    },
}


def setup_plan(provider: str, root: Path, run_id: str, *, service_url: str | None = None) -> dict:
    if provider not in PINS:
        raise ValueError('Unknown provider')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
        raise ValueError('run_id must be a non-path identifier of at most 80 characters')
    root = root.expanduser().resolve()
    scope = 'pacbench_' + hashlib.sha256(run_id.encode()).hexdigest()[:20]
    home = root / provider / 'hermes-home'
    plan = {
        'protocol': 'local-provider-qualification-v1', 'provider': provider,
        'run_id': run_id, 'pin': dict(PINS[provider]),
        'qualification': 'not-run',
        'document_condition': 'candidate' if PINS[provider]['document_ingestion'] else 'excluded',
        'root': str(root / provider),
        'environment': {'HERMES_HOME': str(home)},
        'hermes_config': {'memory': {'provider': provider}},
        'required_evidence': [
            'actual loaded provider revision (not only installed revision)',
            'lossless source delivery with ownership and timestamps',
            'native processing completion, not only accepted writes',
            'fresh-session saved model input containing actual prefetch output',
            'all provider tool calls and failures',
            'all extraction/embedding/reranking endpoints and optional cloud fallbacks',
        ],
    }
    if provider == 'cognee':
        if service_url:
            raise ValueError('Cognee plan uses native local mode; do not select remote mode')
        plan['environment'].update({
            'COGNEE_DATA_ROOT': str(root / provider / 'data'),
            'COGNEE_SYSTEM_ROOT': str(root / provider / 'system'),
            'COGNEE_PLUGIN_DATASET': scope,
            'COGNEE_PLUGIN_RECALL_MODE': 'pinned',
        })
        plan['unset_environment'] = ['COGNEE_BASE_URL', 'COGNEE_SERVICE_URL', 'COGNEE_API_KEY']
        plan['configuration'] = {
            'embedded': False, 'service_url': '', 'dataset': scope,
            'recall_mode': 'pinned',
        }
        plan['ingestion'] = {
            'candidate': 'native-raw-document-remember',
            'api': '/api/v1/remember',
            'requires': 'native dataset pipeline status and stored raw-content verification',
        }
        plan['blockers'] = [
            'Isolated native runtime/server, unique local port and inference configuration must be provisioned',
            'Document connector and native completion witnesses still require live qualification',
        ]
    elif provider == 'memobase':
        url = loopback_url(service_url or 'http://127.0.0.1:8019/api/v1')
        plan['environment'].update({
            'MEMOBASE_BASE_URL': url, 'MEMOBASE_USER_NAME': scope,
            'MEMOBASE_TIMEZONE': 'America/New_York',
        })
        plan['configuration'] = {'timezone': 'America/New_York'}
        plan['blockers'] = [
            'Self-hosted PostgreSQL/pgvector, Redis and API must be isolated per run',
            'Native plugin captures paired user/assistant turns; no artificial assistant turns allowed',
            'Server accepts doc blobs but inspected dispatcher extracts only chat/summary blobs',
            'Excluded from the document benchmark until supported document extraction exists',
        ]
    else:
        if service_url:
            raise ValueError('Holographic requires no service URL')
        plan['hermes_config']['plugins'] = {'hermes-memory-store': {
            'db_path': str(root / provider / 'memory_store.db'),
            'embeddings': 'off', 'rerank': 'off',
        }}
        plan['blockers'] = [
            'Older Hermes bundles override the installed standalone provider of the same name',
            'No documented raw-document ingestion; do not seed extracted facts to manufacture support',
            'Excluded from the document benchmark; conversational exposure is not a substitute',
        ]
    return plan
