"""Opt-in read-only lifecycle observation around the pinned stock gateway.

No provider hooks, writes, prompts or native teardown are replaced. This entry
point records when native teardown really returns, including detached workers.
"""
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import runpy
import threading
import time

PROTOCOL='fixed-checkpoint-gateway-reuse-v1'


def provider_state(provider):
    """Pinned ReMe internals, observations only; unknown providers fail closed."""
    name=provider.name
    if name=='builtin':return {'name':name,'clean':True}
    if name=='mem0':
        workers=[provider._sync_thread,provider._prefetch_thread]
        return {'name':name,'backend_attached':provider._backend is not None,
            'workers_alive':sum(bool(t and t.is_alive()) for t in workers),
            'clean':provider._backend is None and not any(t and t.is_alive() for t in workers)}
    if name=='memcal':
        return {'name':name,'lock_held':provider._lock.locked(),'clean':not provider._lock.locked()}
    if name!='reme':return {'name':name,'clean':False,'reason':'unverified provider drain'}
    queue=provider._write_queue
    with queue.mutex:unfinished=queue.unfinished_tasks
    writer=provider._write_thread
    backend=provider._backend
    loop_thread=getattr(backend,'_thread',None)
    deferred=provider._deferred_backend_close
    return {'name':name,'unfinished_writes':unfinished,
        'write_enabled':provider._write_enabled,'shutdown_started':provider._shutdown_started,
        'writer_alive':bool(writer and writer.is_alive()),'deferred_backend_close':deferred,
        'backend_attached':backend is not None,'backend_loop_alive':bool(loop_thread and loop_thread.is_alive()),
        'clean':provider._shutdown_started and unfinished==0 and not (writer and writer.is_alive())
            and not deferred and backend is None and not (loop_thread and loop_thread.is_alive())}


def install(home):
    from agent.memory_manager import MemoryManager
    original_init=MemoryManager.initialize_all;original_shutdown=MemoryManager.shutdown_all
    original_submit=MemoryManager._submit_background;original_sync=MemoryManager.sync_all
    rows={};lock=threading.RLock();path=home/'reuse-observer.json';failures=set()
    observer_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    class FailureObserver(logging.Handler):
        def emit(self,record):
            text=record.getMessage()
            # Only fixed labels leave memory; native logs retain their original detail.
            for phrase in ('ReMe shutdown timed out','ReMe recording failed','ReMe did not record',
                           'ReMe write for session','Memory shutdown drain timed out',
                           'ReMe backend shutdown failed','ReMe backend shutdown is deferred',
                           'Unexpected ReMe recording failure','ReMe provider configuration is invalid',
                           'Mem0 sync failed','Mem0 backend failed to initialize','Failed to embed memory text'):
                if phrase in text:
                    with lock:failures.add(phrase)
    logging.getLogger().addHandler(FailureObserver())
    started=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    def publish():
        with lock:
            result=[]
            for key,row in rows.items():
                manager=row['manager']
                with manager._sync_executor_lock:
                    pending=sum(not f.done() for f in manager._background_futures)
                providers=[]
                for provider in manager.providers:
                    try:providers.append(provider_state(provider))
                    except Exception as exc:providers.append({'name':provider.name,'clean':False,'error_type':type(exc).__name__})
                drain=manager.shutdown_drain_state
                result.append({'session_id':row['session_id'],'manager_id':key,
                    'shutdown_returned':row['shutdown_returned'],'pending_futures':pending,
                    'native_drain':drain,'providers':providers,
                    'submitted_tasks':list(row.get('submitted_tasks',[])),'sync_dispatches':list(row.get('sync_dispatches',[])),
                    'clean':row['shutdown_returned'] and pending==0 and drain.get('status')=='drained'
                        and all(p['clean'] for p in providers)})
            threads=threading.enumerate()
            value={'observed_monotonic_ns':time.monotonic_ns(),'protocol':PROTOCOL,'pid':os.getpid(),'start_time':started,'sessions':result,'native_failures':sorted(failures),
                'observer_sha256':observer_hash,
                'native_release_threads':[t.name for t in threads if t.name.startswith(('agent-evict-','agent-cache-'))],
                'native_memory_threads':[t.name for t in threads if t.name.startswith(('reme-memory-','reme-embedded-','mem0-','mem-sync','memory-prefetch-'))]}
            if os.environ.get('PACBENCH_REUSE_CLOCK_STATE'):
                from pacbench.checkpoint_clock import benchmark_env,descriptor
                from hermes_time import now
                value['application_clock']={'now':now().isoformat(),'declared_at':benchmark_env('PACBENCH_NOW')}
                if Path(os.environ['PACBENCH_REUSE_CLOCK_STATE']).exists():
                    value['application_clock']['nonce']=descriptor(validate_clock=False)['nonce']
            raw=(json.dumps(value,sort_keys=True)+'\n').encode()
            temporary=path.with_suffix('.tmp')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            temporary.replace(path)
    @functools.wraps(original_init)
    def initialize(manager,session_id,**kwargs):
        with lock:rows[str(id(manager))]={'session_id':session_id,'manager':manager,'shutdown_returned':False}
        return original_init(manager,session_id,**kwargs)
    @functools.wraps(original_shutdown)
    def shutdown(manager):
        result=original_shutdown(manager)
        with lock:
            if str(id(manager)) in rows:rows[str(id(manager))]['shutdown_returned']=True
        publish()
        return result
    def nonce():
        if os.environ.get('PACBENCH_REUSE_CLOCK_STATE'):
            from pacbench.checkpoint_clock import descriptor
            return descriptor(validate_clock=False)['nonce']
        return None
    @functools.wraps(original_submit)
    def submit(manager,fn,*,kind='write'):
        task={'kind':kind,'nonce':nonce(),'state':'submitted'}
        with lock:
            row=rows.get(str(id(manager)))
            if row is not None:row.setdefault('submitted_tasks',[]).append(task)
        @functools.wraps(fn)
        def observed():
            with lock:task['state']='running'
            try:result=fn()
            except BaseException:
                with lock:task['state']='failed'
                raise
            with lock:task['state']='returned'
            return result
        return original_submit(manager,observed,kind=kind)
    @functools.wraps(original_sync)
    def sync(manager,user_content,assistant_content,**kwargs):
        dispatch={'nonce':nonce(),'prompt_sha256':hashlib.sha256(user_content.encode()).hexdigest(),
            'session_id':kwargs.get('session_id'),'state':'entered'}
        with lock:
            row=rows.get(str(id(manager)))
            if row is not None:row.setdefault('sync_dispatches',[]).append(dispatch)
        try:result=original_sync(manager,user_content,assistant_content,**kwargs)
        except BaseException:
            with lock:dispatch['state']='failed'
            raise
        with lock:dispatch['state']='returned'
        return result
    MemoryManager.initialize_all=initialize;MemoryManager.shutdown_all=shutdown
    MemoryManager._submit_background=submit;MemoryManager.sync_all=sync
    def observe():
        while True:
            try:publish()
            except Exception as exc:
                import traceback
                error={'error_type':type(exc).__name__,'frames':[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)]}
                (home/'reuse-observer-error.json').write_text(json.dumps(error))
                return
            time.sleep(.02)
    threading.Thread(target=observe,daemon=True,name='pacbench-reuse-observer').start()


def main():
    if os.environ.get('PACBENCH_GATEWAY_REUSE')!=PROTOCOL:
        raise RuntimeError('Explicit reuse condition required')
    install(Path(os.environ['HERMES_HOME']))
    runpy.run_module('hermes_cli.main',run_name='__main__')


if __name__=='__main__':main()
