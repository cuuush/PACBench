"""Production evaluator + stock native Hermes; synthetic stores/model wire only."""
import json
import hashlib
import os
from pathlib import Path
import pytest
from pacbench.checkpoint_evaluator import launch,sha,verify_checkpoint
from pacbench.gateway_reuse import PersistentGateway,clone_observation,tree_manifest
from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.checkpoint_clock import CheckpointClock
from pacbench.semantic_contract import EvaluationContract,ContractClaim
from pacbench.followups import freeze_policy
from tests.test_gateway_reuse import LocalNativeFixture,native_home,PYTHON


def inputs(tmp_path):
    checks=[];groups=[];schedule=[]
    for n,day in enumerate(['05','05','06']):
        at=f'2026-10-{day}T09:{n*10:02}:00-04:00';prompt=f'EXACT-QUESTION-{n}'
        c=EvaluationContract((ContractClaim('fact','Synthetic detail.',('m1',)),),prompt,'synthetic',f'contract-{n}')
        row={'id':f'q{n}','at':at};checks.append((row,c,freeze_policy(c,tmp_path/f'policy{n}.json')))
        if n!=1:groups.append({'id':len(groups)+1,'ready_batch_ids':['day1'] if n==0 else ['day1','day2'],'questions':[]})
        groups[-1]['questions'].append({**row,'prompt':prompt,'contract_hash':c.contract_hash,
            'horizon':f'2026-10-05T09:10:00-04:00' if n==0 else f'2026-10-06T09:20:00-04:00' if n==1 else None})
    schedule=[{'id':'day1','source_ids':['m1']},{'id':'day2','source_ids':['m2']}]
    for b in schedule:b['document_sha256']=hashlib.sha256(('synthetic-'+b['id']).encode()).hexdigest()
    return checks,{'provider':'reme','groups':groups,'questions':3,'criteria':3},schedule


class NativeRuntime:
    """Real native adapter/lifecycle, fake storage/extraction. Not live qualification."""
    def __init__(self,root,group,schedule,*,bad=False,typed=False):
        self.root=root;root.mkdir();self.group=group;self.fixtures=[];self.typed=typed
        baseline=root/'baseline';baseline.mkdir();(baseline/'source-only').write_text(json.dumps(group['ready_batch_ids']))
        records={}
        for identifier in group['ready_batch_ids']:
            path=root/(identifier+'.json');source=next(s for s in schedule if s['id']==identifier)
            path.write_text(json.dumps({'provider':'reme','state':'ready','ready':not bad,'returncode':0,
                'source_ids':source['source_ids'],'document_sha256':source['document_sha256'],'result':{'ready':True,'native_errors':[]}}))
            records[identifier]={'path':str(path),'sha256':sha(path)}
        manifest=tree_manifest(baseline);closure=root/'native-closure.json'
        closure.write_text(json.dumps({'condition':'synthetic storage fixture; no live native ingestion qualification',
            'checkpoint_sha256':manifest['sha256']}))
        self.proof={'condition':'offline-synthetic-storage-fixture','provider':'reme','baseline':str(baseline),'manifest':manifest,
            'ingested_batch_ids':group['ready_batch_ids'],'ingestion_receipts':records,
            'native_closure':{'provider':'reme','closed_and_reopened':True,'pending_work':[],
                'checkpoint_sha256':manifest['sha256'],'evidence':[{'path':str(closure),'sha256':sha(closure)}]}}
        model=LocalNativeFixture(root/'wire');model.release.set();self.fixtures.append(model)
        self.home,self.env=native_home(root,model.endpoint);self.cwd=root/'empty';self.cwd.mkdir()
        self.env['PACBENCH_NOW']=group['questions'][0]['at'];self.env['PACBENCH_SESSION_STARTED_AT']=self.env['PACBENCH_NOW']
        self.owner=None;self.entered=False;self.pids=[]
    def __enter__(self):
        clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
        self.owner=PersistentGateway(self.home,PYTHON,self.env,cwd=self.cwd,checkpoint=self.proof['manifest']['sha256'],
            observation_clock=clock,cleanup_timeout=5)
        self.owner.__enter__();self.entered=True;self.pids.append(self.owner.witness['pid']);return self
    def observation(self,q):
        clone=clone_observation(self.proof['baseline'],self.root/(q['id']+'-observation'))
        store=LocalNativeFixture(Path(clone['workspace']));store.release.set();self.fixtures.append(store)
        if self.typed:store.search_answer='========== daily/synthetic.md:1-1 ==========\nSynthetic derived memory.'
        return {**clone,'endpoint':store.endpoint,'pid':os.getpid()}
    def __exit__(self,*args):
        try:
            if self.entered:self.owner.__exit__(*args);self.entered=False
        finally:
            for f in self.fixtures:f.close()


