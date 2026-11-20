"""Native source checkpoints through the existing ingestion runner.

This qualifies transport/storage lifecycle with synthetic model responses offline;
it does not certify native extraction accuracy or authorize funded execution.
Each adapter requires its own native processing and reopen witnesses.
"""
from pathlib import Path
from datetime import datetime
import hashlib,json,os,subprocess,time,uuid
from .gateway_reuse import clone_observation,tree_manifest,atomic
from .hermes_gateway import GatewayLifecycleError,_live

PROTOCOL='native-source-snapshot-v1'

REOPEN='''
import json,os,sys,hashlib,sqlite3
from pathlib import Path
from pacbench.provider_loader import provider_module
root=Path(sys.argv[1]);home=Path(os.environ['HERMES_HOME'])
cfg=json.loads((home/'mem0.json').read_text())
backend=provider_module('mem0','_backend').OSSBackend(cfg['oss'])
try:
 memory=backend._memory
 actual=Path(memory.db.connection.execute('PRAGMA database_list').fetchone()[2]).resolve()
 assert actual.parent==(root/'mem0-state').resolve()
 assert Path(backend._local_path).resolve()==(root/'qdrant').resolve()
 assert memory.db.connection.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 rows=memory.get_all(filters={'user_id':cfg['user_id']})
 raw=json.dumps(rows,sort_keys=True,default=str).encode()
 result={'provider':'mem0','interface':'shipped OSSBackend + Memory.get_all + native SQLite quick_check',
  'history_namespace_verified':True,'qdrant_namespace_verified':True,'sqlite_integrity':'ok',
  'memory_readback_sha256':hashlib.sha256(raw).hexdigest(),'memory_records':len(rows.get('results',rows) if isinstance(rows,dict) else rows),
  'llm_inference_requested':False,'memory_mutation_requested':False}
finally:backend.close()
print(json.dumps(result))
'''


