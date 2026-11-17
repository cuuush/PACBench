"""Cold native DB backup/resume; synthetic model/store wire, no paid inference."""
import json,hashlib
from pathlib import Path
import pytest
from pacbench.checkpoint_sessions import PROTOCOL,transfer
from pacbench.checkpoint_evaluator import launch
from pacbench.hermes_gateway import GatewayLifecycleError
from tests.test_checkpoint_evaluator import inputs,NativeRuntime
from tests.test_gateway_reuse import LocalNativeFixture


@pytest.mark.parametrize('provider',['reme','mem0','memcal'])
def test_real_native_lunch_identity_crosses_checkpoint_while_independent_question_is_blank(tmp_path,provider):
    checks,plan,schedule=inputs(tmp_path)
    checks[0][0]['session']='intentional-lunch';checks[2][0]['session']='intentional-lunch'
    plan['provider']=provider
    shared=LocalNativeFixture(tmp_path/'shared-model');shared.release.set();runtimes=[]
    cwd=tmp_path/'common-empty';cwd.mkdir()
    class Runtime(NativeRuntime):
        from tests.test_gateway_reuse import PYTHON as owner_python
        def __init__(self,group):
            super().__init__(tmp_path/('checkpoint'+str(group['id'])),group,schedule)
            self.fixtures[0].close();self.fixtures=[]
            config=json.loads((self.home/'config.yaml').read_text());config['model']['base_url']=shared.endpoint+'/v1'
            (self.home/'config.yaml').write_text(json.dumps(config));self.env['OPENAI_BASE_URL']=shared.endpoint+'/v1';self.cwd=cwd
            if provider!='reme':
                import subprocess
                from tests.test_observation_adapters import MEM0,MEMCAL
                from pacbench.gateway_reuse import tree_manifest
                source=MEM0 if provider=='mem0' else MEMCAL/'integrations/hermes/memcal'
                if not source.exists():pytest.skip('Exact native tree absent; live remains gated')
                (self.home/'plugins/reme').unlink();(self.home/'plugins'/provider).symlink_to(source.resolve(),target_is_directory=True)
                config['memory']['provider']=provider;(self.home/'config.yaml').write_text(json.dumps(config))
                self.env.update(MEM0_TELEMETRY='false',TZ='America/New_York')
                if provider=='mem0':
                    cfg={'mode':'oss','user_id':'synthetic-native-cold','oss':{'llm':{'provider':'openai','config':{'model':'offline-model','api_key':'offline','openai_base_url':shared.endpoint+'/v1'}},
                        'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':shared.endpoint,'embedding_dims':768}},
                        'vector_store':{'provider':'qdrant','config':{'collection_name':'mem0','path':'unset'}}}}
                    (self.home/'mem0.json').write_text(json.dumps(cfg))
                else:
                    self.env.update(MEMCAL_SRC=str(MEMCAL),PACBENCH_MEMCAL_REUSE_REVISION='da58057eb00f79082dfcf8f59baa264267c83ca1')
                    setup='import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from memcal import config,db;c=config.Config(home=Path(sys.argv[2]));c.ensure_dirs();db.open_db(c.db_path).close()'
                    subprocess.run([str(self.owner_python),'-c',setup,str(MEMCAL),self.proof['baseline']],env=self.env,check=True,capture_output=True,timeout=10)
                self.proof['provider']=provider;self.proof['manifest']=tree_manifest(self.proof['baseline'])
                self.proof['native_closure'].update(provider=provider,checkpoint_sha256=self.proof['manifest']['sha256'])
                for b in self.proof['ingestion_receipts'].values():
                    path=Path(b['path']);r=json.loads(path.read_text());r['provider']=provider;path.write_text(json.dumps(r));b['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            runtimes.append(self)
        def __enter__(self):
            from pacbench.checkpoint_clock import CheckpointClock
            from pacbench.gateway_reuse import PersistentGateway
            clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
            self.owner=PersistentGateway(self.home,self.owner_python,self.env,cwd=self.cwd,provider=provider,
                checkpoint=self.proof['manifest']['sha256'],observation_clock=clock,cleanup_timeout=8)
            self.owner.__enter__();self.entered=True;self.pids.append(self.owner.witness['pid']);return self
        def observation(self,q):
            o=super().observation(q)
            self.fixtures[-1].search_answer='Synthetic generated source checkpoint '+','.join(self.group['ready_batch_ids'])
            return o
    Runtime.native_session_checkpoint=PROTOCOL
    root=tmp_path/'evaluation'
    try:
        from tests.test_evaluation import correct_judge
        def judge(prompt):
            if provider=='reme':pytest.fail('Untyped source provenance cannot reach judge')
            return json.dumps({**json.loads(correct_judge(prompt)),'interaction':'answer','benchmark_error':None})
        report=launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=Runtime,root=root,
            judge=judge,env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},
            question_dir=tmp_path/'questions',budget_snapshot=lambda:{'projects':[]})
        assert len(report['checks'])==3 and all(c['state'] in {'strict-ungraded','diagnostic'} for c in report['checks']),report
        turns=[json.loads((root/f'turns/q{n}/turn.json').read_text()) for n in range(3)]
        first,independent,later=turns
        assert first['session_id']==later['session_id']!=independent['session_id']
        assert first['hermes_gateway']['pid']==independent['hermes_gateway']['pid']!=later['hermes_gateway']['pid']
        assert later['session_started_at']==first['at'] and later['at']==checks[2][0]['at']
        assert later['hermes_gateway']['authored_checkpoint_resume']
        assert later['hermes_gateway']['prior_user_count']==1 and independent['hermes_gateway']['prior_user_count']==0
        assert later['provider_clock']['role']=='scripted' and later['provider_clock']['requested_at']==later['at']
        if provider=='reme':assert 'day2' not in first['prefetch_context'] and 'day1,day2' in later['prefetch_context']
        requests=[b for p,b in shared.requests if p.endswith('/chat/completions') and b.get('stream')]
        assert len(requests)==3
        assert 'EXACT-QUESTION-0' not in json.dumps(requests[1]['messages'])
        assert 'EXACT-QUESTION-0' in json.dumps(requests[2]['messages'])
        assert 'EXACT-QUESTION-1' not in json.dumps(requests[2]['messages'])
        from pacbench.native_checks import audit_checks
        assert audit_checks(checks,'isolated',root,prompt_protocol='native-question-v2')==[]
        if provider=='reme':assert not list(root.rglob('judgment.json'))
    finally:shared.close()


