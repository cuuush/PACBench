"""Question-level memory-only eligibility; ambiguous or missing provenance stays ungraded."""
import json,re
def dec(value):
    try:return json.loads(value)
    except (ValueError,TypeError):return value

def classify(provider, turn, inherited='eligible'):
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
  from .followups import tool_category
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
