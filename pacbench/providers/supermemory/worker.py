"""Verify stock worker tool rounds and the native chunks they read."""
from hashlib import sha256
import json
import os
import re
import time

from ...canary_capability import digest
from ...dispatch_ownership import OwnershipConflict

from .observation import verify_native_version
from .source_effect import NATIVE_ID

ROUND_PROTOCOL = 'stock-supermemory-worker-round-v1'
CHUNK_PROTOCOL = 'stock-supermemory-active-chunks-v1'
IDENT = re.compile(r'[A-Za-z0-9_-]{1,256}')
HEX = re.compile(r'[0-9a-f]{64}')


def witness(effect):
    evidence = json.loads(os.environ.get('PACBENCH_SUPERMEMORY_ROUND_WITNESS', 'null'))
    if (not isinstance(evidence, dict) or set(evidence) != {'effect', 'visit', 'results'}
            or evidence['effect'] != {'updateId': effect['update_id'], 'documentId': effect['document_id']}
            or not isinstance(evidence['visit'], str) or not re.fullmatch('[0-9a-f-]{36}', evidence['visit'])
            or not isinstance(evidence['results'], dict)):
        raise OwnershipConflict('Stock Supermemory worker round has no physical visit witness')
    for ident, item in evidence['results'].items():
        if (not IDENT.fullmatch(ident) or not isinstance(item, dict) or set(item) != {'name', 'content_sha256'}
                or not isinstance(item['name'], str) or not IDENT.fullmatch(item['name'])
                or not isinstance(item['content_sha256'], str) or not HEX.fullmatch(item['content_sha256'])):
            raise OwnershipConflict('Stock Supermemory tool result observation is invalid')
    return evidence


def _pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate tool argument field')
    return result


def tool_reply(message):
    content = message.get('content')
    reasoning = message.get('reasoning_content')
    if reasoning is None:
        reasoning = message.get('reasoning')
    if any(value is not None and not isinstance(value, str) for value in (content, reasoning)):
        raise ValueError('Unexpected stock assistant tool content')
    calls = message.get('tool_calls')
    if not isinstance(calls, list) or not calls:
        raise ValueError('Stock tool response requires at least one call')
    result = []
    for call in calls:
        ident, fn = call['id'], call['function']
        if (call['type'] != 'function' or not isinstance(ident, str) or not IDENT.fullmatch(ident)
                or not isinstance(fn['name'], str) or not IDENT.fullmatch(fn['name'])):
            raise ValueError('Unexpected stock tool identity')
        arguments = json.loads(fn['arguments'], object_pairs_hook=_pairs)
        if not isinstance(arguments, dict):
            raise ValueError('Stock tool arguments must be an object')
        result.append(dict(id=ident, name=fn['name'], arguments_sha256=digest(arguments)))
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('Stock worker repeated a tool-call ID within one reply')
    return dict(content_sha256=sha256((content or '').encode()).hexdigest(),
                reasoning_sha256=sha256((reasoning or '').encode()).hexdigest(), calls=result)


def observed_response(row, frames, outcome):
    """Use the complete authenticated response body before the SDK sees it."""
    if (row['accounting_role'] != 'memory_provider' or not row['workflow_slot'].get('worker_round')
            or outcome['http_status'] != 200 or not outcome['response_body_complete']
            or frames.sse or frames.errors or len(frames.events) != 1):
        return None
    event = frames.events[0]
    try:
        choice, = event['choices']
        message = choice['message']
        if (event.get('error') or choice.get('index') != 0 or choice['finish_reason'] != 'tool_calls'
                or message.get('role') != 'assistant' or not isinstance(event.get('id'), str)
                or event['id'] != outcome.get('upstream_request_id', row.get('upstream_request_id'))):
            return None
        return {'generation_id': event['id'], 'reply': tool_reply(message)}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None  # Never synthesize tool evidence from an incomplete response.


