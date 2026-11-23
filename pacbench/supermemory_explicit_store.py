"""Bind a stock explicit store to its actual owned tool call and native document."""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from .canary_capability import digest
from .dispatch_ownership import OwnershipConflict
from .supermemory_source_effect import NATIVE_ID
from .supermemory_week_observation import PENDING

PROTOCOL = 'stock-supermemory-explicit-store-v1'
ENV = 'PACBENCH_SUPERMEMORY_EXPLICIT_STORE'
TOOLS = frozenset(('supermemory_store', 'supermemory-save'))


def write_once(path, value):
    """Publish complete immutable evidence without replacing an existing identity."""
    with NamedTemporaryFile(dir=path.parent, prefix='.write-', delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.link(tmp, path)
            fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            tmp.unlink()


def driver_receipts(root, claim):
    from .observational_receipts import replay_attempts

    # Dispatch confirmation can already hold the journal's flock. Read only
    # complete append records and require the exact terminal parent below.
    raw = (root / 'receipts.jsonl').read_bytes()
    rows = replay_attempts(raw[:raw.rfind(b'\n') + 1].splitlines())
    return [row for row in rows.values()
            if row['accounting_role'] == 'hermes'
            and row['workflow_slot'].get('supermemory_week_claim') == claim]


def operation_path(root, claim, tool_call_id):
    token = digest([claim['attempt_id'], tool_call_id])
    return root / 'explicit-tool-operations' / f'{token}.json'


def validate_payload(body, content, metadata):
    if (not isinstance(body, dict) or body.keys() - {'content', 'date', 'metadata', 'supportingContext', 'dreaming'}
            or not isinstance(content, str) or not content.strip() or body.get('content') != content.strip()
            or not isinstance(body.get('date'), str) or body.get('metadata') != metadata):
        raise OwnershipConflict('Native Supermemory explicit add differs from the selected stock tool')
    from .workflow_spec import instant
    instant(body['date'])


def observe_gateway():
    """Tap the exact SDK POST and receipt inside the pinned Hermes tool dispatcher."""
    import httpx
    from agent import tool_executor
    from .checkpoint_clock import real_time
    from .live_dispatch import process_identity
    from .provider_loader import provider_module
    from .supermemory_question_admission import driver_owner, terminal_response
    from .supermemory_week_admission import active_answer, plan
    from .workflow_capability import load
    from .workflow_runner import inspect_source_cutoff

    pending = ContextVar('supermemory_explicit_store', default=None)
    dispatch, send = tool_executor._dispatch_authorized_once, httpx.Client.send

    def observed_tool(name, args, session_id, tool_call_id, execute):
        scope = load()
        root = Path(scope['root'])
        turn, prior, claim, expected, prompts = active_answer(scope)
        session, nonce, submission = driver_owner(scope, turn, prior)
        drivers = driver_receipts(root, claim)
        if not drivers:
            raise OwnershipConflict('Supermemory store precedes its owned assistant response')
        previous = drivers[-1]
        route = previous['workflow_slot']['supermemory_first_question']
        if (session != session_id or not isinstance(tool_call_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', tool_call_id) or pending.get() is not None
                or route['turn_nonce'] != nonce or route['native_request_id'] != submission
                or previous['dispatch_owner'] != process_identity() or not terminal_response(previous)
                or 'tool_calls' not in previous['response_validation']['finish_reasons']):
            raise OwnershipConflict('Supermemory store has no owned active model-selected tool call')
        config = json.loads((root / 'native-server-setup.json').read_text())
        content = args.get('content')
        if not isinstance(content, str) or not content.strip():
            # Preserve the stock tool's validation for an empty argument.
            return execute(args)
        metadata = dict(args.get('metadata')) if isinstance(args.get('metadata'), dict) else {}
        metadata.setdefault('type', provider_module('supermemory')._detect_category(content.strip()))
        metadata.pop('source', None)
        metadata = {'sm_source': 'hermes', **metadata}
        context = dict(root=root, endpoint=config['endpoint'], content=content, metadata=metadata,
                       expected=[d.filename for d in expected], prompts=[*prompts, turn.prompt], sent=False,
                       binding=dict(protocol=PROTOCOL, namespace=scope['namespace'], owner=process_identity(),
                                    claim=claim, question_id=turn.id, session_id=session, nonce=nonce,
                                    submission=submission, parent_receipt=previous['id'], tool=name,
                                    tool_call_id=tool_call_id))
        token = pending.set(context)
        try:
            return execute(args)
        finally:
            pending.reset(token)

    @wraps(send)
    def observed_send(client, request, **kwargs):
        context = pending.get()
        if context is None:
            return send(client, request, **kwargs)
        facts = context['binding']
        endpoint = context['endpoint'] + '/ns/' + quote(facts['namespace'], safe='') + '/document'
        if context['sent'] or request.method != 'POST' or str(request.url) != endpoint:
            raise OwnershipConflict('Supermemory explicit tool crossed its single native add boundary')
        raw = request.content
        body = json.loads(raw)
        validate_payload(body, context['content'], context['metadata'])
        inspect_source_cutoff({'model_requests': [], 'prefetch': json.dumps(body, ensure_ascii=False)},
                              plan()[0], context['expected'], context['prompts'])
        operation = operation_path(context['root'], facts['claim'], facts['tool_call_id'])
        request_sha = sha256(raw).hexdigest()
        write_once(operation, dict(**facts, request_sha256=request_sha, payload_sha256=digest(body)))
        context['sent'] = True
        response = send(client, request, **kwargs)
        if response.is_success:
            ack = response.json()
            identifier = ack.get('id')
            if not isinstance(identifier, str) or not NATIVE_ID.fullmatch(identifier) or ack.get('status') not in PENDING | {'done'}:
                raise OwnershipConflict('Supermemory explicit add has no unique native acknowledgment')
            write_once(context['root'] / 'explicit-adds' / f'{identifier}.json',
                       dict(**facts, native_id=identifier, payload=body, payload_sha256=digest(body),
                            request_sha256=request_sha, acknowledgment=ack,
                            acknowledged_at_ms=int(real_time() * 1000), response_sha256=sha256(response.content).hexdigest()))
        return response

    @wraps(dispatch)
    def observed_dispatch(agent, state, ref, *, execute, **kwargs):
        if ref.name not in TOOLS:
            return dispatch(agent, state, ref, execute=execute, **kwargs)

        def send_owned_tool(args):
            return observed_tool(ref.name, args, agent.session_id, ref.call_id, execute)

        return dispatch(agent, state, ref, execute=send_owned_tool, **kwargs)

    tool_executor._dispatch_authorized_once = observed_dispatch
    httpx.Client.send = observed_send


def read_store(root, identifier, namespace):
    if not isinstance(identifier, str) or not NATIVE_ID.fullmatch(identifier):
        raise OwnershipConflict('Invalid Supermemory tool-created document identity')
    path = root / 'explicit-adds' / f'{identifier}.json'
    if not path.is_file() or path.resolve() != path:
        raise OwnershipConflict('Supermemory explicit result lacks an owned native add acknowledgment')
    entry = json.loads(path.read_text())
    operation = operation_path(root, entry['claim'], entry['tool_call_id'])
    original = json.loads(operation.read_text()) if operation.is_file() and operation.resolve() == operation else None
    expected = {k: v for k, v in entry.items() if k not in
                {'native_id', 'payload', 'acknowledgment', 'acknowledged_at_ms', 'response_sha256'}}
    if (expected != original or entry['protocol'] != PROTOCOL or entry['native_id'] != identifier
            or entry['namespace'] != namespace
            or entry['acknowledgment']['id'] != identifier or entry['acknowledgment']['status'] not in PENDING | {'done'}
            or digest(entry['payload']) != entry['payload_sha256']):
        raise OwnershipConflict('Supermemory explicit add binding changed')
    return entry


def match_driver(scope, slot, body):
    """Join the exact native acknowledgment to the stock result sent in this tool round."""
    from .supermemory_question_admission import terminal_response

    root = Path(scope['root'])
    directory = root / 'explicit-adds'
    if not directory.is_dir():
        raise OwnershipConflict('Missing Supermemory explicit add journal')
    route = slot['supermemory_first_question']
    claim = slot['supermemory_week_claim']
    groups = {ident: number for number, group in enumerate(route['tool_rounds']) for ident in group}
    adds = {}
    calls = {call['id']: call['function'] for msg in body['messages'] if msg['role'] == 'assistant'
             for call in msg.get('tool_calls') or () if call['id'] in groups and call['function']['name'] in TOOLS}
    parents = driver_receipts(root, claim) if calls else ()
    for message in body['messages']:
        ident = message.get('tool_call_id')
        if message['role'] != 'tool' or ident not in calls:
            continue
        result = json.loads(message['content'])
        if not isinstance(result, dict) or result.get('saved') is not True:
            continue  # A rejected stock tool may produce an ordinary error result.
        entry = read_store(root, result.get('id'), scope['namespace'])
        if groups[ident] >= len(parents):
            raise OwnershipConflict('Supermemory store lacks its original assistant tool response')
        parent = parents[groups[ident]]
        tool = calls[ident]
        args = json.loads(tool['arguments'])
        if ((entry['claim'], entry['question_id'], entry['session_id'], entry['nonce'], entry['submission'],
             entry['tool'], entry['tool_call_id'], entry['parent_receipt'], entry['owner'])
                != (claim, slot['question_id'], route['session_id'], route['turn_nonce'], route['native_request_id'],
                    tool['name'], ident, parent['id'], parent['dispatch_owner'])
                or args.get('content', '').strip() != entry['payload']['content']
                or not terminal_response(parent) or 'tool_calls' not in parent['response_validation']['finish_reasons']
                or entry['native_id'] in adds):
            raise OwnershipConflict('Supermemory native add and model-visible explicit tool result differ')
        adds[entry['native_id']] = ident
    observed = {f.stem for f in directory.glob('*.json') if json.loads(f.read_text())['claim'] == claim}
    if observed != adds.keys():
        raise OwnershipConflict('Unmatched native Supermemory explicit add acknowledgment')
    operations = [json.loads(path.read_text()) for path in (root / 'explicit-tool-operations').glob('*.json')]
    submitted = [row['tool_call_id'] for row in operations if row['claim'] == claim]
    if len(submitted) != len(adds) or set(submitted) != set(adds.values()):
        raise OwnershipConflict('Supermemory explicit add has an unresolved native response')
    return sorted(adds)
