"""Transport/source integrity tests; synthetic fixtures are not provider results."""
import json
import sqlite3
import subprocess
from datetime import datetime,timezone,timedelta

import pytest

from pacbench.document_ingestion import daily_batches,prepare_sources,processing_at
from pacbench.imessage_fixture import append_messages,apple_ns


def message(id,sender='Devon',owner=False,thread='one',stamp='2026-09-10T18:00:00-04:00'):
    return {'source_id':id,'sender':sender,'from_owner':owner,'thread':thread,'channel':'imessage',
            'written_at':stamp,'collected_at':'2026-09-10T23:00:00-04:00','text':'Friday dinner at seven.','structured':None}


def test_apple_fixture_has_real_address_identity_and_preserves_source(tmp_path):
    rows=[message('sent','Me',True),message('received')]
    path=tmp_path/'chat.db'
    result=append_messages(path,rows)
    assert result['added']==2
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        h=db.execute('SELECT * FROM handle').fetchone()
        assert h['id'].endswith('@example.test') and h['id']!='Devon'
        chat=db.execute('SELECT * FROM chat').fetchone()
        assert chat['style']==45 and chat['chat_identifier']==h['id']
        assert chat['guid']=='iMessage;-;'+h['id']
        received=db.execute('SELECT * FROM message WHERE is_from_me=0').fetchone()
        assert received['service']=='iMessage' and received['text']==rows[1]['text']
        expected=int((datetime.fromisoformat(rows[1]['written_at'])-datetime(2001,1,1,tzinfo=timezone.utc)).total_seconds())*10**9
        assert received['date']==expected
        assert received['date_read']==received['date_delivered']==0
        assert db.execute('SELECT count(*) FROM chat_message_join').fetchone()[0]==2
    assert 'FN:Devon' in path.with_suffix('.contacts.vcf').read_text()
    assert append_messages(path,rows)['added']==0
    with pytest.raises(ValueError,match='Conflicting source replay'):
        append_messages(path,[{**rows[0],'text':'A different value'}])


def test_group_membership_and_outgoing_handle_are_not_fake_senders(tmp_path):
    rows=[message('a','Me',True),message('b','Devon'),message('c','Maya')]
    path=tmp_path/'chat.db';append_messages(path,rows)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM chat_handle_join').fetchone()[0]==2
        guid,style,identifier=db.execute('SELECT guid,style,chat_identifier FROM chat').fetchone()
        assert style==43 and guid=='iMessage;+;'+identifier and identifier.startswith('chat')
        assert db.execute('SELECT handle_id FROM message WHERE is_from_me=1').fetchone()[0]==0


def test_daily_documents_cover_sources_and_fixture_has_no_future_rows(tmp_path):
    first=message('first')
    later={**message('later',stamp='2026-09-11T18:00:00-04:00'),'collected_at':'2026-09-11T23:00:00-04:00'}
    schedule=prepare_sources(tmp_path,[later,first],'America/New_York')
    assert [r['id'] for r in schedule]==['2026-09-10','2026-09-11']
    for i,row in enumerate(schedule,1):
        source=tmp_path/'sources'/row['id']
        assert len((source/'messages.txt').read_text().splitlines())==1
        with sqlite3.connect(source/'chat.db') as db:
            assert db.execute('SELECT count(*) FROM message').fetchone()[0]==i
    assert json.loads((tmp_path/'sources/2026-09-10/messages.txt').read_text())==first


def test_clock_offsets_and_collection_day_not_string_prefix():
    assert apple_ns('2026-11-01T01:30:00-04:00')+3600*10**9==apple_ns('2026-11-01T01:30:00-05:00')
    m={**message('one'),'collected_at':'2026-09-11T03:00:00Z'}
    assert daily_batches([m],'America/New_York')[0][0]=='2026-09-10'
    assert processing_at('2026-10-31T23:00:00-04:00','memcal','America/New_York')=='2026-11-01T03:00:00-05:00'
    for bad in [{**m,'answer_key':'secret'},{**m,'written_at':'2026-09-11T23:00:00Z'}]:
        with pytest.raises(ValueError):daily_batches([bad],'America/New_York')
    with pytest.raises(ValueError):apple_ns('2026-09-10T18:00:00')


def test_unavailable_recipient_is_not_invented(tmp_path):
    with pytest.raises(ValueError,match='recipient'):
        append_messages(tmp_path/'chat.db',[message('sent','Me',True)])


def test_evaluation_cannot_use_future_source_or_nightly_processing():
    from pacbench.document_ingestion import validate_evaluation_clocks
    schedule=[{'at':'2026-09-10T23:00:00-04:00'}]
    validate_evaluation_clocks(schedule,['memcal','mem0'],['2026-09-11T09:00:00-04:00'],'America/New_York')
    with pytest.raises(ValueError,match='source availability'):
        validate_evaluation_clocks(schedule,['mem0'],['2026-09-10T22:00:00-04:00'],'America/New_York')
    with pytest.raises(ValueError,match='native processing'):
        validate_evaluation_clocks(schedule,['memcal'],['2026-09-11T01:00:00-04:00'],'America/New_York')


