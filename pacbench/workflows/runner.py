"""Chronological workflow execution on an already bound provider and front end.

A backend owns *native* account and chat objects. Only source uploads go to its
import port; its turn port receives exactly the user text, clock and opaque chat id.
A fresh chat never replaces the continuing account. An isolated control always
requires a new source-only account. No history is pasted into user prompts.
"""
from dataclasses import dataclass
from itertools import groupby
import json
import os
import time
from pathlib import Path
from typing import Protocol

from ..provider_versions import MEMCAL_WEEK_VERSION
from pacbench.providers.memcal.contract import verify_captured_driver
from ..semantic_grade import (CONTEXT_JUDGE_PROTOCOL, UnusableJudgeResponse,
                             audit_native_judge_attempts, grade_native_semantic)
from .spec import Document, instant
from .recording import retry_activity, tool_activity, tool_text_boundary
from .tracks import eligibility, validate_native_deployment


@dataclass(frozen=True)
class Condition:
    provider: str
    track: str
    state: str
    frontend: str
    model: str
    model_route: str
    deployment: str
    version: str
    gateway_patch: str
    service_clock: str
    tool_names: tuple[str, ...]
    channels: tuple[str, ...] = ('email', 'imessage')


class BoundAccount(Protocol):
    identity: str
    evidence_kind: str

    def verify_condition(self, condition: Condition) -> dict: ...

    def advance(self, at: str) -> dict: ...
    def submit(self, doc: Document) -> dict: ...
    def readback(self, doc: Document, acceptance: dict) -> dict: ...
    def sync(self, cutoff: str, channels: tuple[str, ...]) -> dict: ...
    def sync_readback(self, acceptance: dict) -> dict: ...
    def isolated_copy(self): ...
    def control_readback(self): ...
    def source_inventory(self): ...
    def ask(self, prompt: str, *, at: str, session_id: str | None) -> dict: ...
    def end_chat(self, session_id: str) -> dict: ...
    def close(self): ...


def inspect_source_cutoff(surface, documents, prefix, public_prompts):
    """Catch verbatim future source bodies in actual inputs; inventories remain the primary gate."""
    ready = set(prefix)
    allowed = '\n'.join([d.text for d in documents if d.filename in ready] + public_prompts)
    # Exclude short/common phrases and already public quotations. Scan decoded
    # contents, including automatic recall and serialized multi-line tool results.
    observed = []
    for request in surface['model_requests']:
        for row in request['messages']:
            if row['role'] in ('system', 'user', 'tool'):
                content = row.get('content')
                if isinstance(content, str):
                    observed.append(content)
    if surface['prefetch']:
        observed.append(surface['prefetch'])
    for doc in documents:
        if doc.filename in ready:
            continue
        for body in doc.bodies:
            if len(body) >= 40 and body not in allowed:
                escaped = json.dumps(body, ensure_ascii=False)[1:-1]
                if any(body in item or escaped in item for item in observed):
                    raise ValueError('answer input contains a verbatim unavailable source body')


def _tool_call_contract(call, *, stored=False):
    # Hermes persists Responses metadata next to the Chat Completions fields.
    # Only its local metadata may be absent from the request sent to the model.
    contract = dict(call)
    if stored:
        if contract.pop('call_id', contract['id']) != contract['id']:
            raise ValueError('native tool call ID aliases disagree')
        contract.pop('response_item_id', None)
    function = dict(contract['function'])
    arguments = json.loads(function['arguments'])
    function['arguments'] = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return {**contract, 'function':function}


def _wire_tool_text(saved, sent):
    return (isinstance(saved, str) and isinstance(sent, str)
            and saved.rstrip('\r\n') == sent and saved != sent and bool(sent))


def _saved_calls(message):
    calls = message.get('tool_calls') or []
    return json.loads(calls) if isinstance(calls, str) else calls


