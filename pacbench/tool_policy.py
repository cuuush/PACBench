"""Exact shipped provider tools; source retrieval is scored separately."""
from .followups import SOURCE_CATEGORIES, tool_category
from .source_policy import SOURCE_ARGUMENT_RULES

RETAINED_ONLY_VERSION = 'retained-memory-only-v2'
VERSION = 'provider-memory-with-source-penalty-v3'
SUPPORTED_VERSIONS = (RETAINED_ONLY_VERSION, VERSION)
# The frozen legacy condition excluded mixed readers entirely. Its receipts
# remain auditable; new trials expose native source tools and penalize retrieval.
BLOCKED_MIXED_READERS = {'viking_read'}


def native_names(native_tools):
    if not isinstance(native_tools,(list,tuple)) or not native_tools or any(
            not isinstance(n,str) or not n or '*' in n for n in native_tools):
        raise ValueError('Provider supplied no exact native tool allowlist')
    return sorted(set(native_tools))


def retained_tools(native_tools):
    names = native_names(native_tools)
    blocked = [n for n in names if n in BLOCKED_MIXED_READERS or
               n not in SOURCE_ARGUMENT_RULES and tool_category(n,{}) in SOURCE_CATEGORIES]
    allowed = [n for n in names if n not in blocked]
    if not allowed:
        raise ValueError('Provider supplied no retained-memory tools')
    return allowed, blocked


def policy_record(provider, native_tools, *, version=VERSION):
    if version == RETAINED_ONLY_VERSION:
        allowed,blocked = retained_tools(native_tools)
        rules = SOURCE_ARGUMENT_RULES
    elif version == VERSION:
        if provider == 'reme' and native_tools == []:
            # Its pinned shipped integration supplies automatic prefetch only.
            # Provider-only dispatch and schema filtering also enforce [] as
            # deny-all; an empty allowlist must never enable built-in tools.
            allowed,blocked,rules = [],[],{}
        else:
            allowed,blocked,rules = native_names(native_tools),[],{}
    else:
        raise ValueError('Unknown provider tool policy')
    record = {'version':version,'provider':provider,'allowed_tools':allowed,
        'blocked_source_tools':blocked,'source_argument_rules':rules,'builtin_memory':False,'os_sandbox':False}
    if provider == 'reme' and not allowed:
        record['native_mode'] = 'automatic-prefetch-only'
    if version == VERSION:
        record['source_fallback_penalty'] = {'retention_weight':0.0,
            'answer_correctness_unchanged':True,
            'scope':'Source retrieval is permitted, logged and charged. A source-assisted turn earns no retention credit; actual costs are never inferred from the penalty.'}
    return record
