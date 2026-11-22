"""Read-only account and gateway witnesses for the stock MemCal Hermes plugin."""
from contextlib import closing
import json
import hashlib
import sqlite3
import time
from pathlib import Path

from .gateway.reuse_runtime import PROTOCOL
from .live_dispatch import process_identity


class MemCalChatWitness:
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
        from .hermes_gateway import _identity
        from .openrouter_direct import BASE_URL
        from .openrouter_route import PROFILES
        from .provider_versions import MEMCAL_WEEK_REVISION, memcal_week_pin
        from .workflow_memcal_tools import manifest
        from .workflow_tracks import memcal_week_tools, validate_native_deployment
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
