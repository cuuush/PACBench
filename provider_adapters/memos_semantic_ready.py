"""Opt-in MemOS 2.0.21 native embedding maintenance, never fact extraction."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import time

RAW = 'native-raw-document-import-v1'
SEMANTIC = 'native-raw-document-semantic-ready-v1'
CONDITIONS = (RAW, SEMANTIC)
ENVIRONMENT = 'PACBENCH_MEMOS_INGEST_CONDITION'
BATCH_LIMIT = 100


def selected_condition():
    value = os.environ.get(ENVIRONMENT, RAW)
    if value not in CONDITIONS:
        raise ValueError('Unknown MemOS ingest condition')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_stats(value, *, complete=False):
    """Validate the native maintenance counters, not a memory/fact quota."""
    fields = ('totalSlots', 'ready', 'missing', 'dimMismatch', 'needsRepair')
    if (not isinstance(value, dict) or value.get('available') is not True or
            type(value.get('dimension')) is not int or value['dimension'] <= 0):
        return False
    buckets = value.get('byKind', {})
    if not isinstance(buckets,dict) or set(buckets) != {'trace', 'policy', 'world_model', 'skill'}:
        return False
    for bucket in [value, *buckets.values()]:
        if not isinstance(bucket, dict) or any(type(bucket.get(k)) is not int or bucket[k] < 0 for k in fields):
            return False
        if (bucket['ready'] + bucket['missing'] + bucket['dimMismatch'] != bucket['totalSlots'] or
                bucket['needsRepair'] != bucket['missing'] + bucket['dimMismatch']):
            return False
    if any(value[k] != sum(b[k] for b in buckets.values()) for k in fields):
        return False
    # Our qualifying raw import has no lightweight tag: native summary + action
    # slots. All eligible slots in this isolated store must be dimension-ready.
    return buckets['trace']['totalSlots'] >= 2 and (not complete or value['needsRepair'] == 0)


def validate_bounds(timeout_s,max_batches):
    if (not math.isfinite(timeout_s) or timeout_s <= 0 or type(max_batches) is not int or max_batches <= 0):
        raise ValueError('Finite positive MemOS repair bounds required')


def valid_batch(result):
    return (isinstance(result,dict) and result.get('mode')=='repair' and type(result.get('done')) is bool and
        all(type(result.get(k)) is int and result[k]>=0 for k in ('processed','updated','failed')) and
        result['processed']<=BATCH_LIMIT and result['updated']+result['failed']<=result['processed'] and
        valid_stats(result.get('statsAfter')))


def trace_vectors(database,readback,dimension,*,timeout_s=5):
    """Read-only per-ID proof; the public TraceDTO deliberately omits vectors."""
    database=Path(database).resolve()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=timeout_s) as db:
        row=db.execute('SELECT id,session_id,episode_id,user_text,agent_text,summary,vec_summary,vec_action FROM traces WHERE id=?',
                       (readback['id'],)).fetchone()
    expected=(readback['id'],readback['sessionId'],readback['episodeId'],readback['userText'],readback['agentText'],readback['summary'])
    if row is None or row[:6]!=expected:raise ValueError('Native vector row differs from submitted trace identity')
    result={'protocol':'memos-readonly-trace-vectors-v1','trace_id':readback['id'],
            'trace_identity_sha256':digest(list(expected)),'fields':{}}
    for name,blob in zip(('vec_summary','vec_action'),row[6:]):
        if not isinstance(blob,bytes) or len(blob)!=dimension*4:raise ValueError('Native trace vector missing or wrong dimension')
        values=struct.unpack('<'+'f'*dimension,blob)
        if not all(math.isfinite(v) for v in values) or not any(v!=0 for v in values):
            raise ValueError('Native trace vector is non-finite or all zero')
        result['fields'][name]={'dimension':dimension,'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest(),
                              'finite':True,'nonzero':True}
    return result


def repair(endpoint, readback, source_sha256, *, call, proof, database, timeout_s=120, max_batches=50):
    validate_bounds(timeout_s,max_batches)
    started = time.monotonic()
    deadline = started + timeout_s
    proof.update(protocol=SEMANTIC, state='incomplete', trace_id=readback['id'],
        document_sha256=source_sha256, trace_before_sha256=digest(readback),
        batch_limit=BATCH_LIMIT, max_batches=max_batches, wall_deadline_s=timeout_s, batches=[],
        vector_scope={'vec_summary': 'Native embedding of the raw summary/document',
            'vec_action': 'Native empty-action placeholder for this non-lightweight raw import',
            'other_slots': 'Existing policy/world_model/skill vectors only; no records or facts are created'},
        extraction_performed=False)

    def fail(reason):
        proof.update(failure=reason, elapsed_s=time.monotonic()-started)
        return False

    def bounded(path, body=None):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('MemOS embedding maintenance deadline exceeded')
        return call(endpoint, path, body, remaining)

    # Both native eligibility implementations (JS capture and SQL maintenance)
    # qualify these whole documents. Refuse a semantic claim for tiny traces
    # that native maintenance intentionally excludes; this is no fact quota.
    if (len(readback['userText'].strip()) < 20 or readback.get('agentText') or
            readback.get('toolCalls') or 'lightweight_memory' in readback.get('tags', [])):
        return fail('raw_trace_not_qualified_for_this_semantic_condition')
    try:
        proof['before'] = bounded('/api/v1/embeddings/maintenance')
        if not valid_stats(proof['before']):
            return fail('embedding_provider_or_native_slot_counters_unverified')
        for _ in range(max_batches):
            result = bounded('/api/v1/embeddings/rebuild', {'mode':'repair', 'limit':BATCH_LIMIT})
            proof['batches'].append(result)
            if not valid_batch(result):
                return fail('invalid_native_repair_response')
            if result.get('error'):
                return fail('native_embedding_repair_error')
            if result['done']:
                if not valid_stats(result['statsAfter'], complete=True):
                    return fail('native_done_does_not_prove_vectors_ready')
                break
            if result['updated'] == 0:
                return fail('native_embedding_repair_made_no_progress')
        else:
            return fail('native_embedding_repair_batch_limit_exceeded')
        proof['after'] = bounded('/api/v1/embeddings/maintenance')
        final = bounded('/api/v1/memory/trace?id='+readback['id'])
        proof['trace_after_sha256'] = digest(final)
        proof['trace_identity_unchanged'] = final == readback
        if not valid_stats(proof['after'], complete=True) or final != readback:
            return fail('final_vectors_or_original_trace_identity_unverified')
        remaining=deadline-time.monotonic()
        if remaining<=0:return fail('native_embedding_repair_deadline_exceeded')
        proof['trace_vectors']=trace_vectors(database,final,proof['after']['dimension'],timeout_s=min(5,remaining))
        if time.monotonic() >= deadline:
            return fail('native_embedding_repair_deadline_exceeded')
        proof.update(state='ready', elapsed_s=time.monotonic()-started)
        return True
    finally:
        proof['elapsed_s'] = time.monotonic()-started


def proof_valid(proof, readback, source_sha256):
    """Offline receipt validation; no live service or inference is needed."""
    vectors=proof.get('trace_vectors',{}) if isinstance(proof,dict) else {}
    expected=[readback.get(k) for k in ('id','sessionId','episodeId','userText','agentText','summary')]
    fields=vectors.get('fields',{})
    return (isinstance(proof, dict) and proof.get('protocol') == SEMANTIC and proof.get('state') == 'ready' and
        not proof.get('failure') and proof.get('trace_id') == readback.get('id') and
        proof.get('document_sha256') == source_sha256 and proof.get('extraction_performed') is False and
        proof.get('trace_identity_unchanged') is True and
        proof.get('trace_before_sha256') == proof.get('trace_after_sha256') == digest(readback) and
        proof.get('batch_limit') == BATCH_LIMIT and type(proof.get('max_batches')) is int and
        0 < len(proof.get('batches', [])) <= proof['max_batches'] and
        valid_stats(proof.get('before')) and valid_stats(proof.get('after'), complete=True) and
        proof['batches'][-1].get('done') is True and
        valid_stats(proof['batches'][-1].get('statsAfter'), complete=True) and
        all(valid_batch(b) and not b.get('error') for b in proof['batches']) and
        all(b['done'] is False and b['updated']>0 for b in proof['batches'][:-1]) and
        vectors.get('protocol')=='memos-readonly-trace-vectors-v1' and vectors.get('trace_id')==readback.get('id') and
        vectors.get('trace_identity_sha256')==digest(expected) and set(fields)=={'vec_summary','vec_action'} and
        all(v.get('dimension')==proof['after']['dimension'] and v.get('bytes')==proof['after']['dimension']*4 and
            isinstance(v.get('sha256'),str) and len(v['sha256'])==64 and v.get('finite') is True and v.get('nonzero') is True for v in fields.values()) and
        isinstance(proof.get('elapsed_s'), (int,float)) and isinstance(proof.get('wall_deadline_s'), (int,float)) and
        0 <= proof['elapsed_s'] < proof['wall_deadline_s'])
