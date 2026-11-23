"""Bind the first Honcho answer to its sealed source and stock Hermes request."""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

from . import honcho_daily_gate as gate
from .canary_capability import binding, digest, read
from .dispatch_ownership import OwnershipConflict
from .live_dispatch import process_identity
from .openrouter_route import validate_request
from .workflow_tracks import HERMES_TOOLS

PROFILE = 'luna-bedrock-global'
QUESTION = 'rsvp-before'
TOOLS = HERMES_TOOLS['honcho']


def question(scope):
    if scope.get('week_schedule'):
        from .honcho_week_state import pending

        return pending(scope)[1]
    turn = gate.schedule()[1][0]
    if scope.get('first_question') != QUESTION or turn.id != QUESTION:
        raise OwnershipConflict('No ordinary Honcho first-question admission')
    return turn


def arm(scope, owner, turn):
    root = Path(scope['root'])
    if process_identity() != scope['controller'] or turn != question(scope):
        raise OwnershipConflict('Only the controller may arm the frozen Honcho question')
    if gate.followup_ready(root, turn.id) != turn.prompt or owner.fence()['sessions']:
        raise OwnershipConflict('The Honcho question needs exact day-one readiness and an empty gateway')
    witness = owner.witness
    return gate._write(root/'question-gateway.json', dict(
        condition=gate.CONDITION, controller=scope['controller'], gateway=process_identity(witness['pid']),
        witness={key:witness[key] for key in ('pid','start_time','process_adoption')},
        ready=binding(root/'honcho-daily-1-ready.json'), question=turn.id))


