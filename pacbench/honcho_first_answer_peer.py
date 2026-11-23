"""Observe stock Honcho peer reads from Hermes to the native inference boundary."""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_hex
from urllib.parse import urlsplit

from . import honcho_daily_gate as gate
from .canary_capability import binding, digest, read
from .dispatch_ownership import OwnershipConflict
from .honcho_first_answer_admission import active, question, settled
from .live_dispatch import process_identity
from .openrouter_route import CHAT_COMPLETIONS_URL, validate_request

HEADER = 'x-pacbench-honcho-read'
_origin = ContextVar('pacbench_honcho_peer_origin', default=None)
_read = ContextVar('pacbench_honcho_peer_read', default=None)


def _last_driver(scope, current):
    from .observational_receipts import RunReceiptJournal

    rows = RunReceiptJournal(Path(scope['root'])/'receipts.jsonl').snapshot()
    drivers = [row for row in rows if 'honcho_first_answer' in row['workflow_slot']]
    last = drivers[-1] if drivers else {}
    proof = last.get('workflow_slot', {}).get('honcho_first_answer', {})
    if not settled(last) or any(proof.get(k) != v for k,v in current.items()):
        raise OwnershipConflict('Honcho peer read lacks the settled active Hermes request')
    return last


def _origin_for_tool(scope, current, args):
    last = _last_driver(scope,current)
    calls = [call for call in last.get('honcho_selected_tools') or ()
             if call['name'] == 'honcho_reasoning' and call['arguments_sha256'] == digest(args)]
    if len(calls) != 1:
        raise OwnershipConflict('Honcho reasoning lacks an unambiguous, settled Hermes tool selection')
    return dict(kind='explicit', selected_by=last['id'], tool_call_id=calls[0]['id'])


def _native(scope):
    saved = read(binding(Path(scope['root'])/gate.RUNTIME))
    if saved['scope'] != binding(Path(scope['root'])/'workflow-scope.json'):
        raise OwnershipConflict('Honcho peer read is outside its run-owned native API')
    return saved


def _peer_request(scope, method, path, raw):
    host = _native(scope)['host']
    workspace = host['workspace']
    observer = path.rsplit('/', 2)[-2]
    body = json.loads(raw)
    observed = body.get('target') or observer
    if (method != 'POST' or path != f'/v3/workspaces/{workspace}/peers/{observer}/chat'
            or observer not in {host['aiPeer'],host['peerName']} or observed not in {host['aiPeer'],host['peerName']}
            or body.keys() - {'query','target','reasoning_level','stream'} or body.get('stream') is not False
            or body.get('reasoning_level') not in {'minimal','low','medium','high','max'}
            or not isinstance(body.get('query'),str) or not body['query'].strip()):
        raise OwnershipConflict('Honcho peer request differs from the stock SDK and owned peers')
    return dict(method=method, path=path, sha256=sha256(raw).hexdigest(), workspace=workspace,
                observer=observer, observed=observed)


def _issue(scope, origin, request):
    current = active(scope)
    if current != origin['submission']:
        raise OwnershipConflict('Honcho peer read no longer belongs to the active submission')
    endpoint = urlsplit(_native(scope)['api_url'])
    if (request.url.scheme, request.url.netloc.decode(), request.url.query) != ('http',endpoint.netloc,b''):
        raise OwnershipConflict('Honcho peer witness may only be sent to the owned loopback API')
    wire = _peer_request(scope, request.method, request.url.path, request.content)
    token = token_hex(32)
    folder = Path(scope['root'])/'peer-reads'
    operation = digest(origin)
    gate._write(folder/(operation+'.json'), dict(origin=origin, request=wire, token_sha256=sha256(token.encode()).hexdigest()))
    return operation+'.'+token