def test_exact_production_launch_uses_readiness_isolated_sessions_and_checkpoint_restarts(tmp_path):
    checks,plan,schedule=inputs(tmp_path);runtimes=[];judged=[]
    def runtime(group):
        r=NativeRuntime(tmp_path/('checkpoint'+str(group['id'])),group,schedule);runtimes.append(r);return r
    # The fixture's untyped ReMe automatic context must not reach a judge.
    report=launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=runtime,
        root=tmp_path/'evaluation',judge=lambda p:judged.append(p),env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},
        question_dir=tmp_path/'questions',budget_snapshot=lambda:{'projects':[]})
    assert len(report['checks'])==3 and all(c['state']=='strict-ungraded' for c in report['checks']),report
    assert judged==[] and len(runtimes)==2 and runtimes[0].pids!=runtimes[1].pids
    turns=[json.loads(p.read_text()) for p in sorted((tmp_path/'evaluation/turns').glob('*/turn.json'))]
    assert len({t['session_id'] for t in turns})==3
    assert turns[0]['hermes_gateway']['pid']==turns[1]['hermes_gateway']['pid']
    assert turns[0]['hermes_gateway']['pid']!=turns[2]['hermes_gateway']['pid']
    for n,t in enumerate(turns):
        assert t['prompt']==checks[n][1].question and t['at']==checks[n][0]['at']
        assert t['hermes_gateway']['prior_user_count']==0 and t['hermes_gateway']['new_user_count']==1
        assert t['hermes_gateway']['native_drain']=='complete'
    from pacbench.native_checks import audit_checks
    assert audit_checks(checks,'isolated',tmp_path/'evaluation',prompt_protocol='native-question-v2')==[]
    assert not list((tmp_path/'evaluation').rglob('judgment.json'))
    from pacbench.hermes_gateway import _live
    assert all(not _live(pid) for r in runtimes for pid in r.pids)


def test_incomplete_native_import_fails_before_starting_gateway_or_answer(tmp_path):
    checks,plan,schedule=inputs(tmp_path);seen=[]
    def runtime(group):
        r=NativeRuntime(tmp_path/'failed',group,schedule,bad=True);seen.append(r);return r
    report=launch(checks=checks[:1],plan={**plan,'groups':[dict(plan['groups'][0],questions=plan['groups'][0]['questions'][:1])],
        'questions':1,'criteria':1},schedule=schedule,runtime_factory=runtime,root=tmp_path/'evaluation',
        judge=lambda p:pytest.fail('judge dispatched'),env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{})
    assert seen[0].pids==[] and report['checks'][0]['state']=='ungraded'
    assert 'ingestion incomplete' in report['checks'][0]['reason']
    assert not list((tmp_path/'evaluation').rglob('turn.json'))


def test_shared_authored_history_cannot_silently_become_fresh_questions(tmp_path):
    checks,plan,schedule=inputs(tmp_path)
    checks[0][0]['session']='deliberate';checks[2][0]['session']='deliberate'
    with pytest.raises(GatewayLifecycleError,match='shared-session migration'):
        launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=lambda g:pytest.fail('started'),
            root=tmp_path/'evaluation',judge=lambda p:pytest.fail('judge'),env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{})


def test_absent_frozen_input_proof_fails_before_factory(tmp_path):
    checks,plan,schedule=inputs(tmp_path)
    with pytest.raises(ValueError,match='frozen r2'):
        launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=lambda g:pytest.fail('started'),
            root=tmp_path/'evaluation',judge=lambda p:pytest.fail('judge'),env={},question_dir=tmp_path/'questions',budget_snapshot=lambda:{})


