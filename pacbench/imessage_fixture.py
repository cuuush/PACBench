"""Provider-independent, text-only Apple Messages source fixture.

The schema comes from an independent Messages reader fixture, not an Apple-created
database. No provider code or expected facts participate in its construction.
Attachments, reactions and attributedBody-only messages are not simulated.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sqlite3
import tempfile
import uuid
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'apple-messages-text-v4'
MEMBERSHIP_LIMITATION = ('Apple Messages fixture has a global chat_handle_join roster, not historical membership. '
    'Changing membership in the same thread is unsupported; refusing to backfill old messages or create a different chat.')
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


def append_messages(path: Path, messages: list[dict], *, contacts=(), membership_condition=None) -> dict:
    """Idempotently append available source messages; reject conflicting replay.

    Handles are synthetic Apple-ID email addresses in the reserved .test domain.
    Names are separate contact data, never handle IDs tailored to a reader.
    Receipt timestamps remain unknown (zero); we do not invent read/delivery times.
    """
    if membership_condition is not None:
        return append_temporal_messages(path, messages, contacts=contacts, condition=membership_condition)
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
    for name in contacts:
        if name != 'Me':
            manifest['contacts'][name]=identity(name)
    owner = identity('Me')
    added = 0
    with sqlite3.connect(path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='message'").fetchone():
            conn.executescript(SCHEMA)
        groups = {}
        for m in messages:groups.setdefault(m['thread'],[]).append(m)
        for tid,batch in groups.items():
            declared = [(m.get('structured') or {}).get('participants') for m in batch]
            rosters = [list(r) for r in declared if r is not None]
            prior_thread = manifest['threads'].get(tid)
            if rosters:
                roster = rosters[0]
                if (len(rosters)!=len(batch) or any(r!=roster for r in rosters) or
                    any(not isinstance(name,str) or not name.strip() for name in roster) or
                    'Me' not in roster or len(roster)<2 or len(set(roster))!=len(roster) or
                    any(m['sender'] not in roster for m in batch)):
                    raise ValueError('Inconsistent source chat membership. '+MEMBERSHIP_LIMITATION)
            else:
                roster = sorted(set((prior_thread or {}).get('participants',[])) |
                                {'Me'} | {m['sender'] for m in batch if not m['from_owner']})
            if len(roster)<2:
                raise ValueError('Outgoing-only chat requires explicit recipient source metadata')
            if prior_thread and rosters and prior_thread['participants'] != roster:
                raise ValueError(MEMBERSHIP_LIMITATION)
            labels = {(m.get('structured') or {}).get('chat_name','') for m in batch}
            if len(labels)!=1:raise ValueError('Inconsistent source group name')
            display = next(iter(labels))
            if display and len(roster)<3:raise ValueError('Group name assigned to a direct chat')
            members = []
            for name in roster:
                if name=='Me':continue
                contact=identity(name);manifest['contacts'][name]=contact
                conn.execute('INSERT OR IGNORE INTO handle(id,country,service,uncanonicalized_id,person_centric_id) VALUES(?,NULL,?,?,?)',
                    (contact['handle'],'iMessage',contact['handle'],contact['uid']))
                members.append(conn.execute('SELECT ROWID FROM handle WHERE id=?',(contact['handle'],)).fetchone()[0])
            if prior_thread is None:
                pending='chat'+uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-thread:'+tid).hex
                cursor=conn.execute('INSERT INTO chat(guid,style,state,account_id,chat_identifier,service_name,account_login,last_addressed_handle) VALUES(?,45,3,?,?,?,?,?)',
                    ('iMessage;+;'+pending,'iMessage:'+owner['handle'],pending,'iMessage',owner['handle'],owner['handle']))
                prior_thread={'rowid':cursor.lastrowid};manifest['threads'][tid]=prior_thread
            prior_thread['participants']=roster
            cid=prior_thread['rowid']
            for hid in members:conn.execute('INSERT OR IGNORE INTO chat_handle_join VALUES(?,?)',(cid,hid))
            conn.execute('UPDATE chat SET display_name=? WHERE ROWID=?',(display or None,cid))
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
        email=c['uid'].lower()+'@mail.example.test'
        vcards.append(f"BEGIN:VCARD\nVERSION:3.0\nUID:{c['uid']}\nFN:{name}\nEMAIL;TYPE=INTERNET:{c['handle']}\nEMAIL;TYPE=INTERNET:{email}\nEND:VCARD")
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
            db.execute('INSERT OR IGNORE INTO ZABCDEMAILADDRESS(ZOWNER,ZADDRESS) VALUES(?,?)',
                       (pk,c['uid'].lower()+'@mail.example.test'))
    return {'version':VERSION,'added':added,'source_ids':[m['source_id'] for m in messages],
            'database':str(path),'metadata':str(sidecar),'contacts':str(path.with_suffix('.contacts.vcf')),
            'contacts_database':str(contacts_db)}


CONDITION = 'apple-messages-additive-v1'


READER_COMMIT = '0d444ab3220f5940d4632c05da4628009c785a02'


READER_GROUP_ACTION_SHA256 = '3c88d0b18b25102545d34f6cd0d8322d37993273d1cc6eb816578e996574faee'


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _join_guid(thread, boundary_source_id, name):
    key = f'pacbench-source-join:{thread}:{boundary_source_id}:{name}'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key)).upper()


def _contacts(path, contacts):
    cards=[]
    with sqlite3.connect(path.with_suffix('.contacts.abcddb')) as db:
        db.executescript('CREATE TABLE ZABCDRECORD(Z_PK INTEGER PRIMARY KEY,ZUNIQUEID TEXT UNIQUE,ZFIRSTNAME TEXT,ZLASTNAME TEXT,ZORGANIZATION TEXT); CREATE TABLE ZABCDEMAILADDRESS(Z_PK INTEGER PRIMARY KEY,ZOWNER INTEGER,ZADDRESS TEXT UNIQUE); CREATE TABLE ZABCDPHONENUMBER(Z_PK INTEGER PRIMARY KEY,ZOWNER INTEGER,ZFULLNUMBER TEXT);')
        for c in contacts.values():
            pk=db.execute('INSERT INTO ZABCDRECORD(ZUNIQUEID,ZFIRSTNAME) VALUES(?,?)', (c['uid'],c['name'])).lastrowid
            email=c['uid'].lower()+'@mail.example.test'
            db.executemany('INSERT INTO ZABCDEMAILADDRESS(ZOWNER,ZADDRESS) VALUES(?,?)', [(pk,c['handle']),(pk,email)])
            name=c['name'].replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
            cards.append(f"BEGIN:VCARD\nVERSION:3.0\nUID:{c['uid']}\nFN:{name}\nEMAIL;TYPE=INTERNET:{c['handle']}\nEMAIL;TYPE=INTERNET:{email}\nEND:VCARD")
    path.with_suffix('.contacts.vcf').write_text('\n'.join(cards)+'\n')


def append_temporal_messages(path, messages, *, contacts=(), condition):
    if condition != CONDITION:
        raise ValueError('Unknown Apple Messages membership condition')
    path=Path(path).resolve(); sidecar=path.with_suffix('.fixture.json')
    if path.exists()!=sidecar.exists():
        raise ValueError('Incomplete temporal fixture commit; restore the previous verified checkpoint')
    old=json.loads(sidecar.read_text()) if sidecar.exists() else {
        'version':CONDITION,'contacts':{},'messages':{},'threads':{},'last_written_ns':-1,
        'limitations':['Synthetic independent-reader schema, not Apple-created',
            'Only additive membership in an existing group; no removals or direct-to-group conversion',
            'Bodyless notices have unspecified actor; boundary clock, ordered before dialogue by ROWID',
            'chat_handle_join is the current checkpoint roster, not a per-message historical roster']}
    if old['version']!=CONDITION:
        raise ValueError('Cannot migrate a historical fixture condition implicitly')
    for name,h in old.get('artifact_sha256',{}).items():
        f=path.parent/name
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest()!=h:
            raise ValueError('Incomplete or modified temporal fixture; restore the previous verified checkpoint')
    manifest=deepcopy(old); pending=[]; seen=set(); latest=old['last_written_ns']
    for m in sorted(messages,key=lambda x:(apple_ns(x['written_at']),x['source_id'])):
        if m['source_id'] in seen:raise ValueError('Duplicate source identity in append')
        seen.add(m['source_id'])
        roster=(m.get('structured') or {}).get('participants')
        if (m['channel']!='imessage' or type(m.get('from_owner')) is not bool or
            m['from_owner']!=(m['sender']=='Me') or not isinstance(roster,list) or
            any(not isinstance(x,str) or not x.strip() or x!=x.strip() for x in roster) or
            len(roster)<2 or len(set(roster))!=len(roster) or 'Me' not in roster or m['sender'] not in roster):
            raise ValueError('Temporal fixture requires exact source speakers and explicit valid rosters')
        fp=_fingerprint(m); prior=old['messages'].get(m['source_id'])
        if prior:
            if prior['source_sha256']!=fp:raise ValueError('Conflicting source replay')
            continue
        ns=apple_ns(m['written_at'])
        if ns<latest:raise ValueError('Cannot insert past source into a later checkpoint')
        latest=ns; thread=manifest['threads'].get(m['thread'])
        previous=thread['participants'] if thread else []
        if previous and (roster[:len(previous)]!=previous or len(roster)<len(previous)):
            raise ValueError('Membership removal, reorder or stale source roster is unsupported')
        added=[x for x in roster if x not in previous] if previous else []
        if added and len(previous)<3:raise ValueError('Direct-to-group conversion requires a different native model')
        label=(m.get('structured') or {}).get('chat_name','')
        if thread and label!=thread['chat_name']:raise ValueError('Group renaming is not modeled')
        if not thread:
            thread={'participants':list(roster),'chat_name':label,'history':[]};manifest['threads'][m['thread']]=thread
        thread['participants']=list(roster)
        if not previous or added:
            thread['history'].append({'at':m['written_at'],'boundary_source_id':m['source_id'],
                'participants':list(roster),'added':added,'actor':None})
        pending.append((m,fp,added))
    for name in contacts:
        if not isinstance(name,str) or not name.strip():raise ValueError('Invalid source contact')
        if name!='Me':manifest['contacts'][name]=identity(name)
    for m,_,_ in pending:
        for name in m['structured']['participants']:
            if name!='Me':manifest['contacts'][name]=identity(name)
    if not pending and manifest==old:
        return _receipt(path,messages,0,0)
    path.parent.mkdir(parents=True,exist_ok=True)
    owner=identity('Me'); notices=0
    with sqlite3.connect(':memory:') as conn:
        if path.exists():
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source:source.backup(conn)
        else:conn.executescript(SCHEMA)
        def handle(name):
            c=identity(name)
            conn.execute('INSERT OR IGNORE INTO handle(id,country,service,uncanonicalized_id,person_centric_id) VALUES(?,NULL,?,?,?)',(c['handle'],'iMessage',c['handle'],c['uid']))
            return conn.execute('SELECT ROWID FROM handle WHERE id=?',(c['handle'],)).fetchone()[0]
        for m,fp,added in pending:
            thread=manifest['threads'][m['thread']]; roster=m['structured']['participants'];group=len(roster)>2
            identifier=('chat'+uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-thread:'+m['thread']).hex) if group else identity(next(x for x in roster if x!='Me'))['handle']
            if 'rowid' not in thread:
                thread['rowid']=conn.execute('INSERT INTO chat(guid,style,state,account_id,chat_identifier,service_name,account_login,last_addressed_handle,display_name,group_id,room_name) VALUES(?,?,3,?,?,?,?,?,?,?,?)',
                    ('iMessage;'+('+' if group else '-')+';'+identifier,43 if group else 45,'iMessage:'+owner['handle'],identifier,'iMessage',owner['handle'],owner['handle'],thread['chat_name'] or None,identifier if group else None,identifier if group else None)).lastrowid
            cid=thread['rowid'];members=[handle(x) for x in roster if x!='Me']
            for hid in members:conn.execute('INSERT OR IGNORE INTO chat_handle_join VALUES(?,?)',(cid,hid))
            ns=apple_ns(m['written_at'])
            for name in added:
                guid=_join_guid(m['thread'], m['source_id'], name)
                rid=conn.execute('INSERT INTO message(guid,text,handle_id,service,account,account_guid,date,is_from_me,is_sent,item_type,other_handle,group_action_type,cache_roomnames,is_finished,is_prepared) VALUES(?,NULL,NULL,?,?,?,?,0,0,1,?,0,?,1,1)',
                    (guid,'iMessage',owner['handle'],'iMessage:'+owner['handle'],ns,handle(name),identifier)).lastrowid
                conn.execute('INSERT INTO chat_message_join VALUES(?,?,?)',(cid,rid,ns));notices+=1
            guid=str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:'+m['source_id'])).upper()
            hid=(0 if group else members[0]) if m['from_owner'] else handle(m['sender'])
            rid=conn.execute('INSERT INTO message(guid,text,handle_id,service,account,account_guid,date,is_from_me,is_sent,destination_caller_id,cache_roomnames,is_finished,is_prepared,date_read,date_delivered) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,1,0,0)',
                (guid,m['text'],hid,'iMessage',owner['handle'],'iMessage:'+owner['handle'],ns,int(m['from_owner']),int(m['from_owner']),owner['handle'],identifier if group else None)).lastrowid
            conn.execute('INSERT INTO chat_message_join VALUES(?,?,?)',(cid,rid,ns))
            conn.execute('UPDATE chat SET properties=? WHERE ROWID=?',(plistlib.dumps({'EnableReadReceiptForChat':False,'lastSeenMessageGuid':guid}),cid))
            manifest['messages'][m['source_id']]={'guid':guid,'rowid':rid,'source_sha256':fp}
        manifest['last_written_ns']=latest
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        conn.commit()
        # Stage the whole bundle. The manifest is installed last and binds every
        # artifact: an interrupted multi-file install is rejected on reopen.
        with tempfile.TemporaryDirectory(prefix='.messages-stage-',dir=path.parent) as temp:
            stage=Path(temp)/path.name
            with sqlite3.connect(stage) as target:conn.backup(target)
            _contacts(stage,manifest['contacts'])
            files=[stage,stage.with_suffix('.contacts.abcddb'),stage.with_suffix('.contacts.vcf')]
            manifest['artifact_sha256']={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
            stage.with_suffix('.fixture.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
            for f in [*files,stage.with_suffix('.fixture.json')]:os.replace(f,path.parent/f.name)
    return _receipt(path,messages,len(pending),notices)


def _receipt(path,messages,added,notices):
    return {'version':CONDITION,'membership_condition':CONDITION,'added':added,'membership_notices_added':notices,
        'source_ids':[m['source_id'] for m in messages],'database':str(path),
        'metadata':str(path.with_suffix('.fixture.json')),'contacts':str(path.with_suffix('.contacts.vcf')),
        'contacts_database':str(path.with_suffix('.contacts.abcddb')),
        'readiness_scope':'Source transport only; no provider processing or membership extraction claim'}


def native_control_read_witness(source, prior_rowid):
    """Verified bodyless participant notices count as reads, never as dialogue.

    Only the explicit temporal fixture condition can justify an extra native read.
    The published artifact hashes, source boundary, date, target handle and actor
    must all match; this does not claim MemCal interprets membership notices.
    """
    source=Path(source);state=json.loads((source/'chat.fixture.json').read_text())
    if state.get('version')!=CONDITION:return {'protocol':'dialogue-only','control_rows':0}
    if set(state.get('artifact_sha256',{}))!={'chat.db','chat.contacts.abcddb','chat.contacts.vcf'}:
        raise ValueError('Native fixture artifact bindings missing')
    for name,digest in state.get('artifact_sha256',{}).items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise ValueError('Native fixture artifact changed')
    batch = {m['source_id']: m for m in json.loads((source / 'messages.json').read_text())}
    expected = Counter()
    for thread in state['threads'].values():
        for frame in thread['history'][1:]:
            boundary = batch.get(frame['boundary_source_id'])
            if boundary is None:
                continue
            chat = 'chat' + uuid.uuid5(uuid.NAMESPACE_URL, 'pacbench-source-thread:' + boundary['thread']).hex
            guid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'pacbench-source-message:' + boundary['source_id'])).upper()
            for name in frame['added']:
                notice = _join_guid(boundary['thread'], boundary['source_id'], name)
                expected[(notice, apple_ns(boundary['written_at']), identity(name)['handle'], chat, guid)] += 1

    # Several chats can gain members at the same instant. Bind each notice to
    # the following dialogue in its own chat, independently of manifest order.
    query = """
        SELECT m.ROWID, m.guid, m.date, h.id, c.chat_identifier,
               (SELECT b.guid FROM chat_message_join bj JOIN message b ON b.ROWID = bj.message_id
                WHERE bj.chat_id = j.chat_id AND b.ROWID > m.ROWID AND b.item_type = 0
                ORDER BY b.ROWID LIMIT 1),
               m.text, m.handle_id, m.item_type, m.group_action_type, m.is_from_me
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.other_handle
        LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = j.chat_id
        WHERE m.ROWID > ? AND m.item_type != 0 ORDER BY m.ROWID
    """
    with sqlite3.connect((source / 'chat.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        rows = conn.execute(query, (prior_rowid,)).fetchall()
    if (Counter(row[1:6] for row in rows) != expected
            or any(row[6:] != (None, None, 1, 0, 0) for row in rows)):
        raise ValueError('Unexpected native membership control rows; do not relax dialogue coverage')
    return {'protocol':CONDITION,'control_rows':len(rows),'control_rowids':[r[0] for r in rows],
        'scope':'Counted by native read cursor; bodyless, not archive dialogue or source facts'}
