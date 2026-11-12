"""Native transport limits, partial acknowledgments and safe error receipts."""
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import subprocess
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from provider_adapters import document_connector as connector


def messages(count):
    return [SimpleNamespace(peer_id='Riley',content=f'Message {i}',
        created_at=datetime(2026,10,19,12,0,tzinfo=timezone.utc),
        metadata={'source_id':f'source-{i}','from_owner':False}) for i in range(count)]


class Session:
    id='opaque-session'
    def __init__(self,fail_at=None,error=None):
        self.calls=[];self.fail_at=fail_at;self.error=error
    def add_messages(self,batch):
        assert len(batch)<=100
        self.calls.append(list(batch))
        if len(self.calls)==self.fail_at:raise self.error
        return [SimpleNamespace(**vars(m),id='native-'+m.metadata['source_id'],session_id=self.id) for m in batch]


@pytest.mark.parametrize('count,counts',[(100,[100]),(101,[100,1]),(201,[100,100,1]),(300,[100,100,100])])
def test_native_transport_preserves_every_message_and_order(count,counts):
    session=Session();original=messages(count);checkpoints=[]
    accepted,error=connector.add_honcho_messages(session,original,lambda *a:checkpoints.append(a))
    assert error is None
    assert [len(c) for c in session.calls]==counts
    assert [m.metadata['source_id'] for m in accepted]==[m.metadata['source_id'] for m in original]
    assert all((a.peer_id,a.content,a.created_at,a.metadata)==(b.peer_id,b.content,b.created_at,b.metadata)
               for a,b in zip(accepted,original))
    assert checkpoints[-1][3]=='acknowledged'


@pytest.mark.parametrize('status,commit',[(422,'rejected'),(401,'rejected'),(403,'rejected'),(503,'unknown'),(None,'unknown')])
def test_failed_chunk_keeps_prior_acknowledgments_and_never_retries(status,commit,tmp_path):
    class NativeAPIError(Exception):pass
    error=NativeAPIError('SECRET_SOURCE_AND_TOKEN')
    error.status=status
    error.body={'detail':[{'type':'too_long','loc':['body','messages'],
        'ctx':{'max_length':100,'actual_length':101},'input':'SECRET_SOURCE_AND_TOKEN'}]}
    session=Session(fail_at=2,error=error);journal=tmp_path/'delivery.json'
    def checkpoint(accepted,index,chunk,state,failure):
        from pacbench.followups import save
        save(journal,{'accepted_ids':[m.id for m in accepted],'chunk':index,'state':state,'error':failure})
    accepted,failure=connector.add_honcho_messages(session,messages(201),checkpoint)
    assert len(accepted)==100 and len(session.calls)==2
    assert failure['commit_state']==commit
    assert failure['acknowledged_messages']==100
    assert failure.get('http_status')==status
    assert failure.get('authentication_blocked',False) is (status in (401,403))
    assert len(failure['error_sha256'])==64
    assert json.loads(journal.read_text())['accepted_ids']==[m.id for m in accepted]
    assert 'SECRET_SOURCE_AND_TOKEN' not in journal.read_text()


def test_acknowledgment_mismatch_stops_before_next_chunk():
    session=Session();original=session.add_messages
    def changed(batch):
        response=original(batch);response[0].content='Unexpected body';return response
    session.add_messages=changed
    accepted,error=connector.add_honcho_messages(session,messages(101),lambda *a:None)
    assert len(session.calls)==1 and len(accepted)==0
    assert error['error_class']=='AcknowledgmentMismatch'


def test_mismatched_second_ack_never_counts_unverified_rows_as_accepted():
    session=Session();original=session.add_messages
    def changed(batch):
        response=original(batch)
        if len(session.calls)==2:response[0].content='Unexpected body'
        return response
    session.add_messages=changed
    accepted,error=connector.add_honcho_messages(session,messages(201),lambda *a:None)
    assert len(session.calls)==2 and len(accepted)==100
    assert error['acknowledged_messages']==100 and error['received_messages']==100
    assert len(error['unverified_response_sha256'])==64


def test_duplicate_native_id_cannot_prove_distinct_source_messages():
    session=Session();original=session.add_messages
    def duplicated(batch):
        response=original(batch);response[1].id=response[0].id;return response
    session.add_messages=duplicated
    accepted,error=connector.add_honcho_messages(session,messages(101),lambda *a:None)
    assert accepted==[] and len(session.calls)==1
    assert error['error_class']=='AcknowledgmentMismatch'


def test_worker_setup_error_does_not_print_exception_body(monkeypatch,capsys):
    def broken(*a):raise ValueError('SECRET_VALIDATION_BODY')
    monkeypatch.setattr(connector,'honcho',broken)
    assert connector.main(['honcho','source','root','at','processing_at'])==1
    captured=capsys.readouterr()
    assert 'SECRET_VALIDATION_BODY' not in captured.out+captured.err
    assert json.loads(captured.out)['native_error']['error_class']=='ValueError'