@pytest.mark.parametrize('provider',['memcal','mem0','hindsight','honcho','openviking','supermemory','byterover'])
def test_every_provider_uses_the_same_nightly_cutoff_with_dst(provider):
    from pacbench.document_ingestion import ready_sources
    at='2026-10-31T23:00:00-04:00';schedule=[{'at':at}]
    assert processing_at(at,provider,'America/New_York')=='2026-11-01T03:00:00-05:00'
    with pytest.raises(ValueError,match='native processing'):
        ready_sources(schedule,provider,'2026-11-01T02:59:00-05:00','America/New_York')
    assert ready_sources(schedule,provider,'2026-11-01T03:00:00-05:00','America/New_York')==schedule


def test_old_ingestion_receipts_keep_their_original_clock_condition():
    from pacbench.document_ingestion import LEGACY_VERSION
    at='2026-10-14T20:00:00-04:00'
    assert processing_at(at,'mem0','America/New_York',version=LEGACY_VERSION)==at
    assert processing_at(at,'memcal','America/New_York',version=LEGACY_VERSION)=='2026-10-15T03:00:00-04:00'


def test_failed_native_processing_keeps_structured_evidence(tmp_path,monkeypatch):
    from pacbench import document_ingestion as module
    schedule=prepare_sources(tmp_path,[message('one')],'America/New_York')
    source=tmp_path/'sources'/schedule[0]['id']
    failure={'ready':False,'processing':{'errors':['native extraction failed']}}
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:
        subprocess.CompletedProcess(a[0],1,json.dumps(failure)+'\n','failure diagnostic'))
    monkeypatch.setattr(module,'snapshot',lambda roles:{'state':'captured','at':'now','calls':[]})
    record=module.ingest_day('memcal',source,tmp_path/'arm',{'PACBENCH_USAGE_ROLES':'{}'},
                             schedule[0]['at'],'America/New_York')
    assert record['state']=='failed' and record['ready'] is False
    assert record['result']==failure and record['returncode']==1
    assert record['hermes_ingestion_turns']==0


def test_recall_isolation_detects_real_native_session_or_builtin_facts(tmp_path):
    from pacbench.document_ingestion import record_recall_isolation
    home=tmp_path/'hermes-home';home.mkdir()
    with sqlite3.connect(home/'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT)')
        db.execute("INSERT INTO sessions VALUES('ingestion-session')")
    with pytest.raises(RuntimeError,match='without ingestion conversations'):
        record_recall_isolation(tmp_path)
    with sqlite3.connect(home/'state.db') as db:db.execute('DELETE FROM sessions')
    memories=home/'memories';memories.mkdir()
    (memories/'MEMORY.md').write_text('Dinner at seven.')
    with pytest.raises(RuntimeError,match='builtin facts'):
        record_recall_isolation(tmp_path)
    (memories/'MEMORY.md').write_text('')
    record_recall_isolation(tmp_path)
    assert json.loads((tmp_path/'pre-recall-isolation.json').read_text())['hermes_sessions_before_recall']==[]


@pytest.mark.parametrize('damage',[None,'text','speaker','time','session','missing','pending','observer','enrollment'])
def test_honcho_native_receipt_audit_rejects_changed_or_unprocessed_source(damage):
    from pacbench.document_audit import audit_honcho_receipt
    import hashlib
    original=message('source-1')
    session='source-'+hashlib.sha256(json.dumps(('imessage','one')).encode()).hexdigest()[:24]
    actual={'id':'native-message','session_id':session,'peer_id':'Devon','content':original['text'],
            'created_at':original['written_at'],'metadata':{k:v for k,v in original.items() if k!='text'}}
    receipt={'result':[{'session_id':session,'observer':{'peer_id':'hermes','observe_me':False,'observe_others':True},'messages':[actual]}],
             'processing_status':[{'sessions':{session:{'pending_work_units':0,'in_progress_work_units':0,'total_work_units':1}}}]}
    if damage=='text':actual['content']='Invented summary'
    elif damage=='speaker':actual['peer_id']='hermes'
    elif damage=='time':actual['created_at']='2026-09-11T18:00:00-04:00'
    elif damage=='session':actual['session_id']='other-source'
    elif damage=='missing':receipt['result'][0]['messages']=[]
    elif damage=='pending':receipt['processing_status'][0]['sessions'][session]['pending_work_units']=1
    elif damage=='observer':receipt['result'][0]['observer']['observe_others']=False
    elif damage=='enrollment':receipt['processing_status'][0]['sessions'][session]['total_work_units']=0
    errors=audit_honcho_receipt([original],receipt)
    assert bool(errors)==bool(damage)


def test_honcho_readiness_audit_carries_enrollment_across_daily_batches():
    from pacbench.document_audit import audit_honcho_receipt
    import hashlib
    original=message('source-1')
    session='source-'+hashlib.sha256(json.dumps(('imessage','one')).encode()).hexdigest()[:24]
    def receipt(source):
        actual={'id':'native-message','session_id':session,'peer_id':'Devon',
            'content':source['text'],'created_at':source['written_at'],
            'metadata':{k:v for k,v in source.items() if k!='text'}}
        return {'result':[{'session_id':session,'observer':{'peer_id':'hermes','observe_others':True},'messages':[actual]}],
            'processing_status':[{'sessions':{session:{'pending_work_units':0,'in_progress_work_units':0,'total_work_units':1}}}]}
    previous={}
    assert not audit_honcho_receipt([original],receipt(original),previous)
    assert previous=={session:1}
    second={**original,'source_id':'source-2'}
    assert 'Honcho readiness preceded new source work enrollment' in audit_honcho_receipt([second],receipt(second),previous)
