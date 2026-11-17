"""Production source ingestion/closed snapshots, native SDK, offline responses."""
import json,os,hashlib
from pathlib import Path
import pytest
from pacbench.native_checkpoint_sources import Mem0SourceCheckpoints
from pacbench.checkpoint_sessions import PROTOCOL as SESSION_PROTOCOL
from pacbench.checkpoint_evaluator import freeze_inputs,launch,sha,verify_checkpoint
from pacbench.gateway_reuse import PersistentGateway,clone_observation
from pacbench.checkpoint_clock import CheckpointClock
from tests.test_gateway_reuse import LocalNativeFixture,native_home,PYTHON,ROOT
from tests.test_observation_adapters import MEM0

PREPARATION=ROOT/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0'


def mem0_home(root,wire):
    home,env=native_home(root,wire.endpoint);(home/'plugins/reme').unlink();(home/'plugins/mem0').symlink_to(MEM0.resolve(),target_is_directory=True)
    config=json.loads((home/'config.yaml').read_text());config['memory']['provider']='mem0';(home/'config.yaml').write_text(json.dumps(config))
    cfg={'mode':'oss','user_id':'synthetic-native-source','oss':{'llm':{'provider':'openai','config':{
        'model':'offline-model','api_key':'offline-not-a-secret','openai_base_url':wire.endpoint+'/p/offline-memory/v1'}},
        'embedder':{'provider':'ollama','config':{'model':'nomic-embed-text','ollama_base_url':wire.endpoint,'embedding_dims':768}},
        'vector_store':{'provider':'qdrant','config':{'collection_name':'mem0','path':'unset'}}}}
    (home/'mem0.json').write_text(json.dumps(cfg))
    env.update(MEM0_TELEMETRY='false',TZ='America/New_York',PACBENCH_DIRECT='0',PACBENCH_TRANSPORT='openrouter',
        PACBENCH_OPENROUTER_PROXY_URL=wire.endpoint,PACBENCH_OPENROUTER_PROXY_TOKEN='offline-not-a-secret',
        OPENROUTER_API_KEY='offline-not-a-secret',PACBENCH_USAGE_ROLES='{}')
    return home,env


