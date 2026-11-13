"""Read-only terminal observation before stopping a run-owned native backend.

This is distinct from readiness before a dependent question. It never commits,
flushes, changes provider time, or replays a model request. The first provider
with full capture coverage is OpenViking; ledger snapshots for other providers
must not be described as proof of their deferred capture completion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

from .followups import digest, save

VERSION = 'openviking-terminal-processing-v1'
TIMEOUT_S = 900


def _persist(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False)
        stream.write('\n');stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def scope_for(root):
    root=Path(root)
    paths=list((root/'turns').rglob('turn.json'))
    paths.extend((root/'followups').rglob('turn.json'))
    if (root/'recall.json').is_file():paths.append(root/'recall.json')
    # combined/turn.json is the actual recovery turn. Filesystem mtimes are not
    # execution order (copies can preserve or tie them).
    paths=list(set(paths))
    documents={p:json.loads(p.read_text()) for p in paths}
    paths.sort(key=lambda p:(documents[p].get('at',''),
        documents[p].get('hermes_gateway',{}).get('real_started_at',''),len(p.parts),str(p)))
    turns=[];explicit=[]
    for path in paths:
        turn=documents[path]
        if not turn.get('session_id'):
            raise ValueError('Executed turn lacks a native session identity')
        row={'file':str(path.relative_to(root)),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'session':turn['session_id'],'prompt':turn['prompt'],'answer':turn.get('answer','')}
        row['binding']=digest(row);turns.append(row)
        for result in turn.get('tool_results',[]):
            if result.get('tool')!='viking_remember' or result.get('success') is not True:continue
            value=json.loads(result['text'])
            if not isinstance(value.get('task_id'),str) or not value['task_id']:
                raise ValueError('Accepted explicit OpenViking write lacks a task identity')
            explicit.append({'task_id':value['task_id'],'session':value.get('session_id'),
                             'turn_binding':row['binding']})
    return {'protocol':VERSION,'provider':'openviking','turns':turns,'explicit_writes':explicit,
            'scope':'Every executed turn and recovery turn; both captured sides plus independent explicit writes'}


def _read(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Native capture witness leaves its run-owned store')
    return path.read_bytes()


def capture_snapshot(root, scope, read_task, *, account='default', user='default', checkpoint=None):
    """Bind archived native message IDs to complete tasks, including late turns."""
    root=Path(root)
    base=root/'openviking-data/viking'/account/'user'/user/'sessions'
    sessions={row['session'] for row in scope['turns']}
    messages={};coverage={};tasks={};files=[]
    for session in sorted(sessions):
        if checkpoint:checkpoint()
        if not session or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in session):
            raise ValueError('Unsafe native capture session identity')
        directory=base/session
        candidates=[directory/'messages.jsonl']
        candidates.extend(sorted((directory/'history').glob('archive_*/messages.jsonl')))
        by_id={}
        for path in candidates:
            if checkpoint:checkpoint()
            if not path.exists():continue
            raw=_read(path,root);rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
            files.append({'file':str(path.relative_to(root)),'sha256':hashlib.sha256(raw).hexdigest()})
            for row in rows:
                text=''.join(p.get('text','') for p in row.get('parts',[]) if p.get('type')=='text')
                item={'id':row['id'],'role':row['role'],'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                      'created_at':row['created_at']}
                if row['id'] in by_id and by_id[row['id']]!=item:
                    raise ValueError('Native captured message identity changed')
                by_id[row['id']]=item
            if path.parent.name.startswith('archive_'):
                metadata_path=path.parent/'.meta.json'
                if not metadata_path.exists():continue
                metadata_raw=_read(metadata_path,root);phase=json.loads(metadata_raw).get('phase1',{})
                files.append({'file':str(metadata_path.relative_to(root)),'sha256':hashlib.sha256(metadata_raw).hexdigest()})
                if phase.get('status')!='ready':continue
                if phase.get('messages_sha256')!=hashlib.sha256(raw).hexdigest():
                    raise ValueError('Native archive message digest differs')
                queued=phase.get('queue_message') or {}
                task_id=queued.get('task_id')
                if queued.get('session_id')!=session or not isinstance(task_id,str) or not task_id:
                    raise ValueError('Native archive has no bound commit task')
                archived=phase.get('archived_message_ids',[])
                if not archived or not set(archived).issubset({r['id'] for r in rows}):
                    raise ValueError('Native archive coverage is missing or invalid')
                bound={'session':session,'message_ids':archived}
                if task_id in coverage and coverage[task_id]!=bound:
                    raise ValueError('Native commit task has inconsistent archive coverage')
                coverage[task_id]=bound
        messages[session]=sorted(by_id.values(),key=lambda r:(r['created_at'],r['id']))
    identities=set(coverage)|{r['task_id'] for r in scope['explicit_writes']}
    for identifier in sorted(identities):
        if checkpoint:checkpoint()
        if not identifier or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in identifier):
            raise ValueError('Unsafe native task identity')
        task=read_task(identifier)
        expected=coverage.get(identifier,{}).get('session') or next(
            (w['session'] for w in scope['explicit_writes'] if w['task_id']==identifier),None)
        if (task.get('task_id')!=identifier or task.get('task_type')!='session_commit'
                or expected and task.get('resource_id')!=expected):
            raise ValueError('Native terminal task identity or session mismatch')
        result=task.get('result') or {}
        tasks[identifier]={'id':identifier,'session':task.get('resource_id'),'status':task.get('status'),
            'native_error':bool(task.get('error') or isinstance(result,dict) and
                (result.get('errors') or result.get('status') in {'failed','error'})),
            'response_sha256':digest(task)}
    complete_ids={(covered['session'],mid) for tid,covered in coverage.items() if tasks[tid]['status']=='completed'
                  and not tasks[tid]['native_error'] for mid in covered['message_ids']}
    matches=[];cursors={}
    for row in scope['turns']:
        session=row['session'];items=messages.get(session,[]);cursor=cursors.get(session,0)
        ids=[]
        for role,key in [('user','prompt'),('assistant','answer')]:
            text=row[key]
            if not text.strip():continue
            wanted=hashlib.sha256(text.encode()).hexdigest();found=None
            for index in range(cursor,len(items)):
                candidate=items[index]
                if candidate['role']==role and candidate['text_sha256']==wanted:
                    found=index;break
                if role=='assistant' and candidate['role']=='user':break
            if found is None:break
            ids.append(items[found]['id']);cursor=found+1
        needed=int(bool(row['prompt'].strip()))+int(bool(row['answer'].strip()))
        witnessed=len(ids)==needed
        if witnessed:cursors[session]=cursor
        matches.append({'turn_binding':row['binding'],'session':session,'captured_ids':ids,
                        'both_sides_captured':witnessed,'processed':witnessed and
                        {(session,mid) for mid in ids}.issubset(complete_ids)})
    failed=any(t['native_error'] or t['status'] in {'failed','cancelled'} for t in tasks.values())
    all_tasks=all(t['status']=='completed' and not t['native_error'] for t in tasks.values())
    done=all_tasks and all(m['processed'] for m in matches)
    return {'state':'native_failed' if failed else 'complete' if done else 'pending',
            'captures':matches,'tasks':list(tasks.values()),'archive_coverage':coverage,
            'native_files':files,'native_messages':messages,'processing_forced':False}


def observe_before_stop(root, env, *, timeout_s=TIMEOUT_S, boundary=None):
    """Persist each changed observation before normal native service shutdown."""
    root=Path(root);directory=root/'terminal-processing'
    directory.mkdir(exist_ok=False)
    deadline=time.monotonic()+timeout_s
    record={'protocol':VERSION,'scope_hash':None,'state':'observing',
            'started_real':datetime.now(timezone.utc).isoformat(),'timeout_s':timeout_s,
            'clock_advanced':False,'processing_forced':False,'observations':[],
            'namespace':{'account':env.get('OPENVIKING_ACCOUNT'),'user':env.get('OPENVIKING_USER')}}
    class DeadlineReached(Exception):pass
    class BoundaryReached(Exception):pass
    def checkpoint():
        if time.monotonic()>=deadline:raise DeadlineReached()
        if boundary:
            try:boundary()
            except Exception as exc:
                record['error_type']=type(exc).__name__
                raise BoundaryReached() from exc
        if time.monotonic()>=deadline:raise DeadlineReached()
    try:
        scope=scope_for(root);_persist(directory/'scope.json',scope)
        record['scope_hash']=digest(scope)
        _persist(directory/'status.json',record)
        checkpoint()
        from .reme_memos_setup import local_endpoint
        endpoint=local_endpoint(env['OPENVIKING_ENDPOINT'])
        if not endpoint.startswith('http://127.0.0.1:'):raise ValueError('Nonlocal terminal observer')
        def read_task(identifier):
            checkpoint()
            request=urllib.request.Request(endpoint+'/api/v1/tasks/'+urllib.parse.quote(identifier,safe=''),
                headers={'X-OpenViking-Account':env['OPENVIKING_ACCOUNT'],'X-OpenViking-User':env['OPENVIKING_USER']})
            with urllib.request.urlopen(request,timeout=min(10,deadline-time.monotonic())) as response:value=json.load(response)
            checkpoint()
            if value.get('status')!='ok':raise ValueError('Native terminal task status unavailable')
            return value['result']
        previous=None
        while True:
            checkpoint()
            observation=capture_snapshot(root,scope,read_task,account=env['OPENVIKING_ACCOUNT'],user=env['OPENVIKING_USER'],checkpoint=checkpoint)
            if observation!=previous:
                record['observations'].append(observation);previous=observation
                _persist(directory/'status.json',record)
            checkpoint()
            if observation['state'] in {'complete','native_failed'}:
                record['state']=observation['state'];break
            if time.monotonic()>=deadline:
                record['state']='deadline_pending';break
            time.sleep(min(2,max(0,deadline-time.monotonic())))
    except DeadlineReached:
        record['state']='deadline_pending'
    except BoundaryReached:
        record['state']='stopped_by_boundary'
    except Exception as exc:
        from .stop import OperatorStop
        from .gateway_client import ModelAccessBlocked,SpendingBlocked
        record['state']='stopped_by_boundary' if isinstance(exc,(OperatorStop,ModelAccessBlocked,SpendingBlocked)) else 'observation_unknown'
        record['error_type']=type(exc).__name__
    record['finished_real']=datetime.now(timezone.utc).isoformat()
    _persist(directory/'status.json',record)
    return record


def audit_terminal(root):
    root=Path(root);path=root/'terminal-processing/status.json'
    try:
        record=json.loads(path.read_text());scope=json.loads((path.parent/'scope.json').read_text())
        if (record['protocol']!=VERSION or record['scope_hash']!=digest(scope) or scope!=scope_for(root)
                or record['clock_advanced'] is not False or record['processing_forced'] is not False
                or record['state']!='complete' or not record['observations']):
            return ['Native terminal processing is incomplete or has an invalid scope binding']
        final=record['observations'][-1]
        if (final['state']!='complete' or final['processing_forced'] is not False
                or any(not r['both_sides_captured'] or not r['processed'] for r in final['captures'])
                or any(t['status']!='completed' or t['native_error'] for t in final['tasks'])):
            return ['Native terminal capture or consolidation completion is unproven']
        for item in final['native_files']:
            if hashlib.sha256(_read(root/item['file'],root)).hexdigest()!=item['sha256']:
                return ['Native terminal source witness differs from the observed file']
        namespace=record['namespace']
        def stored_task(identifier):
            base=root/'openviking-data/viking'/namespace['account']/'_system/tasks'/namespace['user']
            return json.loads(_read(base/(identifier+'.json'),root))
        rebuilt=capture_snapshot(root,scope,stored_task,**namespace)
        for key in ('state','captures','archive_coverage','native_files','native_messages','processing_forced'):
            if rebuilt[key]!=final[key]:
                return ['Native terminal coverage differs from the persisted provider state']
        fields=('id','session','status','native_error')
        if [{k:t[k] for k in fields} for t in rebuilt['tasks']]!=[{k:t[k] for k in fields} for t in final['tasks']]:
            return ['Native terminal task completion differs from the persisted provider state']
    except (OSError,ValueError,KeyError,TypeError,IndexError):
        return ['Native terminal processing evidence is missing or malformed']
    return []
