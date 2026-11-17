"""Opt-in additive group membership on checkpoint-local Messages snapshots.

The independent reader's pinned group_action.rs interprets (item_type=1,
group_action_type=0, other_handle) as ParticipantAdded unless the actor is that
same handle (a phone-number change). Source metadata does not name an adder:
the synthetic notice therefore has NULL handle_id, never a fabricated actor.
The notice shares the first message's boundary clock and precedes it by ROWID;
it asserts no earlier absolute join time. This is reader/schema conformance,
not evidence that Apple's application created the database or that a provider
extracts membership from notices. Bodyless notices are not dialogue.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import uuid

from .imessage_fixture import SCHEMA, apple_ns, identity

CONDITION = 'apple-messages-additive-v1'
READER_COMMIT = '0d444ab3220f5940d4632c05da4628009c785a02'
READER_GROUP_ACTION_SHA256 = '3c88d0b18b25102545d34f6cd0d8322d37993273d1cc6eb816578e996574faee'


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
                guid=str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-join:'+m['thread']+':'+m['source_id']+':'+name)).upper()
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
    ids={m['source_id'] for m in json.loads((source/'messages.json').read_text())}
    expected=[]
    for thread in state['threads'].values():
        for frame in thread['history'][1:]:
            if frame['boundary_source_id'] in ids:
                expected.extend((apple_ns(frame['at']),identity(name)['handle']) for name in frame['added'])
    with sqlite3.connect(f'file:{source/"chat.db"}?mode=ro',uri=True) as conn:
        rows=conn.execute('SELECT m.ROWID,m.date,h.id,m.text,m.handle_id,m.item_type,m.group_action_type FROM message m LEFT JOIN handle h ON h.ROWID=m.other_handle WHERE m.ROWID>? AND m.item_type!=0 ORDER BY m.ROWID',(prior_rowid,)).fetchall()
    if ([(r[1],r[2]) for r in rows]!=expected or any(r[3] is not None or r[4] is not None or r[5:]!=(1,0) for r in rows)):
        raise ValueError('Unexpected native membership control rows; do not relax dialogue coverage')
    return {'protocol':CONDITION,'control_rows':len(rows),'control_rowids':[r[0] for r in rows],
        'scope':'Counted by native read cursor; bodyless, not archive dialogue or source facts'}