def install_gateway():
    """Add a one-use causal marker only to the existing stock SDK loopback request."""
    import httpx
    from .provider_loader import provider_module
    from .workflow_capability import load

    plugin = provider_module('honcho')
    provider = plugin.HonchoMemoryProvider
    spawn = provider._spawn_dialectic
    handle_tool = provider.handle_tool_call
    sync_turn = provider.sync_turn
    send = httpx.Client.send

    def submission(instance, scope):
        current = active(scope)
        if (instance._session_key != current['session_id'] or instance._config.session_strategy != 'per-session'
                or instance._recall_mode != 'hybrid'
                or instance._recall_sync or instance._query_rewrite_enabled or instance._dialectic_depth != 1):
            raise OwnershipConflict('Honcho first-question scope requires the stock hybrid recall configuration')
        return current

    @wraps(spawn)
    def observe_spawn(instance, query, *, thread_name, fired_at, log_label, use_query_rewrite=True):
        scope = load()
        current_question = question(scope)
        options = {}
        number = 1
        if scope.get('week_schedule'):
            from .honcho_week_state import pending, plan

            number = pending(scope)[0]+1
            prior = plan()[1][number-2].prompt if number>1 else ''
            if ((thread_name,log_label)==('honcho-resume-dialectic','resumed-session dialectic')
                    and number>1 and fired_at==number-1
                    and query==(current_question.prompt if instance._is_trivial_prompt(prior) else prior)):
                options['resumed'] = True
        before_answer = ((thread_name,log_label) == ('honcho-prewarm-dialectic','dialectic prewarm')
                and number==1 and query == plugin._PREWARM_QUERY and fired_at == 0 and not use_query_rewrite
                or (thread_name,log_label) == ('honcho-prefetch-first','first-turn dialectic')
                and number==1 and query == current_question.prompt and fired_at == 1
                or options.get('resumed'))
        after_answer = ((thread_name,log_label) == ('honcho-prefetch','prefetch')
                        and query == current_question.prompt and fired_at == number)
        if not (before_answer or after_answer):
            # A later question's stock asynchronous prefetch cannot spend under this scope.
            return spawn(instance, query, thread_name=thread_name, fired_at=fired_at,
                         log_label=log_label, use_query_rewrite=use_query_rewrite)
        current = submission(instance,scope)
        origin = dict(kind=thread_name,submission=current)
        if after_answer:
            previous = _last_driver(scope,current)
            if 'stop' not in previous['response_validation']['finish_reasons']:
                raise OwnershipConflict('Honcho post-answer prefetch has no observed final answer')
            origin.update(kind='post-answer-prefetch',after_answer=previous['id'])
            if scope.get('week_schedule') and number < len(plan()[1]):
                origin['within_week'] = True
        token = _origin.set(origin)
        try:
            return spawn(instance, query, thread_name=thread_name, fired_at=fired_at,
                         log_label=log_label, use_query_rewrite=use_query_rewrite)
        finally:
            _origin.reset(token)

    @wraps(handle_tool)
    def observe_tool(instance, tool_name, args, **kwargs):
        if tool_name != 'honcho_reasoning':
            return handle_tool(instance,tool_name,args,**kwargs)
        scope = load()
        current = submission(instance, scope)
        origin = _origin_for_tool(scope,current,args)
        token = _origin.set(dict(origin,submission=current))
        try:
            return handle_tool(instance,tool_name,args,**kwargs)
        finally:
            _origin.reset(token)

    @wraps(sync_turn)
    def observe_sync(instance,user_content,assistant_content,*,session_id='',turn_author=None):
        scope = load()
        if scope.get('week_schedule'):
            from .honcho_week_capture import prepare

            current = submission(instance,scope)
            prepare(scope,current,_last_driver(scope,current),user_content,assistant_content,session_id)
        return sync_turn(instance,user_content,assistant_content,session_id=session_id,turn_author=turn_author)

    @wraps(send)
    def observe_send(client, request, *args, **kwargs):
        origin = _origin.get()
        if origin is None or not request.url.path.endswith('/chat'):
            return send(client, request, *args, **kwargs)
        from .direct_accounting import stop_measured_admission

        try:
            if HEADER in request.headers or kwargs.get('follow_redirects') is True or client.follow_redirects:
                raise OwnershipConflict('Unexpected Honcho peer read header or redirect mode')
            request.headers[HEADER] = _issue(load(), origin, request)
        except (OSError,KeyError,RuntimeError,ValueError,TypeError):
            stop_measured_admission('honcho_peer_read_issuance_failed')
            raise
        return send(client, request, *args, **kwargs)

    provider._spawn_dialectic = observe_spawn
    provider.handle_tool_call = observe_tool
    provider.sync_turn = observe_sync
    httpx.Client.send = observe_send


