"""Exact provider tools restricted to retained-memory retrieval, never source rescue."""
from .followups import SOURCE_CATEGORIES, tool_category
from .source_policy import SOURCE_ARGUMENT_RULES

VERSION = 'retained-memory-only-v2'


def retained_tools(native_tools):
    if not isinstance(native_tools,(list,tuple)) or not native_tools or any(
            not isinstance(n,str) or not n or '*' in n for n in native_tools):
        raise ValueError('Provider supplied no exact native tool allowlist')
    names = sorted(set(native_tools))
    blocked = [n for n in names if n not in SOURCE_ARGUMENT_RULES and tool_category(n,{}) in SOURCE_CATEGORIES]
    allowed = [n for n in names if n not in blocked]
    if not allowed:
        raise ValueError('Provider supplied no retained-memory tools')
    return allowed, blocked


def policy_record(provider, native_tools):
    allowed,blocked = retained_tools(native_tools)
    return {'version':VERSION,'provider':provider,'allowed_tools':allowed,
        'blocked_source_tools':blocked,'source_argument_rules':SOURCE_ARGUMENT_RULES,'builtin_memory':False,'os_sandbox':False}
