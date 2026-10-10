"""Exact provider tools restricted to retained-memory retrieval, never source rescue."""
from .followups import SOURCE_CATEGORIES, tool_category

VERSION = 'retained-memory-only-v1'


def retained_tools(native_tools):
    if not isinstance(native_tools,(list,tuple)) or not native_tools or any(
            not isinstance(n,str) or not n or '*' in n for n in native_tools):
        raise ValueError('Provider supplied no exact native tool allowlist')
    names = sorted(set(native_tools))
    # A mixed memory/resource reader cannot be granted wholesale under a name-only
    # boundary. Keep search/browse; exclude that reader until argument guards exist.
    blocked = [n for n in names if tool_category(n,{}) in SOURCE_CATEGORIES or n=='viking_read']
    allowed = [n for n in names if n not in blocked]
    if not allowed:
        raise ValueError('Provider supplied no retained-memory tools')
    return allowed, blocked


def policy_record(provider, native_tools):
    allowed,blocked = retained_tools(native_tools)
    return {'version':VERSION,'provider':provider,'allowed_tools':allowed,
        'blocked_source_tools':blocked,'builtin_memory':False,'os_sandbox':False}