def describe_request(body, evidence):
    messages = body['messages']
    if body.get('stream') or not isinstance(messages, list) or not messages:
        raise OwnershipConflict('Unsupported stock worker request modality')
    reply_indices = [i for i, message in enumerate(messages) if message['role'] == 'assistant']
    prefix = len(messages)
    reply, results = None, {}
    if reply_indices:
        prefix = reply_indices[-1]
        assistant = messages[prefix]
        if assistant.keys() - {'role', 'content', 'reasoning_content', 'tool_calls'}:
            raise OwnershipConflict('Stock worker continuation added unwitnessed assistant fields')
        reply = tool_reply(assistant)
        outputs = messages[prefix + 1:]
        if len(outputs) != len(reply['calls']):
            raise OwnershipConflict('Stock worker continuation requires each selected tool result exactly once')
        for call, message in zip(reply['calls'], outputs, strict=True):
            ident = call['id']
            content = message.get('content')
            if (message.keys() - {'role', 'tool_call_id', 'name', 'content'} or message['role'] != 'tool'
                    or message.get('tool_call_id') != ident or message.get('name', call['name']) != call['name']
                    or not isinstance(content, str) or ident in results):
                raise OwnershipConflict('Stock worker tool result did not match the selected call')
            verified = dict(name=call['name'], content_sha256=sha256(content.encode()).hexdigest())
            if evidence['results'].get(ident) != verified:
                raise OwnershipConflict('Stock worker tool reply differs from independent native result')
            results[ident] = verified
    elif evidence['results'] or any(m['role'] not in ('system', 'user') for m in messages):
        raise OwnershipConflict('Stock worker opening has unexplained tool history')
    return dict(visit=evidence['visit'], ordinal=len(reply_indices), prefix_count=prefix, prefix_sha256=digest(messages[:prefix]),
                messages_count=len(messages), messages_sha256=digest(messages), reply=reply, results=results,
                options_sha256=digest({k: v for k, v in body.items() if k != 'messages'}))


def check_history(rows, row):
    slot = row['workflow_slot']
    effect = slot['stock_source_effect']
    current = slot['worker_round']
    previous = [r for r in rows if (r['workflow_slot'].get('stock_source_effect') or {}).get('update_id') == effect['update_id']]
    if len(previous) != current['ordinal']:
        raise OwnershipConflict('Stock Supermemory worker round already prepared or skipped')
    if not previous:
        return
    prior = previous[-1]
    prior_slot = prior['workflow_slot']
    prepared = prior_slot.get('worker_round')
    observed = prior.get('stock_worker_tool_response')
    if (prior_slot['stock_source_effect'] != effect or not prepared or not observed or not prior.get('finished')
            or prior.get('http_status') != 200 or prior.get('dispatch_execution') != 'response_ended'
            or prior.get('error_label') == 'conflicting_receipts'
            or observed['generation_id'] != prior.get('upstream_request_id')
            or prepared['visit'] != current['visit'] or prior['dispatch_owner'] != row['dispatch_owner']
            or current['ordinal'] != prepared['ordinal'] + 1
            or (current['prefix_sha256'], current['prefix_count']) != (prepared['messages_sha256'], prepared['messages_count'])
            or current['options_sha256'] != prepared['options_sha256'] or current['reply'] != observed['reply']):
        raise OwnershipConflict('Stock worker continuation differs from its completed tool-call response and prior transcript')
    older_ids = {ident for item in previous for ident in item['workflow_slot']['worker_round']['results']}
    if older_ids.intersection(current['results']):
        raise OwnershipConflict('Stock Supermemory reused an earlier tool-call identity')


def confirm(prepared, evidence):
    if (prepared['visit'] != evidence['visit'] or any(evidence['results'].get(k) != v
                                                     for k, v in prepared['results'].items())):
        raise OwnershipConflict('Stock Supermemory tool round changed before dispatch')


def _text_sha(text):
    return sha256(text.encode()).hexdigest()


def _chunks(rows):
    if (not isinstance(rows, list) or any(
            not isinstance(row, dict) or set(row) != {'id', 'position', 'sha256'}
            or not isinstance(row['id'], str) or not NATIVE_ID.fullmatch(row['id'])
            or type(row['position']) is not int or row['position'] < 0
            or not isinstance(row['sha256'], str) or not HEX.fullmatch(row['sha256'])
            for row in rows)):
        raise OwnershipConflict('Invalid native Supermemory chunk identities')
    positions = [row['position'] for row in rows]
    if positions != sorted(set(positions)) or len({row['id'] for row in rows}) != len(rows):
        raise OwnershipConflict('Ambiguous native Supermemory chunk order')
    return rows


def capture_anchor(before, wire, after, document, *, native_id, namespace):
    """Bracket the separate stock v5 chunk GET by the active publication marker."""
    revision = verify_native_version(before, document, native_id=native_id, namespace=namespace, wire=wire)
    stable = ('activeContentUpdateId', 'latestRevision', 'status', 'updatedAt', 'createdAt',
              'content', 'metadata', 'title', 'source', 'filepath')
    if (not isinstance(publication := before.get('activeContentUpdateId'), str)
            or not NATIVE_ID.fullmatch(publication) or any(before.get(k) != after.get(k) for k in stable)
            or verify_native_version(after, document, native_id=native_id, namespace=namespace, wire=wire) != revision
            or wire['system']['status'] != 'done'):
        raise OwnershipConflict('Supermemory publication changed during the independent chunk readback')
    rows = wire.get('chunks')
    if not rows:
        raise OwnershipConflict('Supermemory capture has no active chunk readback')
    chunks = _chunks([dict(id=row['id'], position=row['position'], sha256=_text_sha(row['content'])) for row in rows])
    return dict(protocol=CHUNK_PROTOCOL, at_ms=time.time_ns() // 1_000_000, publication=publication,
                revision=revision, chunks=chunks)


