"""Native config/storage conformance canaries; not model extraction or scores."""
import json,os,shutil,subprocess,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from pacbench.observation_adapters import plan

ROOT=Path(__file__).resolve().parents[1];PYTHON=ROOT/'vendor/venv-hermes-isolated/bin/python'
MEM0=ROOT/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0/repeat-1/mem0/hermes-home/plugins/mem0'
MEMCAL=ROOT.parent/'memcal-review-da58057'


def env(tmp_path):
    e={k:v for k,v in os.environ.items() if k in {'PATH','LANG','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR'}}
    e.update(HOME=str(tmp_path),HERMES_HOME=str(tmp_path/'hermes-home'),MEM0_TELEMETRY='false',PYTHONPATH=str(ROOT))
    return e


@pytest.mark.parametrize('provider',['mem0','memcal','reme'])
def test_plan_preserves_native_non_path_configuration_and_never_claims_readiness(tmp_path,provider):
    c={'mode':'http'} if provider=='reme' else {'unchanged':'native'} if provider=='memcal' else {
        'mode':'oss','user_id':'same-principal','oss':{'llm':{'provider':'openai','config':{'model':'openai/gpt-6-luna'}},
        'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text'}},'vector_store':{'provider':'qdrant','config':{'path':'old'}}}}
    before=json.dumps(c,sort_keys=True);result=plan(provider,c,tmp_path,endpoint='http://127.0.0.1:2333')
    assert json.dumps(c,sort_keys=True)==before and result['live_qualified'] is False and result['required_proof']
    if provider=='mem0':assert result['config']['oss']['llm']==c['oss']['llm'] and result['config']['oss']['embedder']==c['oss']['embedder']


def test_native_mem0_oss_qdrant_and_history_follow_distinct_observations(tmp_path):
    if not MEM0.exists() or not PYTHON.exists():pytest.skip('Exact retained native Mem0 plugin/runtime required; paid readiness stays gated')
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def reply(self,value):
            raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            calls.append(self.path);self.reply({'models':[{'name':'nomic-embed-text:latest','model':'nomic-embed-text:latest','modified_at':'2026-10-05T09:00:00Z','size':1,'digest':'a'*64}]})
        def do_POST(self):
            calls.append(self.path);body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path=='/api/embed'  # no LLM extraction in this transport/storage canary
            texts=body['input'];texts=[texts] if isinstance(texts,str) else texts
            self.reply({'model':'nomic-embed-text','embeddings':[[1.]+[0.]*767 for _ in texts]})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    home=tmp_path/'hermes-home';(home/'plugins').mkdir(parents=True);(home/'plugins/mem0').symlink_to(MEM0.resolve(),target_is_directory=True)
    endpoint=f'http://127.0.0.1:{server.server_port}'
    cfg={'mode':'oss','user_id':'synthetic-canary','oss':{'llm':{'provider':'openai','config':{'model':'offline-unused','api_key':'offline','openai_base_url':endpoint+'/v1'}},
        'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':endpoint,'embedding_dims':768}},
        'vector_store':{'provider':'qdrant','config':{'collection_name':'mem0','path':'unset'}}}}
    plans=[plan('mem0',cfg,tmp_path/name) for name in ['one','two']]
    for p in plans:Path(p['state_alias_target']).mkdir(parents=True)
    alias=tmp_path/'mem0-state';alias.symlink_to(plans[0]['state_alias_target'],target_is_directory=True)
    e=env(tmp_path);e['MEM0_DIR']=str(alias)
    script='''
import json,sys,os,importlib.metadata
from pathlib import Path
from pacbench.providers import provider_module
home=Path(os.environ['HERMES_HOME']);plans=json.loads(sys.argv[1]);alias=Path(os.environ['MEM0_DIR'])
native=provider_module('mem0');rows=[]
assert importlib.metadata.version('mem0ai')=='2.2.1'
for i,plan in enumerate(plans):
 if i: alias.unlink();alias.symlink_to(plan['state_alias_target'],target_is_directory=True)
 (home/'mem0.json').write_text(json.dumps(plan['config']))
 p=native.Mem0MemoryProvider();p.initialize('session-'+str(i),hermes_home=str(home));assert p._backend is not None
 memory=p._backend._memory;actual=memory.db.connection.execute('PRAGMA database_list').fetchone()[2]
 assert Path(actual).resolve().parent==Path(plan['state_alias_target']).resolve()
 if i==0:
  p._add([{'role':'user','content':'SYNTHETIC-NAMESPACE-CANARY'}],infer=False)
  assert p._search('SYNTHETIC-NAMESPACE-CANARY')
 else:assert p._search('SYNTHETIC-NAMESPACE-CANARY')==[]
 rows.append({'history_path_follows_alias':True,'qdrant_path':p._backend._local_path})
 p.shutdown();assert p._backend is None
print(json.dumps({'native_oss_storage_canary':'pass','observations':rows,'model_extraction_verified':False}))
'''
    try:
        result=subprocess.run([str(PYTHON),'-c',script,json.dumps(plans)],env=e,capture_output=True,text=True,timeout=35)
        assert result.returncode==0,result.stderr[-3500:]
        assert '"native_oss_storage_canary": "pass"' in result.stdout
        assert calls and all(path in {'/api/tags','/api/embed'} for path in calls)
    finally:server.shutdown();server.server_close();thread.join(2)


