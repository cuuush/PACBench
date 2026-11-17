"""Observe accepted native writes before dependent fresh-session recall.

Polling never flushes, edits, or re-submits provider data. An acknowledgement
is not a processing receipt. No expected answer enters these transport checks.
"""
from __future__ import annotations

import json
from collections import Counter
import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

from .followups import digest,save

VERSION='accepted-native-writes-v3'
SUPPORTED={VERSION,'accepted-native-writes-v2','accepted-native-writes-v1'}
ASYNC={'supermemory','openviking','honcho'}


def capture_description(inputs):
    """Pure native text/config transformation; no clients are instantiated."""
    from .provider_loader import provider_module
    provider=inputs['provider']
    if provider=='honcho':
        plugin=provider_module(provider)
        cfg=provider_module(provider,'client').HonchoClientConfig.from_global_config()
        peers=provider_module(provider,'session_peers')
        from agent.memory_manager import sanitize_context
        if cfg.session_strategy!='per-session' or cfg.observation_mode!='directional':
            raise ValueError('Unsupported Honcho capture observation configuration')
        owner=peers.sanitize_peer_id(cfg.peer_name);assistant=peers.assistant_peer_id_for(cfg)
        rows=[]
        for turn in inputs['captures']:
            chunks=[]
            for peer,key in [(owner,'prompt'),(assistant,'answer')]:
                text=sanitize_context(turn[key]).strip()
                if text:chunks.extend({'peer':peer,'content':part} for part in
                    plugin.HonchoMemoryProvider._chunk_message(text,cfg.message_max_chars))
            rows.append({'binding':digest(turn),'session':peers.sanitize_peer_id(cfg.resolve_session_name(session_id=turn['session'])),'chunks':chunks})
        return {'provider':provider,'workspace':cfg.workspace_id,'owner':owner,'assistant':assistant,
                'message_max_chars':cfg.message_max_chars,'captures':rows}
    if provider=='supermemory':
        plugin=provider_module(provider)
        # Config is already loaded through the native provider's own resolver.
        config=plugin._load_supermemory_config(str(Path(os.environ['HERMES_HOME'])))
        if config.get('auto_capture') is not True:raise ValueError('Native automatic capture is disabled')
        return {'provider':provider,'captures':[{'binding':digest(c),'session':c['session'],
            'document_id':plugin._capture_doc_id(c['session']),
            'user':plugin._clean_text_for_capture(c['prompt']),
            'assistant':plugin._clean_text_for_capture(c['answer'])} for c in inputs['captures']]}
    raise ValueError('No native capture transform for this provider')


def describe_captures(root,inputs):
    """Recompute auditable transforms using the installed, unmodified plugin."""
    from . import harness
    script='import sys,json;sys.path.insert(0,sys.argv[1]);from pacbench.native_writes import capture_description;print(json.dumps(capture_description(json.loads(sys.argv[2]))))'
    env={**os.environ,'HERMES_HOME':str((root/'hermes-home').resolve())}
    result=subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',script,str(harness.REPO),json.dumps(inputs)],
        env=env,cwd=harness.ISOLATED_TREE,text=True,capture_output=True,timeout=30)
    if result.returncode:raise ValueError('Native capture transformation unavailable')
    return json.loads(result.stdout.splitlines()[-1])


