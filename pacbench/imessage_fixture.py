"""Provider-independent, text-only Apple Messages source fixture.

The schema comes from an independent Messages reader fixture, not an Apple-created
database. No provider code or expected facts participate in its construction.
Attachments, reactions and attributedBody-only messages are not simulated.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'apple-messages-text-v2'
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
SCHEMA = (Path(__file__).parent/'source_schemas/apple_messages.sql').read_text()


def apple_ns(value: str) -> int:
    dt = datetime.fromisoformat(value)
    if dt.utcoffset() is None:
        raise ValueError('Source timestamps require an explicit timezone offset')
    delta = dt.astimezone(timezone.utc) - APPLE_EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000


def identity(name: str) -> dict:
    uid = uuid.uuid5(uuid.NAMESPACE_URL, 'pacbench-synthetic-contact:' + name)
    return {'name':name, 'handle':f'{uid.hex[:16]}@example.test', 'uid':str(uid).upper()}


def append_messages(path: Path, messages: list[dict]) -> dict:
    """Idempotently append available source messages; reject conflicting replay.

    Handles are synthetic Apple-ID email addresses in the reserved .test domain.
    Names are separate contact data, never handle IDs tailored to a reader.
    Receipt timestamps remain unknown (zero); we do not invent read/delivery times.
    """
    path = Path(path)
    for m in messages:
        if m['channel'] != 'imessage':
            raise ValueError('Apple Messages fixture requires iMessage source messages')
        if not isinstance(m.get('from_owner'), bool):
            raise ValueError('Source must declare from_owner explicitly')
        apple_ns(m['written_at'])
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = path.with_suffix('.fixture.json')
    manifest = json.loads(sidecar.read_text()) if sidecar.exists() else {
        'version':VERSION, 'contacts':{}, 'messages':{}, 'threads':{}, 'limitations':
        ['Independent reader schema with synthetic text-only rows; not Apple-created',
         'No attachments, reactions, attributed-only bodies or invented receipt timestamps']}
    if manifest['version'] != VERSION:
        raise ValueError('Incompatible source fixture version')
    owner = identity('Me')
    added = 0
    with sqlite3.connect(path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='message'").fetchone():
            conn.executescript(SCHEMA)
        for m in messages:
            if not m['from_owner']:
                contact = identity(m['sender'])
                manifest['contacts'][m['sender']] = contact
                conn.execute('INSERT OR IGNORE INTO handle(id,country,service,uncanonicalized_id,person_centric_id) VALUES(?,NULL,?,?,?)',
                             (contact['handle'],'iMessage',contact['handle'],contact['uid']))
                thread = manifest['threads'].get(m['thread'])
                if thread is None:
                    pending='chat'+uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-thread:'+m['thread']).hex
                    cursor=conn.execute('INSERT INTO chat(guid,style,state,account_id,chat_identifier,service_name,account_login,last_addressed_handle) VALUES(?,45,3,?,?,?,?,?)',
                        ('iMessage;+;'+pending,'iMessage:'+owner['handle'],pending,'iMessage',owner['handle'],owner['handle']))
                    thread={'rowid':cursor.lastrowid}
                    manifest['threads'][m['thread']]=thread
                cid=thread['rowid']
                hid=conn.execute('SELECT ROWID FROM handle WHERE id=?',(contact['handle'],)).fetchone()[0]
                conn.execute('INSERT OR IGNORE INTO chat_handle_join VALUES(?,?)',(cid,hid))
        for m in sorted(messages,key=lambda x:(apple_ns(x['written_at']),x['source_id'])):
            guid = str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:'+m['source_id'])).upper()
            fingerprint = hashlib.sha256(json.dumps(m,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            prior=manifest['messages'].get(m['source_id'])
            if prior:
                if prior['source_sha256'] != fingerprint:
                    raise ValueError('Conflicting source replay; edit semantics must be modeled explicitly')
                continue
            thread=manifest['threads'].get(m['thread'])
            if thread is None:
                raise ValueError('Outgoing-only chat requires explicit recipient source metadata')
            cid=thread['rowid']
            members=conn.execute('SELECT h.ROWID,h.id FROM chat_handle_join j JOIN handle h ON h.ROWID=j.handle_id WHERE j.chat_id=?',(cid,)).fetchall()
            group=len(members)>1
            chat_identifier=('chat'+uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-thread:'+m['thread']).hex) if group else members[0][1]
            props=plistlib.dumps({'EnableReadReceiptForChat':False,'lastSeenMessageGuid':guid})
            conn.execute('UPDATE chat SET guid=?,chat_identifier=?,style=?,properties=?,group_id=?,room_name=? WHERE ROWID=?',
                         ('iMessage;'+('+' if group else '-')+';'+chat_identifier,chat_identifier,
                          43 if group else 45,props,chat_identifier if group else None,chat_identifier if group else None,cid))
            hid=(0 if group else members[0][0]) if m['from_owner'] else conn.execute(
                'SELECT ROWID FROM handle WHERE id=?',(identity(m['sender'])['handle'],)).fetchone()[0]
            ns=apple_ns(m['written_at'])
            cursor=conn.execute('INSERT INTO message(guid,text,handle_id,service,account,account_guid,date,is_from_me,is_sent,destination_caller_id,cache_roomnames,is_finished,is_prepared,date_read,date_delivered) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,1,0,0)',
                (guid,m['text'],hid,'iMessage',owner['handle'],'iMessage:'+owner['handle'],ns,int(m['from_owner']),int(m['from_owner']),owner['handle'],chat_identifier if group else None))
            conn.execute('INSERT INTO chat_message_join VALUES(?,?,?)',(cid,cursor.lastrowid,ns))
            manifest['messages'][m['source_id']]={'guid':guid,'rowid':cursor.lastrowid,'source_sha256':fingerprint}
            added+=1
    sidecar.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    vcards=[]
    for c in manifest['contacts'].values():
        name=c['name'].replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
        vcards.append(f"BEGIN:VCARD\nVERSION:3.0\nUID:{c['uid']}\nFN:{name}\nEMAIL;TYPE=INTERNET:{c['handle']}\nEND:VCARD")
    path.with_suffix('.contacts.vcf').write_text('\n'.join(vcards)+'\n')
    # A separate, disclosed AddressBook schema subset allows native contact readers
    # to consume the same source roster, without provider-specific identity writes.
    contacts_db=path.with_suffix('.contacts.abcddb')
    with sqlite3.connect(contacts_db) as db:
        db.executescript('CREATE TABLE IF NOT EXISTS ZABCDRECORD(Z_PK INTEGER PRIMARY KEY,ZUNIQUEID TEXT UNIQUE,ZFIRSTNAME TEXT,ZLASTNAME TEXT,ZORGANIZATION TEXT); CREATE TABLE IF NOT EXISTS ZABCDEMAILADDRESS(Z_PK INTEGER PRIMARY KEY,ZOWNER INTEGER,ZADDRESS TEXT UNIQUE); CREATE TABLE IF NOT EXISTS ZABCDPHONENUMBER(Z_PK INTEGER PRIMARY KEY,ZOWNER INTEGER,ZFULLNUMBER TEXT);')
        for c in manifest['contacts'].values():
            db.execute('INSERT OR IGNORE INTO ZABCDRECORD(ZUNIQUEID,ZFIRSTNAME) VALUES(?,?)',(c['uid'],c['name']))
            pk=db.execute('SELECT Z_PK FROM ZABCDRECORD WHERE ZUNIQUEID=?',(c['uid'],)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO ZABCDEMAILADDRESS(ZOWNER,ZADDRESS) VALUES(?,?)',(pk,c['handle']))
    return {'version':VERSION,'added':added,'source_ids':[m['source_id'] for m in messages],
            'database':str(path),'metadata':str(sidecar),'contacts':str(path.with_suffix('.contacts.vcf')),
            'contacts_database':str(contacts_db)}
