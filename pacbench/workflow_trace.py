"""Inspect exact answer-visible records without clipping payloads or inferring retries."""
import json
from collections import Counter


def tool_activity(calls, results):
    if len(calls) != len(results):
        raise ValueError('incomplete tool trace')
    ids, requests, output = set(), Counter(), Counter()
    for call in calls:
        ident = call['tool_call_id']
        if not ident or ident in ids:
            raise ValueError('duplicate or missing tool attempt ID')
        ids.add(ident)
        requests[call['name'], json.dumps(call['input'], sort_keys=True)] += 1
    names = {c['tool_call_id']:c['name'] for c in calls}
    delivered = set()
    for result in results:
        ident = result['tool_call_id']
        if ident in delivered or ident not in names or names[ident] != result['tool']:
            raise ValueError('duplicate or mismatched tool result')
        delivered.add(ident)
        output[result['tool'], result['text']] += 1
    return {'explicit_attempts': len(ids), 'repeat_requests': len(ids)-len(requests),
            'repeat_results': len(ids)-len(output),
            'failed': sum(not r['success'] for r in results)}


def retry_activity(attempts):
    indexed = {}
    counts = Counter()
    for row in attempts:
        key, parent, origin = row['id'], row['parent'], row['origin']
        if not key or origin not in ('provider', 'harness', 'unknown'):
            raise ValueError('invalid attempt identity or origin')
        if key in indexed:
            if indexed[key] != row:
                raise ValueError('conflicting copies of one attempt')
            continue
        if parent is not None:
            if parent not in indexed or indexed[parent]['operation'] != row['operation']:
                raise ValueError('unresolved or unrelated retry parent')
            counts[origin] += 1
        indexed[key] = row
    return {'unique_attempts':len(indexed), 'linked_retries':dict(counts)}


def clip_evidence(stored, native, visible):
    """Only attribute clipping when both sides of that boundary were captured."""
    def comparison(a, b):
        if a is None or b is None:
            return 'unknown'
        if a == b:
            return 'identical'
        if b.endswith('...') and len(b[:-3]) < len(a) and a.startswith(b[:-3]):
            return 'clipped-prefix'
        return 'different'
    return {'store_to_provider_surface':comparison(stored, native),
            'provider_to_assistant':comparison(native, visible)}


def tool_text_boundary(witness, answer_visible):
    """Compare the same field when a native reader wraps source text in JSON."""
    path = witness.get('field_path', [])
    if not isinstance(path, list) or any(not isinstance(key, str) for key in path):
        raise ValueError('invalid tool field path')
    if path:
        answer_visible = json.loads(answer_visible)
        for key in path:
            answer_visible = answer_visible[key]
        if not isinstance(answer_visible, str):
            raise ValueError('tool text witness must identify a string')
    return {**clip_evidence(witness.get('stored'), witness.get('native'), answer_visible),
            'field_path':path}