def write_inputs(turns,provider,version=VERSION):
    ids,sessions=accepted(turns,provider)
    result={'provider':provider,'accepted_ids':ids,'sessions':sessions}
    if provider=='honcho' or provider=='supermemory' and version!='accepted-native-writes-v1' or provider=='openviking' and version==VERSION:
        result['captures']=[{'session':t['session_id'],'prompt':t['prompt']} for t in turns]
        if version==VERSION:
            result['capture_sides']='user-and-assistant-v1'
            for capture,turn in zip(result['captures'],turns):capture['answer']=turn.get('answer','')
    if provider=='honcho' and version!='accepted-native-writes-v1':
        result['conclusions']=[]
        for turn,capture in zip(turns,result['captures']):
            saved=[]
            for response in turn.get('tool_results',[]):
                if (response.get('tool')!='honcho_conclude' or response.get('success') is not True or
                        response.get('truncated') or response.get('policy_blocked')):continue
                try:saved.append(json.loads(response['text']))
                except (ValueError,KeyError,TypeError):continue
            if not saved:continue
            for call in turn.get('tool_calls',[]):
                args=call.get('input',call.get('arguments',{}))
                if call.get('name')!='honcho_conclude' or not isinstance(args,dict):continue
                content=args.get('conclusion');peer=args.get('peer','user')
                if not isinstance(content,str) or not isinstance(peer,str):continue
                content=content.strip()
                if (not content or peer not in {'user','ai'} or args.get('list') or args.get('delete_id') or
                        {'result':f'Conclusion saved for {peer}: {content}'} not in saved):continue
                result['conclusions'].append({'session':capture['session'],'input_binding':digest(capture),
                                              'peer':peer,'content':content})
    return result


def dependencies(row,checks,context):
    from .native_checks import session_key
    return [r for r,_,_ in checks if r['id'] in row.get('requires',[]) and
            session_key(r,context)!=session_key(row,context)]


def accepted(turns,provider):
    ids=set();sessions=set()
    names={'supermemory':{'supermemory_store','supermemory-save'},'openviking':{'viking_remember'}}
    for turn in turns:
        if turn.get('session_id'):sessions.add(turn['session_id'])
        for result in turn.get('tool_results',[]):
            if result.get('tool') not in names.get(provider,set()) or result.get('success') is not True:continue
            try:value=json.loads(result.get('text',''))
            except (TypeError,ValueError):continue
            key='id' if provider=='supermemory' else 'task_id'
            if isinstance(value,dict) and isinstance(value.get(key),str) and value[key]:ids.add(value[key])
    return sorted(ids),sorted(sessions)


def ensure_ready(provider,row,checks,context,root,env,directory):
    required=dependencies(row,checks,context)
    if not required or provider not in ASYNC:return
    from . import harness
    turns=[json.loads((root/'turns'/r['id']/'turn.json').read_text()) for r in required]
    summary=root.parent.parent/'summary.json'
    version=json.loads(summary.read_text()).get('write_readiness_protocol',VERSION) if summary.exists() else VERSION
    if version not in SUPPORTED:raise ValueError('Unknown native write readiness protocol')
    inputs=write_inputs(turns,provider,version)
    if version==VERSION and provider in {'honcho','supermemory'}:
        inputs['capture_description']=describe_captures(root,inputs)
    hashes=[digest(t) for t in turns]
    bound={'dependencies':[r['id'] for r in required],'inputs':inputs,'turn_hashes':hashes}
    binding=digest(bound)
    cached=root/'write-readiness'/(binding+'.json')
    if cached.exists():record=json.loads(cached.read_text())
    else:
        from .document_ingestion import PROCESSING_TIMEOUT_S
        if provider=='honcho':
            PROCESSING_TIMEOUT_S+=json.loads((root/'native-processing-settings.json').read_text())['deriver']['REPRESENTATION_BATCH_MAX_AGE_SECONDS']
        from .usage import snapshot,window
        roles=json.loads(env.get('PACBENCH_USAGE_ROLES','{}'));before=snapshot(roles)
        worker='import sys;sys.path.insert(0,sys.argv[1]);from pacbench.native_writes import worker;worker(sys.argv[2:])'
        started=time.monotonic()
        try:p=subprocess.run([str(harness.ISOLATED_BIN.parent/'python'),'-c',worker,str(harness.REPO),
                          str(root),json.dumps(inputs),str(PROCESSING_TIMEOUT_S)],
            env=harness.turn_env(row['at'],root/'hermes-home','',env),cwd=harness.ISOLATED_TREE,
            text=True,capture_output=True,timeout=PROCESSING_TIMEOUT_S+30)
        except subprocess.TimeoutExpired:p=None
        try:observed=json.loads(p.stdout.splitlines()[-1]) if p is not None and p.returncode==0 else {}
        except (ValueError,IndexError):observed={}
        record={'version':version,'binding':binding,'dependencies':[r['id'] for r in required],
                'inputs':inputs,'turn_hashes':hashes,'latency_s':round(time.monotonic()-started,2),
                'state':'ready' if observed.get('ready') is True else 'unavailable','observed':observed,
                'usage':window(before,snapshot(roles))}
        save(cached,record)
    save(directory/'write-readiness.json',record)
    if not ready_proof(record,bound):raise RuntimeError('Accepted native writes are not ready; inspect write-readiness.json')


