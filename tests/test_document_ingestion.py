"""Transport/source integrity tests; synthetic fixtures are not provider results."""
import json
import sqlite3
import subprocess
import os
import sys
from datetime import datetime,timezone,timedelta

import pytest

from pacbench.document_ingestion import daily_batches,prepare_sources,processing_at
from pacbench.imessage_fixture import append_messages,apple_ns


def message(id,sender='Devon',owner=False,thread='one',stamp='2026-09-10T18:00:00-04:00'):
    return {'source_id':id,'sender':sender,'from_owner':owner,'thread':thread,'channel':'imessage',
            'written_at':stamp,'collected_at':'2026-09-10T23:00:00-04:00','text':'Friday dinner at seven.','structured':None}


@pytest.mark.parametrize('mode',['success','missing_outcome','incomplete','source_failure','count_mismatch'])
def test_memcal_native_collection_records_transport_and_original_times(tmp_path, mode):
    chat=message('chat')
    mail={**message('mail'), 'channel':'email', 'thread':'mail',
          'structured':{'participants':['Me','Devon'],'email_subject':'Dinner'}}
    prepare_sources(tmp_path,[chat,mail],'America/New_York')
    root=tmp_path/'arm'; root.mkdir()
    home=tmp_path/'home'; home.mkdir()
    src=os.environ.get('MEMCAL_TEST_SRC',str(__import__('pathlib').Path(__file__).resolve().parents[2]/'memcal'))
    program='''
import json, os, sqlite3, sys
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0, os.environ['MEMCAL_SRC'])
from memcal import archive, sources
from memcal.sources import imessage
from provider_adapters.document_connector import memcal
class Dream:
    run_id=1
    errors=[]
    def report(self): return 'test'
mode=sys.argv[3]
original=sources.catch_up
def intercept(source, conn, cfg, **kwargs):
    report=original(source,conn,cfg,**kwargs)
    if mode=='incomplete':
        report.more=True
        archive.record_source(conn,kwargs['collection_id'],report)
    if mode=='source_failure':
        report.error='fixture source failure'
        archive.record_source(conn,kwargs['collection_id'],report)
    if mode=='count_mismatch': report.read+=1
    return report
with ExitStack() as stack:
    stack.enter_context(patch.object(imessage, 'CHAT_DB', Path.home()/'Library/Messages/chat.db'))
    stack.enter_context(patch('pacbench.gateway_client.validate_route'))
    dream=stack.enter_context(patch('memcal.dream.run.dream', return_value=Dream()))
    if mode=='missing_outcome': stack.enter_context(patch.object(archive,'record_source',side_effect=sqlite3.OperationalError('fixture')))
    if mode in ('incomplete','source_failure','count_mismatch'): stack.enter_context(patch.object(sources,'catch_up',side_effect=intercept))
    try:
        result=memcal(Path(sys.argv[1]),Path(sys.argv[2]),'2026-09-10T23:00:00-04:00','2026-09-11T03:00:00-04:00')
        error=None
    except RuntimeError as exc:
        result=None; error=str(exc)
    dream_called=dream.called
with sqlite3.connect(Path(os.environ['MEMCAL_HOME'])/'memcal.db') as db:
    rows=db.execute("select channel,ts,collection_id from archive order by channel").fetchall()
    collection=db.execute('select finished_at,error from collections').fetchone()
    statuses=db.execute('select channel,status from collection_sources').fetchall()
print(json.dumps({'result':result,'rows':rows,'error':error,'dream_called':dream_called,'collection':collection,'statuses':statuses}, default=lambda x: list(x) if isinstance(x,set) else str(x)))
'''
    env={**os.environ,'HOME':str(home),'MEMCAL_HOME':str(root/'memcal-home'),
         'MEMCAL_SRC':src,'MEMCAL_LLM_PROVIDER':'openai-compatible',
         'MEMCAL_OPENAI_BASE_URL':'http://localhost:1','OPENAI_COMPAT_API_KEY':'fixture',
         'TZ':'America/New_York'}
    proc=subprocess.run([sys.executable,'-c',program,str(tmp_path/'sources/2026-09-10'),str(root),mode],
                        env=env,capture_output=True,text=True,timeout=30)
    assert proc.returncode==0, proc.stderr
    payload=json.loads(proc.stdout.splitlines()[-1]); result=payload['result']
    if mode!='success':
        assert result is None and payload['error'] and not payload['dream_called']
        assert payload['collection']==['2026-09-11T03:00:00-04:00',
                                      'native collection incomplete' if mode=='incomplete' else 'native collection failed']
        if mode=='incomplete': assert payload['statuses']==[['imessage','incomplete']]
        if mode=='source_failure': assert payload['statuses']==[['imessage','failed']]
        return
    assert result['collector_protocol']=='native-cli-collection-v1' and result['ready']
    collection=result['collection']
    assert collection['started_at']==collection['finished_at']=='2026-09-11T03:00:00-04:00'
    assert {s['channel']:s['status'] for s in collection['sources']}=={'imessage':'complete','email':'complete'}
    assert len(payload['rows'])==2
    assert all(row[1]==chat['written_at'] and row[2]==collection['id'] for row in payload['rows'])


