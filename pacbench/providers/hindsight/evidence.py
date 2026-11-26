"""Observe native Hindsight document processing, model calls and server traces."""
from collections import Counter
from contextvars import Token
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import UUID
import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request


class HindsightDocumentReadback:
    def __init__(self, client, bank, documents, *, all_documents=None):
        self.client = client
        self.bank = bank
        self.documents = tuple(documents)
        self.known_sources = {doc.filename for doc in (self.documents if all_documents is None else all_documents)}
        self.inputs = {}

    def wait(self, operation_ids, *, deadline, timing=None):
        """Observe native file processing followed by bank-scoped job drain."""
        if len(set(operation_ids)) != len(operation_ids) or not operation_ids:
            raise ValueError('Hindsight did not identify its native file operations')
        started = time.monotonic()
        terminal = {'failed','cancelled','canceled','not_found'}
        while time.monotonic() < deadline:
            statuses = [self.client.operations.get_operation_status(
                self.bank, operation, _request_timeout=15.0) for operation in operation_ids]
            if any(item.status in terminal for item in statuses):
                raise RuntimeError('Hindsight native file processing failed')
            if all(item.status=='completed' for item in statuses):
                drained_from = time.monotonic()
                jobs = self.drain(deadline=deadline)
                if timing is not None:
                    timing['file_operations_s'] = drained_from - started
                    timing['bank_drain_s'] = time.monotonic() - drained_from
                return jobs
            time.sleep(.5)
        raise TimeoutError('Hindsight native file operation remained incomplete')

    def drain(self, *, deadline):
        while time.monotonic() < deadline:
            outstanding = {}
            for status in ('pending','processing','failed','cancelled'):
                page = self.client.operations.list_operations(
                    self.bank, status=status, limit=1, _request_timeout=15.0)
                outstanding[status] = page.total
            if outstanding['failed'] or outstanding['cancelled']:
                raise RuntimeError('Hindsight bank has failed or cancelled native operations')
            if not outstanding['pending'] and not outstanding['processing']:
                return outstanding
            time.sleep(.5)
        raise TimeoutError('Hindsight bank readiness timed out')

    def document(self, expected, operations, *, timeout=900):
        index = len(self.inputs)
        if index >= len(self.documents) or self.documents[index] != expected:
            raise ValueError('Hindsight file readback would change the frozen release order')
        timing = {}
        drain = self.wait(operations, deadline=time.monotonic()+timeout, timing=timing)
        read_started = time.monotonic()
        row = self.client.documents.get_document(self.bank, expected.filename, _request_timeout=15.0)
        if row.bank_id!=self.bank or row.id!=expected.filename or not isinstance(row.original_text,str):
            raise RuntimeError('Hindsight original file is missing from its native bank')
        genuine = hashlib.sha256(row.original_text.encode()).hexdigest()
        if genuine != expected.sha256:
            raise RuntimeError('Hindsight native uploaded text differs from the frozen source')
        timing['document_readback_s'] = time.monotonic() - read_started
        self.inputs[row.id] = row.content_hash
        return dict(id=row.id, content_sha256=genuine, native_content_hash=row.content_hash,
                    memory_unit_count=row.memory_unit_count, native_operations=list(operations),
                    bank_jobs=drain, timing=timing, observed_at=datetime.now(timezone.utc).isoformat())

    def inventory(self):
        """Use one paginated native list, with exact hash checks after import."""
        documents = {}
        cursor = 0
        total = None
        while total is None or cursor < total:
            page = self.client.documents.list_documents(
                self.bank, limit=100, offset=cursor, _request_timeout=15.0)
            if total is None:
                total = page.total
            if (page.total_is_estimate or page.offset!=cursor or page.total!=total
                    or (cursor<total and not page.items)):
                raise RuntimeError('Hindsight document inventory changed or was incomplete')
            for row in page.items:
                if row.bank_id!=self.bank or row.id in documents:
                    raise RuntimeError('Hindsight document inventory crossed a bank or repeated an ID')
                documents[row.id] = row
            cursor += len(page.items)
        if cursor != total:
            raise RuntimeError('Hindsight document inventory listed a different total')
        observed = {key:row.content_hash for key,row in documents.items() if key in self.known_sources}
        if observed != self.inputs:
            raise RuntimeError('Hindsight native inventory differs from the verified source prefix')
        return list(self.inputs), documents


_calls = {}
_lock = Lock()
_CONSOLIDATION = {'consolidation', 'consolidation_dedup'}