def complete(provider,states):
    if not states:return False,False
    if provider=='supermemory':
        failed=any(r['status'] in {'failed','error'} or r['dreaming_status'] in {'failed','error'} for r in states)
        done=all(r['status']=='done' and r['dreaming_status']=='done' for r in states)
    else:
        failed=any(r['status'] in {'failed','cancelled'} or r['native_error'] for r in states)
        done=all(r['status']=='completed' and not r['native_error'] for r in states)
    return done,failed


def ready_proof(record,bound):
    try:
        observation=record['observed'];states=observation['snapshots'][-1]
        done,failed=complete(bound['inputs']['provider'],states)
        joint=bound['inputs'].get('capture_sides')=='user-and-assistant-v1'
        if joint:
            if record.get('version')!=VERSION:return False
            expected=Counter(digest(c) for c in bound['inputs']['captures'])
            witnessed=Counter(h for s in states for h in s.get('verified_inputs',[]))
            if not expected or expected!=witnessed:return False
            if bound['inputs']['provider']=='supermemory':
                captures=bound['inputs']['capture_description']['captures']
                for state in states:
                    if state.get('verified_inputs'):
                        document=state['capture_document']
                        if state['id']!=document['id']:return False
                        if supermemory_matches(document,captures)!=state['verified_inputs']:return False
                        native=state.get('native_completion_witness')
                        if native and native['content_sha256']!=hashlib.sha256(document['content'].encode()).hexdigest():return False
            if bound['inputs']['provider']=='openviking':
                snapshot=next(s['capture_snapshot'] for s in states if 'capture_snapshot' in s)
                if snapshot['state']!='complete':return False
                expected_captures=bound['inputs']['captures'];actual=snapshot['captures']
                if [digest(c) for c in expected_captures]!=[c['turn_binding'] for c in actual]:return False
                used=set()
                tasks={t['id']:t for t in snapshot['tasks']}
                processed={(coverage['session'],mid) for tid,coverage in snapshot['archive_coverage'].items()
                    if tasks[tid]['status']=='completed' and not tasks[tid]['native_error'] for mid in coverage['message_ids']}
                for wanted,capture in zip(expected_captures,actual):
                    if capture['session']!=wanted['session'] or not capture['processed'] or not capture['both_sides_captured']:return False
                    messages={m['id']:m for m in snapshot['native_messages'][wanted['session']]}
                    sides=[(role,wanted[key]) for role,key in [('user','prompt'),('assistant','answer')] if wanted[key].strip()]
                    if len(capture['captured_ids'])!=len(sides):return False
                    for mid,(role,text) in zip(capture['captured_ids'],sides):
                        identity=(wanted['session'],mid)
                        if (identity in used or identity not in processed or messages[mid]['role']!=role or
                            messages[mid]['text_sha256']!=hashlib.sha256(text.encode()).hexdigest()):return False
                        used.add(identity)
            if bound['inputs']['provider']=='honcho':
                descriptions=sorted(bound['inputs']['capture_description']['captures'],key=lambda c:c['session'])
                actual=[c for s in states for c in s.get('capture_witnesses',[])]
                if [c['binding'] for c in actual]!=[c['binding'] for c in descriptions]:return False
                used=set();previous_sequence={}
                for expected_capture,actual_capture in zip(descriptions,actual):
                    if expected_capture['session']!=actual_capture['session']:return False
                    chunks=actual_capture['chunks'];wanted=expected_capture['chunks']
                    if not wanted or len(chunks)!=len(wanted):return False
                    for old,new in zip(wanted,chunks):
                        identity=(actual_capture['session'],new['id'])
                        if (identity in used or old['peer']!=new['peer'] or
                                hashlib.sha256(old['content'].encode()).hexdigest()!=new['text_sha256']):return False
                        used.add(identity)
                    sequence=[c['sequence'] for c in chunks]
                    if sequence!=list(range(sequence[0],sequence[0]+len(sequence))):return False
                    if sequence[0]<=previous_sequence.get(actual_capture['session'],-1):return False
                    previous_sequence[actual_capture['session']]=sequence[-1]
        if bound['inputs']['provider']=='honcho':
            if {s['hermes_session'] for s in states}!=set(bound['inputs']['sessions']):
                return False
            for state in states:
                if state.get('write_path')=='explicit_conclusions' and not joint:
                    expected={digest(c) for c in bound['inputs']['captures'] if c['session']==state['hermes_session']}
                    if (not expected or set(state.get('verified_inputs',[]))!=expected or
                            not state.get('conclusion_ids') or not bound['inputs'].get('conclusions')):return False
                elif not state.get('captures_verified') or not state.get('captured_ids'):return False
                if joint:
                    work=state.get('processing_witnesses',[])
                    if [c['id'] for w in state.get('capture_witnesses',[]) for c in w['chunks']]!=state['captured_ids']:return False
                    native_sessions={c['session'] for c in state.get('capture_witnesses',[])}
                    represented={w['message_id'] for w in work if w['task_type']=='representation'
                                 and w['session_name'] in native_sessions
                                 and w['processed'] and not w['native_error']}
                    if (not state.get('explicit_writes_verified') or not work or
                            not set(state['captured_ids'])<=represented or
                            any(not w['processed'] or w['native_error'] for w in work)):return False
                    description=bound['inputs']['capture_description']
                    native_sessions={c['session'] for c in description['captures']
                        if c['binding'] in state['verified_inputs']}
                    for write in bound['inputs'].get('conclusions',[]):
                        if write['session']!=state['hermes_session']:continue
                        target=description['owner'] if write['peer']=='user' else description['assistant']
                        if not any(w['id'] in state['conclusion_ids'] and w['session_id'] in native_sessions and
                            w['content']==write['content'] and w['observer_id']==description['assistant'] and
                            w['observed_id']==target for w in state.get('conclusion_witnesses',[])):return False
        if (bound['inputs']['provider']=='supermemory' and 'captures' in bound['inputs'] and
                not bound['inputs']['accepted_ids']):
            expected={digest(c) for c in bound['inputs']['captures']}
            if not expected or expected!={h for s in states for h in s.get('verified_inputs',[])}:
                return False
        return (record.get('version') in SUPPORTED and record.get('binding')==digest(bound) and
                all(record.get(k)==v for k,v in bound.items()) and record.get('state')=='ready' and
                observation.get('ready') is True and observation.get('processing_overrides') is False and
                done and not failed and not set(bound['inputs']['accepted_ids'])-{s['id'] for s in states})
    except (ValueError,KeyError,TypeError,IndexError,StopIteration):return False