def test_frozen_r2_native_mem0_ingest_snapshot_and_lunch_session_through_production_runner(tmp_path):
    if not PREPARATION.exists() or not MEM0.exists():pytest.skip('Frozen local r2/native plugin unavailable; paid qualification remains gated')
    retained=json.loads((ROOT/'runs/pizza-week-evaluator-reuse-offline-20261115/r2-input-preflight.json').read_text())['files']
    schedule=[]
    for p in sorted((PREPARATION/'sources').glob('*/source.json')):
        r=json.loads(p.read_text());schedule.append({'id':p.parent.name,'at':r['at'],'source_ids':r['source_ids'],
            'source_file':str((p.parent/'messages.txt').relative_to(PREPARATION)),'document_sha256':r['document_sha256']})
    checks,full_plan=freeze_inputs(PREPARATION,schedule,'mem0',retained)
    selected={'rsvp-before','lunch-snapshot','rsvp-after','lunch-refreshed','lunch-fresh'}
    checks=[c for c in checks if c[0]['id'] in selected]
    groups=[dict(g,questions=[q for q in g['questions'] if q['id'] in selected]) for g in full_plan['groups']]
    groups=[g for g in groups if g['questions']]
    plan={**full_plan,'groups':groups,'questions':len(checks),'criteria':sum(len(c.claims) for _,c,_ in checks),
        'offline_diagnostic_slice':{'full_questions':42,'full_criteria':188,'selected_ids':sorted(selected)}}
    wire=LocalNativeFixture(tmp_path/'model-wire');wire.release.set()
    # Simulate a model returning a benign non-answer-bearing memory through the
    # native infer=True path. No infer=False or direct memory insert is used.
    wire.completion_reply=lambda b:json.dumps({'memory':[{'text':'OFFLINE RESPONSE STUB '+hashlib.sha256(json.dumps(b['messages']).encode()).hexdigest()[:16]}]})
    canonical=tmp_path/'canonical';canonical.mkdir();_,env=mem0_home(canonical,wire)
    sources=Mem0SourceCheckpoints(PREPARATION,canonical,schedule,env,PYTHON)
    cwd=tmp_path/'common-empty';cwd.mkdir();runtimes=[]
    class Runtime:
        native_session_checkpoint=SESSION_PROTOCOL
        def __init__(self,group):
            self.group=group;self.root=tmp_path/('checkpoint-'+str(group['id']));self.root.mkdir()
            self.home,self.env=mem0_home(self.root,wire);self.env['PACBENCH_NOW']=group['questions'][0]['at']
            self.proof=sources.materialize(group,self.root/'baseline');self.entered=False;self.owner=None;runtimes.append(self)
        def __enter__(self):
            clock=CheckpointClock(self.home,self.env['PACBENCH_NOW'],self.proof['manifest']['sha256'])
            self.owner=PersistentGateway(self.home,PYTHON,self.env,cwd=cwd,provider='mem0',checkpoint=self.proof['manifest']['sha256'],observation_clock=clock,cleanup_timeout=12)
            self.owner.__enter__();self.entered=True;return self
        def observation(self,q):return clone_observation(self.proof['baseline'],self.root/(q['id']+'-observation'))
        def __exit__(self,*a):
            if self.entered:self.owner.__exit__(*a);self.entered=False
    from tests.test_evaluation import correct_judge
    def synthetic_judge(prompt):
        return json.dumps({**json.loads(correct_judge(prompt)),'interaction':'answer','benchmark_error':None})
    root=tmp_path/'evaluation'
    try:
        report=launch(checks=checks,plan=plan,schedule=schedule,runtime_factory=Runtime,root=root,
            judge=synthetic_judge,env={'PACBENCH_OFFLINE_CONFORMANCE':'1'},question_dir=tmp_path/'questions',budget_snapshot=lambda:{'projects':[]})
        assert len(report['checks'])==5 and all(c['state']=='diagnostic' for c in report['checks']),report
        turns={q['id']:json.loads((root/'turns'/q['id']/'turn.json').read_text()) for q,_,_ in checks}
        first=turns['lunch-snapshot'];later=turns['lunch-refreshed']
        assert first['session_id']==later['session_id'] and first['at']=='2026-10-06T09:30:00-04:00' and later['at']=='2026-10-07T09:10:00-04:00'
        assert later['session_started_at']==first['at']
        assert len({t['session_id'] for t in turns.values()})==4
        assert turns['lunch-fresh']['hermes_gateway']['prior_user_count']==0
        assert sources.ingested==['2026-10-05','2026-10-06']
        source_calls=[(p,b) for p,b in wire.requests if p.startswith('/p/offline-memory/')]
        for batch in schedule[:2]:
            text=(PREPARATION/batch['source_file']).read_text()
            assert any(text in json.dumps(b['messages'],ensure_ascii=False) or any(text in m.get('content','') for m in b['messages']) for _,b in source_calls)
        actor_calls=[b for p,b in wire.requests if p.endswith('/chat/completions') and b.get('stream')]
        assert len(actor_calls)==5
        assert checks[1][1].question in json.dumps(actor_calls[3]['messages'],ensure_ascii=False)
        assert checks[0][1].question not in json.dumps(actor_calls[3]['messages'],ensure_ascii=False)
        assert checks[1][1].question not in json.dumps(actor_calls[4]['messages'],ensure_ascii=False)
        for r in runtimes:
            verify_checkpoint(r.group,r.proof,schedule,'mem0')
            native=json.loads(Path(r.proof['native_closure']['evidence'][0]['path']).read_text())
            assert native['native_reopen']['memory_records']==len(r.group['ready_batch_ids'])
            assert native['model_extraction_quality_verified'] is False
            assert [c['batch_id'] for c in native['source_and_billing_clock_proofs']]==r.group['ready_batch_ids']
            for stamp in native['source_and_billing_clock_proofs']:
                from datetime import datetime
                assert datetime.fromisoformat(stamp['provider_at']).month==10
                assert abs(datetime.fromisoformat(stamp['billing_at']).timestamp()-stamp['real_epoch'])<5
        first=runtimes[0];calls=Path(first.proof['ingestion_receipts']['2026-10-05']['native_calls']['path']);original=calls.read_bytes()
        try:
            calls.write_text('[]')
            from pacbench.hermes_gateway import GatewayLifecycleError
            with pytest.raises(GatewayLifecycleError,match='submission evidence changed'):
                verify_checkpoint(first.group,first.proof,schedule,'mem0')
        finally:calls.write_bytes(original)
        from pacbench.native_checks import audit_checks
        assert audit_checks(checks,'isolated',root,prompt_protocol='native-question-v2')==[]
        assert all(sha(PREPARATION/p)==v for p,v in retained.items())
        (tmp_path/'conformance.json').write_text(json.dumps({'state':'pass','condition':'offline responses; not model quality or scored results',
            'frozen_messages':142,'full_questions':42,'full_criteria':188,'observed_questions':5,'ingested_batch_ids':sources.ingested,
            'native_memory_records_by_checkpoint':[len(g['ready_batch_ids']) for g in groups],'paid_calls':0}))
    finally:wire.close()


