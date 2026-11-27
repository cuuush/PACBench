"""Provider tool allowlists and argument-aware restrictions on native source reads."""
from __future__ import annotations

import hashlib
import json
import re

SOURCE_ARGUMENT_RULES = {
    'viking_read': {
        'scheme': 'viking',
        'memory_namespaces': ['user','agent','~'],
        'memory_path': '/memories/',
        'summary_suffixes': {'abstract':'/.abstract.md','overview':'/.overview.md'},
        'default_level': 'overview',
    },
}

def viking_read_result_category(text):
    """Classify the observed native response, including mixed URI batches."""
    import json
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return 'unclassified'
    if not isinstance(value, dict):
        return 'unclassified'
    rows = value.get('results', [value])
    if not isinstance(rows, list):
        return 'unclassified'
    categories = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('content'), str) or 'error' in row:
            continue
        uri, level = row.get('resolved_uri', ''), row.get('level')
        if not isinstance(uri, str) or level not in {'full', 'abstract', 'overview'}:
            continue
        if uri.startswith('viking://resources/') or uri == 'viking://resources':
            category = ('source_evidence' if level == 'full' or row.get('fallback') == 'content/read'
                        else 'source_summary')
        else:
            category = 'memory_detail'
        categories.add(category)
    return next((category for category in ('source_evidence', 'source_summary', 'memory_detail')
                 if category in categories), 'unclassified')


def proven_honcho_snapshot(result):
    """The shipped formatter labels recent raw messages separately from memory."""
    import json
    if (not isinstance(result,dict) or result.get('success') is not True or
            result.get('policy_blocked') or result.get('truncated')):return False
    try:value=json.loads(result.get('text',''))
    except (TypeError,ValueError):return False
    if not isinstance(value,dict) or set(value)!={'result'} or not isinstance(value['result'],str):return False
    text=value['result']
    if '## Recent messages' in text:return False
    return text.startswith(('## Summary\n','## Representation\n','## Card\n')) or text in {
        'No context available yet.','No context available.'}


def proven_viking_summary(arguments, result):
    """Use the pinned native reader's receipt, which labels full-text fallback.

    An overview request alone is insufficient: files can fall back to content/read.
    Only complete successful receipts matching every requested URI establish that
    the answer saw generated summaries rather than the original source.
    """
    import json
    if (not isinstance(result, dict) or result.get('success') is not True or
            result.get('policy_blocked') or result.get('truncated')):
        return False
    level = arguments.get('level') or 'overview'
    if level not in {'abstract', 'overview'}:
        return False
    raw = arguments.get('uris') or arguments.get('uri')
    values = raw if isinstance(raw, list) else [raw]
    if not values or any(not isinstance(uri, str) or not uri.strip() for uri in values):
        return False
    uris = list(dict.fromkeys(uri.strip() for uri in values))
    try:
        payload = json.loads(result.get('text', ''))
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if isinstance(raw, list):
        if (payload.get('level') != level or payload.get('requested') != len(uris) or
                payload.get('returned') != len(uris) or payload.get('truncated') is not False):
            return False
        rows = payload.get('results')
    else:
        rows = [payload]
    if not isinstance(rows, list) or len(rows) != len(uris):
        return False
    for uri, row in zip(uris, rows):
        resolved = uri
        for suffix in ('/.abstract.md', '/.overview.md', '/.read.md', '/.full.md'):
            if uri.endswith(suffix):
                resolved = uri[:-len(suffix)] or 'viking://'
                break
        if (not isinstance(row, dict) or row.get('uri') != uri or
                row.get('resolved_uri') != resolved or row.get('level') != level or
                'fallback' in row or 'error' in row or not isinstance(row.get('content'), str)):
            return False
    return True