def audit_ready(checks,context,root,version):
    if not version or root.name not in ASYNC:return []
    errors=[]
    if version not in SUPPORTED:return ['Unknown native write readiness protocol']
    for row,_,_ in checks:
        required=dependencies(row,checks,context)
        turn=root/'turns'/row['id']/'turn.json'
        if not required or not turn.exists():continue
        try:
            r=json.loads((turn.parent/'write-readiness.json').read_text())
            prior=[json.loads((root/'turns'/d['id']/'turn.json').read_text()) for d in required]
            inputs=write_inputs(prior,root.name,version)
            if version==VERSION and root.name in {'honcho','supermemory'}:
                inputs['capture_description']=describe_captures(root,inputs)
            bound={'dependencies':[d['id'] for d in required],'inputs':inputs,'turn_hashes':[digest(t) for t in prior]}
            if not ready_proof(r,bound):
                errors.append(row['id']+': dependent recall preceded proven native write completion')
        except (OSError,ValueError,KeyError,TypeError,IndexError):
            errors.append(row['id']+': missing or invalid native write readiness proof')
    return errors


def supermemory_matches(value,captures):
    metadata=value.get('metadata') or {};content=value.get('content') or ''
    users=re.findall(r'(?:\A|\n\n---\n\n)\[role: user\]\n(.*?)\n\[user:end\](?=\n\[role: assistant\]|\Z)',content,re.S)
    pairs=re.findall(r'\[role: user\]\n(.*?)\n\[user:end\](?:\n\[role: assistant\]\n(.*?)\n\[assistant:end\])?',content,re.S)
    verified=[];cursor=0
    for capture in captures:
        if (metadata.get('type')!='conversation' or capture['session']!=metadata.get('session_id') or
                capture.get('document_id',value['id'])!=value['id']):continue
        if 'assistant' not in capture:
            if capture['user'] in users:verified.append(capture['binding'])
            continue
        wanted=(capture['user'],capture['assistant'])
        index=next((i for i in range(cursor,len(pairs)) if pairs[i]==wanted),None)
        if index is None:continue
        cursor=index+1;verified.append(capture['binding'])
    return verified


