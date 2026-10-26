"""Observe accepted native writes before dependent fresh-session recall.

Polling never flushes, edits, or re-submits provider data. An acknowledgement
is not a processing receipt. No expected answer enters these transport checks.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

from .followups import digest,save

VERSION='accepted-native-writes-v1'
ASYNC={'supermemory','openviking','honcho'}


def write_inputs(turns,provider):
    ids,sessions=accepted(turns,provider)
    result={'provider':provider,'accepted_ids':ids,'sessions':sessions}
    if provider=='honcho':
        result['captures']=[{'session':t['session_id'],'prompt':t['prompt']} for t in turns]
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
    inputs=write_inputs(turns,provider)
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
        record={'version':VERSION,'binding':binding,'dependencies':[r['id'] for r in required],
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
        if bound['inputs']['provider']=='honcho':
            if ({s['hermes_session'] for s in states}!=set(bound['inputs']['sessions']) or
                    any(not s.get('captures_verified') or not s.get('captured_ids') for s in states)):
                return False
        return (record.get('version')==VERSION and record.get('binding')==digest(bound) and
                all(record.get(k)==v for k,v in bound.items()) and record.get('state')=='ready' and
                observation.get('ready') is True and observation.get('processing_overrides') is False and
                done and not failed and not set(bound['inputs']['accepted_ids'])-{s['id'] for s in states})
    except (ValueError,KeyError,TypeError,IndexError):return False


def audit_ready(checks,context,root,version):
    if not version or root.name not in ASYNC:return []
    errors=[]
    if version!=VERSION:return ['Unknown native write readiness protocol']
    for row,_,_ in checks:
        required=dependencies(row,checks,context)
        turn=root/'turns'/row['id']/'turn.json'
        if not required or not turn.exists():continue
        try:
            r=json.loads((turn.parent/'write-readiness.json').read_text())
            prior=[json.loads((root/'turns'/d['id']/'turn.json').read_text()) for d in required]
            inputs=write_inputs(prior,root.name)
            bound={'dependencies':[d['id'] for d in required],'inputs':inputs,'turn_hashes':[digest(t) for t in prior]}
            if not ready_proof(r,bound):
                errors.append(row['id']+': dependent recall preceded proven native write completion')
        except (OSError,ValueError,KeyError,TypeError,IndexError):
            errors.append(row['id']+': missing or invalid native write readiness proof')
    return errors


def supermemory_status(client,tag,ids,sessions):
    found=set(ids);page=1
    while not ids:
        response=client.documents.list(container_tags=[tag],include_content=False,limit=100,page=page)
        value=response.model_dump(mode='json')
        rows=value.get('memories');pagination=value.get('pagination',{})
        if not isinstance(rows,list):raise ValueError('Native document listing unavailable')
        pages=pagination.get('total_pages',pagination.get('totalPages'))
        current=pagination.get('current_page',pagination.get('currentPage'))
        if (type(pages) not in (int,float) or type(current) not in (int,float) or current!=page or
                pages<0 or pages!=int(pages) or pages>10000):
            raise ValueError('Native document pagination unavailable')
        for item in rows:
            metadata=item.get('metadata') or {}
            if isinstance(metadata,dict) and metadata.get('session_id') in sessions:found.add(item['id'])
        if page>=pages:break
        page+=1
    statuses=[]
    for id in sorted(found):
        value=client.documents.get(id).model_dump(mode='json')
        statuses.append({'id':value.get('id'),'status':value.get('status'),
            'dreaming_status':value.get('dreaming_status',value.get('dreamingStatus'))})
        if value.get('id')!=id:raise ValueError('Native write identity mismatch')
    return statuses


def openviking_status(endpoint,headers,ids,sessions):
    def get(path):
        with urllib.request.urlopen(urllib.request.Request(endpoint+path,headers=headers),timeout=30) as response:
            value=json.load(response)
        if value.get('status')!='ok':raise ValueError('Native task status unavailable')
        return value['result']
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


def honcho_status(endpoint,workspace,owner,captures,read_errors):
    """Read native captured messages and queue state; never create or flush work."""
    if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal memory server')
    base=endpoint.rstrip('/')+'/v3/workspaces/'+urllib.parse.quote(workspace,safe='')
    def read(path,body=None):
        data=None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base+path,data=data,
                headers={'Content-Type':'application/json'}),timeout=30) as response:
            return json.load(response)
    failures=read_errors();states=[]
    for native,expected in sorted(captures.items()):
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
            from plugins.memory.supermemory import _load_supermemory_config,_resolve_container_tag
            cfg=_load_supermemory_config(str(root/'hermes-home'))
            if not cfg['base_url'].startswith('http://127.0.0.1:'):raise ValueError('Nonlocal memory server')
            client=Supermemory(api_key=os.environ['SUPERMEMORY_API_KEY'],base_url=cfg['base_url'],max_retries=0,timeout=30)
            tag=_resolve_container_tag(cfg['container_tag'],'default')
            observe=lambda:supermemory_status(client,tag,inputs['accepted_ids'],inputs['sessions'])
        elif provider=='openviking':
            endpoint=os.environ['OPENVIKING_ENDPOINT']
            if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal memory server')
            headers={'X-OpenViking-Account':os.environ['OPENVIKING_ACCOUNT'],'X-OpenViking-User':os.environ['OPENVIKING_USER']}
            observe=lambda:openviking_status(endpoint,headers,inputs['accepted_ids'],inputs['sessions'])
        elif provider=='honcho':
            from plugins.memory.honcho.client import HonchoClientConfig
            from plugins.memory.honcho.session_peers import sanitize_peer_id
            from agent.memory_manager import sanitize_context
            from provider_adapters.honcho_local import queue_errors
            cfg=HonchoClientConfig.from_global_config()
            if cfg.session_strategy!='per-session':raise ValueError('Honcho fresh-session observer requires native per-session setup')
            captures={}
            for turn in inputs['captures']:
                native=sanitize_peer_id(cfg.resolve_session_name(session_id=turn['session']))
                entry=captures.setdefault(native,{'hermes_session':turn['session'],'hashes':[]})
                entry['hashes'].append(hashlib.sha256(sanitize_context(turn['prompt']).strip().encode()).hexdigest())
            observe=lambda:honcho_status(cfg.base_url,cfg.workspace_id,cfg.peer_name,captures,lambda:queue_errors(root))
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