@pytest.mark.parametrize('damage',['none','timeout','errors','counter-only','missing-queues'])
def test_openviking_native_completion_rejects_error_counters_even_without_error_text(damage):
    from pacbench.document_audit import audit_openviking_receipt
    native={'status':'success','errors':[],'root_uri':'viking://resources/day',
            'queue_status':{'Semantic':{'errors':[],'error_count':0}}}
    if damage=='timeout':native['status']='timeout'
    if damage=='errors':native['errors']=['failed']
    if damage=='counter-only':native['queue_status']['Semantic']['error_count']=1
    if damage=='missing-queues':native.pop('queue_status')
    assert bool(audit_openviking_receipt({'result':{'result':native}}))==(damage!='none')


@pytest.mark.parametrize('damage',['none','index-only','missing-status','wrong-source','wrong-document','wrong-namespace','wrong-mode'])
def test_supermemory_ready_flag_cannot_replace_native_completion_evidence(damage):
    from pacbench.document_audit import audit_supermemory_receipt
    final={'status':'done','dreaming_status':'done','id':'doc-1','custom_id':'2026-10-19',
           'content':'Raw source text','container_tags':['native_tag']}
    receipt={'result':{'id':'doc-1'},'container_tag':'native_tag','processing_status':[final]}
    if damage=='index-only':final['dreaming_status']='dreaming'
    if damage=='missing-status':final.pop('dreaming_status')
    if damage=='wrong-source':final['content']='Different source'
    if damage=='wrong-document':final['id']='other-doc'
    if damage=='wrong-namespace':final['container_tags']=['other_tag']
    if damage=='wrong-mode':receipt['delivery_options']={'dreaming':'dynamic'}
    assert bool(audit_supermemory_receipt(receipt,'2026-10-19','Raw source text'))==(damage!='none')


@pytest.mark.parametrize('text',['café plans','café\u2028plans\u2029next day'])
def test_native_json_acknowledgment_is_stored_once_without_losing_other_output(text):
    from pacbench.document_ingestion import remove_duplicate_stdout
    result={'ready':True,'result':{'memories':[text]}}
    record={'stdout':'native progress\n'+json.dumps(result,ensure_ascii=False)+'\n\n','result':result,'stderr':'native warning'}
    assert remove_duplicate_stdout(record)
    assert record['stdout']=='native progress\n\n' and record['result']==result
    assert record['stderr']=='native warning' and record['stdout_json_in']=='result'
    assert not remove_duplicate_stdout(record)
    changed={'stdout':json.dumps({'ready':False}), 'result':result}
    assert not remove_duplicate_stdout(changed)
    assert changed['stdout']==json.dumps({'ready':False})


def test_checkpoint_references_bind_to_canonical_daily_manifests_without_repeating_ids():
    from pacbench.document_ingestion import source_state,compact_source_state
    schedule=[{'id':'first','source_ids':['m1','m2']},{'id':'later','source_ids':['m3']}]
    old={'at':'clock','ingested_batches':['first'],'source_ids':['m1','m2']}
    small=compact_source_state(old,schedule)
    assert small==source_state('clock',['first'])
    assert 'source_ids' not in small and small['reference_version']==2
    with pytest.raises(ValueError,match='canonical'):
        compact_source_state({**old,'source_ids':['m1','future-message']},schedule)


@pytest.mark.parametrize('observed,original,expected',[
    ('daily text','daily text\n',True),('daily text\n','daily text\n',True),
    ('daily text','daily text\n\n',False),('daily text',' daily text\n',False),
    ('daily text','daily  text\n',False),('different text','daily text\n',False),
])
def test_native_storage_may_drop_one_export_newline_but_never_change_messages(observed,original,expected):
    from pacbench.document_audit import same_submitted_text
    assert same_submitted_text(observed,original) is expected


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
        assert 'Friday dinner at seven.' in (source/'messages.txt').read_text()
        with sqlite3.connect(source/'chat.db') as db:
            assert db.execute('SELECT count(*) FROM message').fetchone()[0]==i
    assert json.loads((tmp_path/'sources/2026-09-10/messages.json').read_text())==[first]


