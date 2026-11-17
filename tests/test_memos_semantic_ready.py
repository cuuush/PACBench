import copy
import hashlib
import json
from unittest.mock import patch

import pytest

from provider_adapters.reme_memos_documents import ingest,payload
from provider_adapters.memos_semantic_ready import RAW,SEMANTIC,proof_valid


@pytest.fixture
def source(tmp_path):
    text='[2026-10-05] Maya: Synthetic source document with its original content.\n'
    (tmp_path/'messages.txt').write_text(text)
    (tmp_path/'source.json').write_text(json.dumps({'at':'2026-10-05T20:00:00-04:00','source_ids':['opaque'],
        'document_sha256':hashlib.sha256(text.encode()).hexdigest()}))
    import sqlite3,struct
    trace=payload('memos',tmp_path)[0]['traces'][0]
    with sqlite3.connect(tmp_path/'memos.db') as db:
        db.execute('CREATE TABLE traces(id TEXT,session_id TEXT,episode_id TEXT,user_text TEXT,agent_text TEXT,summary TEXT,vec_summary BLOB,vec_action BLOB)')
        vector=struct.pack('<384f',*([.5]*384))
        db.execute('INSERT INTO traces VALUES(?,?,?,?,?,?,?,?)',tuple(trace[k] for k in ('id','sessionId','episodeId','userText','agentText','summary'))+(vector,vector))
    return tmp_path


def stats(missing=0,total=2):
    bucket=lambda n,m:{'totalSlots':n,'ready':n-m,'missing':m,'dimMismatch':0,'needsRepair':m}
    return {'dimension':384,'available':True,**bucket(total,missing),
        'byKind':{'trace':bucket(total,missing),**{k:bucket(0,0) for k in ('policy','world_model','skill')}}}


def batch(done=True,missing=0,updated=2,failed=0):
    return {'mode':'repair','processed':updated+failed,'updated':updated,'failed':failed,'done':done,
        'statsBefore':stats(2),'statsAfter':stats(missing)}


def responses(source,batches=None):
    body,_=payload('memos',source);trace=body['traces'][0]
    return [{'imported':1,'duplicates':0,'invalid':0,'failed':0},trace,stats(2),*(batches or [batch()]),stats(),trace]


def test_semantic_condition_repairs_missing_slots_and_preserves_source_identity(source):
    with patch('provider_adapters.reme_memos_documents.request',side_effect=responses(source)) as call:
        receipt=ingest('memos',source,'http://127.0.0.1:18800',memos_condition=SEMANTIC,memos_database=source/'memos.db')
    assert receipt['ready'] and receipt['ingestion_condition']==SEMANTIC
    proof=receipt['semantic_readiness'];assert proof_valid(proof,receipt['native_readback'],receipt['document_sha256'])
    assert [a.args[1] for a in call.call_args_list].count('/api/v1/import?ownership=target')==1
    repair=call.call_args_list[3];assert repair.args[1:3]==('/api/v1/embeddings/rebuild',{'mode':'repair','limit':100})
    assert 'empty-action' in proof['vector_scope']['vec_action']
    assert receipt['extraction_performed'] is False and receipt['retrieval_credit']=='source-assisted'
    damaged=copy.deepcopy(proof);damaged['trace_after_sha256']='different'
    assert not proof_valid(damaged,receipt['native_readback'],receipt['document_sha256'])


@pytest.mark.parametrize('damage',['done-with-missing','native-error','no-progress','changed-trace','counter-lie','timeout','batch-limit'])
def test_semantic_condition_never_promotes_partial_or_failed_repair(source,damage):
    seq=responses(source)
    if damage=='done-with-missing':seq[3]=batch(missing=1,updated=1,failed=1)
    elif damage=='native-error':seq[3]={**batch(),'error':'embedding unavailable'}
    elif damage=='no-progress':seq[3]=batch(done=False,missing=2,updated=0)
    elif damage=='changed-trace':seq[-1]={**seq[-1],'summary':'changed'}
    elif damage=='counter-lie':seq[4]={**stats(),'needsRepair':1}
    elif damage=='timeout':seq[3]=TimeoutError('write outcome unknown')
    elif damage=='batch-limit':seq[3]=batch(done=False,missing=1,updated=1)
    with patch('provider_adapters.reme_memos_documents.request',side_effect=seq) as call:
        receipt=ingest('memos',source,'http://127.0.0.1:18800',memos_condition=SEMANTIC,max_repair_batches=1,memos_database=source/'memos.db')
    assert not receipt['ready'] and receipt['semantic_readiness']['state']=='incomplete'
    assert [a.args[1] for a in call.call_args_list].count('/api/v1/import?ownership=target')==1


def test_native_partial_progress_has_bounded_missing_only_following_batch(source):
    with patch('provider_adapters.reme_memos_documents.request',side_effect=responses(source,[batch(False,1,1,1),batch(True,0,1,0)])):
        r=ingest('memos',source,'http://127.0.0.1:18800',memos_condition=SEMANTIC,memos_database=source/'memos.db')
    assert r['ready'] and len(r['semantic_readiness']['batches'])==2


@pytest.mark.parametrize('kwargs',[{'repair_timeout_s':float('inf')},{'max_repair_batches':0},{'memos_condition':'unversioned'}])
def test_invalid_condition_or_bounds_are_refused_before_any_write(source,kwargs):
    with patch('provider_adapters.reme_memos_documents.request') as call,pytest.raises(ValueError):
        ingest('memos',source,'http://127.0.0.1:18800',memos_database=source/'memos.db',**{'memos_condition':SEMANTIC,**kwargs})
    call.assert_not_called()


def test_raw_default_still_performs_no_repair(source):
    with patch('provider_adapters.reme_memos_documents.request',side_effect=responses(source)[:2]) as call:
        r=ingest('memos',source,'http://127.0.0.1:18800')
    assert r['ready'] and r['ingestion_condition']==RAW and call.call_count==2
    assert 'semantic_readiness' not in r


@pytest.mark.parametrize('damage',['missing','zero','nonfinite','wrong-identity'])
def test_semantic_proof_requires_real_vectors_for_exact_submitted_id(source,damage):
    import sqlite3,struct
    with sqlite3.connect(source/'memos.db') as db:
        if damage=='missing':db.execute('UPDATE traces SET vec_summary=NULL')
        elif damage=='zero':db.execute('UPDATE traces SET vec_summary=?',(struct.pack('<384f',*([0.0]*384)),))
        elif damage=='nonfinite':db.execute('UPDATE traces SET vec_summary=?',(struct.pack('<384f',*([float('nan')]*384)),))
        else:db.execute("UPDATE traces SET user_text='different source'")
    with patch('provider_adapters.reme_memos_documents.request',side_effect=responses(source)):
        receipt=ingest('memos',source,'http://127.0.0.1:18800',memos_condition=SEMANTIC,memos_database=source/'memos.db')
    assert not receipt['ready'] and receipt['semantic_readiness']['state']=='incomplete'