def claim(scope, token, wire):
    if not re.fullmatch('[0-9a-f]{64}\\.[0-9a-f]{64}',token):
        raise OwnershipConflict('Missing one-use Honcho peer read witness')
    operation, secret = token.split('.')
    folder = Path(scope['root'])/'peer-reads'
    grant = read(binding(folder/(operation+'.json')))
    current = active(scope,gateway_process=False)
    if (grant['request'] != wire or grant['origin']['submission'] != current
            or operation != digest(grant['origin']) or grant['token_sha256'] != sha256(secret.encode()).hexdigest()):
        raise OwnershipConflict('Honcho native peer read differs from the witnessed gateway operation')
    return gate._write(folder/(operation+'.claimed.json'), dict(operation=operation, origin=grant['origin'], request=wire))


def wrap_api(app):
    """Claim the marker at the actual stock peer route, inside the native API process."""
    from .direct_accounting import stop_measured_admission
    from .workflow_capability import load
    from starlette.responses import JSONResponse

    async def native(scope, receive, send):
        if (scope['type'] != 'http' or not scope['path'].endswith('/chat')):
            return await app(scope,receive,send)
        try:
            if scope['client'][0] != '127.0.0.1' or scope['query_string']:
                raise OwnershipConflict('Nonlocal Honcho peer read')
            tokens = [v.decode('ascii') for k,v in scope['headers'] if k == HEADER.encode()]
            if len(tokens) != 1:
                raise OwnershipConflict('Missing or duplicate Honcho peer read marker')
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] != 'http.request':
                    raise OwnershipConflict('Incomplete SDK peer request')
                body.extend(message.get('body',b''))
                if len(body) > 65536:
                    raise OwnershipConflict('Oversize SDK peer request')
                if not message.get('more_body'):
                    break
            request = dict(type='http.request',body=bytes(body),more_body=False)
            cap = load()
            if _native(cap)['api'] != process_identity():
                raise OwnershipConflict('Honcho peer read entered a foreign API process')
            proof = claim(cap,tokens[0],_peer_request(cap,scope['method'],scope['path'],request['body']))
            origin = read(proof)['origin']
            if origin['kind']=='post-answer-prefetch' and not origin.get('within_week'):
                # The stock plugin asks to warm a subsequent turn. This standalone
                # scope has already returned its answer and funds no later turn.
                return await JSONResponse({'detail':'Prefetch for a later turn is outside the first-question scope'},
                                          status_code=403)(scope,receive,send)
        except (OSError,KeyError,RuntimeError,ValueError,TypeError) as exc:
            import logging
            logging.getLogger(__name__).warning('Scoped Honcho read refused: %s',
                                                str(exc) if isinstance(exc,OwnershipConflict) else type(exc).__name__)
            stop_measured_admission('honcho_unverified_native_peer_read')
            return await JSONResponse({'detail':'Unverified scoped Honcho read'},status_code=403)(scope,receive,send)
        context = [proof]
        marker = _read.set(context)

        async def replay_original():
            nonlocal request
            if request is not None:
                value, request = request, None
                return value
            return await receive()

        try:
            clean = {**scope,'headers':[(k,v) for k,v in scope['headers'] if k != HEADER.encode()]}
            return await app(clean,replay_original,send)
        finally:
            context.clear()
            _read.reset(marker)

    return native


def native_slot(scope):
    claim_context, executor = _read.get(), gate._executor.get()
    if not claim_context or not executor or _native(scope)['api'] != process_identity():
        raise OwnershipConflict('Paid Honcho peer analysis has no scoped native invocation')
    bound = claim_context[0]
    peer = read(bound)
    telemetry, plan = executor[0]
    current = active(scope,gateway_process=False)
    request = peer['request']
    if (current != peer['origin']['submission'] or not telemetry or not plan
            or telemetry.call_purpose != 'dialectic.answer' or telemetry.workspace_name != request['workspace']
            or telemetry.observer != request['observer'] or telemetry.observed != request['observed']
            or not telemetry.run_id or telemetry.run_id != telemetry.trace_id or telemetry.session_id is not None
            or type(telemetry.step_seq) is not int or telemetry.step_seq < 1 or telemetry.iteration != telemetry.step_seq
            or plan.attempt != 1 or plan.is_fallback):
        raise OwnershipConflict('Honcho native dialectic role, attempt or endpoints differ')
    return dict(provider='honcho',kind='recall',question_id=question(scope).id,
                honcho_peer_read=dict(claim=bound,operation=peer['operation'],run_id=telemetry.run_id,step=telemetry.step_seq))