def native_consolidation_call(bank):
    # The run-owned server imports this module during ordinary initialization.
    # Avoid importing the server or building a trace from the controller.
    native = sys.modules.get('hindsight_api.engine.llm_trace')
    if native is None:
        return None
    operation = native.current_trace_context()
    if operation is None or operation.operation not in _CONSOLIDATION:
        return None
    if operation.bank_id != bank:
        raise ValueError('Hindsight consolidation call has no scoped native request')
    request_token = native_request_token(native)
    trace = str(UUID(operation.trace_id))
    span = str(UUID(operation.operation_span_id))
    owner = (trace, span, operation.operation)
    with _lock:
        # The SDK binds this token once per logical call, including calls
        # with no optional request parameters, until all its retries exit.
        previous = _calls.get(id(request_token))
        if previous is None:
            previous = (request_token, owner, len(_calls)+1)
            _calls[id(request_token)] = previous
        if previous[0] is not request_token or previous[1] != owner:
            raise ValueError('Hindsight native request context changed its identity')
    return {'kind':'hindsight-consolidation-call-v1','native_trace':trace,
            'native_operation_span':span,'native_operation':operation.operation,'ordinal':previous[2]}


def native_request_token(native):
    wrapper = sys.modules['hindsight_api.engine.llm_wrapper'].LLMProvider
    native_codes = {wrapper._call.__code__, wrapper._call_with_tools.__code__}
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code in native_codes:
            value = frame.f_locals.get('request_token')
            if isinstance(value, Token) and value.var is native._request_ctx:
                return value
            break
        frame = frame.f_back
    raise ValueError('Hindsight transport does not belong to a native consolidation call boundary')


def settled_operations(endpoint,bank,*,deadline_s=70):
    from hindsight_client import Hindsight

    from ...hermes_gateway import GatewayLifecycleError
    async def read():
        client=Hindsight(endpoint,timeout=15,max_attempts=1)
        try:
            deadline=time.monotonic()+deadline_s
            while True:
                result=await client.operations.list_operations(bank_id=bank,limit=100,offset=0)
                ops=[item.model_dump(mode='json') for item in result.operations]
                if len(ops)!=result.total or any(item['status']=='failed' for item in ops):
                    raise GatewayLifecycleError('Hindsight native operation accounting failed')
                if all(item['status']=='completed' for item in ops):
                    return ops
                if time.monotonic()>=deadline:
                    raise GatewayLifecycleError('Hindsight observation operations did not drain')
                await asyncio.sleep(.5)
        finally:await client.aclose()
    return asyncio.run(read())


KEY='hindsight-native-traces'

_DECLARATIONS='## Functions\n\nYou act by calling these functions:\n\n'
_NEXT_CALL=('Decide your next step: call exactly ONE of the declared functions. Reply with one JSON object, '
            '{"call": {"name": <function name>, "arguments": {...}}}.')


def _schema_tools(messages):
    """Recognize the installed 1.2 native schema-tool transcript, not arbitrary JSON."""
    system=next((m.get('content') for m in messages if m.get('role')=='system'),None)
    if not isinstance(system,str) or _DECLARATIONS not in system:return None
    tools=json.loads(system.rsplit(_DECLARATIONS,1)[1])
    if not isinstance(tools,list) or not tools:raise ValueError('Native tool declarations missing')
    result={}
    for tool in tools:
        if not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or tool['name'] in result:
            raise ValueError('Native tool declarations ambiguous')
        result[tool['name']]=tool
    return result


def _schema_instruction(schema,tools):
    if schema=='next_call':return _NEXT_CALL
    if schema not in tools:raise ValueError('Undeclared native schema tool')
    purpose=(tools[schema].get('description') or '').split('. ')[0].strip().rstrip('.')
    return f"Call `{schema}` now{f' ({purpose})' if purpose else ''}. Reply with only its arguments, as one JSON object."


