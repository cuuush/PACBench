"""Synthetic offline native process/lifecycle checks; no model inference service."""
import json
import os
from pathlib import Path
import threading
import time
import subprocess
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from pacbench.gateway_reuse import PersistentGateway,clone_observation,tree_manifest
from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.gateway.reuse_runtime import PROTOCOL

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'vendor/venv-hermes-isolated/bin/python'


@pytest.mark.parametrize('clock',[{'PACBENCH_PYTHON_CLOCK_FILE':'/clock'}, {'LD_PRELOAD':'/clock.so'}, {'FAKETIME':'yesterday'}])
def test_old_clock_condition_cannot_be_relabelled_as_persistent(tmp_path,clock):
    with pytest.raises(GatewayLifecycleError,match='new versioned'):
        PersistentGateway(tmp_path,PYTHON,clock,cwd=tmp_path,checkpoint='frozen')


def test_funded_scoring_cannot_silently_use_fixed_clock_variant(tmp_path):
    with pytest.raises(GatewayLifecycleError,match='unqualified'):
        PersistentGateway(tmp_path,PYTHON,{'PACBENCH_DIRECT':'1'},cwd=tmp_path,checkpoint='frozen')


def test_shared_builtin_memory_or_session_search_cannot_leak_between_observations(tmp_path):
    with pytest.raises(GatewayLifecycleError,match='disabled builtin memory and session search'):
        PersistentGateway(tmp_path,PYTHON,{'PACBENCH_NOW':'2026-10-05T09:00:00-04:00'},cwd=tmp_path,checkpoint='frozen')


