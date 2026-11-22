"""Stock Hermes driver manifest at the reviewed MemCal Week revision."""
import hashlib
import json

from .provider_versions import MEMCAL_WEEK_REVISION, MEMCAL_WEEK_TREE


# SHA-256 of the complete OpenAI function envelopes, sorted by function name.
# UTF-8 JSON, sorted keys, compact separators. Only catalog order is immaterial.
SCHEMAS_SHA256 = 'e75e7a0cedbac49f5c21a363dd96717fbdb47a4fcfa0dd179288d7e27fecd069'
TOOLS = ('memcal_activity', 'memcal_add', 'memcal_alias', 'memcal_answer',
         'memcal_conversation', 'memcal_drop', 'memcal_list_days', 'memcal_list_month',
         'memcal_merge', 'memcal_move_once', 'memcal_note', 'memcal_open',
         'memcal_open_source', 'memcal_refresh', 'memcal_reviewed', 'memcal_schedule',
         'memcal_search_archive', 'memcal_search_wiki', 'memcal_todo', 'memcal_update')


def manifest():
    return dict(memcal_revision=MEMCAL_WEEK_REVISION, memcal_tree=MEMCAL_WEEK_TREE,
                driver_schemas_sha256=SCHEMAS_SHA256)


def verify_driver_schemas(tools):
    if not isinstance(tools, list):
        raise ValueError('missing MemCal driver schemas')
    try:
        ordered = sorted(tools, key=lambda t: t['function']['name'])
        encoded = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('malformed MemCal driver schemas') from exc
    if hashlib.sha256(encoded.encode()).hexdigest() != SCHEMAS_SHA256:
        raise ValueError('MemCal driver schemas differ from the pinned stock manifest')


def verify_captured_driver(request):
    from .canary_capability import digest
    body = request.get('request')
    if (not isinstance(body, dict) or not request.get('request_id') or not request.get('messages')
            or request.get('canonical_request_sha256') != digest(body)
            or body.get('messages') != request.get('messages') or body.get('model') != request.get('model')
            or sorted(request.get('tools', [])) != list(TOOLS)):
        raise ValueError('MemCal driver capture differs from its recorded request')
    verify_driver_schemas(body.get('tools'))