def test_production_followup_uses_only_its_native_session_and_one_judge_per_answer(tmp_path):
    checks,plan,schedule=inputs(tmp_path);row,c,_=checks[0]
    c=EvaluationContract((ContractClaim('given','Synthetic answer.',('m1',)),ContractClaim('missing','Other detail.',('m1',))),
        c.question,c.evidence_hash,c.contract_hash)
    checks=[(row,c,freeze_policy(c,tmp_path/'two-claim-policy.json'))]
    plan={**plan,'groups':[dict(plan['groups'][0],questions=plan['groups'][0]['questions'][:1])],'questions':1,'criteria':2}
    calls=[];runtimes=[]
    def runtime(group):
        r=NativeRuntime(tmp_path/'checkpoint',group,schedule,typed=True);runtimes.append(r);return r
    def judge(prompt):
        if prompt.startswith('Write '):calls.append('write');return json.dumps({'question':'What other detail?'})
        if 'Claims (hidden from the assistant): ' not in prompt:
            calls.append('review');return json.dumps({'safe':True,'covers_claim_ids':['missing'],'reason':'No value supplied.'})
        claims=json.loads(prompt.split('Claims (hidden from the assistant): ')[1]);calls.append('judge')
        verdicts=[{'id':v['id'],'verdict':'missed' if len(claims)==2 and v['id']=='missing' else 'correct','reason':'Synthetic control.'} for v in claims]
        result={'verdicts':verdicts}
        if len(claims)==2:result['recovery']=[{'id':v['id'],'kind':'implicit_omission' if v['verdict']=='missed' else 'provided','reason':'Synthetic control.'} for v in verdicts]
        return json.dumps(result)
    root=tmp_path/'evaluation'
    report=launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=runtime,root=root,judge=judge,
        env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{'projects':[]})
    assert report['checks'][0]['state']=='diagnostic',report
    assert calls==['judge','write','review','judge']
    initial=json.loads((root/'turns/q0/turn.json').read_text());follow=json.loads((root/'turns/q0/followups/combined/turn.json').read_text())
    assert initial['session_id']==follow['session_id']
    assert initial['hermes_gateway']['fresh_session'] and not follow['hermes_gateway']['fresh_session']
    assert follow['hermes_gateway']['prior_user_count']==1 and follow['hermes_gateway']['new_user_count']==1
    assert follow['prompt']=='What other detail?' and follow['at']>=initial['at']
    from pacbench.native_checks import audit_checks
    assert audit_checks(checks,'isolated',root,prompt_protocol='native-question-v2')==[]
    model_requests=[b for path,b in runtimes[0].fixtures[0].requests if path.endswith('/chat/completions') and b.get('stream')]
    assert len(model_requests)==2  # Initial + explicit recovery, no second sender/replay.


@pytest.mark.parametrize('provider',['mem0','memcal'])
def test_production_launch_native_local_adapters_keep_observation_writes_isolated(tmp_path,provider):
    import subprocess,sqlite3
    from tests.test_observation_adapters import MEM0,MEMCAL
    source=MEM0 if provider=='mem0' else MEMCAL/'integrations/hermes/memcal'
    if not source.exists():pytest.skip('Exact retained provider tree missing; live stays blocked')
    checks,plan,schedule=inputs(tmp_path);checks=checks[:2]
    plan={**plan,'provider':provider,'groups':[plan['groups'][0]],'questions':2,'criteria':2}
    observed=[]
    class Runtime(NativeRuntime):
        def __init__(self,group):
            super().__init__(tmp_path/'checkpoint',group,schedule)
            self.provider=provider;(self.home/'plugins/reme').unlink()
            (self.home/'plugins'/provider).symlink_to(source.resolve(),target_is_directory=True)
            cfg=json.loads((self.home/'config.yaml').read_text());cfg['memory']['provider']=provider
            (self.home/'config.yaml').write_text(json.dumps(cfg));self.env.update(MEM0_TELEMETRY='false',TZ='America/New_York')
            endpoint=self.fixtures[0].endpoint
            if provider=='mem0':
                (self.home/'mem0.json').write_text(json.dumps({'mode':'oss','user_id':'synthetic-production-path',
                    'oss':{'llm':{'provider':'openai','config':{'model':'offline-model','api_key':'offline','openai_base_url':endpoint+'/v1'}},
                    'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':endpoint,'embedding_dims':768}},
                    'vector_store':{'provider':'qdrant','config':{'collection_name':'mem0','path':'unset'}}}}))
            else:
                self.env.update(MEMCAL_SRC=str(MEMCAL),PACBENCH_MEMCAL_REUSE_REVISION='da58057eb00f79082dfcf8f59baa264267c83ca1')
                setup='import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from memcal import config,db;c=config.Config(home=Path(sys.argv[2]));c.ensure_dirs();db.open_db(c.db_path).close()'
                subprocess.run([str(PYTHON),'-c',setup,str(MEMCAL),self.proof['baseline']],env=self.env,check=True,capture_output=True,timeout=10)
            self.proof['provider']=provider;self.proof['manifest']=tree_manifest(self.proof['baseline'])
            closure=self.proof['native_closure'];closure.update(provider=provider,checkpoint_sha256=self.proof['manifest']['sha256'])
            for b in self.proof['ingestion_receipts'].values():
                path=Path(b['path']);r=json.loads(path.read_text());r['provider']=provider;path.write_text(json.dumps(r));b['sha256']=sha(path)
        def __enter__(self):
            clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
            self.owner=PersistentGateway(self.home,PYTHON,self.env,cwd=self.cwd,provider=provider,
                checkpoint=self.proof['manifest']['sha256'],observation_clock=clock,cleanup_timeout=8)
            self.owner.__enter__();self.entered=True;return self
        def observation(self,q):
            clone=clone_observation(self.proof['baseline'],self.root/(q['id']+'-observation'));observed.append(clone);return clone
    from tests.test_evaluation import correct_judge
    root=tmp_path/'evaluation'
    report=launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=Runtime,root=root,judge=correct_judge,
        env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{'projects':[]})
    assert len(observed)==2 and all(r['state'] in {'diagnostic','strict-ungraded'} for r in report['checks']),report
    turns=[json.loads(p.read_text()) for p in sorted((root/'turns').glob('*/turn.json'))]
    assert turns[0]['hermes_gateway']['pid']==turns[1]['hermes_gateway']['pid']
    assert turns[0]['session_id']!=turns[1]['session_id']
    from pacbench.native_checks import audit_checks
    assert audit_checks(checks,'isolated',root,prompt_protocol='native-question-v2')==[]
    if provider=='memcal':
        for n,clone in enumerate(observed):
            with sqlite3.connect(Path(clone['workspace'])/'memcal.db') as db:
                assert db.execute('SELECT text FROM archive').fetchall()==[(checks[n][1].question,)]