def _schema_operations(group):
    """Read each native LLM output once; pair replayed results by ordered prefix.

    Hindsight 1.2 erases wire tool IDs while rendering plain role/content turns.
    A stable native request ID identifies each operation instead. Repeated equal
    calls remain separate, and a compacted/ambiguous history fails closed.
    """
    ordered=sorted(group,key=lambda r:r['started_at'])
    if not any(r.get('llm_info',{}).get('request',{}).get('response_schema') and
               isinstance(r.get('input'),list) and any(
                   m.get('role')=='system' and isinstance(m.get('content'),str) and _DECLARATIONS in m['content']
                   for m in r['input']) for r in ordered):return None
    identifiers=[r.get('id') for r in ordered]
    if any(not isinstance(i,str) or not i for i in identifiers) or len(set(identifiers))!=len(identifiers):
        raise ValueError('Native schema request identities missing or repeated')
    operations=[];query=None
    for row in ordered:
        messages=row.get('input')
        if not isinstance(messages,list):raise ValueError('Native schema trace input missing')
        tools=_schema_tools(messages)
        if tools is None:raise ValueError('Native schema tool declarations missing')
        schema=row.get('llm_info',{}).get('request',{}).get('response_schema')
        instruction=_schema_instruction(schema,tools)
        current=next((m.get('content') for m in messages if m.get('role')=='user'),None)
        if not isinstance(current,str):raise ValueError('Native schema query missing')
        suffix='\n\n'+instruction
        if current.endswith(suffix):current=current[:-len(suffix)]
        if query is not None and current!=query:raise ValueError('Native schema query changed')
        query=current
        replay=0
        for index,message in enumerate(messages):
            if message.get('role')!='assistant':continue
            content=message.get('content')
            if not isinstance(content,str):raise ValueError('Native schema replay missing')
            # The native one-function protocol emits exactly this JSON wrapper.
            call=json.loads(content).get('call')
            if (not isinstance(call,dict) or replay>=len(operations) or
                    call.get('name')!=operations[replay]['toolName'] or
                    call.get('arguments')!=operations[replay]['args']):
                raise ValueError('Native schema replay does not match recorded calls')
            if index+1<len(messages) and messages[index+1].get('role')=='user':
                result_text=messages[index+1].get('content')
                prefix=f"Result of `{call['name']}`:\n"
                if isinstance(result_text,str) and result_text.startswith(prefix):
                    # The native renderer joins the next instruction after this
                    # complete JSON result. Never interpret that instruction as data.
                    result,_=json.JSONDecoder().raw_decode(result_text[len(prefix):])
                    previous=operations[replay]['result']
                    if previous is not None and previous!=result:raise ValueError('Native schema result changed')
                    operations[replay]['result']=result
            replay+=1
        if row.get('status')!='success':raise ValueError('Native schema tool call failed')
        reply=row.get('output')
        if isinstance(reply,str):reply=json.loads(reply)
        if not isinstance(reply,dict):raise ValueError('Native schema output missing')
        if schema=='next_call':
            call=reply.get('call',{})
            name=call.get('name');args=call.get('arguments')
        else:
            name=schema;args=reply
            if set(reply)=={name} and isinstance(reply[name],dict):args=reply[name]
            elif set(reply)=={'call'} and isinstance(reply['call'],dict) and reply['call'].get('name')==name:
                args=reply['call'].get('arguments')
        if name not in tools or not isinstance(args,dict):raise ValueError('Native schema output is not a declared call')
        operations.append({'toolName':name,'args':args,'result':None,'id':row['id'],
                           'capture':'native-schema-output-and-replayed-result-v1'})
    return query,operations


def read(env):
    endpoint=env['HINDSIGHT_API_URL']
    if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal trace server')
    base=endpoint.rstrip('/')+'/v1/default/banks/'+urllib.parse.quote(env['HINDSIGHT_BANK_ID'],safe='')
    rows=[]
    for offset in range(0,10000,100):
        query=urllib.parse.urlencode({'operation':'reflect','call_type':'llm','limit':100,'offset':offset})
        with urllib.request.urlopen(base+'/llm-requests?'+query,timeout=10) as response:value=json.load(response)
        rows.extend(value['items'])
        if len(rows)>=value['total']:return rows
    raise ValueError('Native trace pagination exceeded bound')


def snapshot(env):
    try:return {'ids':[row['id'] for row in read(env)]}
    except Exception as exc:return {'error':type(exc).__name__}


def convert(rows,turn):
    groups={}
    for row in rows:
        if not isinstance(row.get('trace_id'),str) or not row['trace_id']:raise ValueError('Native operation identity missing')
        groups.setdefault(row['trace_id'],[]).append(row)
    explicit={(c.get('input',c.get('arguments',{})) or {}).get('query')
              for c in turn.tool_calls if c.get('name')=='hindsight_reflect'}
    traces=[]
    for trace_id,group in groups.items():
        calls={};results={};names=Counter();query=None
        schema=_schema_operations(group)
        if schema is not None:query,operations=schema
        for row in sorted(group,key=lambda r:r['started_at']):
            if schema is not None:continue
            names.update(row['llm_info'].get('tool_calls',[]))
            messages=row.get('input')
            if not isinstance(messages,list):raise ValueError('Native trace input missing or truncated')
            if query is None:query=next((m.get('content') for m in messages if m.get('role')=='user'),None)
            for message in messages:
                if message.get('role')=='assistant':
                    for call in message.get('tool_calls',[]) or []:
                        fn=call['function'];args=fn['arguments']
                        calls[call['id']]={'toolName':fn['name'],'args':json.loads(args) if isinstance(args,str) else args}
                elif message.get('role')=='tool':
                    content=message.get('content')
                    try:content=json.loads(content)
                    except (ValueError,TypeError):pass
                    results[message['tool_call_id']]=content
        if schema is None:operations=[]
        for id,call in calls.items():
            operations.append({**call,'id':id,'result':results.get(id),'capture':'native-messages'})
            names[call['toolName']]-=1
        for name,count in names.items():
            if count<0:raise ValueError('Native trace tool count mismatch')
            operations.extend({'toolName':name,'args':None,'result':None,'capture':'native-name-only'} for _ in range(count))
        traces.append({'provider':'hindsight','attribution':'explicit-query-match' if query in explicit else 'background-or-prefetch',
            'record':{'trace_id':trace_id,'query':query,'timestamp':max(datetime.fromisoformat(r['ended_at']).timestamp() for r in group),
                      'complete':any(r.get('scope')=='reflect' and r.get('status')=='success' for r in group),
                      'model_calls':[{k:r.get(k) for k in ('id','scope','started_at','ended_at','status','input_tokens',
                                     'output_tokens','cached_tokens','thoughts_tokens','duration_ms')} for r in group],
                      'native_request_ids':[r['id'] for r in group],'toolCalls':operations},
            'scope':'Existing native model-call traces; prompts and reasoning omitted. Tool arguments/results are retained where the native trace records them.'})
    return traces