def test_cold_transfer_refuses_a_live_owner_before_copying_any_history(tmp_path):
    import os
    source=tmp_path/'sealed.json';source.write_text(json.dumps({'protocol':PROTOCOL,'pending_native_writes':'drained','source_owner':{'pid':os.getpid()}}))
    binding={'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    with pytest.raises(GatewayLifecycleError,match='stopped'):transfer(binding,tmp_path/'new-home')
    assert not (tmp_path/'new-home/state.db').exists()


def test_cold_native_backup_keeps_unchanged_rows_private_without_starting_hermes(tmp_path):
    import sqlite3
    from pacbench.checkpoint_clock import history
    source=tmp_path/'old';source.mkdir();target=tmp_path/'new';target.mkdir();(target/'config.yaml').write_text('offline config')
    with sqlite3.connect(source/'state.db') as db:
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,tool_name TEXT,api_content TEXT)')
        db.execute("INSERT INTO messages VALUES(1,'retained','user','exact question',NULL,'exact question plus native context')")
    seal=tmp_path/'seal.json';seal.write_text(json.dumps({'protocol':PROTOCOL,'source_home':str(source),'pending_native_writes':'drained',
        'source_owner':{'pid':999999999},'config_sha256':hashlib.sha256((target/'config.yaml').read_bytes()).hexdigest(),
        'pins':{},'sessions':{'retained':{'history':history(source,'retained'),'session_started_at':'2026-10-06T09:30:00-04:00'}}}))
    binding={'path':str(seal),'sha256':hashlib.sha256(seal.read_bytes()).hexdigest()}
    restored=transfer(binding,target)
    assert history(source,'retained')==history(target,'retained')
    assert (target/'state.db').stat().st_mode&0o777==0o600

    from types import SimpleNamespace
    from pacbench.checkpoint_sessions import verify
    owner=SimpleNamespace(home=target,config_sha256=hashlib.sha256((target/'config.yaml').read_bytes()).hexdigest(),pins={})
    verify(restored,owner,'retained')
    receipt=Path(restored['path']);data=json.loads(receipt.read_text());data['sessions']['retained']['session_started_at']='2026-10-07T09:30:00-04:00'
    receipt.write_text(json.dumps(data));restored['sha256']=hashlib.sha256(receipt.read_bytes()).hexdigest()
    with pytest.raises(GatewayLifecycleError,match='sealed session history'):verify(restored,owner,'retained')
