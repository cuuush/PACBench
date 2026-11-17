"""Native resource/write/index lifecycle, with synthetic local completions only."""
import hashlib,json,re
import pytest
from pathlib import Path
from tests.offline_native_wire import NativeWire
from tests.test_native_checkpoint_sources import PREPARATION,ROOT
from pacbench.checkpoint_evaluator import freeze_inputs,verify_checkpoint
from pacbench.native_checkpoint_sources import ReMeSourceCheckpoints


def write_response(body):
    messages=body['messages']
    if any(m.get('role')=='tool' for m in messages):return 'Created the source-linked offline fixture card.'
    names=[t.get('function',{}).get('name') for t in body.get('tools',[])]
    name=next((n for n in names if n=='write' or n.endswith('_write')),None)
    raw='\n'.join(m.get('content','') if isinstance(m.get('content'),str) else '\n'.join(b.get('text','') for b in m.get('content',[]) if isinstance(b,dict)) for m in messages)
    target=re.search(r'Target note path: (.+)',raw);source=re.search(r'Source resource link: (.+)',raw)
    assert name and target and source,'Unexpected native resource call; no synthetic fallback'
    args={'path':target.group(1).strip(),'name':'offline-native-resource',
        'description':'Synthetic native resource lifecycle witness; no extraction quality claim.',
        'content':'Offline derived fixture memory '+hashlib.sha256(raw.encode()).hexdigest()[:16],
        'metadata':{'source_resource':source.group(1).strip()}}
    return {'role':'assistant','content':None,'tool_calls':[{'id':'offline-resource-write','type':'function',
        'function':{'name':name,'arguments':json.dumps(args)}}]}


@pytest.mark.skipif(not PREPARATION.exists(),reason='Frozen private r2 input missing; live gate remains closed')
def test_native_reme_resource_and_persisted_bm25_cover_all_frozen_prefixes(tmp_path,monkeypatch):
    schedule=[]
    for path in sorted((PREPARATION/'sources').glob('*/source.json')):
        row=json.loads(path.read_text());schedule.append({'id':path.parent.name,'at':row['at'],'source_ids':row['source_ids'],
            'source_file':str((path.parent/'messages.txt').relative_to(PREPARATION)),'document_sha256':row['document_sha256']})
    retained=json.loads((ROOT/'runs/pizza-week-evaluator-reuse-offline-20261115/r2-input-preflight.json').read_text())['files']
    _,plan=freeze_inputs(PREPARATION,schedule,'reme',retained)
    wire=NativeWire(write_response);env={'PACBENCH_TRANSPORT':'openrouter','PACBENCH_DIRECT':'0',
        'PACBENCH_OPENROUTER_PROXY_URL':wire.endpoint,'PACBENCH_OPENROUTER_PROXY_TOKEN':'offline-not-a-secret',
        'OPENAI_API_KEY':'offline-not-a-secret','OPENROUTER_API_KEY':'offline-not-a-secret','PACBENCH_USAGE_ROLES':'{}'}
    for key,value in env.items():monkeypatch.setenv(key,value)
    root=tmp_path/'canonical';(root/'hermes-home/plugins').mkdir(parents=True);(root/'hermes-home/reme').mkdir()
    source=ReMeSourceCheckpoints(PREPARATION,root,schedule,env,ROOT/'vendor/venv-reme/bin/python',model='offline-model',
        base=wire.endpoint+'/p/offline-memory/v1',key='offline-not-a-secret')
    try:
        for group in plan['groups']:
            proof=source.materialize(group,tmp_path/('snapshot-'+str(group['id'])))
            # The production verifier must reread the exact typed receipts.
            verify_checkpoint(group,proof,schedule,'reme')
            assert len(wire.requests)==2*len(source.ingested),'Unexpected replay, probe inference or hidden second job'
        assert len(source.ingested)==7 and len(plan['groups'])==6 and plan['criteria']==188
        assert sum(len(b['source_ids']) for b in schedule)==142
        assert all(hashlib.sha256((PREPARATION/p).read_bytes()).hexdigest()==digest for p,digest in retained.items())
        (tmp_path/'captured-requests.private.json').write_text(json.dumps(wire.requests,indent=2))
        (tmp_path/'conformance.json').write_text(json.dumps({'state':'pass','model_quality_verified':False,'native_checkpoints':6,
            'source_messages':142,'native_calls':len(wire.requests),'planned_questions':42,'planned_criteria':188,'questions_answered':0}))
    finally:wire.close()
