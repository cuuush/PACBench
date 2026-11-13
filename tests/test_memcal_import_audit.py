import email,json,sqlite3
from pathlib import Path
import pytest
from pacbench.email_fixture import append_emails
from pacbench.memcal_import_audit import audit


def fixture(tmp_path):
    pytest.importorskip('memcal.sources.proton')
    source=tmp_path/'sources/2026-10-05';source.mkdir(parents=True)
    message={'source_id':'native-input-audit-unit','channel':'email','sender':'Maya','from_owner':False,'text':'Dinner Friday?','written_at':'2026-10-05T12:00:00-04:00','collected_at':'2026-10-05T12:00:00-04:00','thread':'thread-unit','structured':{'email_subject':'Plans','participants':['Me','Maya']}}
    (source/'messages.json').write_text(json.dumps([message]));append_emails(source/'mailbox.json',[message]);mail=json.loads((source/'mailbox.json').read_text())['messages'][0]
    dbpath=tmp_path/'repeat-1/memcal/memcal-home/memcal.db';dbpath.parent.mkdir(parents=True)
    with sqlite3.connect(dbpath) as db:
        db.execute('create table archive(id integer,channel text,external_id text,ts text,text text,from_me integer,handle text)')
        db.execute('insert into archive values(1,?,?,?,?,0,null)',('email',mail['message_id'].strip('<>'),message['written_at'],'Plans\n\nDinner Friday?'))
    return source,dbpath


def test_complete_native_tls_delivery_is_verified_without_changing_archive(tmp_path):
    source,db=fixture(tmp_path);before=db.read_bytes();r=audit(tmp_path)
    assert r['state']=='pass' and r['native_tls_imap_emails_verified']==1 and r['model_calls']==0
    assert db.read_bytes()==before


@pytest.mark.parametrize('fault',['transport','archive'])
def test_corrupted_transport_or_store_cannot_pass(tmp_path,fault):
    source,db=fixture(tmp_path)
    if fault=='transport':
        p=source/'mailbox.json';d=json.loads(p.read_text());d['messages'][0]['rfc822']=d['messages'][0]['rfc822'].replace('Dinner Friday?','Wrong source!');p.write_text(json.dumps(d))
    else:
        with sqlite3.connect(db) as connection:connection.execute("update archive set text='Wrong stored body'")
    with pytest.raises(ValueError):audit(tmp_path)