def test_all_frozen_r2_source_prefixes_reopen_without_answering_any_more_questions(tmp_path):
    if not PREPARATION.exists() or not MEM0.exists():pytest.skip('Frozen r2/native plugin unavailable; live remains gated')
    retained=json.loads((ROOT/'runs/pizza-week-evaluator-reuse-offline-20261115/r2-input-preflight.json').read_text())['files']
    schedule=[]
    for path in sorted((PREPARATION/'sources').glob('*/source.json')):
        row=json.loads(path.read_text());schedule.append({'id':path.parent.name,'at':row['at'],'source_ids':row['source_ids'],
            'source_file':str((path.parent/'messages.txt').relative_to(PREPARATION)),'document_sha256':row['document_sha256']})
    _,plan=freeze_inputs(PREPARATION,schedule,'mem0',retained)
    wire=LocalNativeFixture(tmp_path/'wire');wire.release.set()
    wire.completion_reply=lambda b:json.dumps({'memory':[{'text':'OFFLINE SOURCE STUB '+hashlib.sha256(json.dumps(b['messages']).encode()).hexdigest()[:16]}]})
    root=tmp_path/'canonical';root.mkdir();_,env=mem0_home(root,wire)
    sources=Mem0SourceCheckpoints(PREPARATION,root,schedule,env,PYTHON);proofs=[]
    try:
        for group in plan['groups']:
            proof=sources.materialize(group,tmp_path/('snapshot-'+str(group['id'])))
            verify_checkpoint(group,proof,schedule,'mem0');proofs.append(proof)
            closure=json.loads(Path(proof['native_closure']['evidence'][0]['path']).read_text())
            assert closure['native_reopen']['memory_records']==len(group['ready_batch_ids'])
        assert sources.ingested==[b['id'] for b in schedule]
        assert sum(len(b['source_ids']) for b in schedule)==142 and plan['questions']==42 and plan['criteria']==188
        assert len(proofs)==6 and len(sources.receipts)==7
        assert not any(b.get('stream') for p,b in wire.requests if p.endswith('/chat/completions'))
        assert all(sha(PREPARATION/p)==v for p,v in retained.items())
        (tmp_path/'conformance.json').write_text(json.dumps({'state':'pass','condition':'offline synthetic extraction responses; native storage lifecycle only',
            'frozen_messages':142,'questions_planned':42,'criteria_planned':188,'questions_answered':0,
            'native_checkpoint_count':6,'ingested_batch_ids':sources.ingested,'paid_calls':0}))
    finally:wire.close()