@pytest.mark.parametrize('tamper',['future','source','snapshot','closure'])
def test_checkpoint_proof_refuses_future_sources_wrong_content_changed_store_and_missing_drain(tmp_path,tamper):
    _,plan,schedule=inputs(tmp_path);group=plan['groups'][0]
    r=NativeRuntime(tmp_path/'checkpoint',group,schedule)
    try:
        if tamper=='future':r.proof['ingested_batch_ids'].append('future')
        if tamper=='source':schedule[0]['document_sha256']='wrong'
        if tamper=='snapshot':(Path(r.proof['baseline'])/'extra').write_text('changed')
        if tamper=='closure':r.proof['native_closure']['pending_work']=['native writer']
        with pytest.raises(GatewayLifecycleError):verify_checkpoint(group,r.proof,schedule,'reme')
        assert r.pids==[]
    finally:r.__exit__(None,None,None)


def test_funded_path_refuses_before_factory_ingestion_even_with_offline_flag(tmp_path):
    checks,plan,schedule=inputs(tmp_path)
    with pytest.raises(GatewayLifecycleError,match='before native ingestion'):
        launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=lambda g:pytest.fail('paid ingest'),
            root=tmp_path/'evaluation',judge=lambda p:pytest.fail('judge'),
            env={'PACBENCH_OFFLINE_CONFORMANCE':'1','PACBENCH_DIRECT':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{})


def test_fabricated_ready_flags_and_generic_file_hashes_do_not_qualify_production(tmp_path):
    _,plan,schedule=inputs(tmp_path);r=NativeRuntime(tmp_path/'checkpoint',plan['groups'][0],schedule)
    try:
        with pytest.raises(GatewayLifecycleError,match='synthetic readiness'):
            verify_checkpoint(plan['groups'][0],r.proof,schedule,'reme')
    finally:r.__exit__(None,None,None)


def test_short_checkpoint_plan_cannot_hide_questions_behind_original_denominator(tmp_path):
    checks,plan,schedule=inputs(tmp_path);plan['groups']=plan['groups'][:1]
    with pytest.raises(ValueError,match='checkpoint question coverage'):
        launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=lambda g:pytest.fail('ingestion started'),
            root=tmp_path/'evaluation',judge=lambda p:pytest.fail('judge'),env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},
            question_dir=tmp_path/'questions',budget_snapshot=lambda:{})