def source_read_blocked(name, arguments, rules):
    """Fail closed on original reads; allow native memory and generated summaries.

    Summary pseudo-files are safe because the native reader normalizes them to a
    directory summary and cannot fall back to original file content. A plain file
    with overview requested is not safe: native readers can fall back to full text.
    """
    import posixpath
    from urllib.parse import unquote, urlsplit
    rule = rules.get(name)
    if rule is None:
        return False
    if not isinstance(arguments,dict):
        return True
    uri, uris = arguments.get('uri',''), arguments.get('uris',[])
    values = (uris if isinstance(uris,list) and uris else
              uri if isinstance(uri,list) else [uri])
    level = arguments.get('level') or rule['default_level']
    if not values or level not in ('abstract','overview','full'):
        return True
    for value in values:
        if not isinstance(value,str) or not value.strip():
            return True
        value = value.strip()
        try:
            parsed = urlsplit(value)
        except ValueError:
            return True
        if parsed.scheme != rule['scheme'] or parsed.query or parsed.fragment:
            return True
        path = posixpath.normpath('/'+unquote(parsed.netloc+parsed.path)).rstrip('/')+'/'
        memory = (parsed.netloc in rule['memory_namespaces'] and
                  rule['memory_path'] in path)
        suffix = rule['summary_suffixes'].get(level)
        summary = bool(suffix and value.endswith(suffix))
        if not memory and not summary:
            return True
    return False


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
    from pacbench.judging.recovery import SOURCE_CATEGORIES, tool_category

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


PREFETCH_PROTOCOL = 'native-prefetch-provenance-v1'


def observe_prefetch(declaration, text):
    if not declaration or not text:
        return None
    if (declaration.get('protocol') != PREFETCH_PROTOCOL or declaration.get('state') not in {'source','unverified'} or
            declaration.get('provider') not in {'cognee','memos','supermemory','memcal'} or
            not isinstance(declaration.get('reason'),str) or not declaration['reason'].strip()):
        raise ValueError('Invalid native prefetch provenance condition')
    return {**declaration, 'prefetch_sha256':hashlib.sha256(text.encode()).hexdigest()}


def dec(value):
    try:return json.loads(value)
    except (ValueError,TypeError):return value