def supermemory_status(client,tag,ids,sessions,captures=None,*,endpoint=None,key=None):
    from provider_adapters.supermemory_native import document
    joint=bool(captures and any('assistant' in c for c in captures))
    found=set(ids);page=1
    while not ids or joint:
        response=client.list(tag,'documents',limit=100,page=page)
        value=response.model_dump(mode='json')
        rows=value.get('documents');pagination=value.get('pagination',{})
        if not isinstance(rows,list):raise ValueError('Native document listing unavailable')
        pages=pagination.get('total_pages',pagination.get('totalPages'))
        current=pagination.get('current_page',pagination.get('currentPage'))
        if (type(pages) not in (int,float) or type(current) not in (int,float) or current!=page or
                pages<0 or pages!=int(pages) or pages>10000):
            raise ValueError('Native document pagination unavailable')
        for item in rows:
            metadata=item.get('metadata') or {}
            if (isinstance(metadata,dict) and metadata.get('session_id') in sessions and
                    (captures is None or metadata.get('type')=='conversation')):found.add(item['id'])
        if page>=pages:break
        page+=1
    statuses=[]
    for id in sorted(found):
        value,status=document(client,tag,id,endpoint=endpoint,key=key)
        statuses.append(status)
        if captures is not None and (not ids or joint):
            statuses[-1]['verified_inputs']=supermemory_matches(value,captures)
            if joint:statuses[-1]['capture_document']={k:value.get(k) for k in ('id','metadata','content')}
    if captures is not None and (not ids or joint):
        statuses=[s for s in statuses if s['id'] in ids or s['verified_inputs']]
        witnessed=Counter(h for s in statuses for h in s['verified_inputs'])
        missing=Counter(c['binding'] for c in captures)-witnessed
        if missing:statuses.append({'id':None,'status':'capture_pending','dreaming_status':None,
                                   'verified_inputs':[],'missing_inputs':sorted(missing.elements())})
    return statuses


def openviking_status(endpoint,headers,ids,sessions,*,root=None,captures=None):
    def get(path):
        with urllib.request.urlopen(urllib.request.Request(endpoint+path,headers=headers),timeout=30) as response:
            value=json.load(response)
        if value.get('status')!='ok':raise ValueError('Native task status unavailable')
        return value['result']
    if captures is not None:
        from .terminal_processing import capture_snapshot
        scope={'turns':[{**c,'binding':digest(c)} for c in captures],
               'explicit_writes':[{'task_id':id,'session':None} for id in ids]}
        observed=capture_snapshot(root,scope,lambda id:get('/api/v1/tasks/'+urllib.parse.quote(id,safe='')),
            account=headers['X-OpenViking-Account'],user=headers['X-OpenViking-User'])
        states=[{'id':t['id'],'status':t['status'],'native_error':t['native_error']} for t in observed['tasks']]
        states.extend({'id':'capture:'+c['turn_binding'],'status':'completed' if c['processed'] else 'running',
            'native_error':False,'verified_inputs':[c['turn_binding']] if c['processed'] else [],
            'native_captured_ids':c['captured_ids']} for c in observed['captures'])
        if states:states[0]['capture_snapshot']=observed
        return states
    found=set(ids)
    for session in sessions if not ids else []:
        cursor=None
        for _ in range(10000):
            query={'task_type':'session_commit','resource_id':session,'pagination':'cursor','limit':100}
            if cursor:query['cursor']=cursor
            page=get('/api/v1/tasks?'+urllib.parse.urlencode(query))
            found.update(t['task_id'] for t in page['items'])
            if not page.get('has_more'):break
            following=page.get('next_cursor')
            if not following or following==cursor:raise ValueError('Native task pagination incomplete')
            cursor=following
        else:raise ValueError('Native task pagination exceeded bound')
    statuses=[]
    for id in sorted(found):
        task=get('/api/v1/tasks/'+urllib.parse.quote(id,safe=''))
        if task.get('task_id')!=id:raise ValueError('Native task identity mismatch')
        result=task.get('result') or {}
        failed=bool(task.get('error') or isinstance(result,dict) and
                    (result.get('errors') or result.get('status') in {'failed','error'}))
        statuses.append({'id':id,'status':task.get('status'),'native_error':failed})
    return statuses


