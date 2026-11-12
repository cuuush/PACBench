"""Allowlisted metering metadata; never persist arbitrary provider payloads."""
from __future__ import annotations

import re

# Accept only known opaque identifier shapes. In particular, do not persist
# arbitrary response fields, headers, prompts, or upstream error bodies.
_ID = re.compile(r'^(?:gen-[A-Za-z0-9-]{1,123}|req-[A-Za-z0-9-]{1,123}|chatcmpl-[A-Za-z0-9_-]{1,119}|resp_[A-Za-z0-9_-]{1,123}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$')
_TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens',
                 'total_tokens', 'reasoning_tokens', 'cached_tokens',
                 'cache_read_tokens', 'cache_write_tokens', 'audio_tokens', 'image_tokens',
                 'accepted_prediction_tokens', 'rejected_prediction_tokens')
_DETAILS = ('prompt_tokens_details', 'completion_tokens_details',
            'input_tokens_details', 'output_tokens_details')
_MAX = 1_048_576


def safe_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('upstream_request_id', 'generation_id'):
        v = value.get(key)
        if isinstance(v, str) and _ID.fullmatch(v):
            result[key] = v
    usage = value.get('usage')
    if isinstance(usage, dict):
        safe = {}
        for key, v in usage.items():
            # Keys are canonical paths assembled below, not arbitrary JSON.
            parts = key.split('.') if isinstance(key, str) else []
            if (len(parts) == 1 and parts[0] in _TOKEN_FIELDS or
                len(parts) == 2 and parts[0] in _DETAILS and parts[1] in _TOKEN_FIELDS):
                if type(v) is int and 0 <= v <= _MAX:
                    safe[key] = v
        if safe:
            result['usage'] = safe
    return result


def observe_metadata(target, *, value=None, headers=None):
    candidate = {}
    if headers is not None:
        candidate['upstream_request_id'] = headers.get('X-Request-ID')
    if isinstance(value, dict):
        obj = value.get('response') if isinstance(value.get('response'), dict) else value
        candidate['generation_id'] = obj.get('id')
        usage = obj.get('usage')
        if isinstance(usage, dict):
            fields = {k: usage[k] for k in _TOKEN_FIELDS if k in usage}
            for group in _DETAILS:
                detail = usage.get(group)
                if isinstance(detail, dict):
                    fields.update({group+'.'+k: detail[k] for k in _TOKEN_FIELDS if k in detail})
            candidate['usage'] = fields
    candidate = safe_metadata(candidate)
    if 'usage' in candidate:
        target.setdefault('usage', {}).update(candidate.pop('usage'))
    target.update(candidate)
