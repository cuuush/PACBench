import importlib.metadata
import json
import logging
from types import SimpleNamespace

import pytest
from provider_adapters import document_connector as dc


def connector(tmp_path,monkeypatch,add):
    source=tmp_path/'day';source.mkdir();(source/'messages.txt').write_text('A complete unmodified source transcript.')
    root=tmp_path/'arm';(root/'hermes-home').mkdir(parents=True)
    cfg={'user_id':'fixture-user','oss':{'llm':{'config':{'api_key':'inert','openai_base_url':'http://127.0.0.1:9'}}}}
    (root/'hermes-home/mem0.json').write_text(json.dumps(cfg));closed=[]
    llm=SimpleNamespace(client=SimpleNamespace(base_url='http://127.0.0.1:9',api_key='inert'))
    backend=SimpleNamespace(_memory=SimpleNamespace(llm=llm,add=add),close=lambda:closed.append(True))
    monkeypatch.setattr('pacbench.provider_loader.provider_module',lambda *a:SimpleNamespace(OSSBackend=lambda _:backend))
    monkeypatch.setattr('pacbench.gateway_client.validate_route',lambda *a:None)
    monkeypatch.setattr('pacbench.openrouter_direct.active',lambda:False)
    monkeypatch.setenv('OPENAI_API_KEY','inert')
    receipt=dc.mem0(source,root,'2026-10-05T18:00:00-04:00','2026-10-05T18:00:00-04:00')
    assert closed==[True] and (root/'ingestion-model-calls/day.json').is_file()
    # The outer ingest wrapper persists exactly this JSON-serializable receipt.
    assert json.loads(json.dumps(receipt))==receipt
    return receipt


def test_pinned_native_batch_and_fallback_failure_cannot_report_ready(tmp_path,monkeypatch):
    native=pytest.importorskip('mem0.memory.main')
    assert importlib.metadata.version('mem0ai')=='2.2.1'
    saved=[]
    class Embedder:
        def embed_batch(self,*a):raise RuntimeError('synthetic batch embedding unavailable')
        def embed(self,text,mode):
            if mode=='search':return [0.0]
            raise RuntimeError('synthetic per-item embedding unavailable')
    fake=SimpleNamespace(db=SimpleNamespace(get_last_messages=lambda *a,**kw:[],save_messages=lambda *a:saved.append(a)),
        vector_store=SimpleNamespace(search=lambda **kw:[]),embedding_model=Embedder(),custom_instructions=None,
        llm=SimpleNamespace(generate_response=lambda **kw:json.dumps({'memory':[{'text':'First extracted fact'},{'text':'Second extracted fact'}]})))
    def add(text,**kw):
        assert text=='A complete unmodified source transcript.' and kw['infer'] is True
        return {'results':native.Memory._add_to_vector_store(fake,[{'role':'user','content':text}],{}, {'user_id':kw['user_id']},True)}
    receipt=connector(tmp_path,monkeypatch,add)
    assert receipt['result']=={'results':[]} and saved
    assert receipt['ready'] is False and len(receipt['native_errors'])==2
    assert all(e['classification']=='native_per_item_embedding_failed' and e['level']=='WARNING' for e in receipt['native_errors'])
    assert all('synthetic per-item' not in e['message'] for e in receipt['native_errors'])


@pytest.mark.parametrize('warning,ready',[
    ('Failed to embed memory text: an individual item failed',False),
    ('Failed to embed memory text (async): an individual item failed',False),
    ('Batch embed unavailable; native fallback succeeded',True),
])
def test_targeted_native_warning_is_not_a_fact_count_quota(tmp_path,monkeypatch,warning,ready):
    def add(*a,**kw):
        logging.getLogger('mem0.memory.main').warning(warning)
        return {'results':[{'event':'ADD','memory':'A successfully persisted item'}]}
    receipt=connector(tmp_path,monkeypatch,add)
    assert receipt['ready'] is ready


def test_legitimate_empty_extraction_and_unrelated_warning_remain_allowed(tmp_path,monkeypatch):
    def add(*a,**kw):
        logging.getLogger('unrelated.sdk').warning('Failed to embed memory text: not Mem0 native extraction')
        return {'results':[]}
    r=connector(tmp_path,monkeypatch,add)
    assert r['ready'] and not r['native_errors']


def test_quiet_host_does_not_suppress_native_failure_and_level_is_restored(tmp_path,monkeypatch):
    native=logging.getLogger('mem0.memory.main');before=native.level
    native.setLevel(logging.ERROR)
    try:
        def add(*a,**kw):
            native.warning('Failed to embed memory text: an individual item failed')
            return {'results':[]}
        receipt=connector(tmp_path,monkeypatch,add)
        assert not receipt['ready'] and receipt['native_errors'][0]['classification']=='native_per_item_embedding_failed'
        assert native.level==logging.ERROR
    finally:native.setLevel(before)