def honcho_status(endpoint,workspace,owner,captures,read_errors,*,read_processing=None,witnesses=None):
    """Read native captured messages and queue state; never create or flush work."""
    if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal memory server')
    base=endpoint.rstrip('/')+'/v3/workspaces/'+urllib.parse.quote(workspace,safe='')
    def read(path,body=None):
        data=None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base+path,data=data,
                headers={'Content-Type':'application/json'}),timeout=30) as response:
            return json.load(response)
    failures=None;conclusions=None;states=[]
    processing=read_processing() if read_processing is not None else None
    witnesses=witnesses if witnesses is not None else {}
    if processing is not None:
        for item in processing['queue']:witnesses[item['id']]=item
    for native,expected in sorted(captures.items()):
        writes=expected.get('conclusions',[])
        explicit_ids=[];explicit_verified=not writes;explicit_witnesses=[]
        if writes:
            if conclusions is None:
                conclusions=[]
                for page in range(1,10001):
                    result=read('/conclusions/list?'+urllib.parse.urlencode({'page':page,'size':100}),{})
                    if result.get('page')!=page or not isinstance(result.get('pages'),int):
                        raise ValueError('Native conclusion pagination unavailable')
                    conclusions.extend(result['items'])
                    if page>=result['pages']:break
                else:raise ValueError('Native conclusion pagination exceeded bound')
            matched={};all_witnessed=True
            for write in writes:
                ids=[c['id'] for c in conclusions if c.get('session_id')==native and
                     all(c.get(k)==write[k] for k in ('content','observer_id','observed_id'))]
                if ids:matched.setdefault(write['input_binding'],[]).extend(ids)
                else:all_witnessed=False
            if all_witnessed and matched and (processing is not None or set(matched)==set(expected['input_bindings'])):
                explicit_ids=sorted({id for ids in matched.values() for id in ids});explicit_verified=True
                explicit_witnesses=[{k:c[k] for k in ('id','session_id','content','observer_id','observed_id')}
                    for c in conclusions if c['id'] in explicit_ids]
                if processing is None:
                    states.append({'id':native,'hermes_session':expected['hermes_session'],
                        'status':'completed','native_error':False,'write_path':'explicit_conclusions',
                        'verified_inputs':sorted(matched),'conclusion_ids':explicit_ids})
                    continue
        if failures is None:failures=read_errors()
        messages=[]
        for page in range(1,10001):
            try:result=read('/sessions/'+urllib.parse.quote(native,safe='')+'/messages/list?'+
                            urllib.parse.urlencode({'page':page,'size':100}),{})
            except urllib.error.HTTPError as exc:
                if exc.code!=404:raise
                result={'page':page,'pages':0,'items':[]}
            if result.get('page')!=page or not isinstance(result.get('pages'),int):
                raise ValueError('Native capture pagination unavailable')
            messages.extend(result['items'])
            if page>=result['pages']:break
        else:raise ValueError('Native capture pagination exceeded bound')
        if processing is not None:
            by_id={m['id']:m for m in messages}
            ordered=[m for m in processing['messages'] if m['session_name']==native]
            cursor=0;captured=[];verified_inputs=[];capture_witnesses=[]
            for capture in expected['capture_chunks']:
                chunks=capture['chunks'];found=None
                for start in range(cursor,len(ordered)-len(chunks)+1):
                    candidate=ordered[start:start+len(chunks)]
                    if all(row['peer_name']==chunk['peer'] and
                           by_id.get(row['public_id'],{}).get('content')==chunk['content']
                           for row,chunk in zip(candidate,chunks)):
                        found=start;break
                if found is None or not chunks:break
                rows=ordered[found:found+len(chunks)]
                matched_ids=[r['public_id'] for r in rows];cursor=found+len(chunks)
                captured.extend(matched_ids);verified_inputs.append(capture['binding'])
                capture_witnesses.append({'binding':capture['binding'],'session':native,'chunks':[
                    {'id':r['public_id'],'peer':r['peer_name'],'sequence':r['seq_in_session'],
                     'text_sha256':hashlib.sha256(by_id[r['public_id']]['content'].encode()).hexdigest()} for r in rows]})
            verified=Counter(verified_inputs)==Counter(expected['input_bindings']) and bool(captured)
            work=[w for w in witnesses.values() if w['session_name']==native or w['task_type']=='dream']
            represented={w['message_id'] for w in work if w['task_type']=='representation' and w['processed'] and not w['native_error']}
            error=any(w['native_error'] for w in work) or any(f.get('session_name')==native for f in failures)
            done=(verified and explicit_verified and set(captured)<=represented and
                  all(w['processed'] and not w['native_error'] for w in work))
            states.append({'id':native,'hermes_session':expected['hermes_session'],
                'status':'completed' if done else 'failed' if error else 'running','native_error':error,
                'captures_verified':verified,'captured_ids':captured,'verified_inputs':verified_inputs,
                'capture_witnesses':capture_witnesses,'missing_representation_witnesses':sorted(set(captured)-represented),
                'conclusion_ids':explicit_ids,'explicit_writes_verified':explicit_verified,
                'conclusion_witnesses':explicit_witnesses,
                'processing_witnesses':work,'processing_scope':'Causal message representations, scheduled session summaries and active workspace dreams; future native idle dreaming is deferred and unclaimed'})
            continue
        matched={hashlib.sha256(m['content'].strip().encode()).hexdigest():m['id']
                 for m in messages if m.get('peer_id')==owner}
        verified=all(h in matched for h in expected['hashes'])
        owner_count=sum(m.get('peer_id')==owner for m in messages)
        status=(read('/queue/status?'+urllib.parse.urlencode({'session_id':native,'sender_id':owner})) if messages else
                {'total_work_units':0,'pending_work_units':0,'in_progress_work_units':0})
        error=any(f.get('session_name')==native for f in failures)
        done=(verified and owner_count>0 and status['total_work_units']>=owner_count and
              status['pending_work_units']==0 and status['in_progress_work_units']==0 and not error)
        states.append({'id':native,'hermes_session':expected['hermes_session'],
            'status':'completed' if done else 'failed' if error else 'running','native_error':error,
            'captures_verified':verified,'captured_ids':[matched[h] for h in expected['hashes'] if h in matched],
            'queue':status})
    return states