def permit(scope, project, body):
    from .workflow_capability import ENV, PROFILE
    from .workflow_daily_documents import compile_daily_week
    from .workflow_runner import inspect_source_cutoff

    validate_request(body,profile=PROFILE)
    slot = native_slot(scope)
    documents = compile_daily_week()[1]
    names, prompts = [documents[0].filename],[question(scope).prompt]
    if scope.get('week_schedule'):
        from .honcho_week_state import pending, plan

        index,_,scheduled = pending(scope)
        names = scheduled['ready_files']
        prompts = [turn.prompt for turn in plan()[1][:index+1]]
    inspect_source_cutoff({'model_requests':[body],'prefetch':''},documents,names,prompts)
    return dict(root=scope['root'],run_id=scope['run_id'],capability=json.loads(os.environ[ENV]),projects=[project],slot=slot)


def bind_attempt(rows, row):
    from .direct_http import _native_http_request
    from .workflow_capability import load

    wire = _native_http_request.get()
    evidence = wire.extensions.get(gate.EXTENSION) if wire is not None else None
    if not evidence:
        raise OwnershipConflict('Missing stock SDK physical first-send proof for Honcho peer read')
    active_executor, sdk_key, retry, request = evidence
    proof = row['workflow_slot']['honcho_peer_read']
    previous = [r['workflow_slot']['honcho_peer_read'] for r in rows.values()
                if r['workflow_slot'].get('honcho_peer_read',{}).get('operation') == proof['operation']]
    if (not active_executor or active_executor is not gate._executor.get() or not sdk_key or retry != 0
            or request != gate._request_binding(wire) or request[:2] != ('POST',CHAT_COMPLETIONS_URL)
            or native_slot(load()) != row['workflow_slot'] or proof['step'] != len(previous)+1
            or any(p['run_id'] != proof['run_id'] or p['claim'] != proof['claim'] for p in previous)):
        raise OwnershipConflict('Reissued, skipped or changed Honcho peer inference step')


def confirm_sender(row):
    from .workflow_capability import load

    if native_slot(load()) != row['workflow_slot']:
        raise OwnershipConflict('Honcho peer invocation changed before physical dispatch')


def serve(source, port):
    """Keep routing, request handling and inference in the pinned native service."""
    import subprocess
    import httpx
    import openai
    import uvicorn
    from .direct_http import _httpx_wrappers
    from .workflow_capability import budget
    from src.llm import api, executor, tool_loop
    from src.main import app

    source = Path(source).resolve(strict=True)
    git = ['git','-C',str(source)]
    if (subprocess.check_output([*git,'rev-parse','HEAD'],text=True).strip() != gate.REVISION
            or subprocess.run([*git,'diff','--quiet','HEAD','--','src','uv.lock']).returncode
            or openai.__version__ != '2.36.0' or httpx.AsyncClient.send not in _httpx_wrappers
            or not api.honcho_llm_call_inner is executor.honcho_llm_call_inner is tool_loop.honcho_llm_call_inner):
        raise OwnershipConflict('Pinned native Honcho API and observed SDK are required')
    budget()
    api.honcho_llm_call_inner = tool_loop.honcho_llm_call_inner = gate.observe(
        executor.honcho_llm_call_inner,gate._executor,gate.executor)
    openai.AsyncOpenAI._build_request = gate.observe_build(openai.AsyncOpenAI._build_request)
    uvicorn.run(wrap_api(app),host='127.0.0.1',port=int(port))


if __name__ == '__main__':
    import sys
    # ASGI, request instrumentation and admission must use the same context variables.
    from pacbench.honcho_first_answer_peer import serve as run
    run(*sys.argv[1:])