class Mem0SourceCheckpoints:
    def __init__(self,preparation,canonical,schedule,env,python,*,timeout_s=None):
        self.preparation=Path(preparation).resolve();self.root=Path(canonical).resolve();self.schedule=schedule
        self.timeout_s=timeout_s
        self.env=dict(env);self.python=Path(python);self.ingested=[];self.receipts={};self.clock_proofs=[];self.failed=False
        self.store=self.root/'source-only-store';self.store.mkdir()
        (self.store/'mem0-state').mkdir();self.home=self.root/'hermes-home'
        from .observation_adapters import native_source,plan
        self.pins=native_source('mem0',self.home,Path(__file__).resolve().parents[1],self.env,self.python)
        self.config=json.loads((self.home/'mem0.json').read_text())
        config=plan('mem0',self.config,self.store)['config'];(self.home/'mem0.json').write_text(json.dumps(config))
        self.env.update(MEM0_DIR=str(self.store/'mem0-state'),PACBENCH_USAGE_ROLES=self.env.get('PACBENCH_USAGE_ROLES','{}'))
        self.clock=self.root/'source-python-clock';self.clock.mkdir()

    def materialize(self,group,destination):
        from .document_ingestion import ingest_day,processing_at
        from .python_provider_clock import VERSION as CLOCK_VERSION
        if self.failed:raise GatewayLifecycleError('Native source checkpoint failed; no automatic replay')
        ids=group['ready_batch_ids']
        if len(set(ids))!=len(ids) or ids!=[r['id'] for r in self.schedule[:len(ids)]]:
            raise GatewayLifecycleError('Native source checkpoint must be an exact ordered schedule prefix')
        if ids[:len(self.ingested)]!=self.ingested:raise GatewayLifecycleError('Native source checkpoint cannot rewind or change its prefix')
        try:
            by_id={r['id']:r for r in self.schedule}
            for identifier in ids[len(self.ingested):]:
                batch=by_id[identifier]
                if getattr(self,'before_batch',None):self.before_batch(batch)
                source=self.preparation/'sources'/identifier
                if hashlib.sha256((source/'messages.txt').read_bytes()).hexdigest()!=batch['document_sha256']:
                    raise GatewayLifecycleError('Frozen native source document changed')
                for p in (self.clock/'python-adoptions').glob('*.json'):
                    if _live(json.loads(p.read_text())['pid']):raise GatewayLifecycleError('Previous native source clock consumer is live')
                at=processing_at(batch['at'],'mem0','America/New_York');clock_started=time.monotonic()
                (self.clock/'offset').write_text(f'{datetime.fromisoformat(at).timestamp()-time.time():+.6f}\n')
                repo=Path(__file__).resolve().parents[1]
                env={**self.env,'PACBENCH_PYTHON_CLOCK_FILE':str(self.clock/'offset'),
                    'PACBENCH_PYTHON_CLOCK_PROTOCOL':CLOCK_VERSION,'PACBENCH_PYTHON_CLOCK_NONCE':uuid.uuid4().hex,
                    'PACBENCH_PYTHON_CLOCK_ROLE':'native-source-ingest','PACBENCH_DIRECT_CLOCK':f'{time.time()},{time.monotonic()}',
                    'PYTHONPATH':os.pathsep.join([str(repo/'pacbench/direct_runtime'),str(repo),self.env.get('PYTHONPATH','')])}
                record=ingest_day('mem0',source,self.root,env,batch['at'],'America/New_York',timeout_s=self.timeout_s)
                path=self.root/'ingestion'/(identifier+'.json');path.parent.mkdir(exist_ok=True);atomic(path,record)
                result=record.get('result') or {}
                if record.get('state')!='ready' or not record.get('ready') or result.get('native_errors') or result.get('model_output_truncated'):
                    raise GatewayLifecycleError('Native source ingestion failed; retain receipt, no replay')
                probe_code='import json;from datetime import datetime,timezone;from pacbench.direct_accounting import now;from pacbench.checkpoint_clock import real_time;print(json.dumps(dict(provider_at=datetime.now(timezone.utc).isoformat(),billing_at=now(),real_epoch=real_time())))'
                clock_probe=subprocess.run([str(self.python),'-c',probe_code],env={**env,'PACBENCH_PYTHON_CLOCK_ROLE':'native-source-clock-probe'},capture_output=True,text=True,timeout=10)
                if clock_probe.returncode:raise GatewayLifecycleError('Native source billing clock probe failed')
                stamp=json.loads(clock_probe.stdout.strip());provider_time=datetime.fromisoformat(stamp['provider_at'])
                if (abs(datetime.fromisoformat(stamp['billing_at']).timestamp()-stamp['real_epoch'])>5 or
                    abs((provider_time-datetime.fromisoformat(at)).total_seconds()-(time.monotonic()-clock_started))>5):
                    raise GatewayLifecycleError('Native source/billing clocks differ from their explicit scopes')
                self.clock_proofs.append({'batch_id':identifier,**stamp,'scope':'Same native source environment; local clock probe, not a paid request'})
                calls=Path(result['model_call_record']);native_calls=json.loads(calls.read_text());text=(source/'messages.txt').read_text()
                # Verify actual native SDK input, not an acknowledgment or item count.
                observed=[m.get('content','') for c in native_calls for m in c['request'].get('messages',[]) if m.get('role')=='user']
                if not any(text in content for content in observed):raise GatewayLifecycleError('Native extraction input does not contain the exact frozen source')
                self.receipts[identifier]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'native_calls':{'path':str(calls),'sha256':hashlib.sha256(calls.read_bytes()).hexdigest()}}
                self.ingested.append(identifier)
            live=[json.loads(p.read_text())['pid'] for p in (self.clock/'python-adoptions').glob('*.json') if _live(json.loads(p.read_text())['pid'])]
            if live:raise GatewayLifecycleError('Native ingestion process remains live')
            clone=clone_observation(self.store,Path(destination));probe=Path(destination).with_name(Path(destination).name+'-reopen-probe')
            clone_observation(Path(destination),probe)
            from .observation_adapters import plan
            cfg=plan('mem0',self.config,probe)['config'];probe_home=probe.parent/(probe.name+'-home');probe_home.mkdir();(probe_home/'plugins').mkdir()
            (probe_home/'plugins/mem0').symlink_to((self.home/'plugins/mem0').resolve(),target_is_directory=True)
            (probe_home/'mem0.json').write_text(json.dumps(cfg))
            env={**self.env,'HERMES_HOME':str(probe_home),'MEM0_DIR':str(probe/'mem0-state')}
            for key in ['PACBENCH_PYTHON_CLOCK_FILE','PACBENCH_PYTHON_CLOCK_PROTOCOL','PACBENCH_PYTHON_CLOCK_NONCE','PACBENCH_REUSE_CLOCK_STATE']:
                env.pop(key,None)
            proc=subprocess.run([str(self.python),'-c',REOPEN,str(probe)],env=env,capture_output=True,text=True,timeout=45)
            atomic(probe_home/'native-reopen-result.private.json',{'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr})
            if proc.returncode:raise GatewayLifecycleError('Native Mem0 store cannot reopen; raw diagnostics retained locally')
            reopened=json.loads(proc.stdout.strip().split('\n')[-1])
            if tree_manifest(Path(destination))!=clone['snapshot_manifest']:raise GatewayLifecycleError('Source-only snapshot changed during native reopen')
            evidence=Path(destination).parent/(Path(destination).name+'-native-closure.json')
            atomic(evidence,{'protocol':PROTOCOL,'provider':'mem0','checkpoint_sha256':clone['checkpoint'],
                'ingested_batch_ids':ids,'pending_work':[],'native_ingest_worker_pids':[
                    json.loads(p.read_text())['pid'] for p in (self.clock/'python-adoptions').glob('*.json')],
                'native_reopen':reopened,'pins':self.pins,'source_only':True,'source_and_billing_clock_proofs':list(self.clock_proofs),'model_extraction_quality_verified':False})
            return {'protocol':PROTOCOL,'provider':'mem0','baseline':str(destination),'manifest':clone['snapshot_manifest'],
                'ingested_batch_ids':list(self.ingested),'ingestion_receipts':dict(self.receipts),
                'input_documents':{i:{'path':str(self.preparation/'sources'/i/'messages.txt')} for i in self.ingested},
                'native_closure':{'provider':'mem0','checkpoint_sha256':clone['checkpoint'],'closed_and_reopened':True,
                    'pending_work':[],'evidence':[{'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}}
        except BaseException:self.failed=True;raise


MEMCAL_REOPEN='''
import sys,json,sqlite3,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from memcal import config,db
home=Path(sys.argv[2]);cfg=config.Config(home=home);conn=db.open_db(cfg.db_path)
try:
 assert conn.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 pending=conn.execute('SELECT count(*) FROM spool WHERE processed_at IS NULL').fetchone()[0]
 failed=conn.execute("SELECT count(*) FROM runs WHERE error IS NOT NULL AND error!=''").fetchone()[0]
 incomplete=conn.execute("SELECT count(*) FROM collection_sources WHERE status!='complete'").fetchone()[0]
 rows=[dict(r) for r in conn.execute('SELECT channel,external_id,ts,text FROM archive ORDER BY id')]
 result={'provider':'memcal','interface':'native db.open_db + persisted collector/Dream state',
  'sqlite_integrity':'ok','archive_rows':len(rows),'archive_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
  'pending_spool':pending,'failed_runs':failed,'incomplete_collection_sources':incomplete,
  'wiki_pages':len(list(cfg.wiki_dir.rglob('*.md'))),'llm_inference_requested':False,'memory_mutation_requested':False}
finally:conn.close()
print(json.dumps(result))
'''


def memcal_archive_readback(preparation,ids,store):
    """Compare submitted dialogue with actual native archive; never repair it.

    MemCal's email cleaner can deliberately transform text. Preserve those
    differences as product observations, not exact-source or extraction proof.
    """
    import sqlite3,email
    with sqlite3.connect('file:'+str(Path(store)/'memcal.db')+'?mode=ro&immutable=1',uri=True) as conn:
        actual={(ch,key):(text,ts) for ch,key,text,ts in conn.execute('SELECT channel,external_id,text,ts FROM archive')}
    witnesses=[]
    for identifier in ids:
        source=Path(preparation)/'sources'/identifier;fixture=json.loads((source/'chat.fixture.json').read_text())
        mailbox=json.loads((source/'mailbox.json').read_text()) if (source/'mailbox.json').exists() else {'messages':[]}
        for m in json.loads((source/'messages.json').read_text()):
            if m['channel']=='imessage':key=fixture['messages'][m['source_id']]['guid'];expected=m['text']
            else:
                row=next(r for r in mailbox['messages'] if r['source']['source_id']==m['source_id'])
                key=row['message_id'].strip('<>');subject=str(email.message_from_string(row['rfc822'])['Subject'])
                expected=subject+'\n\n'+m['text']
            found=actual.get((m['channel'],key))
            if found is None or found[1]!=m['written_at']:raise GatewayLifecycleError('Native archive identity/time differs from source')
            text=found[0]
            condition=('exact' if text==expected else 'line-ending-only' if text.replace('\r\n','\n')==expected else 'native-text-transformation')
            witnesses.append({'source_id':m['source_id'],'channel':m['channel'],'condition':condition,
                'submitted_text_sha256':hashlib.sha256(expected.encode()).hexdigest(),'archive_text_sha256':hashlib.sha256(text.encode()).hexdigest()})
    return {'protocol':'native-archive-source-comparison-v1','compared':len(witnesses),
        'all_identities_and_times_match':True,'rows':witnesses,'native_text_unchanged':all(w['condition']=='exact' for w in witnesses)}


class MemcalSourceCheckpoints:
    """Exact native source fixtures, collectors and Dream; no seeded wiki facts."""
    def __init__(self,preparation,canonical,schedule,env,python,*,timeout_s=None):
        self.preparation=Path(preparation).resolve();self.root=Path(canonical).resolve();self.schedule=schedule;self.env=dict(env);self.python=Path(python);self.timeout_s=timeout_s
        self.ingested=[];self.receipts={};self.failed=False;self.store=self.root/'source-only-store';self.store.mkdir()
        from .observation_adapters import native_source
        self.pins=native_source('memcal',self.root/'hermes-home',Path(__file__).resolve().parents[1],self.env,self.python)
        from .provider_versions import MEMCAL_CONTEXT_REVISION,MEMCAL_OPERATIONS_REVISION,MEMCAL_REVISION
        if self.pins.get('memcal') not in {MEMCAL_CONTEXT_REVISION,MEMCAL_OPERATIONS_REVISION} and not (
                self.pins.get('memcal')==MEMCAL_REVISION and self.pins.get('memcal_baseline')=='stable-0.8.11'
                and self.env.get('PACBENCH_R2_LIVE_SCOPE')):
            raise GatewayLifecycleError('MemCal checkpoint pin differs')
        self.env.update(MEMCAL_HOME=str(self.store),PACBENCH_USAGE_ROLES=self.env.get('PACBENCH_USAGE_ROLES','{}'))

    def materialize(self,group,destination):
        from .document_ingestion import ingest_day
        if self.failed:raise GatewayLifecycleError('Native MemCal checkpoint failed; no replay')
        ids=group['ready_batch_ids']
        if ids!=[r['id'] for r in self.schedule[:len(ids)]] or ids[:len(self.ingested)]!=self.ingested:
            raise GatewayLifecycleError('Native MemCal checkpoint must retain exact source order')
        try:
            for batch in self.schedule[len(self.ingested):len(ids)]:
                if getattr(self,'before_batch',None):self.before_batch(batch)
                source=self.preparation/'sources'/batch['id']
                if hashlib.sha256((source/'messages.txt').read_bytes()).hexdigest()!=batch['document_sha256']:
                    raise GatewayLifecycleError('Frozen native MemCal source changed')
                result=ingest_day('memcal',source,self.root,self.env,batch['at'],'America/New_York',timeout_s=self.timeout_s)
                path=self.root/'ingestion'/(batch['id']+'.json');path.parent.mkdir(exist_ok=True);atomic(path,result)
                r=result.get('result',{})
                if not result.get('ready') or result.get('state')!='ready' or r.get('processing',{}).get('errors'):
                    raise GatewayLifecycleError('Native MemCal collection/Dream did not complete')
                self.receipts[batch['id']]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};self.ingested.append(batch['id'])
            clone=clone_observation(self.store,Path(destination));probe=Path(destination).with_name(Path(destination).name+'-reopen-probe')
            clone_observation(Path(destination),probe)
            proc=subprocess.run([str(self.python),'-c',MEMCAL_REOPEN,self.env['MEMCAL_SRC'],str(probe)],env=self.env,capture_output=True,text=True,timeout=30)
            atomic(probe.parent/(probe.name+'-result.private.json'),{'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr})
            if proc.returncode:raise GatewayLifecycleError('Native repaired MemCal checkpoint could not reopen')
            native=json.loads(proc.stdout.strip().split('\n')[-1]);expected=sum(len(b['source_ids']) for b in self.schedule[:len(ids)])
            if native['archive_rows']!=expected or native['pending_spool'] or native['failed_runs'] or native['incomplete_collection_sources']:
                raise GatewayLifecycleError('Native MemCal archive/processing coverage incomplete')
            readback=memcal_archive_readback(self.preparation,ids,Path(destination))
            if tree_manifest(Path(destination))!=clone['snapshot_manifest']:raise GatewayLifecycleError('Native MemCal snapshot changed during inspection')
            path=Path(destination).parent/(Path(destination).name+'-native-closure.json')
            atomic(path,{'protocol':PROTOCOL,'provider':'memcal','checkpoint_sha256':clone['checkpoint'],'ingested_batch_ids':ids,
                'source_only':True,'pending_work':[],'pins':self.pins,'native_reopen':native,'expected_archive_rows':expected,
                'archive_source_readback':readback,
                'processing_condition':'native source collectors and nightly Dream; read-only cloned-store inspection',
                'model_extraction_quality_verified':False})
            return {'protocol':PROTOCOL,'provider':'memcal','baseline':str(destination),'manifest':clone['snapshot_manifest'],
                'ingested_batch_ids':list(self.ingested),'ingestion_receipts':dict(self.receipts),
                'input_documents':{i:{'path':str(self.preparation/'sources'/i/'messages.txt')} for i in self.ingested},
                'native_closure':{'provider':'memcal','checkpoint_sha256':clone['checkpoint'],'closed_and_reopened':True,'pending_work':[],
                    'evidence':[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}}
        except BaseException:self.failed=True;raise


REME_REOPEN='''
import asyncio,json,sys,re
from pathlib import Path
from reme import Application
from reme.config import resolve_app_config
async def inspect():
 root=Path(sys.argv[1]);expected=json.loads(Path(sys.argv[2]).read_text())
 app=Application(**resolve_app_config(workspace_dir=str(root)))
 try:
  # Start persisted storage components only. No resource, index or memory jobs
  # are run. This is readback, not proof of a future HTTP service's readiness.
  for component in app._topological_order():await app._start_one(component)
  store=app.context.components['file_store']['default'];out=[]
  for item in expected:
   nodes=await store.get_nodes([item['card_path']]);assert len(nodes)==1
   node=nodes[0];assert node.chunk_ids and node.front_matter.source_resource=='[['+item['resource_path']+']]'
   # Query a token from an existing indexed chunk, without adding/reindexing it.
   chunk=store.file_chunks[node.chunk_ids[0]]
   tokens=re.findall(r'[A-Za-z0-9]{4,}',chunk.text);assert tokens
   query=max(tokens,key=len);hits=await store.keyword_search(query,10000,{})
   matched=[h.id for h in hits if h.path==node.path and h.id in node.chunk_ids];assert matched
   out.append({'card_path':node.path,'source_resource':node.front_matter.source_resource,
       'chunk_ids':list(node.chunk_ids),'keyword_hit_ids':matched})
  return {'provider':'reme','interface':'native persisted components: get_nodes + keyword_search; no jobs started',
    'cards':out,'jobs_started':False,'llm_inference_requested':False,'memory_mutation_requested':False}
 finally:await app._close()
print(json.dumps(asyncio.run(inspect())))
'''


class ReMeSourceCheckpoints:
    """Native resource watcher/index, closed snapshots and persisted BM25 readback."""
    def __init__(self,preparation,canonical,schedule,env,python,*,model,base,key):
        self.preparation=Path(preparation).resolve();self.root=Path(canonical).resolve();self.schedule=schedule;self.env=dict(env)
        self.python=Path(python);self.model=model;self.base=base;self.key=key
        self.ingested=[];self.receipts={};self.failed=False;self.stopped=[]
        self.store=self.root/'reme-workspace'
        plugin=self.root/'hermes-home/plugins/reme'
        target=Path(__file__).resolve().parents[1]/'vendor/reme/integrations/hermes_agent'
        if plugin.exists() or plugin.is_symlink():
            if not plugin.is_symlink() or plugin.resolve()!=target.resolve():
                raise GatewayLifecycleError('Native ReMe plugin differs')
        else:plugin.symlink_to(target,target_is_directory=True)
        from .observation_adapters import native_source
        self.pins=native_source('reme',self.root/'hermes-home',Path(__file__).resolve().parents[1],self.env,self.python)

    def materialize(self,group,destination):
        from .document_ingestion import ingest_day
        from .document_audit import audit_reme_resource_receipt
        from provider_adapters.reme_local import start
        if self.failed:raise GatewayLifecycleError('Native ReMe checkpoint failed; no automatic replay')
        ids=group['ready_batch_ids']
        if ids!=[r['id'] for r in self.schedule[:len(ids)]] or ids[:len(self.ingested)]!=self.ingested:
            raise GatewayLifecycleError('Native ReMe checkpoint must retain exact source order')
        stop=None
        try:
            stop=start(self.root,self.env,self.model,self.base,self.key)
            setup=json.loads((self.root/'native-server-setup.json').read_text());pid=setup['pid']
            for batch in self.schedule[len(self.ingested):len(ids)]:
                if getattr(self,'before_batch',None):self.before_batch(batch)
                source=self.preparation/'sources'/batch['id'];text=(source/'messages.txt').read_text()
                if hashlib.sha256(text.encode()).hexdigest()!=batch['document_sha256']:raise GatewayLifecycleError('Frozen native ReMe source changed')
                record=ingest_day('reme',source,self.root,self.env,batch['at'],'America/New_York',timeout_s=90)
                path=self.root/'ingestion'/(batch['id']+'.json');path.parent.mkdir(exist_ok=True);atomic(path,record)
                result=record.get('result',{})
                if not record.get('ready') or record.get('state')!='ready' or audit_reme_resource_receipt(result,batch['id'],text,batch['source_ids']):
                    raise GatewayLifecycleError('Native ReMe resource/card/index readiness incomplete')
                self.receipts[batch['id']]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};self.ingested.append(batch['id'])
            shutdown=stop();stop=None
            if (_live(pid) or shutdown['forced_kill'] or shutdown['returncode'] not in (0,-15) or
                shutdown['native_shutdown_complete'] is not True):
                raise GatewayLifecycleError('Native ReMe graceful closure unverified')
            self.stopped.append(pid)
            clone=clone_observation(self.store,Path(destination));probe=Path(destination).with_name(Path(destination).name+'-reopen-probe')
            clone_observation(Path(destination),probe)
            expected=[json.loads(Path(v['path']).read_text())['result'] for v in self.receipts.values()]
            requested=probe.parent/(probe.name+'-expected.json');atomic(requested,[{k:r[k] for k in ['card_path','resource_path']} for r in expected])
            env={**self.env,'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
            proc=subprocess.run([str(self.python),'-c',REME_REOPEN,str(probe),str(requested)],env=env,capture_output=True,text=True,timeout=45)
            atomic(probe.parent/(probe.name+'-result.private.json'),{'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr})
            if proc.returncode:raise GatewayLifecycleError('Native ReMe persisted card/index cannot reopen')
            native=json.loads(proc.stdout.strip().split('\n')[-1])
            if tree_manifest(Path(destination))!=clone['snapshot_manifest']:raise GatewayLifecycleError('Native ReMe source snapshot changed during inspection')
            evidence=Path(destination).parent/(Path(destination).name+'-native-closure.json')
            atomic(evidence,{'protocol':PROTOCOL,'provider':'reme','checkpoint_sha256':clone['checkpoint'],'ingested_batch_ids':ids,
                'source_only':True,'pending_work':[],'pins':self.pins,'native_reopen':native,'native_ingest_worker_pids':list(self.stopped),
                'expected_cards':len(self.ingested),'processing_condition':'Native resource watcher and index; real service shutdown; inspection-only persisted component reopen',
                'latest_shutdown':shutdown,
                'model_extraction_quality_verified':False})
            return {'protocol':PROTOCOL,'provider':'reme','baseline':str(destination),'manifest':clone['snapshot_manifest'],
                'ingested_batch_ids':list(self.ingested),'ingestion_receipts':dict(self.receipts),
                'input_documents':{i:{'path':str(self.preparation/'sources'/i/'messages.txt')} for i in self.ingested},
                'native_closure':{'provider':'reme','checkpoint_sha256':clone['checkpoint'],'closed_and_reopened':True,'pending_work':[],
                    'evidence':[{'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}}
        except BaseException:self.failed=True;raise
        finally:
            if stop:stop()


def verify_collector_snapshot(provider,proof,schedule,ids,manifest):
    """Provider-specific qualification after common receipt/hash validation."""
    from .document_audit import audit_reme_resource_receipt
    from .provider_versions import MEMCAL_CONTEXT_REVISION,MEMCAL_OPERATIONS_REVISION,MEMCAL_REVISION
    from .reme_memos_setup import PINS
    by_id={b['id']:b for b in schedule}
    expected_rows=sum(len(by_id[i]['source_ids']) for i in ids)
    for binding in proof['native_closure']['evidence']:
        native=json.loads(Path(binding['path']).read_text());reopened=native.get('native_reopen',{})
        if (native.get('protocol')!=PROTOCOL or native.get('provider')!=provider or
            native.get('checkpoint_sha256')!=manifest['sha256'] or native.get('ingested_batch_ids')!=ids or
            native.get('source_only') is not True or native.get('pending_work')!=[] or
            reopened.get('provider')!=provider or reopened.get('llm_inference_requested') is not False or
            reopened.get('memory_mutation_requested') is not False):
            raise GatewayLifecycleError('Native collector/store reopen evidence invalid')
        if provider=='memcal':
            pins=native.get('pins',{})
            valid_pin=pins.get('memcal') in {MEMCAL_CONTEXT_REVISION,MEMCAL_OPERATIONS_REVISION} or (
                pins.get('memcal')==MEMCAL_REVISION and pins.get('memcal_baseline')=='stable-0.8.11')
            if (not valid_pin or reopened.get('sqlite_integrity')!='ok' or
                reopened.get('archive_rows')!=expected_rows or native.get('expected_archive_rows')!=expected_rows or
                any(reopened.get(k)!=0 for k in ['pending_spool','failed_runs','incomplete_collection_sources'])):
                raise GatewayLifecycleError('Native repaired MemCal archive/processing coverage incomplete')
            readback=native.get('archive_source_readback',{})
            if (readback.get('protocol')!='native-archive-source-comparison-v1' or readback.get('compared')!=expected_rows or
                readback.get('all_identities_and_times_match') is not True or
                [w.get('source_id') for w in readback.get('rows',[])]!=[sid for i in ids for sid in by_id[i]['source_ids']]):
                raise GatewayLifecycleError('Native MemCal archive source comparison absent')
        elif provider=='reme':
            cards=reopened.get('cards',[]);pids=native.get('native_ingest_worker_pids',[])
            if (native.get('pins',{}).get('reme')!=PINS['reme'][1] or native.get('expected_cards')!=len(ids) or
                len(cards)!=len(ids) or reopened.get('jobs_started') is not False or
                not pids or any(_live(pid) for pid in pids) or
                native.get('latest_shutdown',{}).get('forced_kill') is not False or
                native.get('latest_shutdown',{}).get('native_shutdown_complete') is not True or
                native.get('latest_shutdown',{}).get('returncode') not in (0,-15) or
                any(not c.get('chunk_ids') or not c.get('keyword_hit_ids') or
                    not set(c['keyword_hit_ids']).issubset(c['chunk_ids']) for c in cards)):
                raise GatewayLifecycleError('Native ReMe resource/index/quiescence coverage incomplete')
            log=native['latest_shutdown']['log_slice']
            with Path(log['path']).open('rb') as stream:
                stream.seek(log['offset']);segment=stream.read(log['length'])
            if hashlib.sha256(segment).hexdigest()!=log['sha256']:
                raise GatewayLifecycleError('Native ReMe shutdown log evidence changed')
        else:raise GatewayLifecycleError('Unqualified native collector provider')
    for identifier in ids:
        document=proof.get('input_documents',{}).get(identifier,{})
        path=Path(document['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=by_id[identifier]['document_sha256']:
            raise GatewayLifecycleError('Native collector source document changed')
        result=json.loads(Path(proof['ingestion_receipts'][identifier]['path']).read_text())['result']
        if provider=='memcal':
            if (result.get('collector_protocol')!='native-cli-collection-v1' or
                result.get('processing',{}).get('errors') or result.get('collection',{}).get('error')):
                raise GatewayLifecycleError('Native MemCal collection/Dream readiness unverified')
        else:
            if audit_reme_resource_receipt(result,identifier,path.read_text(),by_id[identifier]['source_ids']):
                raise GatewayLifecycleError('Native ReMe resource/card/index binding invalid')
            resource=Path(proof['baseline'])/result['resource_path'];card=Path(proof['baseline'])/result['card_path']
            if resource.read_bytes()!=path.read_bytes() or hashlib.sha256(card.read_bytes()).hexdigest()!=result['card_sha256']:
                raise GatewayLifecycleError('Native ReMe persisted source/card bytes differ')
            if not any(c['card_path']==result['card_path'] and c['source_resource']=='[['+result['resource_path']+']]'
                for binding in proof['native_closure']['evidence']
                for c in json.loads(Path(binding['path']).read_text())['native_reopen']['cards']):
                raise GatewayLifecycleError('Native ReMe reopened card provenance differs')
