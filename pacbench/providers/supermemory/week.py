"""Run the frozen Week through one native Supermemory store and one warm Hermes owner."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import quote
from urllib.request import Request

from pacbench import harness
from pacbench.workflows import capability as cap
from ...canary_capability import binding, digest, sha
from ...catalog_plugins import verify_retained_catalog_provider
from ...checkpoint_clock import CheckpointClock, atomic
from ...context_isolation import validate_recall_environment
from ...execution_capability import selected
from ...gateway.transport import open_without_redirect
from ...gateway_client import client_settings
from ...gateway_reuse import PROTOCOL as OBSERVER_PROTOCOL, PersistentGateway
from ...live_dispatch import CONDITION, process_identity
from ...observational_receipts import COHORT_STOP, ENV as JOURNAL
from ...openrouter_direct import direct_environment
from ...openrouter_route import ENDPOINT, MODEL, PROFILE_NAME
from ...openrouter_run import fetch_catalog
from ...providers import provider_module
from .question_admission import PROTOCOL, projects, terminal_response
from .source_admission import BINARY_SHA, PROFILE, PROVIDER
from .source_effect import BOUNDARY, NATIVE_ID
from . import explicit_store as explicit
from .source_qualify import account_lock, check_runtime, check_stops, verify_prepaid
from .week_admission import SCOPE, WEEK_RUN, plan, prepare_driver, verified_final_response
from .worker import ROUND_PROTOCOL as WORKER_ROUND_PROTOCOL
from .worker import CHUNK_PROTOCOL as CHUNK_READ_PROTOCOL, capture_anchor
from .observation import PENDING, read_release_processing, stock_temporal_metadata, verify_native_version, verify_release_inventory
from .. import run_environment
from pacbench.workflows.native import NativeAccount, WarmHermes
from pacbench.workflows.recording import receipt_summary
from pacbench.workflows.lifecycle import RunFailures
from pacbench.workflows.runner import Condition
from pacbench.campaigns.pizza_week import SPEC
from pacbench.workflows.tracks import HERMES_TOOLS


class StockWeekWitness:
    def __init__(self, root, scope, owner, sdk, env, ledger):
        self.root, self.scope, self.owner, self.sdk, self.ledger = root, scope, owner, sdk, ledger
        self.namespace = scope['namespace']
        self.endpoint, self.token = env['SUPERMEMORY_BASE_URL'], env['SUPERMEMORY_API_KEY']
        self.setup = json.loads((root / 'native-server-setup.json').read_text())
        self.documents = plan()[0]
        self.accepted, self.ready, self.captures = {}, {}, {}
        self.explicit_writes = {}
        self.cutoff, self.batch = None, ()
        with run_environment({'HERMES_HOME': str(owner.home)}):
            self.plugin = provider_module(PROVIDER)

    def read_native(self, path, body=None):
        headers = {'Authorization': 'Bearer ' + self.token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.endpoint + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers)
        with open_without_redirect(request, timeout=5) as response:
            return json.load(response)

    def catalog(self, expected):
        expected = set(expected)
        result = self.read_native('/v3/documents/list', dict(containerTags=[self.namespace], includeContent=False,
                                  page=1, limit=100))
        try:
            rows, page = result['memories'], result['pagination']
            native_ids = {row['id'] for row in rows}
            explicit_ids = set(self.explicit_writes)
            by_public = {row['customId']: row for row in rows if row['id'] not in explicit_ids}
            if (len(by_public) + len(explicit_ids) != len(rows) or len(native_ids) != len(rows)
                    or by_public.keys() != expected or not explicit_ids <= native_ids
                    or page['currentPage'] != 1 or page['totalPages'] not in ({1} if rows else {0, 1})
                    or any(row['containerTags'] != [self.namespace] or not NATIVE_ID.fullmatch(row['id']) for row in rows)
                    or any(row.get('customId') is not None for row in rows if row['id'] in explicit_ids)):
                raise ValueError('Supermemory authenticated inventory differs from its exact source, capture and tool documents')
        except (KeyError, TypeError, ValueError):
            self.reject_inventory('v3', expected, result)
            raise
        return by_public

    def reject_inventory(self, api, expected, response):
        explicit.write_once(self.root / 'inventory-rejections' / f'{uuid4().hex}.json',
                            dict(api=api, namespace=self.namespace, expected=sorted(expected),
                                 authorized_tool_ids=sorted(self.explicit_writes), response=response))

    def register_tool_documents(self, ids):
        proofs = []
        for identifier in ids:
            if (identifier in self.explicit_writes or identifier in self.accepted.values()
                    or any(entry['native_id'] == identifier for entry in self.captures.values())):
                raise ValueError('Supermemory store reused a known native document identity')
            entry = explicit.read_store(self.root, identifier, self.namespace)
            proof = self.read_tool_document(entry)
            explicit.write_once(self.root / 'explicit-ready' / f'{identifier}.json', proof)
            self.explicit_writes[identifier] = proof
            proofs.append(proof)
        return proofs

    def read_tool_document(self, entry):
        from supermemory import APITimeoutError

        identifier = entry['native_id']
        path = '/v3/documents/' + quote(identifier, safe='')
        payload = entry['payload']
        # The pinned v5 Add consumes sm_source as a routing field. Its absence
        # from the native document does not remove it from the POST witness.
        expected_metadata = {k: v for k, v in payload['metadata'].items() if k != 'sm_source'}
        expected_metadata.update(stock_temporal_metadata(payload['date']))
        document = SimpleNamespace(filename=None)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            check_stops(self.root.parent.parent)
            try:
                response = self.sdk.documents.get(
                    self.namespace, identifier, include='chunks',
                    request_options={'max_retries': 0, 'timeout': min(5, deadline - time.monotonic())})
            except APITimeoutError:
                continue  # Only the same idempotent read, within the existing deadline.
            wire = response.model_dump(mode='json', by_alias=True)
            state = wire['system']['status']
            if wire['id'] != identifier or state not in PENDING | {'done'}:
                raise ValueError('Supermemory tool-created native document failed or changed identity')
            if state == 'done':
                native = self.read_native(path)
                if wire.get('content') != payload['content'] or native.get('metadata') != expected_metadata:
                    self.reject_inventory('explicit-readback', [identifier], dict(native=native, wire=wire))
                    raise ValueError('Supermemory tool-created document differs from the acknowledged payload')
                anchor = capture_anchor(native, wire, self.read_native(path), document, native_id=identifier,
                                        namespace=self.namespace)
                if anchor['revision'] != 1:
                    raise ValueError('Supermemory tool-created document is outside its initial revision')
                return dict(native_id=identifier, status=state, store_sha256=digest(entry),
                            question_id=entry['question_id'], tool_call_id=entry['tool_call_id'], chunk_anchor=anchor)
            time.sleep(.25)
        raise TimeoutError('Supermemory tool-created document did not finish native processing')

    def verify_deployment(self, condition, identity):
        verify_retained_catalog_provider(PROVIDER, self.owner.home)
        service = self.setup['process_identity']
        if (identity != str(self.root) or self.setup['installation']['sha256'] != BINARY_SHA
                or self.setup['resumed_store'] is not False or process_identity(service['pid']) != service):
            raise ValueError('Supermemory Week requires a fresh pinned native store and owned listener')
        return dict(account=identity, condition=vars(condition), store_identity=str(self.root / 'supermemory-data'),
                    synthetic_source_provenance='Frozen Pizza Week v2 nightly exports', observed_version=condition.version,
                    frontend_identity=condition.frontend, observed_patch=condition.gateway_patch,
                    effective_model_route=condition.model_route, granted_tools=list(condition.tool_names),
                    native_processes={'memory': service, 'driver': process_identity(self.owner.witness['pid'])})

    def control_readback(self):
        idle = self.idle()
        self.catalog(())
        verify_release_inventory(self.sdk, self.namespace, (), deadline=time.monotonic() + 10,
                                 on_rejection=self.reject_inventory)
        if idle['readback']['sessions']:
            raise ValueError('Supermemory Week did not start with an empty native Hermes account')
        return dict(account=str(self.root), chat_count=0, conversation_writes=0, source_files=[],
                    readback={'empty_authenticated_namespace': True, 'empty_warm_gateway': True})

    def collection_clock(self, cutoff):
        self.idle()
        self.batch = tuple(doc for doc in self.documents[len(self.ready):] if doc.cutoff == cutoff)
        if not self.batch or self.batch[0] != self.documents[len(self.ready)] or len(self.accepted) != len(self.ready):
            raise ValueError('Supermemory release is not the next frozen nightly batch')
        self.cutoff = cutoff
        return dict(cutoff=cutoff, as_of=cutoff, service_at=datetime.now(timezone.utc).isoformat())

    def acceptance(self, raw, doc):
        response = raw.http_response
        request = response.request
        ack = raw.parse()
        body = json.loads(request.content)
        if (len(self.accepted) >= len(self.documents) or self.documents[len(self.accepted)] != doc
                or doc.cutoff != self.cutoff or ack.id != doc.filename or ack.status not in PENDING | {'done'}
                or request.method != 'POST' or not response.is_success
                or str(request.url) != self.endpoint + '/ns/' + quote(self.namespace, safe='') + '/document'
                or body != dict(id=doc.filename, content=doc.text, date=doc.cutoff)):
            raise ValueError('Supermemory stock add differs from the next frozen source document')
        native = self.catalog([*self.accepted, doc.filename, *self.captures])[doc.filename]['id']
        atomic(self.root / 'source-membership' / (native + '.json'), dict(native_id=native, public_id=doc.filename,
               submitted_sha256=doc.sha256, cutoff=doc.cutoff, namespace=self.namespace))
        self.accepted[doc.filename] = native
        return dict(native_id=doc.filename, accepted=True, at=doc.cutoff,
                    request={'namespace': self.namespace, 'native_id': native, 'body_sha256': sha256(request.content).hexdigest(),
                             'submitted_date': doc.cutoff, 'dreaming': 'native default'})

    def document_readback(self, doc, acceptance):
        if doc.filename not in self.ready:
            if any(item.filename not in self.accepted for item in self.batch):
                raise ValueError('Supermemory cannot observe readiness before the entire nightly release is accepted')

            def verify(item, wire):
                native = self.accepted[item.filename]
                row = self.read_native('/v3/documents/' + quote(native, safe=''))
                version = verify_native_version(row, item, native_id=native, namespace=self.namespace, wire=wire)
                if version != 1:
                    raise ValueError('Supermemory source document is not its accepted initial revision')
                return version

            proof = read_release_processing(self.sdk, self.namespace, self.batch, deadline=time.monotonic() + 180,
                       other_ids=(*self.ready, *self.captures, *self.explicit_writes), native_readback=verify,
                       on_rejection=self.reject_inventory,
                       before_read=lambda: check_stops(self.root.parent.parent))
            self.ready.update(proof['documents'])
        if acceptance['request']['native_id'] != self.accepted[doc.filename]:
            raise ValueError('Supermemory readiness is outside the witnessed native add')
        return dict(native_id=doc.filename, ready=True, at=doc.cutoff, submitted_sha256=doc.sha256,
                    readback={'native_id': self.accepted[doc.filename], **self.ready[doc.filename]})

    def source_inventory(self):
        names = [*self.ready, *self.captures]
        rows = self.catalog(names)
        if any(rows[name]['id'] != self.accepted[name] for name in self.ready):
            raise ValueError('Supermemory source document identity changed before driver dispatch')
        if any(rows[name]['id'] != proof['native_id'] for name, proof in self.captures.items()):
            raise ValueError('Supermemory conversation ID changed')
        verify_release_inventory(self.sdk, self.namespace, [*names, *self.explicit_writes],
                                 deadline=time.monotonic() + 10, on_rejection=self.reject_inventory)
        for identifier, proof in self.explicit_writes.items():
            current = self.read_native('/v3/documents/' + quote(identifier, safe=''))
            anchor = proof['chunk_anchor']
            if ((current.get('id'), current.get('customId'), current.get('containerTags'), current.get('status'),
                 current.get('latestRevision'), current.get('activeContentUpdateId'))
                    != (identifier, None, [self.namespace], 'done', anchor['revision'], anchor['publication'])):
                self.reject_inventory('explicit-inventory', [identifier], current)
                raise ValueError('Supermemory tool-created document changed since its verified version')
        evidence = [dict(name=d.filename, sha256=self.ready[d.filename]['sha256'], cutoff=d.cutoff)
                    for d in self.documents if d.filename in self.ready]
        atomic(self.root / 'ready-inventory.json', dict(namespace=self.namespace, sources=evidence))
        return [doc['name'] for doc in evidence]

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic() + 45
        ident = [self.owner.witness['pid'], self.owner.witness['start_time']]
        observer = self.owner.home / 'reuse-observer.json'
        while time.monotonic() < deadline:
            check_stops(self.root.parent.parent)
            if (self.owner.home / 'reuse-observer-error.json').exists():
                raise RuntimeError('Supermemory warm native observer failed')
            if observer.is_file():
                sample = json.loads(observer.read_text())
                providers = [p for s in sample['sessions'] for p in s['providers'] if p['name'] != 'builtin']
                if ([sample['pid'], sample['start_time']] != ident or sample['protocol'] != OBSERVER_PROTOCOL
                        or sample['observer_sha256'] != self.owner.pins['reuse_observer_sha256'] or sample['native_failures']
                        or any(p['name'] != PROVIDER or p['endpoint'] != self.endpoint or p['namespace'] != self.namespace
                               for p in providers)):
                    raise ValueError('Supermemory warm observer differs from its native gateway or namespace')
                active = (sample['native_release_threads'] or any(s['pending_futures'] for s in sample['sessions'])
                          or any(p['workers'] for p in providers))
                if (not active and sample['observed_monotonic_ns'] >= since
                        and not self.ledger.has_active_attempts()):
                    return dict(gateway_identity=ident, pending_native_writes=0, inflight_requests=0,
                                native_errors=[], readback=sample)
            time.sleep(.1)
        raise TimeoutError('Supermemory native work did not drain with the chat open')

    def capture(self, sid, expected, *, anchor_chunks=False):
        from supermemory import APITimeoutError, NotFoundError

        public = self.plugin._capture_doc_id(sid)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            check_stops(self.root.parent.parent)
            try:
                response = self.sdk.documents.get(
                    self.namespace, public, request_options={'max_retries': 0, 'timeout': min(5, deadline - time.monotonic())})
            except (NotFoundError, APITimeoutError):
                time.sleep(.25)
                continue
            wire = response.model_dump(mode='json', by_alias=True)
            state, meta = wire['system']['status'], wire.get('metadata') or {}
            if wire['id'] != public or state not in PENDING | {'done'}:
                raise ValueError('Supermemory native chat document failed or changed identity')
            if state == 'done' and wire.get('content') == expected:
                if (meta.get('session_id'), meta.get('type'), meta.get('docId')) != (sid, 'conversation', public):
                    raise ValueError('Supermemory native capture is outside the authored chat')
                native_id = self.catalog({*self.ready, *self.captures, public})[public]['id']
                native_path = '/v3/documents/' + quote(native_id, safe='')
                native = self.read_native(native_path)
                document = SimpleNamespace(filename=public)
                rev = verify_native_version(native, document, native_id=native_id, namespace=self.namespace, wire=wire)
                anchor = {}
                if anchor_chunks:
                    snapshot = self.sdk.documents.get(self.namespace, public, include='chunks',
                                      request_options={'max_retries': 0, 'timeout': 5}).model_dump(mode='json', by_alias=True)
                    if snapshot.get('id') != public or snapshot.get('content') != expected:
                        raise ValueError('Supermemory chunk readback differs from the captured conversation')
                    anchor['chunk_anchor'] = capture_anchor(native, snapshot, self.read_native(native_path), document,
                                                             native_id=native_id, namespace=self.namespace)
                return dict(id=public, native_id=native_id, status=state, revision=rev,
                            capture_sha256=sha256(expected.encode()).hexdigest(),
                            dreaming_status=wire['system'].get('dreamingStatus'), **anchor)
            time.sleep(.25)
        raise TimeoutError('Supermemory stock processing did not expose the exact conversation document')

    def read_chat(self, sid, answer):
        with closing(sqlite3.connect((self.owner.home / 'state.db').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            history = [dict(r) for r in db.execute('SELECT role,content FROM messages WHERE session_id=? ORDER BY id', (sid,))]
        turns = self.plugin._turns_from_messages(history)
        if not turns or turns[-1]['assistant'] != self.plugin._clean_text_for_capture(answer):
            raise ValueError('Supermemory stock capture differs from the completed native answer')
        return '\n\n'.join(self.plugin._format_turn(t['user'], t['assistant']) for t in turns)

    def native_write_barrier(self, sid, answer):
        self.idle()
        expected = self.read_chat(sid, answer)
        seen = self.capture(sid, expected, anchor_chunks=True)
        old = self.captures.get(seen['id'])
        if old and (old['native_id'] != seen['native_id'] or old['revision'] >= seen['revision']):
            raise ValueError('Supermemory continuation did not publish a new revision of the same capture document')
        self.captures[seen['id']] = {**seen, 'expected': expected}
        return dict(session_id=sid, pending_writes=0, readback=seen)

    def after_native_close(self, sid):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            sample = self.idle()['readback']
            matching = [s for s in sample['sessions'] if s['session_id'] == sid]
            with closing(sqlite3.connect((self.owner.home / 'state.db').as_uri() + '?mode=ro', uri=True)) as db:
                ended = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?', (sid,)).fetchone()
            if (ended and ended[0] and ended[1] == 'cli_close' and matching
                    and all(s['shutdown_returned'] and s['clean'] for s in matching)):
                break
            time.sleep(.1)
        else:
            raise ValueError('Supermemory native shutdown was not witnessed for the selected chat')
        saved = self.captures[self.plugin._capture_doc_id(sid)]
        observed = self.capture(sid, saved['expected'])
        if any(saved[key] != observed[key] for key in ('native_id', 'revision', 'capture_sha256')):
            raise ValueError('Supermemory capture changed while closing its native chat')
        return dict(session_id=sid, pending_writes=0, readback=observed)

    @staticmethod
    def tool_boundaries(_):
        return {}

    def request_lineage(self, requests):
        by_id = {row['id']: row for row in self.ledger.snapshot()}
        selected = [by_id[req['request_id']] for req in requests]
        if any(row['request_sha256'] != request['canonical_request_sha256'] or row['accounting_role'] != 'hermes'
               or not row.get('finished') for request, row in zip(requests, selected, strict=True)):
            raise ValueError('Supermemory answer wire capture differs from its prepared receipts')
        return [dict(id=row['id'], parent=None, operation=row['dispatch_context']['operation_id'], origin='unknown')
                for row in selected]

    def close(self):
        pass  # The outer controller owns both native processes.


class StockWeekAccount(NativeAccount):
    def capture_answer(self, prompt, *, at, session_id, on_return=None):
        prepare_driver(self.witness.scope, prompt, at, session_id)
        return super().capture_answer(prompt, at=at, session_id=session_id, on_return=on_return)

    def observe_answer(self, trace):
        selected = {r['id']: r for r in self.witness.ledger.snapshot()}
        reqs = [selected[r['request_id']] for r in trace['model_requests']]
        if not all(terminal_response(row) for row in reqs[:-1]) or not verified_final_response(reqs[-1], trace['answer']):
            raise ValueError('Supermemory final native answer or preceding tool response is unverified')
        slot = reqs[-1]['workflow_slot']
        writes = self.witness.register_tool_documents(slot['explicit_store_ids'])
        surface = super().observe_answer(trace)
        surface['write_barrier']['explicit_writes'] = writes
        readback = surface['write_barrier']['readback']
        history = self.witness.root / 'capture-membership' / readback['native_id']
        history.mkdir(mode=0o700, exist_ok=True)
        record = history / (str(readback['revision']) + '.json')
        if record.exists():
            raise ValueError('Supermemory captured revision is already bound')
        atomic(record, dict(
            namespace=self.witness.namespace, native_id=readback['native_id'], revision=readback['revision'],
            question_id=slot['question_id'], session_id=surface['session_id'], readback=readback))
        return surface


def run_native(root, scope, env, ledger, marker):
    from supermemory import Supermemory
    from pacbench.campaigns.pizza_week import compile_pizza_week
    from pacbench.workflows.runner import run_compiled_workflow

    home = root / 'hermes-home'
    compiled = compile_pizza_week(seven_daily_documents=True)
    first = compiled.turns[0]
    tools = HERMES_TOOLS[PROVIDER]
    child = harness.turn_env(compiled.documents[0].cutoff, home, ','.join(tools), {
        **env, 'PACBENCH_WORKFLOW_CAPTURE': PROTOCOL, 'PACBENCH_API_INPUT_TRACE': str(root / 'api-inputs.jsonl'),
        'PACBENCH_PROVIDER_ONLY': '1', 'HERMES_ENABLE_PROJECT_PLUGINS': '0', 'OPENROUTER_API_KEY': marker,
        'TZ': 'America/New_York',
    })
    clock = CheckpointClock(home, first.at, scope['spec_sha256'])
    with TemporaryDirectory(prefix='pacbench-supermemory-week-chat-') as cwd, closing(Supermemory(
            base_url=env['SUPERMEMORY_BASE_URL'], api_key=env['SUPERMEMORY_API_KEY'], max_retries=0)) as sdk:
        with PersistentGateway(home, harness.ISOLATED_BIN.parent / 'python', child, cwd=cwd, provider=PROVIDER,
                               checkpoint=scope['spec_sha256'], observation_clock=clock) as owner:
            w = owner.witness
            atomic(root / 'question-gateway.json', dict(process=process_identity(w['pid']), controller=scope['controller'],
                   witness={k: w[k] for k in ('pid', 'start_time', 'process_adoption')}))
            witness = StockWeekWitness(root, scope, owner, sdk, env, ledger)
            frontend = WarmHermes(owner, clock, witness.idle)
            account = StockWeekAccount(str(root), PROVIDER, scope['namespace'], sdk.with_raw_response, frontend, witness)
            return run_compiled_workflow(compiled, _stock_week_condition(owner), account, root / 'workflow')


def _stock_week_condition(owner):
    return Condition(PROVIDER, 'nightly-document', 'continuing-account', 'Hermes ' + owner.pins['hermes'],
                     MODEL, ENDPOINT, 'Stock Supermemory with seven chronological daily source documents',
                     'local server 0.0.9 / SDK 5.0.1',
                     owner.pins['hermes_isolation_sha256'], 'Physical provider clock and authored Hermes clock',
                     HERMES_TOOLS[PROVIDER])


def verify_daily_execution(condition, account, directory, docs, basis, manifest):
    """Bind the shared runner to the stock launcher's native account and verified scope."""
    if (not isinstance(account, StockWeekAccount) or not isinstance(account.witness, StockWeekWitness)
            or not isinstance(account.frontend, WarmHermes) or not os.environ.get(cap.ENV)
            or os.environ.get('PACBENCH_SUPERMEMORY_STOCK_SOURCE') != '1'):
        raise ValueError('native seven daily files require a qualified stock Supermemory Week account and scope')
    scope = cap.load()
    witness = account.witness
    directory = Path(directory).absolute()
    if (scope.get('admission') != SCOPE or witness.scope != scope or process_identity() != scope['controller']
            or str(witness.root) != scope['root'] or account.identity != scope['root']
            or account.provider != PROVIDER or account.scope != scope['namespace']
            or witness.namespace != scope['namespace'] or account.frontend.owner is not witness.owner
            or directory != witness.root / 'workflow' or directory != directory.resolve()
            or condition != _stock_week_condition(witness.owner)
            or witness.documents != docs or scope['frozen_inputs'] != basis or scope['contract'] != manifest):
        raise ValueError('native seven daily files differ from the bound stock Supermemory Week account, scope or plan')