def worker(args):
    root,inputs,timeout=Path(args[0]),json.loads(args[1]),int(args[2])
    snapshots=[];report={'ready':False,'snapshots':snapshots,'processing_overrides':False}
    try:
        provider=inputs['provider']
        if provider=='supermemory':
            from supermemory import Supermemory
            from pacbench.provider_loader import provider_module
            plugin = provider_module("supermemory")
            from provider_adapters.supermemory_native import namespace
            cfg,tag=namespace(plugin,root/'hermes-home')
            client=Supermemory(api_key=os.environ['SUPERMEMORY_API_KEY'],base_url=cfg['base_url'],max_retries=0,timeout=30)
            captures=None
            if 'captures' in inputs:
                if inputs.get('capture_sides')=='user-and-assistant-v1':
                    description=capture_description(inputs)
                    if description!=inputs['capture_description']:raise ValueError('Native capture transform changed')
                    captures=description['captures']
                else:
                    captures=[{'session':c['session'],'user':plugin._clean_text_for_capture(c['prompt']),
                               'binding':digest(c)} for c in inputs['captures']]
            observe=lambda:supermemory_status(client,tag,inputs['accepted_ids'],inputs['sessions'],captures,
                endpoint=cfg['base_url'],key=os.environ['SUPERMEMORY_API_KEY'])
        elif provider=='openviking':
            endpoint=os.environ['OPENVIKING_ENDPOINT']
            if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal memory server')
            headers={'X-OpenViking-Account':os.environ['OPENVIKING_ACCOUNT'],'X-OpenViking-User':os.environ['OPENVIKING_USER']}
            observe=lambda:openviking_status(endpoint,headers,inputs['accepted_ids'],inputs['sessions'],
                root=root,captures=inputs.get('captures'))
        elif provider=='honcho':
            from pacbench.provider_loader import provider_module
            HonchoClientConfig = provider_module("honcho", "client").HonchoClientConfig
            sanitize_peer_id = provider_module("honcho", "session_peers").sanitize_peer_id
            assistant_peer_id_for = provider_module("honcho", "session_peers").assistant_peer_id_for
            from agent.memory_manager import sanitize_context
            from provider_adapters.honcho_local import queue_errors,processing_snapshot
            cfg=HonchoClientConfig.from_global_config()
            if cfg.session_strategy!='per-session':raise ValueError('Honcho fresh-session observer requires native per-session setup')
            captures={}
            joint=inputs.get('capture_sides')=='user-and-assistant-v1'
            description=capture_description(inputs) if joint else None
            if joint and description!=inputs['capture_description']:raise ValueError('Native capture transform changed')
            for index,turn in enumerate(inputs['captures']):
                native=sanitize_peer_id(cfg.resolve_session_name(session_id=turn['session']))
                entry=captures.setdefault(native,{'hermes_session':turn['session'],'hashes':[],
                                                  'input_bindings':[],'conclusions':[],'capture_chunks':[]})
                entry['hashes'].append(hashlib.sha256(sanitize_context(turn['prompt']).strip().encode()).hexdigest())
                entry['input_bindings'].append(digest(turn))
                if joint:
                    native_capture=description['captures'][index]
                    entry['capture_chunks'].append({'binding':digest(turn),'chunks':native_capture['chunks']})
            if cfg.observation_mode=='directional':
                for write in inputs.get('conclusions',[]):
                    native=sanitize_peer_id(cfg.resolve_session_name(session_id=write['session']))
                    target=cfg.peer_name if write['peer']=='user' else assistant_peer_id_for(cfg)
                    captures[native]['conclusions'].append({**write,'observer_id':assistant_peer_id_for(cfg),
                                                           'observed_id':sanitize_peer_id(target)})
            witnesses={}
            observe=lambda:honcho_status(cfg.base_url,cfg.workspace_id,cfg.peer_name,captures,lambda:queue_errors(root),
                read_processing=(lambda:processing_snapshot(root,cfg.workspace_id,captures)) if joint else None,
                witnesses=witnesses)
        else:raise ValueError('Unverified native write observer')
        from .gateway_client import require_live_cost_boundary
        from .usage import snapshot,provider_model_health
        roles=json.loads(os.environ['PACBENCH_USAGE_ROLES']);deadline=time.monotonic()+timeout
        while True:
            require_live_cost_boundary()
            if provider_model_health(snapshot(roles))['state']=='stop':raise RuntimeError('Native model execution/cost barrier')
            states=observe()
            if not snapshots or states!=snapshots[-1]:snapshots.append(states)
            done,failed=complete(provider,states)
            if failed:report['reason']='Native queued write failed';break
            if done:report.update(ready=True,reason='Observed native completion; no processing forced');break
            if time.monotonic()>=deadline:report['reason']='Native queued write readiness deadline exceeded';break
            time.sleep(2)
    except Exception as exc:report.update(error_type=type(exc).__name__)
    print(json.dumps(report))
