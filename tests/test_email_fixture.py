"""Protocol/source checks, including the real Memcal IMAP reader when installed."""
import imaplib
import json
import os
import shutil
import ssl
import subprocess
from email import message_from_bytes
from pathlib import Path

import pytest

from pacbench.document_ingestion import prepare_sources
from pacbench.email_fixture import MailboxServer,append_emails,mail_address
from pacbench.imessage_fixture import identity


def email(id, sender, text, stamp, thread='ben-email'):
    return {'source_id':id,'channel':'email','thread':thread,'sender':sender,
            'text':text,'from_owner':sender=='Me','written_at':stamp,'collected_at':stamp,
            'structured':{'participants':['Me','Ben'],'email_subject':'Thursday dinner'}}


def exchange():
    return [email('incoming','Ben','Can we move our dinner to Saturday?', '2026-10-06T07:00:00-04:00'),
            email('reply','Me','Saturday works, same time.', '2026-10-06T07:10:00-04:00')]


def test_mailbox_tls_headers_references_folders_and_replay(tmp_path):
    rows=exchange();path=tmp_path/'mailbox.json'
    append_emails(path,rows)
    before=path.read_bytes();append_emails(path,rows)
    assert path.read_bytes()==before
    with pytest.raises(ValueError,match='Conflicting'):
        append_emails(path,[{**rows[0],'text':'changed'}])
    with MailboxServer(path) as server:
        context=ssl._create_unverified_context()  # synthetic localhost certificate
        with imaplib.IMAP4_SSL('127.0.0.1',server.port,ssl_context=context) as client:
            assert client.login(server.user,server.password)[0]=='OK'
            assert client.select('INBOX',readonly=True)==('OK',[b'1'])
            assert client.uid('SEARCH',None,'UID 1:*')[1]==[b'1']
            assert client.uid('SEARCH',None,'UID 2:*')[1]==[b'']
            first=message_from_bytes(client.uid('FETCH','1','(BODY.PEEK[])')[1][0][1])
            assert first['From'].endswith('<'+mail_address('Ben')+'>')
            assert first.get_payload(decode=True).decode().strip()==rows[0]['text']
            assert client.select('Sent',readonly=True)[0]=='OK'
            reply=message_from_bytes(client.uid('FETCH','1','(BODY.PEEK[])')[1][0][1])
            assert reply['In-Reply-To']==first['Message-ID']
            assert reply['References']==first['Message-ID']
            assert reply['Subject']=='Re: Thursday dinner'
            assert client.status('Sent','(UIDVALIDITY)')[1]==[b'"Sent" (UIDVALIDITY 1)']
        assert all(c['status']=='OK' for c in server.commands)


def test_email_daily_snapshot_has_no_future_messages_and_shared_contact(tmp_path):
    rows=exchange()
    later={**rows[1],'written_at':'2026-10-07T07:10:00-04:00','collected_at':'2026-10-07T07:10:00-04:00'}
    schedule=prepare_sources(tmp_path,[rows[0],later],'America/New_York')
    for i,row in enumerate(schedule,1):
        source=tmp_path/'sources'/row['id']
        mailbox=json.loads((source/'mailbox.json').read_text())
        assert len(mailbox['messages'])==i
        assert all(m['source']['written_at'][:10]<=row['id'] for m in mailbox['messages'])
        vcf=(source/'chat.contacts.vcf').read_text()
        assert identity('Ben')['handle'] in vcf and mail_address('Ben') in vcf


def test_mailbox_rejects_inconsistent_owner_metadata(tmp_path):
    with pytest.raises(ValueError,match='ownership'):
        append_emails(tmp_path/'mailbox.json',[{**exchange()[0],'from_owner':True}])


def test_native_memcal_imap_reader_and_contacts_resolve_two_accounts(tmp_path):
    repo=Path(__file__).resolve().parents[2]/'memcal'
    python=Path(__file__).resolve().parents[1]/'vendor/venv-hermes-isolated/bin/python'
    if not repo.exists() or not python.exists():
        pytest.skip('Optional sibling Memcal checkout/runtime not installed')
    rows=exchange();schedule=prepare_sources(tmp_path,rows,'America/New_York')
    source=tmp_path/'sources'/schedule[0]['id']
    system_home=tmp_path/'system-home'
    target=system_home/'Library/Application Support/AddressBook/Sources/test/AddressBook-v22.abcddb'
    target.parent.mkdir(parents=True);shutil.copyfile(source/'chat.contacts.abcddb',target)
    with MailboxServer(source/'mailbox.json') as server:
        script='''
import json,os
from pathlib import Path
from memcal import db,identity
from memcal.config import Config
from memcal.sources import proton
cfg=Config(home=Path(os.environ['MEMCAL_HOME']))
cfg.ensure_dirs()
conn=db.open_db(cfg.db_path)
db.set_today('2026-10-06')
identity.import_contacts(conn)
first=proton.ingest(conn,cfg)
again=proton.ingest(conn,cfg)
print(json.dumps({'read':first.read,'error':first.error,'replay_read':again.read,
    'rows':[dict(r) for r in conn.execute('SELECT person,from_me,text,thread FROM archive')],
    'identities':[identity.resolve(conn,a) for a in json.loads(os.environ['TEST_ADDRESSES'])]}))
conn.close()
'''
        env={**os.environ,'PYTHONPATH':str(repo),'HOME':str(system_home),'TZ':'America/New_York',
             'MEMCAL_HOME':str(tmp_path/'memcal-home'),'PROTON_BRIDGE_HOST':'127.0.0.1',
             'PROTON_BRIDGE_PORT':str(server.port),'PROTON_BRIDGE_USER':server.user,
             'PROTON_BRIDGE_PASSWORD':server.password,
             'TEST_ADDRESSES':json.dumps([identity('Ben')['handle'],mail_address('Ben')])}
        proc=subprocess.run([str(python),'-c',script],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=30)
        assert proc.returncode==0,proc.stderr
        result=json.loads(proc.stdout)
        assert result['error'] is None and result['read']==2 and result['replay_read']==0
        assert result['identities']==['Ben','Ben']
        assert len(result['rows'])==2 and len({r['thread'] for r in result['rows']})==1
        assert all('Saturday' in r['text'] for r in result['rows'])