def _save_snapshot(env,rows):
    if not env.get('PACBENCH_HINDSIGHT_TRACE_DIR'):return None
    # Keep the native bytes even when a future format defeats conversion.
    directory=Path(env['PACBENCH_HINDSIGHT_TRACE_DIR'])
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    raw=(json.dumps(rows,sort_keys=True,ensure_ascii=False)+'\n').encode()
    digest=hashlib.sha256(raw).hexdigest();path=directory/(digest+'.json')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Native trace snapshot changed')
    else:
        descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(raw)
    return {'sha256':digest,'bytes':len(raw),'rows':len(rows),
        'scope':'Private native model-call rows; prompts are excluded from public traces'}


def capture(env,before,turn):
    witness=None
    try:
        if before.get('error'):raise ValueError('Initial native trace snapshot unavailable')
        # The native writer flushes every 0.5 seconds. Allow its ordinary
        # flush, then stop as soon as explicit reflection coverage is complete.
        started=time.monotonic();earliest=started+0.75;deadline=started+3
        known=set(before['ids'])
        expected=Counter((c.get('input',c.get('arguments',{})) or {}).get('query')
                         for c in turn.tool_calls if c.get('name')=='hindsight_reflect')
        while True:
            rows=read(env)
            delta=[r for r in rows if r['id'] not in known]
            witness=_save_snapshot(env,delta)
            traces=convert(delta,turn)
            observed=Counter(t['record']['query'] for t in traces if t['attribution']=='explicit-query-match' and t['record']['complete'])
            now=time.monotonic()
            if now>=deadline or now>=earliest and not expected-observed:break
            time.sleep(0.25)
        if expected-observed:traces.append({'provider':'hindsight','error':'Native reflection trace coverage incomplete'})
        if witness:
            for trace in traces:trace['native_snapshot']=witness
        return traces
    except Exception as exc:
        failure={'provider':'hindsight','error':'Native trace observation failed: '+type(exc).__name__}
        if witness:failure['native_snapshot']=witness
        return [failure]


LEGACY_PROVENANCE = 'source-provenance-v1'
UNIFORM_PROVENANCE = 'source-provenance-v2'


def category(call, *, provenance_version=LEGACY_PROVENANCE):
    if provenance_version=='source-provenance-v3':provenance_version=UNIFORM_PROVENANCE
    if provenance_version not in {LEGACY_PROVENANCE, UNIFORM_PROVENANCE}:
        raise ValueError('Unknown source provenance version')
    name=call.get('toolName');result=call.get('result')
    if name=='expand':return 'source_evidence'
    if name=='recall':
        if not isinstance(result,dict):return 'unclassified'
        chunks=result.get('chunks')
        if provenance_version == UNIFORM_PROVENANCE:
            memories=result.get('memories')
            if (not isinstance(memories,list) or not isinstance(chunks,dict) or
                    any(not isinstance(m,dict) for m in memories) or
                    any(not isinstance(k,str) or not isinstance(v,dict) or
                        not isinstance(v.get('chunk_text'),str) or not v['chunk_text']
                        for k,v in chunks.items())):
                return 'unclassified'
            if not chunks:return 'memory_search'
            if not memories:return 'source_messages'
            # A cited chunk is attached only when the returned retained records
            # actually identify it; co-occurrence in one response is insufficient.
            cited={m.get('chunk_id') for m in memories if isinstance(m.get('chunk_id'),str)}
            if set(chunks) <= cited and all(isinstance(m.get('text'),str) and m['text'] for m in memories):
                return 'memory_search'
            return 'unclassified'
        if isinstance(chunks,dict) and any(isinstance(v,dict) and v.get('chunk_text') for v in chunks.values()):
            return 'source_messages'
        return 'memory_search'
    if name in {'search_observations','list_observations','get_observation'}:return 'memory_search'
    if name in {'answer_ready','done'}:return 'memory_reasoning'
    return 'unclassified'