class LocalNativeFixture:
    """Deterministic model wire + storage for exercising real native clients.

    The ReMe provider itself, its queue, locks, writer and HTTP backend are real.
    The storage handler is synthetic: this does not test native LLM extraction.
    """
    def __init__(self,workspace):
        self.workspace=workspace;workspace.mkdir(exist_ok=True);self.requests=[];self.entered=threading.Event();self.release=threading.Event()
        self.model_entered=threading.Event();self.model_release=threading.Event();self.model_release.set()
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/api/tags':self.reply({'models':[{'name':'nomic-embed-text:latest','model':'nomic-embed-text:latest','modified_at':'2026-10-05T09:00:00Z','size':1,'digest':'a'*64}]})
                else:self.reply({'data':[{'id':'offline-model','object':'model'}]})
            def reply(self,value):
                raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));fixture.requests.append((self.path,body))
                if self.path.endswith('/chat/completions'):
                    fixture.model_entered.set();fixture.model_release.wait(10)
                    if body.get('stream'):
                        events=[{'id':'offline-only','object':'chat.completion.chunk','model':'offline-model','choices':[{'index':0,'delta':{'role':'assistant','content':'Synthetic answer.'},'finish_reason':None}]},
                            {'id':'offline-only','object':'chat.completion.chunk','model':'offline-model','choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}}]
                        raw=(''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
                        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                    else:self.reply({'id':'offline-only','model':'offline-model','choices':[{'index':0,'message':{'role':'assistant','content':fixture.completion_reply(body) if hasattr(fixture,'completion_reply') else '{"facts":[]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
                elif self.path=='/api/embed':
                    texts=body['input'];texts=[texts] if isinstance(texts,str) else texts
                    self.reply({'model':'nomic-embed-text','embeddings':[[1.]+[0.]*767 for _ in texts]})
                elif self.path.endswith('/health_check'):self.reply({'success':True,'metadata':{'health':{'healthy':True}}})
                elif self.path.endswith('/search'):
                    writes=(workspace/'native-writes.jsonl')
                    self.reply({'success':True,'answer':getattr(fixture,'search_answer','Synthetic source-only memory.')+(writes.read_text() if writes.exists() else ''),'metadata':{'counts':{'returned':1}}})
                elif self.path.endswith('/auto_memory'):
                    fixture.entered.set();fixture.release.wait(getattr(fixture,'write_wait_s',10))
                    with (workspace/'native-writes.jsonl').open('a') as stream:stream.write(json.dumps(body)+'\n')
                    self.reply({'success':True,'answer':'recorded'})
                else:self.send_error(404)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.endpoint='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.release.set();self.model_release.set();self.server.shutdown();self.server.server_close();self.thread.join(2)


def native_home(tmp_path,model_endpoint):
    home=tmp_path/'hermes-home';home.mkdir();(home/'plugins').mkdir();(home/'plugins/reme').symlink_to(ROOT/'vendor/reme/integrations/hermes_agent',target_is_directory=True)
    (home/'reme').mkdir()
    (home/'reme/config.json').write_text(json.dumps({'mode':'http','endpoint':model_endpoint}))
    cfg={'model':{'provider':'custom','default':'offline-model','base_url':model_endpoint+'/v1','api_mode':'chat_completions'},
        'memory':{'provider':'reme','memory_enabled':False,'user_profile_enabled':False},
        'agent':{'max_turns':2,'disabled_toolsets':['terminal','file','web','browser','session_search','delegation','code_execution','skills','todo','clarify']}}
    (home/'config.yaml').write_text(json.dumps(cfg))
    env={k:v for k,v in os.environ.items() if k in {'PATH','TMPDIR','LANG','TZ','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
    env.update(HERMES_HOME=str(home),HOME=str(tmp_path/'os-home'),HERMES_INFERENCE_PROVIDER='custom',OPENAI_BASE_URL=model_endpoint+'/v1',OPENAI_API_KEY='offline-not-a-secret',
        PACBENCH_NOW='2026-10-05T09:00:00-04:00',PACBENCH_SESSION_STARTED_AT='2026-10-05T09:00:00-04:00',PACBENCH_NO_BUILTIN_MEMORY='1',PACBENCH_NO_SESSION_SEARCH='1',
        PACBENCH_PROVIDER_ONLY='1',PACBENCH_TOOL_ALLOWLIST='',TERM='dumb')
    Path(env['HOME']).mkdir();return home,env


@pytest.mark.parametrize('stall',[False,True])
@pytest.mark.parametrize('clocked',[False,True])
def test_two_real_native_sessions_share_one_gateway_and_preserve_delayed_reme_writes(tmp_path,stall,clocked):
    if not PYTHON.exists():pytest.skip('Pinned Hermes runtime absent')
    baseline=tmp_path/'frozen';baseline.mkdir();(baseline/'fact.txt').write_text('Synthetic source-only fact.')
    one=clone_observation(baseline,tmp_path/'store-one');two=clone_observation(baseline,tmp_path/'store-two');before=tree_manifest(baseline)
    first=LocalNativeFixture(tmp_path/'store-one');second=LocalNativeFixture(tmp_path/'store-two')
    home,env=native_home(tmp_path,first.endpoint)
    clock=None
    if clocked:
        from pacbench.checkpoint_clock import CheckpointClock
        clock=CheckpointClock(home,env['PACBENCH_NOW'],before['sha256'])
    workspace=tmp_path/'empty';workspace.mkdir()
    def select(fixture):
        return {**(one if fixture is first else two),'endpoint':fixture.endpoint,'pid':os.getpid()}
    try:
        # Native writer is delayed, then allowed to finish; never disabled.
        def release_after_ready():
            assert first.entered.wait(20)
            deadline=time.monotonic()+20
            while not list(tmp_path.glob('reuse-turns/*/answer-ready.json')) and time.monotonic()<deadline:time.sleep(.02)
            time.sleep(.15);first.release.set()
        if not stall:
            releaser=threading.Thread(target=release_after_ready,daemon=True);releaser.start()
        second.release.set()
        with PersistentGateway(home,PYTHON,env,cwd=workspace,checkpoint=before['sha256'],cleanup_timeout=2 if stall else 5,observation_clock=clock) as owner:
            if stall:
                with pytest.raises(GatewayLifecycleError,match='drain unverified'):
                    owner.run('FIRST-QUESTION-SENTINEL',observation=select(first),timeout=30)
                assert list(tmp_path.glob('reuse-turns/*/answer-ready.json'))
                with pytest.raises(GatewayLifecycleError,match='no replay'):
                    owner.run('MUST-NOT-BE-SENT',observation=select(second),timeout=30)
                assert not second.requests
                first.release.set();owner.fence()
                assert owner.failed and (first.workspace/'native-writes.jsonl').exists()
                assert tree_manifest(baseline)==before
                return
            a,ap=owner.run('FIRST-QUESTION-SENTINEL',observation=select(first),timeout=30)
            pid=owner.witness['pid']
            b,bp=owner.run('SECOND-QUESTION-SENTINEL',observation=select(second),timeout=30,
                at='2026-10-05T09:10:00-04:00' if clocked else None)
            assert a.session_id!=b.session_id and owner.witness['pid']==pid
            assert (ap/'answer-ready.json').exists() and (bp/'cleanup.json').exists()
            proof=json.loads((bp/'answer-ready.json').read_text())
            if clocked:
                assert proof['observation_clock']['state']=='pass'
                assert proof['observation_clock']['descriptor']['at']==b.at=='2026-10-05T09:10:00-04:00'
                assert proof['observation_clock']['gateway_process_adoption']['pid']==pid
                assert proof['observation_clock']['native_application_clock']['now']=='2026-10-05T09:10:00-04:00'
                from pacbench.checkpoint_clock import audit_question
                with pytest.raises(RuntimeError,match='binding differs'):
                    audit_question(clock.folder,proof['observation_clock']['descriptor'],b,{**owner.witness,'pid':pid+1})
            assert 'Synthetic source-only memory.' in proof['native_turn']['prefetch_context']
            assert len([r for r in proof['answer_visible_records'] if r['role']=='user'])==1
            assert 'FIRST-QUESTION-SENTINEL' not in json.dumps(proof['answer_visible_records'])
            assert first.entered.is_set() and second.entered.is_set()
            assert (first.workspace/'native-writes.jsonl').exists() and (second.workspace/'native-writes.jsonl').exists()
            second_calls=[body for path,body in first.requests if path.endswith('/chat/completions') and 'SECOND-QUESTION-SENTINEL' in json.dumps(body.get('messages',[]))]
            assert second_calls
            assert all('FIRST-QUESTION-SENTINEL' not in json.dumps(c) for c in second_calls)
            assert tree_manifest(baseline)==before
            assert json.loads((ap/'cleanup.json').read_text())['post_answer_cleanup_s']>=.1
            # A config edit may not silently change the next session's model/instructions.
            (home/'config.yaml').write_text((home/'config.yaml').read_text()+'\n# changed\n')
            with pytest.raises(GatewayLifecycleError,match='model, instructions or environment changed'):
                owner.run('MUST-NOT-BE-SENT',observation=select(second))
    finally:first.close();second.close()


def test_interrupted_native_request_never_replays_and_restart_uses_new_clone(tmp_path):
    if not PYTHON.exists():pytest.skip('Pinned Hermes runtime absent')
    baseline=tmp_path/'frozen';baseline.mkdir();(baseline/'fact.txt').write_text('Source only')
    one=clone_observation(baseline,tmp_path/'one');two=clone_observation(baseline,tmp_path/'two')
    first=LocalNativeFixture(tmp_path/'one');second=LocalNativeFixture(tmp_path/'two')
    first.release.set();second.release.set();first.model_release.clear()
    home,env=native_home(tmp_path,first.endpoint);workspace=tmp_path/'empty';workspace.mkdir()
    observation=lambda clone,fixture:{**clone,'endpoint':fixture.endpoint,'pid':os.getpid()}
    try:
        def unblock():
            if first.model_entered.wait(10):time.sleep(1.2);first.model_release.set()
        releaser=threading.Thread(target=unblock,daemon=True);releaser.start()
        with PersistentGateway(home,PYTHON,env,cwd=workspace,checkpoint=one['checkpoint'],cleanup_timeout=5) as owner:
            with pytest.raises(subprocess.TimeoutExpired):
                owner.run('INTERRUPTED-QUESTION',observation=observation(one,first),timeout=1)
            old_pid=owner.witness['pid']
            assert owner.failed and list(tmp_path.glob('reuse-turns/*/interrupted-result.json'))
            with pytest.raises(GatewayLifecycleError,match='no replay'):
                owner.run('MUST-NOT-REPLAY',observation=observation(two,second))
            first.model_release.set();time.sleep(.1)
        assert owner.witness['stopped']
        # Explicit recovery is a new owned process and an untouched checkpoint clone.
        with PersistentGateway(home,PYTHON,env,cwd=workspace,checkpoint=two['checkpoint'],cleanup_timeout=5) as recovered:
            result,_=recovered.run('RECOVERED-QUESTION',observation=observation(two,second),timeout=30)
            assert recovered.witness['pid']!=old_pid and result.answer
        requests=[body for path,body in first.requests if path.endswith('/chat/completions')]
        assert sum('INTERRUPTED-QUESTION' in json.dumps(r) for r in requests)<=1
        assert all('MUST-NOT-REPLAY' not in json.dumps(r) for r in requests)
    finally:first.close();second.close()


def test_checkpoint_clone_fidelity_and_link_rejection(tmp_path):
    baseline=tmp_path/'baseline';baseline.mkdir();(baseline/'x').write_text('immutable')
    manifest=tree_manifest(baseline);clone=clone_observation(baseline,tmp_path/'fork')
    (tmp_path/'fork/x').write_text('native answer memory')
    assert tree_manifest(baseline)==manifest and tree_manifest(tmp_path/'fork')!=clone['snapshot_manifest']
    (baseline/'link').symlink_to(tmp_path/'fork/x')
    with pytest.raises(GatewayLifecycleError,match='links'):tree_manifest(baseline)


@pytest.mark.parametrize('provider',['mem0','memcal'])
def test_native_local_adapters_share_gateway_with_exact_times_and_isolated_writes(tmp_path,provider):
    from tests.test_observation_adapters import MEM0,MEMCAL
    source=MEM0 if provider=='mem0' else MEMCAL/'integrations/hermes/memcal'
    if not source.exists():pytest.skip('Exact native source absent; live qualification remains blocked')
    fixture=LocalNativeFixture(tmp_path/'model-wire');fixture.release.set()
    home,e=native_home(tmp_path,fixture.endpoint);(home/'plugins/reme').unlink()
    (home/'plugins'/provider).symlink_to(source.resolve(),target_is_directory=True)
    c=json.loads((home/'config.yaml').read_text());c['memory']['provider']=provider;(home/'config.yaml').write_text(json.dumps(c))
    e.update(MEM0_TELEMETRY='false',TZ='America/New_York')
    baseline=tmp_path/'baseline';baseline.mkdir()
    if provider=='mem0':
        cfg={'mode':'oss','user_id':'synthetic-canary','oss':{'llm':{'provider':'openai','config':{'model':'offline-model','api_key':'offline','openai_base_url':fixture.endpoint+'/v1'}},
            'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':fixture.endpoint,'embedding_dims':768}},
            'vector_store':{'provider':'qdrant','config':{'collection_name':'mem0','path':'unset'}}}}
        (home/'mem0.json').write_text(json.dumps(cfg))
    else:
        e.update(MEMCAL_SRC=str(MEMCAL),PACBENCH_MEMCAL_REUSE_REVISION='da58057eb00f79082dfcf8f59baa264267c83ca1')
        setup='import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from memcal import config,db;c=config.Config(home=Path(sys.argv[2]));c.ensure_dirs();db.open_db(c.db_path).close()'
        subprocess.run([str(PYTHON),'-c',setup,str(MEMCAL),str(baseline)],env=e,check=True,capture_output=True,text=True,timeout=10)
    a=clone_observation(baseline,tmp_path/'one');b=clone_observation(baseline,tmp_path/'two');frozen=tree_manifest(baseline)
    from pacbench.checkpoint_clock import CheckpointClock
    clock=CheckpointClock(home,e['PACBENCH_NOW'],a['checkpoint']);workspace=tmp_path/'empty';workspace.mkdir()
    try:
        with PersistentGateway(home,PYTHON,e,cwd=workspace,checkpoint=a['checkpoint'],provider=provider,observation_clock=clock,cleanup_timeout=8) as owner:
            first,_=owner.run('FIRST-NATIVE-OBSERVATION',observation=a,timeout=30)
            second,receipt=owner.run('SECOND-NATIVE-OBSERVATION',observation=b,at='2026-10-05T09:10:00-04:00',timeout=30)
            proof=json.loads((receipt/'answer-ready.json').read_text())
            assert first.session_id!=second.session_id and proof['observation_clock']['state']=='pass'
            assert 'FIRST-NATIVE-OBSERVATION' not in json.dumps(proof['answer_visible_records'])
            assert proof['observation_clock']['native_application_clock']['now']=='2026-10-05T09:10:00-04:00'
            assert tree_manifest(baseline)==frozen
            if provider=='memcal':
                import sqlite3
                from datetime import datetime
                for clone,text,at in [(a,'FIRST-NATIVE-OBSERVATION',first.at),(b,'SECOND-NATIVE-OBSERVATION',second.at)]:
                    with sqlite3.connect(Path(clone['workspace'])/'memcal.db') as db:
                        rows=db.execute('SELECT text FROM archive').fetchall();assert rows==[(text,)]
                        ts=db.execute('SELECT ts FROM archive').fetchone()[0]
                        assert 0<=(datetime.fromisoformat(ts)-datetime.fromisoformat(at)).total_seconds()<6
    finally:fixture.close()


def test_native_followup_resumes_only_its_question_session_without_clock_rewind(tmp_path):
    baseline=tmp_path/'baseline';baseline.mkdir();(baseline/'fact').write_text('Synthetic baseline')
    observation=clone_observation(baseline,tmp_path/'one');fixture=LocalNativeFixture(tmp_path/'one');fixture.release.set()
    home,e=native_home(tmp_path,fixture.endpoint);workspace=tmp_path/'empty';workspace.mkdir()
    from pacbench.checkpoint_clock import CheckpointClock
    clock=CheckpointClock(home,e['PACBENCH_NOW'],observation['checkpoint']);observation.update(endpoint=fixture.endpoint,pid=os.getpid())
    try:
        with PersistentGateway(home,PYTHON,e,cwd=workspace,checkpoint=observation['checkpoint'],observation_clock=clock,cleanup_timeout=5) as owner:
            first,_=owner.run('INITIAL-EXACT-QUESTION',observation=observation,horizon='2026-10-05T09:10:00-04:00')
            follow,folder=owner.run('FOLLOWUP-EXACT-QUESTION',observation=observation,at=first.at,session_id=first.session_id)
            assert follow.session_id==first.session_id and follow.at>=first.at and follow.session_started_at==first.at
            assert follow.hermes_gateway['new_user_count']==1 and follow.hermes_gateway['prior_user_count']==1
            assert follow.hermes_gateway['clock_evidence']['descriptor']['kind']=='continuation'
            with pytest.raises(GatewayLifecycleError,match='single unused'):
                owner.run('NO-SECOND-FOLLOWUP',observation=observation,session_id=first.session_id)
            assert tree_manifest(baseline)==observation['snapshot_manifest']
    finally:fixture.close()


@pytest.mark.parametrize('changed',[{'native_memory_threads':['memory-prefetch-reme']},
    {'native_release_threads':['agent-evict-fixture']},{'native_failures':['ReMe backend shutdown failed']},
    {'observer_sha256':'wrong'},{'sessions':[{'clean':False,'session_id':'test'}]}])
def test_native_fence_never_confuses_ended_or_empty_queue_with_complete_cleanup(tmp_path,monkeypatch,changed):
    from pacbench import gateway_reuse
    monkeypatch.setattr(gateway_reuse,'_identity',lambda *a:{})
    value={'protocol':PROTOCOL,'pid':42,'start_time':123,'observer_sha256':'expected','sessions':[],
        'native_memory_threads':[],'native_release_threads':[],'native_failures':[],**changed}
    (tmp_path/'reuse-observer.json').write_text(json.dumps(value))
    owner=object.__new__(PersistentGateway);owner.home=tmp_path;owner.witness={'pid':42,'start_time':123}
    owner.pins={'reuse_observer_sha256':'expected'};owner.failed=False;owner.cleanup_timeout=0
    with pytest.raises(GatewayLifecycleError):owner.fence()
    assert owner.failed


def test_real_reme_embedded_writer_can_outlive_native_shutdown(tmp_path,monkeypatch,caplog):
    """Exercise the native queue AND native asyncio backend, not a transport mock."""
    import asyncio,importlib.util,sys,types
    source=ROOT/'vendor/reme/integrations/hermes_agent'
    spec=importlib.util.spec_from_file_location('_reuse_native_reme',source/'__init__.py',submodule_search_locations=[str(source)])
    native=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,native);spec.loader.exec_module(native)
    entered=threading.Event();release=threading.Event();recorded=[]
    class Application:
        def __init__(self,**kw):pass
        async def start(self):pass
        async def close(self):pass
        async def run_job(self,name,**kw):
            if name=='health_check':return {'success':True,'metadata':{'health':{'healthy':True}}}
            if name=='auto_memory':
                entered.set()
                while not release.is_set():await asyncio.sleep(.005)
                recorded.append(kw);return {'success':True}
            return {'success':True,'answer':''}
    sdk=types.ModuleType('reme');sdk.Application=Application
    config=types.ModuleType('reme.config');config.resolve_app_config=lambda **kw:{}
    monkeypatch.setitem(sys.modules,'reme',sdk);monkeypatch.setitem(sys.modules,'reme.config',config)
    cfg=native.ReMeConfig(mode='embedded',workspace_dir=str(tmp_path),shutdown_timeout=.03)
    monkeypatch.setattr(native,'load_config',lambda *a:cfg)
    p=native.ReMeMemoryProvider();p.initialize('fresh-session',hermes_home=str(tmp_path))
    backend=p._backend
    from pacbench.gateway.reuse_runtime import provider_state
    try:
        p.sync_turn('Synthetic question','Synthetic answer');assert entered.wait(2)
        assert p._write_thread.is_alive() and backend._thread.is_alive()
        p.shutdown()
        state=provider_state(p)
        assert not state['clean'] and state['deferred_backend_close'] and state['writer_alive']
        assert 'ReMe shutdown timed out' in caplog.text
        with pytest.raises(RuntimeError,match='previous writer'):p.initialize('must-not-rebind',hermes_home=str(tmp_path))
    finally:
        release.set();limit=time.monotonic()+3
        while (p._write_thread is not None or backend._thread.is_alive()) and time.monotonic()<limit:time.sleep(.01)
    assert len(recorded)==1 and 'fresh-session' in recorded[0]['session_id']
    assert p._write_thread is None and not backend._thread.is_alive()
