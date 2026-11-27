"""Run MemCal Week and prepare its native account."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from pacbench import harness
from pacbench.corpus.prepare import read_spec
from pacbench.corpus.r2 import ROOT, SPEC
from pacbench.corpus.sources import load_bundle
from pacbench.gateway.observer import PROTOCOL
from pacbench.workflows.progress import process_identity

from ... import harness


def _source_messages():
    spec = read_spec(SPEC)
    dataset = ROOT / spec['dataset']
    manifest = json.loads((dataset/'manifest.json').read_bytes())
    bundle = load_bundle(dataset/manifest['source_bundle'], manifest['source_bundle_hash'])
    return [r for life in bundle['lives'].values() for r in life['messages']]


def _model_config(store, model, base, key):
    from memcal import config
    cfg = config.load(home=store)
    if (cfg.llm_provider != 'openai-compatible' or cfg.fallback_provider or cfg.openai_base_url != base
            or cfg.openai_api_key != key or (cfg.propose_model,cfg.sweep_model,cfg.match_model) != (model,model,model)
            or cfg.publish_calendar or cfg.publish_reminders):
        raise ValueError('Existing MemCal configuration conflicts with the scoped Luna route or enables outward writes')
    return cfg


def check_canary(root):
    from pacbench.corpus.r2 import SPEC
    from pacbench.evidence_store import file_binding as binding
    from pacbench.evidence_store import file_sha as sha
    from pacbench.judging.retained_scope import MEMCAL_SCOPE
    from pacbench.judging.retained_scope import read_canary_binding as read
    from pacbench.judging.retained_scope import retained_schedule as schedule
    from pacbench.receipt_costs import read_attempts
    from pacbench.workflows.grading import retained_grade
    from pacbench.workflows.progress import WorkflowProgress
    from pacbench.workflows.recording import receipt_summary

    root = Path(root).resolve(strict=True)
    scope_binding = binding(root/'workflow-scope.json')
    scope = read(scope_binding)
    meta = json.loads((root/'workflow/run.json').read_text())
    result = json.loads((root/'result.json').read_text())
    if (root.name != 'memcal' or scope['provider'] != 'memcal' or scope['root'] != str(root)
            or scope['canary'] is not True or scope.get('admission') != MEMCAL_SCOPE or scope['spec_sha256'] != sha(SPEC)
            or result.get('scope') != scope_binding or result.get('canary') is not True
            or result.get('state') != 'live-canary-complete' or result.get('cleanup') != 'complete'):
        raise ValueError('a completed native MemCal canary on this frozen schedule is required')
    docs, turns, source_basis = schedule(sha(SPEC), True)
    with WorkflowProgress(root/'workflow', meta['run_key']) as progress:
        progress.audit_artifacts()
        identity = progress.binding('identity')
        activation = progress.binding('memcal_activation') or {}
        if (identity != meta['identity'] or not progress.execution_sealed or identity['canary'] is not True
                or identity['condition']['track'] != 'first-party-connector' or identity['evidence_kind'] != 'native'
                or identity['source_basis'] != source_basis or progress.binding('workflow_scope') != scope_binding
                or activation.get('scope') != scope_binding or not progress.binding('memcal_platform_capture')
                or activation.get('store') != identity['store'] or scope['frozen_inputs'] != source_basis
                or progress.last_cutoff() != docs[-1].cutoff):
            raise ValueError('canary scope, source, or native execution seal differs')
        saved = list(progress.iter_verified())
        if [t for t, _, _ in saved] != list(turns) or any(
                activity['driver_receipts']['coverage'] != 'complete' for _, _, activity in saved):
            raise ValueError('canary needs four native turns with complete driver lineage')
        for turn, surface, _ in saved:
            if turn.claims:
                grade = retained_grade(turn, surface, root/'workflow')
                if [vars(item) for item in grade.slots] != progress.grade_record(turn.id)['criteria']:
                    raise ValueError('canary criterion verdict differs from the original judge receipt')
        if sum(r['graded_criteria'] for r in progress.summary().values()) != sum(len(t.claims) for t in turns):
            raise ValueError('canary grades are incomplete')
    rows = read_attempts(root / 'receipts.jsonl')
    roles = {row['accounting_role'] for row in rows if row.get('upstream_request_id')}
    if (roles != {'hermes','memory_provider','judge'} or any(
            (row.get('upstream_request_id') or '').startswith('offline-native-') for row in rows)
            or not result['billing'] or result['billing'].get('complete_cost_usd') is None
            or receipt_summary(root, rows, scope['run_id']) != result['billing']):
        raise ValueError('canary transport receipts are inconsistent')
    return identity['store']


def memcal_model_settings(model: str, base: str, key: str) -> dict:
    """Native store-scoped model settings; pure and usable in offline plans."""
    return {'MEMCAL_LLM_PROVIDER': 'openai-compatible', 'MEMCAL_OPENAI_BASE_URL': base,
            'OPENAI_COMPAT_API_KEY': key, 'MEMCAL_PROPOSE_MODEL': model,
            'MEMCAL_SWEEP_MODEL': model, 'MEMCAL_MATCH_MODEL': model}


def _memcal_plan(root, model, base, key):
    return memcal_model_settings(model, base, key)


def _provision_memcal(arm):
    root, env, model = arm.root, arm.env, arm.model
    memory_key, resume = arm.memory_key, arm.resume
    configured_model = model if model != "unconfigured" else os.environ.get("MODEL", "muse-spark-1.3-contributor")
    env.update(memcal_model_settings(configured_model, arm.memory_url, memory_key))
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
                               bootstrap, env['MEMCAL_SRC']],
                              env={**os.environ, **env}, capture_output=True, timeout=30)
        if proc.returncode:
            raise RuntimeError("Memcal native scratch-store bootstrap failed")


class chat_MemCalChatWitness:
    def __init__(self, owner, attempts, *, deployment=None, timeout=60):
        self.owner = owner
        self.account = None
        self.deployment = deployment
        self.attempts = attempts
        self.timeout = timeout
        self.store = owner.home.parent / 'memcal-home' / 'memcal.db'
        self.last_archive = {}

    def verify_deployment(self, condition, identity):
        """Compose observed chat settings with the site's independent, read-only attestation.

        deployment() supplies account, store_path (the SQLite file), synthetic_source_provenance,
        observed_connector_grants by channel, as_of_upstream, and platform_created_messages_fixture.
        A native archive or plugin mounting alone cannot attest Apple provenance or consent.
        """
        from ruamel.yaml import YAML

        from pacbench.hermes_gateway import _identity
        BASE_URL = 'https://openrouter.ai/api/v1'
        from pacbench.openrouter_route import PROFILES
        from pacbench.provider_versions import MEMCAL_WEEK_REVISION, memcal_week_pin
        from pacbench.providers import memcal_week_tools, validate_native_deployment
        from pacbench.providers.memcal.source import manifest
        if not callable(self.deployment):
            raise ValueError('MemCal requires independent synthetic site deployment observations')
        site = self.deployment()
        if site.get('account') != identity or site.get('store_path') != str(self.store):
            raise ValueError('site account or source store differs from this gateway')
        env = self.owner.env
        profile = PROFILES['luna-bedrock-global']
        cfg = YAML(typ='safe').load((self.owner.home / 'config.yaml').read_text())
        granted = sorted(env.get('PACBENCH_TOOL_ALLOWLIST','').split(','))
        label, revision, version = memcal_week_pin()
        if (self.owner.provider != 'memcal' or self.owner.pins['memcal'] != revision
                or env.get('PACBENCH_MEMCAL_CONDITION') != label
                or hashlib.sha256((self.owner.home/'config.yaml').read_bytes()).hexdigest() != self.owner.config_sha256
                or cfg['model'].get('provider') != 'openrouter' or cfg['model'].get('default') != profile['model']
                or cfg['model'].get('base_url') != BASE_URL or cfg.get('provider_routing') != {'only':[profile['endpoint']]}
                or cfg['memory'].get('provider') != 'memcal'
                or cfg['memory'].get('memory_enabled') is not False or cfg['memory'].get('user_profile_enabled') is not False
                or granted != list(memcal_week_tools(revision)) or env.get('PACBENCH_PROVIDER_ONLY') != '1'
                or env.get('PACBENCH_DIRECT') != '1' or env.get('PACBENCH_OPENROUTER_PROFILE') != 'luna-bedrock-global'
                or env.get('MEMCAL_HOME') != str(self.store.parent) or self.store.resolve() != self.store):
            raise ValueError('MemCal gateway route, stable release, store, or tool grants differ')
        pid = self.owner.witness['pid']
        if _identity(self.owner.home, pid)['start_time'] != self.owner.witness['start_time']:
            raise ValueError('MemCal gateway process changed')
        result = {key:site[key] for key in ('synthetic_source_provenance','observed_connector_grants','as_of_upstream')}
        for key in ('platform_created_messages_fixture', 'synthetic_chatdb_fixture'):
            if key in site:
                result[key] = site[key]
        result.update(account=identity, store_identity=str(self.store.parent), condition=vars(condition),
                      observed_version=version, memcal_revision=revision,
                      frontend_identity='Hermes ' + self.owner.pins['hermes'], observed_patch=self.owner.pins['hermes_isolation_sha256'],
                      effective_model_route=profile['endpoint'], granted_tools=granted,
                      native_processes={'driver':process_identity(pid),'memory':process_identity()},
                      route_evidence='Direct route configuration; actual request payloads and receipts audited separately')
        if revision == MEMCAL_WEEK_REVISION:
            result.update(manifest())
        validate_native_deployment(condition, result)
        self.account = identity
        return result

    def control_readback(self):
        if self.account is None:
            raise RuntimeError('fresh readback needs the verified site identity')
        path = self.owner.home / 'state.db'
        sessions = 0
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                sessions = db.execute('SELECT count(*) FROM sessions').fetchone()[0]
        with closing(sqlite3.connect(self.store.as_uri() + '?mode=ro', uri=True)) as db:
            rows = dict(db.execute('SELECT channel,count(*) FROM archive GROUP BY channel'))
            if rows:
                raise ValueError('fresh MemCal account already contains archived records')
        return dict(account=self.account, chat_count=sessions, conversation_writes=0,
                    source_files=[], readback=dict(archive_counts=rows, store=str(self.store), native_sessions=sessions))

    def tool_boundaries(self, results):
        # The wire and session DB retain exact tool output. Only read native archive
        # lines explicitly returned by a shipped tool; leave unsupported joins unknown.
        fields = {'memcal_search_archive':'results','memcal_open_source':'evidence','memcal_conversation':'conversation'}
        boundaries = {}
        with closing(sqlite3.connect(self.store.as_uri() + '?mode=ro', uri=True)) as db:
            for result in results:
                name = result['tool']
                if name not in fields or not result['success']:
                    continue
                try:
                    response = json.loads(result['text'])
                except (TypeError, ValueError):
                    continue
                rows = response.get(fields[name]) if isinstance(response,dict) else None
                if not isinstance(rows,list):
                    continue
                confirmed = []
                for row in rows:
                    if not isinstance(row,dict):
                        continue
                    ident = row.get('line_id',row.get('id'))
                    stored = db.execute('SELECT text,channel,thread FROM archive WHERE id=?', (ident,)).fetchone()
                    if not stored:
                        continue
                    visible = stored[0][:400] if name == 'memcal_search_archive' else stored[0]
                    if (row.get('text'),row.get('channel'),row.get('thread')) == (visible, stored[1],stored[2] or ''):
                        confirmed.append(dict(archive_id=ident,channel=stored[1],
                                              rendered_text_sha256=hashlib.sha256(visible.encode()).hexdigest()))
                boundaries[result['tool_call_id']] = dict(native_tool=name,returned_lines=len(rows),verified_archive_lines=confirmed)
        return boundaries

    def request_lineage(self, requests):
        selected = {r['request_id'] for r in requests}
        if callable(self.attempts):
            rows = self.attempts()
            for row in reversed(rows):
                if row['id'] in selected and row.get('retry_of'):
                    selected.add(row['retry_of'])
        else:
            rows = self.attempts.lineage_for_requests(selected)
            selected.update(r['id'] for r in rows)
        return [dict(id=r['id'],parent=r.get('retry_of'),operation=r['dispatch_context']['operation_id'],
                     origin='harness' if r.get('retry_of') else 'unknown') for r in rows if r['id'] in selected]

    def close(self):
        pass  # Caller owns the authenticated source, database and gateway lifetimes.

    def _active_attempts(self):
        if callable(self.attempts):
            return any(not r.get('finished') for r in self.attempts() if r.get('accounting_role') != 'judge')
        return self.attempts.has_active_attempts(roles=('hermes','memory_provider'))

    def idle(self):
        since = time.monotonic_ns()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if (self.owner.home / 'reuse-observer-error.json').exists():
                raise RuntimeError('native MemCal gateway observer failed; inspect its error receipt')
            path = self.owner.home / 'reuse-observer.json'
            if path.exists():
                state = json.loads(path.read_bytes())
                identity = [state['pid'], state['start_time']]
                expected = self.owner.witness
                if (identity != [expected['pid'], expected['start_time']] or state['protocol'] != PROTOCOL
                        or state['observer_sha256'] != self.owner.pins['reuse_observer_sha256']
                        or state['native_failures']):
                    raise RuntimeError('invalid MemCal gateway lifecycle observation')
                idle = all(not row['pending_futures'] and all(p.get('clean') for p in row['providers'])
                           for row in state['sessions'])
                if idle and state['observed_monotonic_ns'] >= since and not self._active_attempts():
                    return dict(gateway_identity=identity, inflight_requests=0, pending_native_writes=0,
                                readback=state, native_errors=[])
            time.sleep(.05)
        raise RuntimeError('MemCal gateway or model requests did not become idle')

    def _user_archive(self, session):
        with closing(sqlite3.connect(self.store.as_uri() + '?mode=ro', uri=True)) as db:
            return list(db.execute("SELECT id, text FROM archive WHERE channel='agent' AND thread=? "
                                   "AND from_me=1 ORDER BY id", ('hermes:' + session,)))

    def native_write_barrier(self, session, answer):
        state = self.idle()['readback']
        if not any(row['session_id'] == session and any(p['name'] == 'memcal' for p in row['providers'])
                   for row in state['sessions']):
            raise RuntimeError('MemCal plugin did not observe this native chat')
        path = self.owner.home / 'state.db'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            rows = list(db.execute("SELECT role,content FROM messages WHERE session_id=? ORDER BY id", (session,)))
        archive = self._user_archive(session)
        if (not rows or rows[-1] != ('assistant', answer) or not archive
                or [r[1] for r in archive] != [text for role,text in rows if role == 'user']):
            raise RuntimeError('MemCal archive differs from the exact native user turns')
        self.last_archive[session] = archive
        return dict(session_id=session, pending_writes=0,
                    readback=dict(archive_ids=[r[0] for r in archive], sample_ns=state['observed_monotonic_ns']))

    def after_native_close(self, session):
        self.check_close(session, closed=True)
        archive = self._user_archive(session)
        if archive != self.last_archive.pop(session):
            raise RuntimeError('MemCal user archive changed on chat close')
        return dict(session_id=session, pending_writes=0, readback=dict(archive_ids=[r[0] for r in archive]))

    def check_close(self, session, *, closed):
        # Other conversations may stay open across this ordinary native close.
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = self.idle()['readback']
            managers = [r for r in state['sessions'] if r['session_id'] == session]
            with closing(sqlite3.connect((self.owner.home/'state.db').as_uri() + '?mode=ro', uri=True)) as db:
                end = db.execute('SELECT ended_at,end_reason FROM sessions WHERE id=?', (session,)).fetchone()
            if closed:
                if (end and end[0] is not None and end[1] == 'cli_close' and managers
                        and all(r['shutdown_returned'] and r['clean'] for r in managers)):
                    return
            elif end == (None, None) and managers and any(not r['shutdown_returned'] for r in managers):
                return
            time.sleep(.05)
        raise RuntimeError('MemCal native session close state differs from its verified history')


def verify_resume(account, progress, plan):
    frontend = account.frontend
    identity = progress.binding('identity')
    gateway = frontend.gateway_identity()
    if (account.provider != 'memcal' or identity['account'] != account.identity
            or progress.binding('gateway') != gateway):
        raise RuntimeError('cannot resume without the original MemCal account and warm gateway')
    source = account.source_witness.verify_source_resume(progress)
    store = Path(frontend.owner.env['MEMCAL_HOME'])
    if (source['cutoff'] != progress.last_cutoff() or str(store / 'memcal.db') != source['store_path']
            or store.resolve() != store or str(store) != identity['store']):
        raise RuntimeError('source collector and warm gateway do not share the verified native store')
    if source['upstream_cutoff'] == source['cutoff']:
        plan.check_inventory(account.source_inventory(), source['cutoff'])
    # A controller interruption may leave one complete native return ahead of
    # its post-answer write readback. Verify that exact return without dispatch.
    next_n = progress.next_ordinal()
    pending_turn, captured = None, None
    if next_n is not None:
        _, current, _ = next(plan.iter_turns(next_n))
        if progress.get_verified(current.id) is None:
            claim = progress.active_effect('answer', current.id)
            if claim is not None:
                returned = progress.observation('answer', current.id, 'cli-return')
                if returned is not None and 'wire_cursor' in returned:
                    previous = progress.latest_surface(current.session, before=next_n)
                    sid = previous['session_id'] if previous else None
                    inputs = dict(ordinal=next_n, prompt=current.prompt, at=current.at, session=sid, cutoff=source['cutoff'])
                    def authenticate(operation):
                        trace = frontend.read_return(current.prompt, current.at, sid, returned)
                        proof = dict(native_binding=trace['clock_evidence']['binding'],
                                     gateway=gateway, session=trace['session_id'],
                                     request_ids=[r['request_id'] for r in trace['model_requests']])
                        return trace, dict(operation=operation, native_evidence=proof)
                    progress.reconcile_effect('answer', current.id, inputs, authenticate)
            captured = progress.get_effect('answer', current.id)
            if captured is not None:
                pending_turn = current

    chat = account.witness
    count = 0
    def verify(logical, sid, saved):
        nonlocal count
        if (not saved or saved['session_id'] != sid or saved['gateway_identity'] != gateway
                or frontend.session_history(sid) != saved['history']):
            raise RuntimeError('native Hermes session history differs from its durable turn')
        end_turn = plan.last_turn_id(logical)
        closed = progress.get_effect('chat-close', end_turn) is not None
        chat.native_write_barrier(sid, saved['answer'])
        chat.check_close(sid, closed=closed)
        if closed:
            chat.last_archive.pop(sid, None)
        else:
            descriptor = saved['clock_evidence']['descriptor']
            frontend.starts[sid] = descriptor.get('initial_at',descriptor['at'])
        count += 1

    for logical, sid in progress.iter_sessions():
        if pending_turn and logical == pending_turn.session:
            verify(logical, sid, captured)
            pending_turn = None
        else:
            verify(logical, sid, progress.latest_surface(logical))
    if pending_turn:
        verify(pending_turn.session, captured['session_id'], captured)
    path = frontend.owner.home / 'state.db'
    actual_count = 0
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            actual_count = db.execute('SELECT count(*) FROM sessions').fetchone()[0]
    if actual_count != count:
        raise RuntimeError('unexpected physical Hermes sessions in the continuing account')
    with closing(sqlite3.connect(chat.store.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute("SELECT count(DISTINCT thread) FROM archive WHERE channel='agent'").fetchone()[0] != count:
            raise RuntimeError('unexpected native conversational writes in the continuing account')
    return dict(gateway_identity=gateway, sessions=count, source=source)