def test_native_memcal_archive_writes_stay_in_the_selected_observation(tmp_path):
    if not MEMCAL.exists() or not PYTHON.exists():pytest.skip('Exact local MemCal base tree required; target-SHA gate stays blocked')
    assert subprocess.check_output(['git','-C',str(MEMCAL),'rev-parse','HEAD'],text=True).strip()=='da58057eb00f79082dfcf8f59baa264267c83ca1'
    assert not subprocess.check_output(['git','-C',str(MEMCAL),'status','--porcelain'],text=True).strip()
    e=env(tmp_path);e.update(MEMCAL_SRC=str(MEMCAL),MEMCAL_HOME=str(tmp_path/'selected'),PYTHONPATH=str(ROOT)+os.pathsep+str(MEMCAL))
    script='''
import os,json,sys,importlib.util,shutil
from pathlib import Path
from memcal import config,db
root=Path(sys.argv[1]);base=root/'baseline';cfg=config.Config(home=base);cfg.ensure_dirs();conn=db.open_db(cfg.db_path);conn.close()
for name in ['one','two']:shutil.copytree(base,root/name)
alias=root/'selected';alias.symlink_to(root/'one',target_is_directory=True)
source=Path(os.environ['MEMCAL_SRC'])/'integrations/hermes/memcal/__init__.py'
spec=importlib.util.spec_from_file_location('native_memcal_canary',source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for i,name in enumerate(['one','two']):
 if i:alias.unlink();alias.symlink_to(root/name,target_is_directory=True)
 p=m.MemcalMemoryProvider();p.initialize('fresh-'+name)
 with db.open_db(root/name/'memcal.db') as conn:assert conn.execute('SELECT count(*) FROM archive').fetchone()[0]==0
 p.on_turn_start(1,message='SYNTHETIC OBSERVATION '+name)
 p.sync_turn('SYNTHETIC OBSERVATION '+name,'Synthetic answer',session_id='fresh-'+name)
 p.shutdown()
 with db.open_db(root/name/'memcal.db') as conn:
  rows=conn.execute('SELECT text FROM archive').fetchall();assert len(rows)==1
  assert name in str(tuple(rows[0]))
print(json.dumps({'native_archive_isolation_canary':'pass','model_extraction_verified':False,'source_sha':'da58057eb00f79082dfcf8f59baa264267c83ca1','target_sha_qualified':False}))
'''
    result=subprocess.run([str(PYTHON),'-c',script,str(tmp_path)],env=e,capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stderr[-3500:]
    assert '"native_archive_isolation_canary": "pass"' in result.stdout
