"""Authorize a stock worker's next tool round from two independent observations."""
from hashlib import sha256
import json
import os
import re

from .canary_capability import digest
from .dispatch_ownership import OwnershipConflict

PROTOCOL = 'stock-supermemory-worker-round-v1'
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