def active(scope, *, gateway_process=True):
    from .checkpoint_clock import ENV, audit_question, descriptor
    from .hermes_gateway import _identity
    from .observational_receipts import check_cohort_stop

    check_cohort_stop()
    stop = os.environ.get('PACBENCH_STOP_FILE')
    if stop and Path(stop).exists():
        raise OwnershipConflict('Honcho admission is stopped')
    root = Path(scope['root'])
    if scope.get('week_schedule'):
        from .honcho_week_state import active as week_active, ready_bindings

        turn,owner,names,week_index = week_active(scope)
        ready = ready_bindings(root,len(names))
        expected_session = owner['session_id']
    else:
        turn = question(scope)
        owner = read(binding(root/'question-gateway.json'))
        ready = binding(root/'honcho-daily-1-ready.json')
        expected_session = None
    if (owner['condition'] != gate.CONDITION or owner['controller'] != scope['controller']
            or owner['ready'] != ready or owner['question'] != turn.id
            or process_identity(owner['gateway']['pid']) != owner['gateway']):
        raise OwnershipConflict('Honcho source or gateway changed after question admission')
    completed, pending = gate._progress(root, scope)
    from .workflow_spec import instant
    if (pending is not None or [s['at'] for s in completed]
            != [entry['at'] for entry in scope['plan'] if instant(entry['at'])<=instant(turn.at)]):
        raise OwnershipConflict('Honcho source prefix differs from the frozen turn time')
    home, clock = root/'hermes-home',root/'reuse-clock'
    if gateway_process and (owner['gateway'] != process_identity() or os.environ.get('HERMES_HOME') != str(home)
            or Path(os.environ.get(ENV, '')) != clock/'active.json'
            or os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE') != 'hermes-gateway'):
        raise OwnershipConflict('Honcho driver is outside its owned Hermes gateway')
    _identity(home, owner['gateway']['pid'])
    if gateway_process:
        clock_row = descriptor()
    else:
        clock_row = descriptor(pointer=clock/'active.json', home=home)
    prompt_hash = sha256(turn.prompt.encode()).hexdigest()
    if (clock_row['kind'] != ('continuation' if expected_session else 'fresh')
            or clock_row.get('continued_session_id')!=expected_session
            or clock_row['home'] != str(home) or clock_row['at'] != turn.at
            or clock_row['checkpoint'] != scope['spec_sha256'] or clock_row['question_sha256'] != prompt_hash):
        raise OwnershipConflict('Honcho request differs from the frozen submission')
    nonce = clock_row['nonce']
    native = read(binding(clock/'bindings'/(nonce+'.json')))
    sid = native['session_id']
    observed = audit_question(clock,clock_row,{'prompt':turn.prompt,'session_id':sid},
                              owner['witness'],remote_runtime=not gateway_process)
    if (native['nonce'] != nonce or native['question_sha256'] != prompt_hash or expected_session and sid!=expected_session
            or not observed['native_admission'][-1].get('in_window')):
        raise OwnershipConflict('No active native Honcho submission witness')
    if scope.get('week_schedule'):
        from .honcho_week_state import validate_session

        validate_session(scope, week_index, sid)
    return dict(session_id=sid,turn_nonce=nonce,native_request_id=native['request_id'],ready=ready)


def _tool_selection(call, *, stored=False):
    from .workflow_runner import _tool_call_contract

    tool = _tool_call_contract(call, stored=stored)
    fn = tool['function']
    if fn['name'] not in TOOLS or not tool['id']:
        raise OwnershipConflict('Unknown Honcho tool or missing model-selected call ID')
    args = json.loads(fn['arguments'])
    return dict(id=tool['id'],name=fn['name'],arguments_sha256=digest(args))


def _history(home, sid):
    with closing(sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        session = db.execute(
            'SELECT coalesce(p.prompt,s.system_prompt) AS system_prompt FROM sessions s '
            'LEFT JOIN system_prompts p ON p.hash=s.system_prompt_hash WHERE s.id=?',(sid,)).fetchone()
        if not session:
            raise OwnershipConflict('Honcho driver has no owned native Hermes session')
        return session['system_prompt'], [dict(row) for row in db.execute(
            'SELECT role, content, api_content, tool_call_id, tool_calls, tool_name '
            'FROM messages WHERE session_id=? ORDER BY id', (sid,))]


def tool_rounds(wire, stored):
    from .workflow_runner import _wire_tool_text

    rounds, expected = [], {}
    for actual, native in zip(wire, stored, strict=True):
        role = actual['role']
        sent = actual.get('content') or ''
        text = native.get('api_content') or native.get('content') or ''
        if (role != native['role']
                or sent != text and (role != 'tool' or not _wire_tool_text(text,sent))):
            raise OwnershipConflict('Honcho tool continuation differs from native Hermes history')
        if role == 'assistant' and not expected:
            calls = [_tool_selection(c) for c in actual.get('tool_calls', [])]
            if not calls or calls != [_tool_selection(c,stored=True) for c in json.loads(native['tool_calls'] or '[]')]:
                raise OwnershipConflict('Honcho continuation lacks its selected native tools')
            rounds.append(calls)
            expected = {call['id']:call['name'] for call in calls}
        elif (role == 'tool' and actual.get('tool_call_id') == native['tool_call_id']
              and native['tool_call_id'] in expected
              and expected.pop(native['tool_call_id'],None) == native['tool_name']):
            continue
        else:
            raise OwnershipConflict('Unexpected Honcho continuation role or result')
    identifiers = [call['id'] for sequence in rounds for call in sequence]
    if expected or len(identifiers) != len(set(identifiers)):
        raise OwnershipConflict('Unfinished or repeated Honcho tool result')
    return rounds


def driver_slot(scope, body):
    from agent.memory_context import build_memory_context_block
    from agent.memory_manager_tools import normalize_tool_schema
    from agent.portal_tags import get_conversation_context
    from providers import get_provider_profile
    from .api_input_trace import rows
    from .provider_loader import provider_module
    from .workflow_daily_documents import compile_daily_week
    from .workflow_runner import inspect_source_cutoff

    validate_request(body,profile=PROFILE)
    root, turn = Path(scope['root']), question(scope)
    current = active(scope)
    sid = current['session_id']
    affinity = get_provider_profile('openrouter').build_extra_body(session_id=sid)['session_id']
    stock = [{'type':'function','function':normalize_tool_schema(tool)}
             for tool in provider_module('honcho').ALL_TOOL_SCHEMAS]
    name = lambda schema: schema['function']['name']
    if (sid != get_conversation_context() or body.get('session_id') != affinity
            or sorted(map(name,body['tools'])) != sorted(TOOLS)
            or sorted(body['tools'],key=name) != sorted(stock,key=name)
            or body.get('stream') is not True or body.get('stream_options') != {'include_usage':True}
            or body.get('reasoning') != {'enabled':True,'effort':'medium'}
            or body.get('max_completion_tokens') != 32768
            or body.keys() - {'model','provider','messages','tools','session_id','stream','stream_options',
                              'reasoning','max_completion_tokens'}):
        raise OwnershipConflict('Honcho driver affinity, stream or stock tool surface differs')
    documents = compile_daily_week()[1]
    prior, names, prior_prompts = [], [documents[0].filename], []
    context_trace = root/'reuse-context.jsonl'
    if scope.get('week_schedule'):
        from .honcho_week_state import active as week_active, plan, prior_surface
        from .workflow_native import appended_rows

        _, owner, names, index = week_active(scope)
        saved = prior_surface(scope,index)
        prior = saved['history'] if saved else []
        prior_prompts = [past.prompt for past in plan()[1][:index]]
        observed = appended_rows(context_trace,owner['cursor'])
    else:
        observed = rows(context_trace)
    observations = [r for r in observed if r['session_id']==sid and r['query']==turn.prompt]
    if len(observations) != 1 or not isinstance(observations[0]['prefetch'],str):
        raise OwnershipConflict('No unique stock Honcho memory prefetch for the active question')
    prefetch = observations[0]['prefetch']
    block = build_memory_context_block(prefetch)
    user = turn.prompt + ('\n\n'+block if block else '')
    messages = body['messages']
    boundary = len(prior)+1
    if (len(messages)<=boundary or messages[0]['role']!='system' or not isinstance(messages[0]['content'],str)
            or '# Project Context' in messages[0]['content'] or messages[boundary]!={'role':'user','content':user}):
        raise OwnershipConflict('Honcho driver prompt or native recall differs')
    system, stored = _history(root/'hermes-home',sid)
    if system != messages[0]['content']:
        raise OwnershipConflict('Honcho system prompt differs from the persisted native prompt')
    if prior:
        verify_prior(prior,stored[:len(prior)],messages[1:boundary])
        stored = stored[len(prior):]
    if stored and (stored[0]['role']!='user' or stored[0]['content']!=turn.prompt
                   or (stored[0]['api_content'] or turn.prompt)!=user):
        raise OwnershipConflict('Honcho native question row differs from its scoped submission')
    rounds = tool_rounds(messages[boundary+1:],stored[1:])
    inspect_source_cutoff({'model_requests':[body],'prefetch':prefetch},documents,names,[*prior_prompts,turn.prompt])
    return dict(provider='honcho',kind='answer',question_id=turn.id,
                honcho_first_answer={**current,'tool_rounds':rounds})


def verify_prior(prior,live,wire):
    from .workflow_runner import _tool_call_contract, _wire_tool_text

    for saved,native,sent in zip(prior,live,wire,strict=True):
        role = saved['role']
        stored = saved.get('api_content') or saved.get('content') or ''
        delivered = sent.get('content') or ''
        if (any(saved.get(key)!=value for key,value in native.items()) or sent['role']!=role
                or delivered!=stored and not (role=='user' and delivered==saved['content'])
                and not (role=='tool' and _wire_tool_text(stored,delivered))
                or role=='tool' and sent.get('tool_call_id')!=saved['tool_call_id']
                or [_tool_call_contract(c,stored=True) for c in json.loads(saved['tool_calls'] or '[]')]
                != [_tool_call_contract(c) for c in sent.get('tool_calls') or []]):
            raise OwnershipConflict('Honcho driver changed prior native history or tool results')


def permit(scope, project, body):
    from .direct_accounting import stop_measured_admission
    from .workflow_capability import ENV

    try:
        slot = driver_slot(scope,body)
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        stop_measured_admission('honcho_first_question_unverified_driver')
        raise
    return dict(root=scope['root'],run_id=scope['run_id'],capability=json.loads(os.environ[ENV]),projects=[project],slot=slot)


def confirm_sender(row):
    from .api_input_trace import rows
    from .workflow_capability import load

    scope = load()
    [captured] = [entry for entry in rows(Path(scope['root'])/'api-inputs.jsonl') if entry['request_id'] == row['id']]
    owner, slot = row['dispatch_owner'],row['workflow_slot']
    if (captured['protocol']!='workflow-driver-input-v1' or captured['project']!=row['project']
            or (captured['pid'],captured['process_start_ticks']) != (owner['pid'],owner['start_ticks'])
            or captured['turn_nonce'] != slot['honcho_first_answer']['turn_nonce']
            or captured['canonical_request_sha256'] != row['request_sha256']
            or digest(captured['request']) != row['request_sha256']
            or driver_slot(scope,captured['request']) != slot):
        raise OwnershipConflict('Honcho first-answer wire or operation changed before dispatch')


def check_prepared(rows,row):
    from .direct_accounting import stop_measured_admission

    proof = row['workflow_slot']['honcho_first_answer']
    previous = [r for r in rows.values() if 'honcho_first_answer' in r['workflow_slot']
                and r['workflow_slot'].get('question_id')==row['workflow_slot'].get('question_id')]
    order = proof['tool_rounds']
    if len(previous) != len(order):
        stop_measured_admission('honcho_first_answer_duplicate_or_skipped')
        raise OwnershipConflict('Honcho opening or tool round was already attempted or skipped')
    if previous:
        last = previous[-1]
        if (last['workflow_slot']['honcho_first_answer'] != {**proof,'tool_rounds':order[:-1]}
                or not settled(last)
                or last.get('honcho_selected_tools') != order[-1]):
            stop_measured_admission('honcho_first_answer_unverified_tool_choice')
            raise OwnershipConflict('Honcho continuation did not come from an exact settled tool decision')


def settled(row):
    from .openrouter_route import PROFILES

    validation = row.get('response_validation', {})
    # Decoding the terminal SSE frame can close the native iterator before HTTP EOF.
    terminal = (row.get('dispatch_execution') == 'response_ended' and row.get('response_body_complete') is True
                or 'honcho_first_answer' in row.get('workflow_slot',{})
                and row.get('dispatch_execution') == 'ambiguous' and validation.get('sse_done_observed') is True)
    return (row.get('state') == 'settled' and row.get('http_status') == 200
            and terminal and bool(validation.get('finish_reasons')) and not validation.get('envelope_errors')
            and not validation.get('wire_parse_errors')
            and row.get('served_model') == PROFILES[PROFILE]['model'] and row.get('served_provider') == 'Amazon Bedrock'
            and bool(row.get('billing_receipt')) and bool(row.get('upstream_request_id')))


def verify_receipts(rows):
    from .direct_accounting import stop_measured_admission

    completed = [row for row in rows.values() if row.get('finished')]
    identifiers = {row.get('upstream_request_id') for row in completed}
    if len(identifiers) != len(completed) or not all(map(settled, completed)):
        stop_measured_admission('honcho_first_answer_unsettled_or_duplicate_receipt')
        raise OwnershipConflict('Honcho requires unique settled Luna Bedrock receipts before another request')


def selected_tools(events):
    """Retain only the identifiers and hashes of model-selected driver tools."""
    calls = {}
    stopped = False
    for event in events:
        for choice in event.get('choices') or []:
            if choice.get('index',0) != 0:
                return None
            if choice.get('finish_reason') == 'tool_calls':
                stopped = True
            message = choice.get('delta') or choice.get('message') or {}
            for k, piece in enumerate(message.get('tool_calls') or []):
                index = piece.get('index',k)
                dest = calls.setdefault(index,dict(id='',type='function',function=dict(name='',arguments='')))
                dest['id'] += piece.get('id') or ''
                for key in ('name','arguments'):
                    dest['function'][key] += (piece.get('function') or {}).get(key) or ''
    if not stopped or not calls or sorted(calls)!=list(range(len(calls))):
        return None
    try:
        selected = [_tool_selection(calls[i]) for i in range(len(calls))]
    except (ValueError,RuntimeError,KeyError,TypeError):
        return None
    return selected if len({call['id'] for call in selected})==len(selected) else None