def read_witness(effect, visit):
    readback = json.loads(os.environ.get('PACBENCH_SUPERMEMORY_CHUNK_WITNESS', 'null'))
    if (not isinstance(readback, dict)
            or set(readback) != {'effect', 'visit', 'opening', 'prompt_sha256', 'tools'}
            or readback['effect'] != dict(updateId=effect.update_id, documentId=effect.document_id)
            or readback['visit'] != visit or not isinstance(readback['tools'], dict)
            or not isinstance(readback['prompt_sha256'], str) or not HEX.fullmatch(readback['prompt_sha256'])):
        raise OwnershipConflict('Supermemory capture lacks an exact native chunk witness')
    return readback


def _selected_positions(arguments):
    if not isinstance(arguments, dict):
        raise OwnershipConflict('Missing native Supermemory readChunks arguments')
    positions = arguments.get('positions', [])
    start, count = arguments.get('start', 0), arguments.get('count', 5)
    if (arguments.keys() - {'positions', 'start', 'count'} or not isinstance(positions, list)
            or len(positions) > 20 or any(type(pos) is not int or pos < 0 for pos in positions)
            or type(start) is not int or start < 0 or type(count) is not int or not 1 <= count <= 20):
        raise OwnershipConflict('Invalid stock Supermemory readChunks positions')
    return set(positions or range(start, start + count))


def _candidates(observed, anchors, *, opening):
    shape = {'at_ms', 'chunks'} | (set() if opening else {'result_sha256', 'arguments'})
    if (not isinstance(observed, dict) or set(observed) != shape
            or type(observed['at_ms']) is not int or observed['at_ms'] <= 0):
        raise OwnershipConflict('Supermemory capture lacks a timed native chunk read')
    rows = _chunks(observed['chunks'])
    if opening and not rows:
        raise OwnershipConflict('Supermemory opening had no bound capture chunks')
    positions = _selected_positions(observed['arguments']) if not opening else None
    candidates = []
    for entry in anchors:
        anchor = entry['readback']['chunk_anchor']
        if anchor['at_ms'] > observed['at_ms']:
            continue
        known = anchor['chunks']
        if rows == (known if opening else [row for row in known if row['position'] in positions]):
            candidates.append(dict(revision=anchor['revision'], publication=anchor['publication'],
                                   question_id=entry['question_id']))
    if not candidates:
        raise OwnershipConflict('Observed Supermemory chunks do not match an eligible verified capture')
    return dict(at_ms=observed['at_ms'], chunks_sha256=digest(rows), candidates=candidates)


def verify_reads(witness, anchors, round_evidence, body=None):
    """Match exact observed rows; partial or byte-identical reads can share candidates."""
    tools = {}
    for ident, result in round_evidence['results'].items():
        if result['name'] == 'readChunks':
            observed = witness['tools'].get(ident)
            if not isinstance(observed, dict) or observed.get('result_sha256') != result['content_sha256']:
                raise OwnershipConflict('Supermemory readChunks tool result lacks its own native chunk read')
            tools[ident] = {**_candidates(observed, anchors, opening=False),
                            'result_sha256': result['content_sha256'],
                            'arguments_sha256': digest(observed['arguments'])}
    if body is not None:
        original = body['messages'][:2]
        if (len(original) != 2 or [m['role'] for m in original] != ['system', 'user']
                or not isinstance(original[1].get('content'), str)
                or _text_sha(original[1]['content']) != witness['prompt_sha256']):
            raise OwnershipConflict('Supermemory wire prompt differs from the stock rendered capture prompt')
        calls = [call for message in body['messages'] if message['role'] == 'assistant' and message.get('tool_calls')
                 for call in tool_reply(message)['calls'] if call['name'] == 'readChunks']
        if len(calls) != len(tools) or {call['id']: call['arguments_sha256'] for call in calls} != {
                ident: detail['arguments_sha256'] for ident, detail in tools.items()}:
            raise OwnershipConflict('Supermemory wire readChunks calls differ from the native observed arguments')
    return dict(visit=witness['visit'], opening=_candidates(witness['opening'], anchors, opening=True),
                prompt_sha256=witness['prompt_sha256'], tools=tools)
