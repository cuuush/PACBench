"""Bindings for a provisioned, synthetic account. No service setup or credential acquisition."""
from contextlib import closing
import hashlib
import os
import re
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from .checkpoint_clock import audit_question, history
from .context_isolation import actual_context
from .harness import _parse_turn, capture_prefetch_provenance
from .hermes_gateway import _identity
from .gateway_reuse import NATIVE_CHAT_CLOSE
from .stop import communicate_until_stop
from .workflow_delivery import submit_document


def trace_position(path):
    """Bind an append cursor to the file and its last complete pre-turn bytes."""
    try:
        with path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            stream.seek(max(0, stat.st_size - 256))
            return [stat.st_dev, stat.st_ino, stat.st_size, hashlib.sha256(stream.read(256)).hexdigest()]
    except FileNotFoundError:
        return None


def appended_rows(path, position):
    with path.open('rb') as stream:
        if position is not None:
            device, inode, size, digest = position
            stat = os.fstat(stream.fileno())
            if (stat.st_dev, stat.st_ino) != (device, inode) or stat.st_size < size:
                raise ValueError('turn trace rotated or truncated')
            start = max(0, size - 256)
            stream.seek(start)
            if hashlib.sha256(stream.read(size - start)).hexdigest() != digest:
                raise ValueError('turn trace append boundary changed')
        for line in stream:
            yield json.loads(line)


def tool_provenance(calls, results):
    """Record explicit native operations separately from automatic memory delivery."""
    from .followups import tool_category
    from .source_policy import viking_read_result_category
    observed = []
    for call,result in zip(calls,results,strict=True):
        name = call['name']
        category = tool_category(name,call['input'])
        if name=='viking_read' and result['success']:
            category = viking_read_result_category(result['text'])
        observed.append(dict(tool_call_id=call['tool_call_id'],tool=name,category=category,success=result['success']))
    return observed


class OpenChatClockOwner:
    """The isolation runner's fence requires shutdown. A continuing chat must stay open.

    Bind the native account's *open-session* idle/readback observation to the
    same live gateway process before stepping a scenario clock.
    """
    def __init__(self, owner, observe_idle):
        self.home, self.env, self.witness = owner.home, owner.env, owner.witness
        self.observe_idle = observe_idle

    def fence(self):
        _identity(self.home, self.witness['pid'])
        proof = self.observe_idle()
        if (proof.get('gateway_identity') != [self.witness['pid'],self.witness['start_time']]
                or proof.get('pending_native_writes') != 0 or proof.get('inflight_requests') != 0
                or not proof.get('readback') or proof.get('native_errors') != []):
            raise RuntimeError('open-session native idle or write visibility is unverified')
        return proof