def test_readable_document_preserves_bodies_context_ownership_and_delayed_delivery():
    from pacbench.document_ingestion import document_text
    a={**message('opaque-id-a','Me',True),'structured':{'participants':['Me','Ben'],'email_subject':'Dinner'},
       'channel':'email','text':'  Saturday works.\n\n> Me: Thursday?\n> Ben: yes!  ',
       'arrived_at':'2026-09-10T18:00:00-04:00'}
    b={**message('opaque-id-b','Ben'),'channel':'email','structured':a['structured'],
       'arrived_at':'2026-09-10T18:05:00-04:00','text':'Thanks! 🦆'}
    c={**message('opaque-id-c','Jen',thread='pond'),'structured':{'participants':['Me','Jen','Alex'],'chat_name':'Duck crew'},
       'text':'I can’t make Friday.'}
    text=document_text([a,b,c],c['collected_at'])
    for row in (a,b,c):assert row['text']+'\n' in text and row['written_at'] in text
    assert text.count('Conversation:')==2 and text.count('Participants: Me, Ben')==1
    assert 'Subject: Dinner' in text and 'Name: Duck crew' in text
    assert '] Me (sent by owner)' in text and '] Ben\n' in text
    assert 'Delivered: '+b['arrived_at'] in text and 'Captured: '+a['collected_at'] in text
    assert 'opaque-id-' not in text and '"source_id"' not in text


@pytest.mark.parametrize('version',['daily-source-documents-v1','daily-source-documents-v2'])
def test_old_document_bytes_remain_auditable(version):
    from pacbench.document_ingestion import document_text
    rows=[message('original')]
    assert json.loads(document_text(rows,rows[0]['collected_at'],version=version))==rows[0]
    expected=rows[0]['collected_at'] if version.endswith('v1') else '2026-09-11T03:00:00-04:00'
    assert processing_at(rows[0]['collected_at'],'mem0','America/New_York',version=version)==expected


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


@pytest.mark.parametrize('damage',[None,'text','speaker','time','session','missing','pending','observer','enrollment',
                                  'unknown-error-scope','unattributed-error','misattributed-source-error'])
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
    elif damage=='unknown-error-scope':receipt['source_error_scope']='ignore-all-errors'
    elif damage in ('unattributed-error','misattributed-source-error'):
        receipt['processing_status'][0]['unrelated_queue_errors']=[{
            'session_name':None if damage=='unattributed-error' else session,'error_class':'StructuredOutputError'}]
    else:
        receipt['processing_status'][0]['unrelated_queue_errors']=[{
            'session_name':'unrelated-capture-session','error_class':'StructuredOutputError'}]
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


def test_v5_supermemory_native_status_cannot_claim_consolidation_from_indexing():
    from types import SimpleNamespace
    from provider_adapters.supermemory_native import document
    from pacbench.native_writes import complete
    seen=[]
    def get(namespace, identifier):
        seen.append((namespace,identifier))
        return SimpleNamespace(model_dump=lambda **_: {'id':'source/day','content':'Original raw text',
            'system':{'status':'done','dreamingStatus':'dreaming'}})
    client=SimpleNamespace(documents=SimpleNamespace(get=get))
    raw,state=document(client,'isolated-namespace','source/day')
    assert seen==[('isolated-namespace','source%2Fday')]
    assert raw['content']=='Original raw text' and complete('supermemory',[state])==(False,False)


def test_v5_supermemory_document_audit_binds_namespace_source_and_both_statuses():
    from copy import deepcopy
    from pacbench.document_audit import audit_supermemory_receipt
    system={'status':'done','dreamingStatus':'done'}
    receipt={'native_api_protocol':'supermemory-v5-documents-v1','request_namespace':'namespace',
        'container_tag':'namespace','result':{'id':'2026-10-05'},
        'delivery_options':{'dreaming':'instant'},'processing_status':[{
            'id':'2026-10-05','content':'Exact original raw text',
            'metadata':{'source':'daily-transcript','collected_at':'2026-10-05T23:00:00-04:00'},
            'status':'done','dreaming_status':'done','native_system':system,'request_namespace':'namespace'}]}
    assert audit_supermemory_receipt(receipt,'2026-10-05','Exact original raw text')==[]
    for change in ['namespace','source','consolidation']:
        broken=deepcopy(receipt)
        if change=='namespace':broken['processing_status'][-1]['request_namespace']='other'
        if change=='source':broken['processing_status'][-1]['content']='Extracted or changed text'
        if change=='consolidation':broken['processing_status'][-1]['native_system']['dreamingStatus']='dreaming'
        assert audit_supermemory_receipt(broken,'2026-10-05','Exact original raw text')