def run(campaign, checkpoint, plugin_home):
    from provider_adapters.supermemory_local import start
    from pacbench.workflows.recording import summarize

    campaign, checkpoint, plugin_home = [Path(p).resolve(strict=True) for p in (campaign, checkpoint, plugin_home)]
    root = campaign / WEEK_RUN / PROVIDER
    began = time.monotonic()
    key = os.environ['OPENROUTER_API_KEY']
    if PROFILE_NAME != PROFILE or selected() is not None or any(os.environ.get(name) for name in (
            JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_JUDGE_OUTPUT_RECOVERY')):
        raise ValueError('Supermemory Week requires an isolated Luna process')
    with account_lock(campaign) as lock:
        check_stops(campaign)
        if root.parent.exists():
            raise RuntimeError('Supermemory Week already owns durable state; no automatic replay')
        _, _, basis, contract = plan()
        validate_recall_environment()
        verify_retained_catalog_provider(PROVIDER, plugin_home)
        check_runtime()
        fetch_catalog(profile=PROFILE)
        confirmation, credit, prepaid = verify_prepaid(checkpoint, key)
        root.parent.mkdir(mode=0o700)
        root.mkdir(mode=0o700)
        for name in ('prepaid-verification', 'source-membership', 'capture-membership', 'answer-admissions',
                     'explicit-tool-operations', 'explicit-adds', 'explicit-ready', 'inventory-rejections', 'fallback-home'):
            (root / name).mkdir(mode=0o700)
        credit_path = root / 'prepaid-verification' / (prepaid['receipt_sha256'] + '.private.json')
        with os.fdopen(os.open(credit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(credit)
        scope = dict(admission=SCOPE, protocol=PROTOCOL, root=str(root), provider=PROVIDER, run_id=WEEK_RUN, canary=False,
                     namespace=f'supermemory-data-{WEEK_RUN}-{PROVIDER}', contract=contract, frozen_inputs=basis,
                     native_effect=BOUNDARY, worker_round_protocol=WORKER_ROUND_PROTOCOL,
                     chunk_read_protocol=CHUNK_READ_PROTOCOL, explicit_store_protocol=explicit.PROTOCOL, prepaid=prepaid,
                     auto_recharge_evidence=confirmation, controller=process_identity(),
                     lock_fd=lock, projects=projects(root), spec_sha256=sha(SPEC))
        atomic(root / 'workflow-scope.json', scope)
        settings = {cap.ENV: json.dumps(binding(root / 'workflow-scope.json')), JOURNAL: str(root / 'receipts.jsonl'),
                    COHORT_STOP: str(cap.campaign_stop(root, nested_run=True)), 'PACBENCH_STOP_FILE': str(root / 'STOP'),
                    'PACBENCH_DIRECT_STOP_ON_429': str(root / 'STOP'), 'PACBENCH_DISPATCH_TRACKING': CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1', 'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768', 'PACBENCH_SUPERMEMORY_STOCK_SOURCE': '1',
                    'PACBENCH_SUPERMEMORY_STOCK_ROUNDS': '1', 'PACBENCH_SUPERMEMORY_STOCK_CHUNKS': '1',
                    explicit.ENV: explicit.PROTOCOL}
        result = dict(state='incomplete', condition=SCOPE, cleanup='unverified', contract=contract)

        failures = RunFailures(result)
        ledger = None
        with failures.record('setup'), run_environment(settings):
            ledger = cap.budget()
            with failures.record('direct_environment'), direct_environment(key), failures.record('setup'):
                home = root / 'hermes-home'
                base, marker = client_settings(scope['projects'][PROVIDER]['driver'])
                harness.provision_home(home, provider_plugin_src=plugin_home / 'plugins' / PROVIDER,
                                       provider_name=PROVIDER, model=MODEL, model_provider='openrouter', model_base_url=base)
                shutil.copyfile(plugin_home / 'plugins' / '.install-metadata.json',
                                home / 'plugins' / '.install-metadata.json')
                env = {'HOME': str(root / 'fallback-home')}
                base, memory = client_settings(scope['projects'][PROVIDER]['memory'])
                stop = start(root, env, MODEL, base, memory)
                try:
                    with failures.record('native_execution'):
                        result['execution'] = run_native(root, scope, env, ledger, marker)
                    with failures.record('transport_drain'):
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline and ledger.has_active_attempts():
                            time.sleep(.25)
                finally:
                    with failures.record('native_shutdown'):
                        stop()

        result['elapsed_seconds'] = time.monotonic() - began
        if ledger is not None:
            with failures.record('receipt_summary'):
                rows = ledger.snapshot()
                result.update(unsettled_transports=sum(not r.get('finished') for r in rows),
                              terminal_sse_observed=sum((r.get('response_validation') or {}).get('sse_done_observed') is True for r in rows))
                result['billing'] = receipt_summary(root, rows, WEEK_RUN)
        journal, shutdown = root / 'workflow/workflow.jsonl', root / 'native-service-stop.json'
        with failures.record('workflow_summary'):
            if journal.is_file():
                result['workflow'] = summarize(journal)
        with failures.record('shutdown_receipt'):
            if shutdown.is_file():
                result['cleanup'] = json.loads(shutdown.read_text())['state']
        if (failures.primary is None and 'execution' in result and result['cleanup'] == 'verified-inventory-stopped'
                and result.get('unsettled_transports') == 0 and not (root / 'STOP').exists()
                and result.get('workflow', {}).get('coverage', {}).get('verified_turns') == contract['turns']):
            result['state'] = 'stock-week-native-complete-ungraded'
        with failures.record('result_write'):
            atomic(root / 'result.json', result)
        if failures.primary is not None:
            raise failures.primary
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('plugin_home', type=Path)
    args = parser.parse_args()
    result = run(args.campaign, args.checkpoint, args.plugin_home)
    print(json.dumps({k: result[k] for k in ('state', 'cleanup', 'elapsed_seconds')}))
    return int(result['state'] != 'stock-week-native-complete-ungraded')


if __name__ == '__main__':
    raise SystemExit(main())