class WarmHermes:
    """Use the existing Hermes CLI and actual gateway/session database on every turn.

    The owner holds a running gateway, clock and native write fence. The harness never
    closes a conversation between its related turns. Per-provider readback of the
    write and source inventory belongs to the provisioned account binding.
    """
    def __init__(self, owner, clock, observe_idle, *, chat_close_timeout_s=120, watch_stop=False):
        self.owner = owner
        self.clock = clock
        self.clock_owner = OpenChatClockOwner(owner, observe_idle)
        self.starts = {}
        self.chat_close_timeout_s = chat_close_timeout_s
        self.watch_stop = watch_stop
        if owner.env.get('PACBENCH_WORKFLOW_CAPTURE') != 'pizza-week-conversation-v2':
            raise ValueError('exact driver capture must be enabled at gateway startup')

    def gateway_identity(self):
        expected = self.owner.witness
        actual = _identity(self.owner.home, expected['pid'])
        if actual['start_time'] != expected['start_time']:
            raise RuntimeError('warm Hermes gateway process identity changed')
        return [expected['pid'], actual['start_time']]

    def session_history(self, sid):
        path = Path(self.owner.home) / 'state.db'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                'SELECT id,role,content,api_content,tool_name,tool_call_id,tool_calls FROM messages '
                'WHERE session_id=? ORDER BY id', (sid,))]

    def ask(self, prompt, *, at, session_id, on_return=None):
        owner = self.owner
        _identity(owner.home, owner.witness['pid'])
        context = None
        if session_id is not None:
            context = {'initial_at':self.starts[session_id], 'continued_session_id':session_id,
                       'continuation_reason':'authored-checkpoint',
                       'history_sha256':history(owner.home, session_id)['sha256']}
        desc = self.clock.prepare(self.clock_owner, prompt, at, continuation=context)
        env = {**owner.env, 'PACBENCH_PYTHON_CLOCK_ROLE':'hermes-cli'}
        cmd = [str(owner.python),'-m','hermes_cli.main','chat','-q',prompt,'-Q','--format','stream-json']
        if session_id is not None:
            cmd.extend(['--resume',session_id])
        trace_path = Path(env['PACBENCH_CONTEXT_TRACE'])
        hook_cursor = trace_position(trace_path)
        wire_cursor = trace_position(Path(env['PACBENCH_API_INPUT_TRACE']))
        started = time.monotonic()
        if self.watch_stop:
            with subprocess.Popen(cmd, cwd=owner.cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as child:
                stdout, stderr = communicate_until_stop(child, timeout=240, env=env)
                proc = subprocess.CompletedProcess(cmd, child.returncode, stdout, stderr)
        else:
            proc = subprocess.run(cmd, cwd=owner.cwd, env=env, capture_output=True, text=True, timeout=240)
        result_received = time.monotonic()
        raw = dict(stdout=proc.stdout, returncode=proc.returncode, cli_elapsed_s=result_received-started,
                   question_at=at, turn_nonce=desc['nonce'], hook_cursor=hook_cursor, wire_cursor=wire_cursor)
        if on_return is not None:
            on_return(raw)
        return self.read_return(prompt, at, session_id, raw, observed_at=result_received)

    def read_return(self, prompt, at, session_id, raw, *, observed_at=None):
        """Recover the saved CLI return against its original clock, trace, and native session."""
        owner = self.owner
        gateway = self.gateway_identity()
        env = owner.env
        if raw['question_at'] != at or not re.fullmatch('[0-9a-f]{32}', raw['turn_nonce']):
            raise ValueError('saved return does not belong to the scheduled turn')
        elapsed = raw['cli_elapsed_s']
        proc = subprocess.CompletedProcess([], raw['returncode'], stdout=raw['stdout'], stderr='')
        turn = _parse_turn(prompt, at, proc, elapsed)
        if proc.returncode or not turn.session_id or (session_id and turn.session_id != session_id):
            raise RuntimeError('native Hermes answer or session failed')
        sid = turn.session_id
        self.starts.setdefault(sid,at)
        desc = json.loads((self.clock.folder/'questions'/(raw['turn_nonce']+'.json')).read_text())
        if desc['at'] != at or desc.get('continued_session_id') != session_id:
            raise ValueError('saved native clock or continuation differs')
        clock_receipt = audit_question(self.clock.folder, desc, turn, owner.witness)
        isolation = actual_context(owner.home,sid,owner.cwd,capture=True)
        if isolation.get('state') != 'pass' or turn.policy_blocks:
            raise RuntimeError(f"native context check returned {isolation.get('reason')!r} with {turn.policy_blocks} blocked tool calls")
        records = self.session_history(sid)
        requests = [r for r in appended_rows(Path(env['PACBENCH_API_INPUT_TRACE']), raw['wire_cursor'])
                    if r.get('turn_nonce') == desc['nonce'] and r['protocol']=='workflow-driver-input-v1']
        if not requests:
            raise RuntimeError('missing request-bound wire capture')
        users = [r for r in records if r['role']=='user']
        if not users or users[-1]['content'] != prompt or records[-1]['role'] != 'assistant' or records[-1]['content'] != turn.answer:
            raise ValueError('native user or assistant history differs from the saved CLI return')
        wire = users[-1].get('api_content') or users[-1]['content']
        # Do not treat additional non-prefetch injections as native recall.
        from .context_isolation import memory_delivery
        turn.prefetch_context = None
        try:
            hooks = [r for r in appended_rows(Path(env['PACBENCH_CONTEXT_TRACE']), raw['hook_cursor'])
                     if isinstance(r, dict) and r.get('session_id') == sid and r.get('query') == prompt]
            if len(hooks) == 1 and isinstance(hooks[0].get('prefetch'), str):
                turn.prefetch_context = hooks[0]['prefetch']
        except (OSError, ValueError, UnicodeError):
            pass  # The wire records remain authoritative; unavailable hook provenance stays unknown.
        capture_prefetch_provenance(turn, env)
        sent_users = [msg for msg in requests[0]['messages'] if msg['role'] == 'user']
        delivery = memory_delivery(owner.home, sid, prompt, turn.prefetch_context,
                                   native_row_id=users[-1]['id'],
                                   delivered_text=sent_users[-1].get('content') if sent_users else None)
        delta = records[max(i for i,r in enumerate(records) if r['role']=='user')+1:]
        native_results = [r for r in delta if r['role']=='tool']
        if len(native_results) != len(turn.tool_results):
            raise RuntimeError('incomplete native tool-result capture')
        results = [{**event, 'text':r.get('api_content') or r['content'],
                    'tool_call_id':r['tool_call_id'], 'tool':r['tool_name']}
                   for r,event in zip(native_results,turn.tool_results,strict=True)]
        return {'answer':turn.answer,'session_id':sid,'history':records,'question_at':at,
                'gateway_identity':gateway,
                'clock_evidence':clock_receipt,'context_isolation':isolation,'model_requests':requests,
                'offered_tools':requests[0]['tools'],'tool_calls':turn.tool_calls,'tool_results':results,
                'prefetch':turn.prefetch_context, 'memory_delivery':delivery,
                'prefetch_provenance':turn.prefetch_provenance,
                'tool_provenance':tool_provenance(turn.tool_calls, results),
                'user_api_content':wire, 'raw_native_stream':proc.stdout,
                'timing':{'cli_result_received_s':elapsed,
                          'answer_ready_bound':'upper bound at CLI return',
                          'native_reported_turn_s':turn.native_result_duration_s,
                          'post_result_trace_capture_s':time.monotonic()-observed_at if observed_at is not None else None}}

    def end_chat(self, session_id):
        owner = self.owner
        _identity(owner.home,owner.witness['pid'])
        timeout = self.chat_close_timeout_s
        subprocess.run([str(owner.python),'-c',NATIVE_CHAT_CLOSE,session_id,str(timeout)],
                       env=owner.env,cwd=owner.cwd,capture_output=True,check=True,timeout=timeout+5)
        self.starts.pop(session_id)


class NativeAccount:
    """Compose an ordinary native uploader, warm front end and deployment-specific witnesses.

    Witness methods inspect actual acceptance, readiness, inventory and write completion;
    they cannot substitute a planned prefix for readback. The connector owns its existing
    synthetic upstream. sync never receives benchmark message bodies, questions or metadata.
    """
    def __init__(self, identity, provider, scope, client, frontend, witness, *, source_witness=None):
        self.identity, self.provider, self.scope = identity, provider, scope
        self.client, self.frontend, self.witness = client, frontend, witness
        self.source_witness = source_witness

    evidence_kind = 'native'

    def verify_condition(self, condition):
        if condition.provider != self.provider:
            raise ValueError('bound provider differs from declared route')
        record = self.witness.verify_deployment(condition, self.identity)
        if self.provider == 'memcal' and condition.track == 'first-party-connector':
            from .workflow_delivery import MemCalReaders
            from .workflow_tracks import validate_native_deployment

            validate_native_deployment(condition, record)
            if record.get('account') != self.identity or record.get('condition') != vars(condition):
                raise ValueError('MemCal deployment witness belongs to a different account or condition')
            if not isinstance(self.client, MemCalReaders):
                raise ValueError('Stock MemCal readers are required for native connector qualification')
            from .workflow_memcal_source import MemCalSourceWitness
            if (not isinstance(self.source_witness, MemCalSourceWitness)
                    or self.source_witness.site != self.client.site):
                raise ValueError('Native source release and archive witnesses must bind the same synthetic site')
            from functools import partial
            from .workflow_memcal import verify_memcal_mailbox
            access = self.client.preflight(self.identity, condition.channels)
            self.source_witness.verify_mailbox = partial(verify_memcal_mailbox, self.client.site,
                                                        self.client.connection, self.client.config, checked={})
            return {**record, 'source_access': access,
                    'source_map_sha256': self.source_witness.bindings_sha256}
        return record

    def advance(self, at):
        return (self.source_witness or self.witness).collection_clock(at)

    def submit(self, doc):
        raw = submit_document(self.provider,self.client,self.scope,doc)
        return self.witness.acceptance(raw,doc)

    def readback(self, doc, acceptance):
        return self.witness.document_readback(doc,acceptance)

    def sync(self, cutoff, channels):
        if self.provider == 'memcal':
            with self.memcal_clock(cutoff):
                raw = self.client.collect(channels)
            return self.source_witness.connector_acceptance(raw, cutoff)
        # An eligible email-only source uses the installed official client. A
        # missing grant or implementation cannot fall back to uploading exports.
        return self.witness.sync_source(self.scope, cutoff, channels)

    def sync_readback(self, acceptance):
        if self.provider == 'memcal':
            return self.observe_sync(acceptance, self.process_sync())
        return self.witness.connector_readback(acceptance)

    def process_sync(self):
        active = getattr(self.source_witness, 'active', None)
        with self.memcal_clock(active['cutoff'] if active else None):
            return self.client.process()

    def memcal_clock(self, cutoff):
        from contextlib import nullcontext
        from .workflow_memcal_live import run_environment
        if getattr(getattr(self.client, 'site', None), 'synthetic_chatdb', False):
            if not cutoff:
                raise ValueError('Synthetic MemCal collection and Dream require a bound cutoff')
            return run_environment({'MEMCAL_TODAY':cutoff})
        return nullcontext()

    def observe_sync(self, acceptance, dream):
        return {**self.source_witness.connector_readback(acceptance, processing=dream), 'native_dream': dream}

    def isolated_copy(self):
        return self.witness.clone_source_only()

    def control_readback(self):
        chat = self.witness.control_readback()
        if self.source_witness is not None:
            files = self.source_witness.source_inventory()
            if 'source_files' in chat and chat['source_files'] != files:
                raise ValueError('Native chat and upstream source inventories disagree')
            return {**chat, 'source_files': files}
        return chat

    def source_inventory(self):
        return (self.source_witness or self.witness).source_inventory()

    def capture_answer(self, prompt, *, at, session_id, on_return=None):
        if on_return is None:
            return self.frontend.ask(prompt, at=at, session_id=session_id)
        return self.frontend.ask(prompt, at=at, session_id=session_id, on_return=on_return)

    def _post_answer_inventory(self, barrier):
        return self.source_inventory()

    def observe_answer(self, trace):
        started = time.monotonic()
        barrier = self.witness.native_write_barrier(trace['session_id'],trace['answer'])
        timing = {**trace.get('timing', {}), 'native_write_barrier_s':time.monotonic()-started}
        return {**trace,'timing':timing,'account':self.identity,'source_files':self._post_answer_inventory(barrier),'write_barrier':barrier,
                'tool_boundaries':self.witness.tool_boundaries(trace['tool_results']),
                'attempts':self.witness.request_lineage(trace['model_requests'])}

    def ask(self, prompt, *, at, session_id, on_return=None, on_trace=None):
        trace = self.capture_answer(prompt, at=at, session_id=session_id, on_return=on_return)
        if on_trace is not None:
            on_trace(trace)
        return self.observe_answer(trace)

    def verify_resume(self, progress, plan):
        """Read the existing MemCal account and native chats before continuing a saved prefix."""
        from .workflow_memcal_chat import verify_resume
        return verify_resume(self, progress, plan)

    def end_chat(self, session_id):
        self.frontend.end_chat(session_id)
        return self.witness.after_native_close(session_id)

    def close(self):
        self.witness.close()
