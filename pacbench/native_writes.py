"""Observe accepted native writes before dependent fresh-session recall.

Polling never flushes, edits, or re-submits provider data. An acknowledgement
is not a processing receipt. No expected answer enters these transport checks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from .followups import digest,save

VERSION='accepted-native-writes-v1'
ASYNC={'supermemory','openviking'}


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
    ids,sessions=accepted(turns,provider)
    inputs={'provider':provider,'accepted_ids':ids,'sessions':sessions}
    hashes=[digest(t) for t in turns]
    bound={'dependencies':[r['id'] for r in required],'inputs':inputs,'turn_hashes':hashes}
    binding=digest(bound)
    cached=root/'write-readiness'/(binding+'.json')
    if cached.exists():record=json.loads(cached.read_text())
    else:
        from .document_ingestion import PROCESSING_TIMEOUT_S
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
                'state':'ready' if observed.get('ready') is True else 'unavailable','observed':observed}
        save(cached,record)
    save(directory/'write-readiness.json',record)
    if not ready_proof(record,bound):raise RuntimeError('Accepted native writes are not ready; inspect write-readiness.json')


def complete(provider,states):
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
            ids,sessions=accepted(prior,root.name)
            inputs={'provider':root.name,'accepted_ids':ids,'sessions':sessions}
            bound={'dependencies':[d['id'] for d in required],'inputs':inputs,'turn_hashes':[digest(t) for t in prior]}
            if not ready_proof(r,bound):
                errors.append(row['id']+': dependent recall preceded proven native write completion')
        except (OSError,ValueError,KeyError,TypeError,IndexError):
            errors.append(row['id']+': missing or invalid native write readiness proof')
    return errors


def supermemory_status(client,tag,ids,sessions):
    found=set(ids);page=1
    while True:
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
    for session in sessions:
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