def classify_retention(provider, turn, inherited='eligible'):
 k=provider
 if k=='memos':k='memos_raw'
 t=vars(turn) if not isinstance(turn,dict) else turn
 reasons=['inherited-source'] if inherited=='excluded' else [];unknown=['inherited-unknown'] if inherited=='unknown' else [];pc=t.get('prefetch_context') or ''
 if (t.get('hermes_gateway') or {}).get('version')=='checkpoint-session-v1':
  if t['hermes_gateway'].get('answer_visible_capture')!='complete':unknown.append('answer-visible-context-unverified')
  if (t.get('context_isolation') or {}).get('state')!='pass':unknown.append('context-isolation-unverified')
 if len(t['tool_calls'])!=len(t['tool_results']):unknown.append('unmatched-tool-logs')
 if k.startswith('memos_') and pc:
  if (t.get('prefetch_provenance') or {}).get('state')=='source':reasons.append('raw-trace-prefetch')
  else:unknown.append('untyped-prefetch')
 if k=='reme' and pc:
  paths=re.findall(r'========== (.*?):[0-9]+-[0-9]+ ',pc)
  if not paths or any(not p.startswith('daily/') for p in paths):unknown.append('untyped-reme-prefetch')
 if k=='supermemory' and pc:unknown.append('hybrid-prefetch-lost-record-type')
 if k=='memcal' and pc:
  if (t.get('prefetch_provenance') or {}).get('state')=='source':reasons.append('raw-memcal-prefetch')
  else:unknown.append('memcal-prefetch-record-types-unverified')
 if k=='openviking' and pc:
  if re.search(r'# .*ChatLog:',pc):reasons.append('raw-conversation-in-memory-prefetch')
  elif any('/memories/' not in u for u in re.findall(r'<uri>(.*?)</uri>',pc)):unknown.append('untyped-prefetch-resource')
 # ReMe every returned search chunk is daily/ generated memory; Mem0 memory fields,
 # Memcal generated brief/pages; Honcho representation: normalized records.
 for c,r in zip(t['tool_calls'],t['tool_results']):
  n=c['name'];a=c.get('input',c.get('arguments',{})) or {};d=dec(r.get('text',''))
  from pacbench.judging.recovery import tool_category
  if tool_category(n,a)=='unclassified':unknown.append('unclassified-native-tool')
  if n=='honcho_context' and isinstance(d,dict) and '## Recent messages' in str(d.get('result','')):reasons.append('raw-recent-messages')
  if r.get('truncated'):unknown.append('truncated-tool-receipt')
  if not r.get('success'):
   if n=='supermemory_read_document' and isinstance(d,dict) and d.get('error'):continue
   unknown.append('failed-tool-uncertain-payload');continue
  if n=='memos_search':
   for z in d.get('hits',[]):
    if z.get('refKind')=='trace' and z.get('snippet'):reasons.append('raw-trace-search')
    elif z.get('refKind') not in {'policy','world_model','skill','trace'}:unknown.append('untyped-hit')
  elif n=='memos_get' and a.get('kind','trace')=='trace':
   if d.get('body') or (d.get('meta') or {}).get('userText') or (d.get('meta') or {}).get('agentText'):reasons.append('raw-trace-detail')
  elif n=='memos_timeline' and d.get('traces'):reasons.append('raw-timeline')
  elif n=='supermemory_read_document' and d.get('content'):reasons.append('raw-document-read')
  elif n in {'supermemory_search','supermemory_profile'} and d.get('results'):unknown.append('hybrid-results-lost-record-type')
  elif n=='viking_read':
   for z in d.get('results',[d]):
    if not z.get('content'):continue
    if '/memories/' not in z.get('uri','') and (z.get('fallback') or z.get('level')=='full'):reasons.append('raw-resource-read')
    elif re.search(r'# .*ChatLog:',z.get('content','')):reasons.append('raw-conversation-in-memory-read')
    elif z.get('level') not in {'abstract','overview'} and '/memories/' not in z.get('uri',''):unknown.append('untyped-resource-read')
  elif n=='cognee_recall' and d.get('results'):
   if a.get('search_type') in {'CHUNKS','CHUNKS_LEXICAL','RAG_COMPLETION','HYBRID_COMPLETION'}:reasons.append('raw-chunk-recall')
   else:unknown.append('graph-label-does-not-prove-native-source-free-context')
  elif n=='memcal_open' and isinstance(d,str) and re.search(r'cited sources \([1-9]',d):reasons.append('quoted-cited-source-lines')
  elif n=='memcal_search_archive' and d.get('results'):reasons.append('raw-archive-results')
  elif n=='memcal_open_source' and d.get('evidence'):reasons.append('raw-source-evidence')
  elif n=='memcal_conversation' and d.get('conversation'):reasons.append('raw-conversation')
  elif n=='honcho_search' and isinstance(d,dict) and d.get('result') and not d['result'].startswith(('No messages','No relevant','No results')):reasons.append('raw-message-search')
 for trace in t.get('provider_traces') or []:
  if trace.get('error'):unknown.append('native-trace-observation-error');continue
  if (trace.get('path_attribution') or {}).get('affects_answer') is False:continue
  for op in trace.get('record',{}).get('toolCalls',[]):
   n=op.get('toolName');v=op.get('result');raw=False
   if trace.get('provider')=='hindsight' and n in {'recall','expand'} and isinstance(v,dict):raw=bool(v.get('chunks'))
   if trace.get('provider')=='honcho' and n in {'search_messages','search_messages_temporal','grep_messages'}:raw=bool(v)
   if raw:
    if trace.get('attribution')=='explicit-query-match':reasons.append('answer-linked-native-raw-read')
    else:unknown.append('unlinked-native-raw-read')
 # Reflect without complete trace is unknown, even if no error record saved.
 if k=='hindsight' and any(c['name']=='hindsight_reflect' for c in t['tool_calls']):
  if not any(z.get('record',{}).get('toolCalls') for z in t.get('provider_traces') or []):unknown.append('reflect-native-read-path-unobserved')
 return {'protocol':'strict-memory-only-v1','state':'excluded' if reasons else 'unknown' if unknown else 'eligible',
        'source_reasons':sorted(set(reasons)), 'unknown_reasons':sorted(set(unknown))}
