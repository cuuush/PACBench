"""Native transport failure receipts, without live providers or inference."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from provider_adapters.document_connector import supermemory


@pytest.fixture
def native(tmp_path,monkeypatch):
    source=tmp_path/'day';source.mkdir();(source/'messages.txt').write_text('Raw daily text\n')
    root=tmp_path/'arm';(root/'hermes-home').mkdir(parents=True)
    (root/'document-ingestion-config.json').write_text(json.dumps({'dreaming':'instant'}))
    monkeypatch.setitem(sys.modules,'plugins.memory.supermemory',SimpleNamespace(
        _load_supermemory_config=lambda path:{'base_url':'http://127.0.0.1:1234','container_tag':'raw-tag'},
        _resolve_container_tag=lambda *a:'native-tag'))
    documents=SimpleNamespace(add=Mock(return_value=SimpleNamespace(
        id='accepted-id',model_dump=lambda **kw:{'id':'accepted-id','status':'queued'})),get=Mock())
    client=Mock(return_value=SimpleNamespace(documents=documents))
    monkeypatch.setitem(sys.modules,'supermemory',SimpleNamespace(Supermemory=client))
    monkeypatch.setenv('SUPERMEMORY_API_KEY','local-test-key')
    monkeypatch.delenv('PACBENCH_USAGE_ROLES',raising=False)
    monkeypatch.setattr('provider_adapters.document_connector.time.sleep',lambda seconds:None)
    return source,root,documents,client


@pytest.mark.parametrize('status',[None,400,401,403,422,500])
def test_document_delivery_failure_is_sanitized_and_never_retried(native,status):
    source,root,documents,client=native
    error=RuntimeError('SECRET_NATIVE_BODY')
    if status is not None:error.status_code=status
    documents.add.side_effect=error
    result=supermemory(source,root,'now','now')
    assert not result['ready']
    documents.add.assert_called_once();documents.get.assert_not_called()
    assert client.call_args.kwargs['max_retries']==0
    assert result['native_error']['phase']=='document-add'
    assert result['native_error']['commit_state']==('rejected' if status in (400,401,403,422) else 'unknown')
    assert result['native_error'].get('authentication_blocked',False)==(status in (401,403))
    assert 'SECRET_NATIVE_BODY' not in json.dumps(result)


@pytest.mark.parametrize('observed_first',[False,True])
def test_status_failure_preserves_accepted_document_and_prior_states(native,observed_first):
    source,root,documents,_=native
    state={'id':'accepted-id','status':'done','dreaming_status':'dreaming',
           'content':'Raw daily text\n','custom_id':'day','container_tags':['native-tag']}
    prior=[SimpleNamespace(model_dump=lambda **kw:state)] if observed_first else []
    documents.get.side_effect=[*prior,TimeoutError('SECRET_NATIVE_BODY')]
    result=supermemory(source,root,'now','now')
    assert not result['ready'] and result['result']['id']=='accepted-id'
    assert result['processing_status']==([state] if observed_first else [])
    assert result['native_error']['commit_state']=='accepted'
    assert result['native_error']['phase']=='document-status'
    assert result['native_error']['error_class']=='TimeoutError'
    assert documents.add.call_count==1 and documents.get.call_count==len(prior)+1
    assert 'SECRET_NATIVE_BODY' not in json.dumps(result)