@pytest.mark.parametrize('fail_second',[False,True])
def test_connector_writes_atomic_per_chunk_receipts_before_processing(tmp_path,monkeypatch,fail_second):
    cfg=SimpleNamespace(user_observe_me=True,user_observe_others=False,
        ai_observe_me=False,ai_observe_others=True)
    session=Session(fail_at=2 if fail_second else None,error=TimeoutError('SECRET_NATIVE_BODY'))
    session.add_peers=lambda peers:None
    session.get_peer_configuration=lambda peer:SimpleNamespace(observe_me=False,observe_others=True)
    session.queue_status=lambda:SimpleNamespace(model_dump=lambda **kw:{'total_work_units':0})
    class Peer:
        def __init__(self,name):self.name=name
        def message(self,content,created_at,metadata):
            return SimpleNamespace(peer_id=self.name,content=content,
                created_at=datetime.fromisoformat(created_at),metadata=metadata)
    def get_session(name):session.id=name;return session
    client=SimpleNamespace(session=get_session,peer=Peer)
    native=ModuleType('honcho');native.Honcho=lambda **kwargs:client
    api=ModuleType('honcho.api_types');api.SessionPeerConfig=SimpleNamespace
    plugin=ModuleType('plugins.memory.honcho.client')
    plugin.HonchoClientConfig=SimpleNamespace(from_global_config=lambda:cfg)
    for name,value in [('honcho',native),('honcho.api_types',api)]:
        monkeypatch.setitem(sys.modules,name,value)
    resolver=Mock(return_value=plugin)
    monkeypatch.setattr('pacbench.provider_loader.provider_module',resolver)
    monkeypatch.setattr('pacbench.gateway_client.require_live_cost_boundary',lambda:{})
    monkeypatch.setattr('pacbench.stop.check_stop',lambda *a:None)
    monkeypatch.setattr('provider_adapters.honcho_clock.enabled',lambda r:True)
    processed=[]
    def wait(root,acks,roles):processed.append(acks);return {'ready':True,'result':acks}
    monkeypatch.setattr('provider_adapters.honcho_clock.wait_for_batch',wait)
    source=tmp_path/'day';source.mkdir();root=tmp_path/'arm';(root/'hermes-home').mkdir(parents=True)
    (root/'hermes-home/honcho.json').write_text(json.dumps({'baseUrl':'http://127.0.0.1:1',
        'hosts':{'hermes':{'aiPeer':'hermes','peerName':'user','workspace':'isolated'}}}))
    (root/'native-processing-settings.json').write_text('{}')
    records=[{'channel':'imessage','thread':'opaque','sender':'Riley','from_owner':False,
        'source_id':f'source-{i}','text':f'Message {i}','written_at':'2026-10-19T12:00:00-04:00'} for i in range(201)]
    (source/'messages.json').write_text(json.dumps(records))
    result=connector.honcho(source,root,'unused','unused')
    resolver.assert_called_once_with('honcho','client')
    assert result['ready'] is not fail_second
    assert bool(processed) is not fail_second
    index=root/'ingestion-delivery/day/index.json'
    journal=json.loads(index.read_text())
    receipts=[json.loads(p.read_text()) for p in sorted((index.parent/session.id).glob('*.json'))]
    assert [len(r['acknowledged']) for r in receipts]==([100,0] if fail_second else [100,100,1])
    assert journal['state']==('failed' if fail_second else 'submitted')
    connector.verify_honcho_delivery(index,complete=not fail_second)
    first=index.parent/session.id/'000000.json'
    original=first.read_bytes();first.write_text(first.read_text()+' ')
    with pytest.raises(ValueError,match='changed'):connector.verify_honcho_delivery(index)
    first.write_bytes(original);first.unlink()
    with pytest.raises(OSError):connector.verify_honcho_delivery(index)
    if fail_second:
        assert receipts[1]['error']['commit_state']=='unknown'
        assert receipts[1]['source_ids']==[f'source-{i}' for i in range(100,200)]
    assert 'SECRET_NATIVE_BODY' not in ''.join(p.read_text() for p in index.parent.rglob('*.json'))


@pytest.mark.parametrize('timeout',[False,True])
def test_worker_streams_cannot_leak_native_error_payload(tmp_path,monkeypatch,timeout):
    from pacbench import document_ingestion as ingestion
    from tests.test_document_ingestion import message
    schedule=ingestion.prepare_sources(tmp_path,[message('one')],'America/New_York')
    source=tmp_path/'sources'/schedule[0]['id'];root=tmp_path/'arm';root.mkdir()
    (root/'native-processing-settings.json').write_text(json.dumps({'deriver':{'REPRESENTATION_BATCH_MAX_AGE_SECONDS':1800}}))
    monkeypatch.setattr('provider_adapters.honcho_clock.enabled',lambda r:False)
    monkeypatch.setattr(ingestion,'snapshot',lambda roles:{'state':'captured','at':'now','calls':[]})
    def run(*a,**kw):
        if timeout:raise subprocess.TimeoutExpired(a[0],1,output=b'SECRET_ERROR_INPUT',stderr=b'SECRET_ERROR_INPUT')
        return subprocess.CompletedProcess(a[0],1,'SECRET_ERROR_INPUT','SECRET_ERROR_INPUT')
    monkeypatch.setattr(ingestion.subprocess,'run',run)
    record=ingestion.ingest_day('honcho',source,root,{'PACBENCH_USAGE_ROLES':'{}'},schedule[0]['at'],'America/New_York')
    assert record['ready'] is False
    assert record['stdout']==record['stderr']==''
    assert 'SECRET_ERROR_INPUT' not in json.dumps(record)
    assert set(record['worker_diagnostics'])=={'stdout','stderr'}
