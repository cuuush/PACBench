"""Production evaluator + stock native Hermes; synthetic stores/model wire only."""

import json

import hashlib

import os

from pathlib import Path

import pytest

from pacbench.workflows.turn_evidence import checkpoint_file_sha as sha, verify_checkpoint

from pacbench.gateway_reuse import PersistentGateway, clone_observation, tree_manifest

from pacbench.hermes_gateway import GatewayLifecycleError

from pacbench.provider_clock import CheckpointClock

from pacbench.corpus.contract import EvaluationContract, ContractClaim

from pacbench.judging.recovery import freeze_policy

from tests.test_gateway_reuse import LocalNativeFixture, native_home, PYTHON

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