def _tool_rounds(history, last_user):
    """Find the requests that could have produced this completed native turn."""
    rounds, calls, outstanding = [], [], set()
    for index, row in enumerate(history[last_user + 1:-1], last_user + 1):
        if row['role'] == 'assistant':
            group = _saved_calls(row)
            if outstanding or not group:
                raise ValueError('native tool group is incomplete or out of order')
            rounds.append(index)
            for call in group:
                fn = call['function']
                calls.append((call['id'], fn['name'], json.loads(fn['arguments'])))
                outstanding.add(call['id'])
        else:
            call_id = row.get('tool_call_id')
            if call_id not in outstanding:
                raise ValueError('native tool group contains a result from another round')
            outstanding.remove(call_id)
    if outstanding or _saved_calls(history[-1]):
        raise ValueError('native tool group did not finish before the final answer')
    rounds.append(len(history) - 1)
    return rounds, calls


def captured_turn(surface, turn, prior, condition, expected_prefix):
    history = surface['history']
    previous = prior['history'] if prior else []
    barrier = surface['write_barrier']
    if (not isinstance(barrier, dict) or barrier.get('session_id') != surface['session_id']
            or barrier.get('pending_writes') != 0 or not barrier.get('readback')):
        raise ValueError('native turn writes are unverified')
    if (not surface['session_id'] or not surface['gateway_identity']
            or instant(surface['question_at']) != instant(turn.at)
            or tuple(sorted(surface['offered_tools'])) != tuple(sorted(condition.tool_names))):
        raise ValueError('missing native session, clock, write barrier or tool surface')
    if prior and ((surface['session_id'], surface['gateway_identity']) !=
                  (prior['session_id'], prior['gateway_identity'])):
        raise ValueError('conversation changed session or restarted its gateway')
    if history[:len(previous)] != previous:
        raise ValueError('native conversation prefix changed')
    delta = history[len(previous):]
    if (len(delta) < 2 or delta[0]['role'] != 'user' or delta[0]['content'] != turn.prompt
            or any(r['role'] not in ('assistant', 'tool') for r in delta[1:])
            or delta[-1]['role'] != 'assistant' or not surface['answer']
            or delta[-1]['content'] != surface['answer']):
        raise ValueError('unexpected native conversation order or final answer')
    if (surface['source_files'] != expected_prefix or
            any(c['name'] not in condition.tool_names for c in surface['tool_calls'])):
        raise ValueError('unexpected sources or tools')
    # Unknown automatic recall is distinct from an observed empty hook.
    if surface['prefetch'] is not None and not isinstance(surface['prefetch'], str):
        raise ValueError('invalid prefetch trace')
    if not isinstance(surface['model_requests'], list) or not surface['model_requests']:
        raise ValueError('missing actual answer input evidence')
    # Each request in a tool loop sees a prefix of the native conversation.
    expected = history[:-1]
    last_user = max(i for i, row in enumerate(expected) if row['role'] == 'user')
    round_prefixes, calls = _tool_rounds(history, last_user)
    captured_prefixes = []
    wire_tool_newline_removed = set()
    for request in surface['model_requests']:
        if condition.provider == 'memcal' and condition.version == MEMCAL_WEEK_VERSION:
            verify_captured_driver(request)
        if (not request.get('request_id') or not request.get('messages') or
                sorted(request['tools']) != sorted(condition.tool_names) or
                request['model'] != condition.model):
            raise ValueError('incomplete model request or unexpected model/tool surface')
        wire = [row for row in request['messages'] if row['role'] != 'system']
        if not captured_prefixes or captured_prefixes[-1] != len(wire):
            captured_prefixes.append(len(wire))
        if not last_user < len(wire) <= len(expected):
            raise ValueError('wire request omitted user context or added messages')
        for index, (saved, sent) in enumerate(zip(expected, wire)):
            role = saved['role']
            if sent['role'] != role:
                raise ValueError('wire conversation roles or tool grouping differ')
            content = saved.get('api_content') or saved.get('content') or ''
            if role == 'user':
                # Hermes may discard stale prefetch from a previous user turn.
                if sent.get('content') != content and not (
                        index < last_user and sent.get('content') == saved['content']):
                    raise ValueError('actual driver input lost or changed user context')
            elif content != (sent.get('content') or ''):
                if role != 'tool' or not _wire_tool_text(content, sent.get('content')):
                    raise ValueError('wire history omitted or altered an answer or tool result')
                wire_tool_newline_removed.add(saved['tool_call_id'])
            if role == 'tool' and saved.get('tool_call_id') != sent.get('tool_call_id'):
                raise ValueError('wire tool response changed call ID')
            if ([_tool_call_contract(call, stored=True) for call in _saved_calls(saved)] !=
                    [_tool_call_contract(call) for call in (sent.get('tool_calls') or [])]):
                raise ValueError('wire assistant tool calls differ')
    if len(wire) != len(expected):
        raise ValueError('final wire request omitted native history')
    if captured_prefixes != round_prefixes:
        raise ValueError('model requests do not follow the complete native tool groups')
    streamed = [(c['tool_call_id'], c['name'], c['input']) for c in surface['tool_calls']]
    if calls != streamed:
        raise ValueError('stream tool identity or arguments differ from native history')
    tools = [r for r in delta if r['role'] == 'tool']
    if [(r['tool_call_id'], r['tool_name'], r.get('api_content') or r['content']) for r in tools] != [
            (r['tool_call_id'], r['tool'], r['text']) for r in surface['tool_results']]:
        raise ValueError('tool result capture differs from answer-visible text')
    comparisons = {}
    tool_wire = {r['tool_call_id']: r['content'] for r in wire[last_user + 1:] if r['role'] == 'tool'}
    for result in surface['tool_results']:
        ident = result['tool_call_id']
        witness = surface.get('tool_boundaries', {}).get(ident, {})
        comparisons[ident] = tool_text_boundary(witness, tool_wire[ident])
    try:
        lineage = retry_activity(surface['attempts'])
    except (KeyError, ValueError, TypeError) as exc:
        lineage = {'state':'invalid', 'reason':str(exc)}
    return {**tool_activity(surface['tool_calls'], surface['tool_results']),
            'tool_text_boundaries':comparisons, 'attempt_lineage':lineage,
            'wire_tool_trailing_newline_removed':sorted(wire_tool_newline_removed)}


