"""Attribute native worker input to independently witnessed conversation bytes."""
from hashlib import sha256
import json
import os
import time

from .canary_capability import digest
from .dispatch_ownership import OwnershipConflict
from .supermemory_source_effect import NATIVE_ID
from .supermemory_week_observation import verify_native_version
from .supermemory_worker_rounds import HEX, tool_reply

PROTOCOL = 'stock-supermemory-active-chunks-v1'


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
    return dict(protocol=PROTOCOL, at_ms=time.time_ns() // 1_000_000, publication=publication,
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