class Journal:
    """Exclusive, append-only private run evidence. A partial run cannot be overwritten."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def __enter__(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.file = (self.directory/'workflow.jsonl').open('x', encoding='utf-8')
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        try:
            if exc[0] is not None:
                self.write('stopped', error=exc[0].__name__, automatic_replay=False)
        finally:
            self.file.close()

    def write(self, phase, **record):
        self.file.write(json.dumps({'phase':phase, 'elapsed_s':time.monotonic()-self.started, **record}, ensure_ascii=False)+'\n')
        self.file.flush()
        os.fsync(self.file.fileno())


def deliver(account, condition, docs, journal, question_at):
    """A connector sees its upstream account and watermark, never a constructed chat or answer key."""
    at = docs[0].cutoff
    clock = account.advance(at)
    journal.write('source-clock', requested=at, observation=clock)
    if (clock.get('cutoff') != at or not clock.get('service_at') or not clock.get('as_of')
            or instant(clock['as_of']) != instant(at)):
        raise ValueError('missing source service clock witness')
    instant(clock['service_at'])  # Physical and scenario clocks may legitimately differ.
    if condition.track == 'nightly-document':
        if condition.provider == 'supermemory':
            # Native dynamic consolidation can combine documents. Release this
            # evening's exports before waiting for any one of them to consolidate.
            submissions = [(doc, submit_unit(account, condition, [doc], journal)) for doc in docs]
            return [filename for doc, ack in submissions for filename in
                    read_unit(account, condition, [doc], ack, journal, question_at)]
        delivered = []
        for doc in docs:
            delivered.extend(deliver_unit(account, condition, [doc], journal, question_at))
        return delivered
    return deliver_unit(account, condition, docs, journal, question_at)


def deliver_unit(account, condition, docs, journal, question_at):
    accepted = submit_unit(account, condition, docs, journal)
    return read_unit(account, condition, docs, accepted, journal, question_at)


def submit_unit(account, condition, docs, journal):
    at = docs[0].cutoff
    if condition.track == 'nightly-document':
        doc, = docs
        journal.write('submit', filename=doc.filename, content=doc.text, sha256=doc.sha256,
                      source_ids=doc.source_ids, at=at)
        accepted = account.submit(doc)
    else:
        journal.write('sync', cutoff=at, channels=condition.channels)
        accepted = account.sync(at, condition.channels)
    journal.write('acceptance', evidence=accepted)
    if not accepted.get('native_id') or accepted.get('accepted') is not True:
        raise ValueError('uncertain acceptance; no resubmission')
    return accepted


def read_unit(account, condition, docs, accepted, journal, question_at, *, observed=None):
    at = docs[0].cutoff
    if observed is not None:
        ready = observed
    elif condition.track == 'nightly-document':
        doc, = docs
        ready = account.readback(doc, accepted)
    else:
        ready = account.sync_readback(accepted)
    journal.write('readback', evidence=ready)
    if (ready.get('native_id') != accepted['native_id'] or ready.get('ready') is not True
            or not ready.get('readback') or not accepted.get('request')
            or not instant(at) <= instant(accepted['at']) <= instant(ready['at']) <= instant(question_at)):
        raise ValueError('missing or late readiness; question remains ungraded')
    if condition.track == 'nightly-document':
        if ready.get('submitted_sha256') != docs[0].sha256:
            raise ValueError('document binding differs')
    else:
        expected = sorted(sid for d in docs for sid in d.source_ids)
        if sorted(ready.get('new_source_ids', [])) != expected or not ready.get('source_cursor'):
            raise ValueError('incremental sync covered an unexpected source set')
        channels = ready.get('channel_readback', {})
        if set(channels) != set(condition.channels):
            raise ValueError('missing per-channel upstream readback')
        for channel, witness in channels.items():
            ids = sorted(sid for d in docs if d.channel == channel for sid in d.source_ids)
            if (not witness.get('cursor') or instant(witness['as_of']) != instant(at)
                    or sorted(witness['new_source_ids']) != ids):
                raise ValueError('connector cursor, cutoff or channel readback differs')
    return [d.filename for d in docs]


def qualify_account(account, condition, evidence_kind):
    if account.evidence_kind != evidence_kind:
        raise ValueError('account evidence kind mismatch')
    deployment = account.verify_condition(condition)
    if (deployment.get('account') != account.identity or deployment.get('condition') != vars(condition)
            or not isinstance(deployment.get('store_identity'), str) or not deployment['store_identity'].strip()):
        raise ValueError('deployment witness differs from requested condition')
    if evidence_kind == 'native':
        validate_native_deployment(condition, deployment)
    return deployment, deployment['store_identity']


def confirm_source_only(account, prefix, journal, question=None):
    witness = account.control_readback()
    journal.write('account-readback', question=question, evidence=witness)
    if (witness.get('account') != account.identity or not witness.get('readback') or
            witness.get('conversation_writes') != 0 or witness.get('chat_count') != 0 or
            witness.get('source_files') != prefix):
        raise ValueError('account is not source-only')


def run_workflow(condition, account: BoundAccount, documents, turns, journal, *, spec_digest, evidence_kind,
                 all_source_documents=None, progress=None, question_channels=('email', 'imessage'),
                 combined_documents=None, allow_simultaneous_questions=False):
    """Run on the supplied account; stop on uncertainty without replaying an import or answer."""
    route = eligibility(condition.provider, condition.track, condition.channels, version=condition.version)
    if not route['documented_for_scope']:
        raise ValueError('no supported route for this source scope: ' + route['boundary'])
    if condition.state not in ('isolated-control','continuing-account') or evidence_kind not in ('recording-fake','native'):
        raise ValueError('unknown workflow state or evidence kind')
    if any(not str(value).strip() for key,value in vars(condition).items() if key != 'tool_names'):
        raise ValueError('incomplete deployment, clock or frontend identity')
    deployment, base_store = qualify_account(account, condition, evidence_kind)
    if not account.identity or not spec_digest:
        raise ValueError('missing account or spec identity')
    turns, documents = list(turns), list(documents)
    if {doc.channel for doc in documents} != set(condition.channels) and not combined_documents:
        if combined_documents is not None:
            raise ValueError('source corpus does not match declared channel scope')
        from pacbench.campaigns.pizza_week import verify_legacy_combined_schedule

        verify_legacy_combined_schedule(condition, documents, turns)
    if turns and set(condition.channels) != set(question_channels):
        raise ValueError('partial connector coverage cannot answer the scenario questions')
    question_times = [instant(turn.at) for turn in turns]
    if (len({turn.id for turn in turns}) != len(turns)
            or any(a > b for a, b in zip(question_times, question_times[1:]))
            or (not allow_simultaneous_questions and len(set(question_times)) != len(question_times))
            or any(instant(a.cutoff) > instant(b.cutoff) for a,b in zip(documents,documents[1:]))
            or len({d.filename for d in documents}) != len(documents)):
        raise ValueError('workflow events must have unique identities and chronological order')
    journal.write('condition', condition=vars(condition), capabilities=route, spec_digest=spec_digest,
                  evidence_kind=evidence_kind, deployment_witness=deployment, judge=CONTEXT_JUDGE_PROTOCOL, prompt_caching='unmeasured')
    # Move the source clock once per evening. Each document still needs its
    # own acceptance and readback; later uploads must not rewind processing.
    batches = [list(batch) for _, batch in groupby(documents, key=lambda d: instant(d.cutoff))]
    confirm_source_only(account, [], journal)
    stores = {base_store}
    index, prefix, sessions, native_sessions, copies = 0, [], {}, set(), set()
    snapshots, public_prompts = [], []
    driver_requests = set()
    continuing_gateway = None
    last_in_chat = {turn.session:turn.id for turn in turns}
    for turn in turns:
        while index < len(batches) and instant(batches[index][0].cutoff) <= instant(turn.at):
            prefix.extend(deliver(account,condition,batches[index],journal,turn.at))
            index += 1
        isolated = condition.state == 'isolated-control'
        if isolated and turn.measure != 'isolated-control':
            raise ValueError('dependent conversation cannot use isolated-question storage')
        if not isolated and turn.measure == 'isolated-control':
            raise ValueError('isolated control cannot use continuing storage')
        observation = account.isolated_copy() if isolated else account
        try:
            if isolated:
                if observation.identity == account.identity or observation.identity in copies:
                    raise ValueError('isolated native account reused')
                clone, store = qualify_account(observation, condition, evidence_kind)
                if store in stores:
                    raise ValueError('isolated control reused a native store')
                stores.add(store)
                journal.write('control-deployment', question=turn.id, evidence=clone)
                confirm_source_only(observation, prefix, journal, turn.id)
                copies.add(observation.identity)
            prior = None if isolated else sessions.get(turn.session)
            inventory = observation.source_inventory()
            journal.write('pre-dispatch-inventory', question=turn.id, account=observation.identity,
                          source_files=inventory)
            if inventory != prefix:
                raise ValueError('unexpected sources before answer dispatch')
            journal.write('dispatch', question=turn.id, account=observation.identity, at=turn.at,
                          prompt=turn.prompt, session=prior['session_id'] if prior else None)
            def ask():
                from .native import NativeAccount
                hooks = {}
                if progress is not None and isinstance(observation, NativeAccount):
                    hooks['on_return'] = lambda raw: progress.record_observation('answer',turn.id,'cli-return',raw)
                    hooks['on_trace'] = lambda trace: progress.record_observation('answer',turn.id,'trace',trace)
                return observation.ask(turn.prompt, at=turn.at, session_id=prior['session_id'] if prior else None, **hooks)
            if progress is None:
                surface = ask()
            else:
                surface = progress.effect('answer', turn.id, {'at':turn.at,'prompt':turn.prompt,
                                          'session_id':prior['session_id'] if prior else None}, ask, role='driver')
            journal.write('answer-visible', question=turn.id, measure=turn.measure, surface=surface)
            activity = captured_turn(surface, turn, prior, condition, prefix)
            ids = [r['request_id'] for r in surface['model_requests']]
            if evidence_kind == 'native':
                for request in surface['model_requests']:
                    body = request.get('request') or {}
                    from ..openrouter_route import validate_request
                    validate_request(body, profile='luna-bedrock-global')
                    if body.get('messages') != request['messages']:
                        raise ValueError('actual driver request differs from approved route')
            linked = {attempt['id'] for attempt in (surface.get('attempts') or [])
                      if isinstance(attempt, dict) and isinstance(attempt.get('id'), str)}
            missing = sorted(set(ids) - linked)
            reused = sorted(driver_requests.intersection(ids))
            activity['driver_receipts'] = {'observed_requests':len(ids), 'missing':missing, 'reused':reused,
                'coverage':'complete' if not (missing or reused or len(set(ids)) != len(ids)
                                              or activity['attempt_lineage'].get('state') == 'invalid') else 'incomplete'}
            driver_requests.update(ids)
            public_prompts.append(turn.prompt)
            inspect_source_cutoff(surface, documents if all_source_documents is None else all_source_documents,
                                  prefix, [turn.prompt] if isolated else public_prompts)
            if surface['account'] != observation.identity or (not prior and surface['session_id'] in native_sessions):
                raise ValueError('wrong account or unexpectedly reused chat')
            if not isolated:
                if continuing_gateway is not None and surface['gateway_identity'] != continuing_gateway:
                    raise ValueError('continuing account restarted its gateway between chats')
                continuing_gateway = surface['gateway_identity']
            native_sessions.add(surface['session_id'])
            surface['conversation_times'] = [*(prior['conversation_times'] if prior else ()), turn.at]
            sessions[turn.session] = surface
            if progress is None:
                snapshots.append((turn, surface))
            else:
                progress.verified_turn(turn, surface, activity)
            journal.write('verified-turn',question=turn.id,measure=turn.measure,activity=activity)
            if last_in_chat[turn.session] == turn.id:
                started = time.monotonic()
                journal.write('chat-close-started', question=turn.id, session_id=surface['session_id'])
                closure = observation.end_chat(surface['session_id'])
                journal.write('chat-closed', question=turn.id, evidence=closure,
                              cleanup_s=time.monotonic()-started)
                if (closure.get('session_id') != surface['session_id'] or not closure.get('readback')
                        or closure.get('pending_writes') != 0):
                    raise ValueError('native chat-end writes unverified')
            if progress is not None:
                progress.mark_turn_done(turn.id)
        finally:
            if isolated and observation is not account:
                observation.close()
    # A channel-only conformance run still exercises the entire import path.
    for batch in batches[index:]:
        deliver(account, condition, batch, journal, "9999-12-31T00:00:00+00:00")
    return snapshots


class UnverifiedJudgment(ValueError):
    """A verdict cannot be bound to the required judge request and route."""


def judge_options(turn, surface):
    history = surface['history']
    final_user = max(i for i,r in enumerate(history) if r['role']=='user')
    times = iter(surface['conversation_times'])
    conversation = []
    for row in history[:final_user]:
        if row['role'] == 'user':
            at = next(times)
        if row['role'] in ('user', 'assistant') and row.get('content'):
            conversation.append({'role':row['role'], 'content':row['content'], 'at':at})
    return dict(judge_protocol=CONTEXT_JUDGE_PROTOCOL, question_at=turn.at, conversation=conversation)


def grade_turn(turn, surface, generate, directory, *, require_receipt=False):
    """The latest semantic judge scores each stage independently, including source-assisted answers."""
    if not turn.claims:
        return None
    directory = Path(directory)/turn.id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory/'judgment.json'
    options = judge_options(turn, surface)
    grade = grade_native_semantic(turn.id, turn.prompt, surface['answer'], turn.claims, generate,
                                  record_path=path, **options)
    if require_receipt:
        try:
            audit_native_judge_attempts(path, question=turn.prompt, answer=surface['answer'], claims=turn.claims,
                                        required_profile='luna-bedrock-global',
                                        model_call_dir=directory.parents[1]/'model-calls', **options)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise UnverifiedJudgment(str(exc)) from exc
    return grade


def run_current_week(condition, account, directory, *, judge=None, canary=False, resume=False,
                     seven_daily_documents=False):
    """Compatibility entry point for the original Pizza Week scenario."""
    from pacbench.campaigns.pizza_week import compile_pizza_week

    plan = compile_pizza_week(condition.state, canary=canary, seven_daily_documents=seven_daily_documents)
    return run_compiled_workflow(plan, condition, account, directory, judge=judge, resume=resume)


def run_compiled_workflow(plan, condition, account, directory, *, judge=None, resume=False):
    """Execute a compiled scenario using the supplied account and declared channel scope."""
    if condition.state != plan.state or not set(condition.channels) <= set(plan.channels):
        raise ValueError('account state or source channels differ from the compiled workflow')
    if plan.offline_only:
        if account.evidence_kind != 'recording-fake' or resume:
            raise ValueError('custom frozen workflows are offline conformance only')
    else:
        plan.authorize(plan, condition, account, directory, judge, resume)
    docs = plan.documents_for(condition.channels)
    turns = list(plan.turns) if set(condition.channels) == set(plan.channels) else []
    all_source_documents = list(plan.all_documents)
    fingerprint, source_basis, canary = plan.spec_sha256, plan.source_basis, plan.canary
    spec, daily_manifest = plan.spec, plan.layout
    if judge is not None and account.evidence_kind != 'native':
        raise ValueError('recording fakes do not receive benchmark grades')
    if condition.frontend.lower().startswith('stock hermes') and condition.gateway_patch.lower() != 'none':
        raise ValueError('patched Hermes cannot be declared stock')
    if (not plan.offline_only and condition.provider == 'memcal' and condition.track == 'first-party-connector'
            and condition.state == 'continuing-account' and turns):
        from .stream import run_account_stream
        return run_account_stream(condition, account, docs, turns, directory, spec_digest=fingerprint,
                                  source_basis=source_basis, all_source_documents=all_source_documents,
                                  judge=judge, canary=canary, resume=resume)
    from pacbench.workflows import document_progress as workflow_document_progress
    if (not plan.offline_only and condition.track == 'nightly-document' and condition.state == 'continuing-account'
            and condition.provider in workflow_document_progress.PROVIDERS and turns):
        return workflow_document_progress.run(condition, account, docs, turns, directory, spec,
                                              fingerprint, source_basis, all_source_documents, judge, canary,
                                              resume=resume, daily_manifest=daily_manifest)
    if resume:
        raise ValueError('This account route has no verified incremental resume')
    return _run_journal_workflow(plan, condition, account, directory, docs, turns, judge)


def _run_journal_workflow(plan, condition, account, directory, docs, turns, judge):
    summary = {}
    for t in turns:
        row = summary.setdefault(t.measure, {'planned_turns':0,'planned_criteria':0,
            'answered':0,'graded_criteria':0,'correct':None,'judge_failures':0})
        row['planned_turns'] += 1
        row['planned_criteria'] += len(t.claims)
    with Journal(directory) as journal:
        if plan.layout is not None:
            journal.write('source-layout', layout=plan.layout['source_layout'],
                          document_index_sha256=plan.layout['normalized_index_sha256'])
        schedule = [dict(id=turn.id, at=turn.at, chat=turn.session, measure=turn.measure) for turn in turns]
        spec_fields = {}
        if plan.offline_only:
            spec_fields['format'] = plan.spec['format']
            for row, turn in zip(schedule, turns, strict=True):
                row['criteria'] = len(turn.claims)
        journal.write('spec', version=plan.spec['version'], canary=plan.canary, sha256=plan.spec_sha256,
                      source_basis=plan.source_basis, schedule=schedule, **spec_fields)
        snapshots = run_workflow(condition, account, docs, turns, journal, spec_digest=plan.spec_sha256,
                                 evidence_kind=account.evidence_kind, all_source_documents=plan.all_documents,
                                 question_channels=plan.channels, combined_documents=plan.layout is not None,
                                 allow_simultaneous_questions=plan.offline_only)
        for turn,surface in snapshots:
            row = summary[turn.measure]
            row['answered'] += 1
            if judge is None or not turn.claims:
                continue
            journal.write('judge-started', question=turn.id, measure=turn.measure)
            def observed_judge(prompt):
                from ..canary_capability import digest
                journal.write('judge-request', question=turn.id, prompt_sha256=digest(prompt))
                return judge(prompt)
            try:
                grade = grade_turn(turn,surface,observed_judge,Path(directory)/'judgments', require_receipt=True)
            except (UnusableJudgeResponse, UnverifiedJudgment) as exc:
                row['judge_failures'] += 1
                journal.write('ungraded',question=turn.id,measure=turn.measure,reason=type(exc).__name__)
                continue
            row['graded_criteria'] += len(grade.slots)
            row['correct'] = (row['correct'] or 0) + grade.correct
            journal.write('grade',question=turn.id,measure=turn.measure,judge=plan.spec['judge'],
                          criteria=[vars(c) for c in grade.slots])
        journal.write('completion',by_measure=summary,prompt_caching='unmeasured')
    return summary
