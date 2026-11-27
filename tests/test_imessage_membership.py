"""Synthetic transport conformance, not native memory-quality evidence."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from pacbench.imessage_fixture import append_messages, apple_ns, identity, SCHEMA
from pacbench.imessage_fixture import CONDITION


def message(i,at,sender='Sam',roster=('Me','Sam','Robin'),thread='neighbors'):
    return {'source_id':i,'written_at':at,'arrived_at':at,'collected_at':at,
        'channel':'imessage','sender':sender,'from_owner':sender=='Me','thread':thread,
        'text':f'Exact source {i}: café.','structured':{'participants':list(roster),'chat_name':''}}


def append(path,rows):return append_messages(path,rows,membership_condition=CONDITION)


def roster(path):
    with sqlite3.connect(path) as db:
        return [r[0] for r in db.execute('SELECT h.id FROM chat_handle_join j JOIN handle h ON h.ROWID=j.handle_id ORDER BY h.ROWID')]


def rows():
    return [message('monday','2026-10-05T20:30:00-04:00'),
        message('thursday','2026-10-08T17:00:00-04:00',roster=('Me','Sam','Robin','Nora')),
        message('friday','2026-10-09T20:30:00-04:00',roster=('Me','Sam','Robin','Nora'))]


def test_checkpoint_rosters_notice_identity_and_verbatim_source(tmp_path):
    import shutil
    mon,thu,fri=rows();before=deepcopy([mon,thu,fri]);a=tmp_path/'monday/chat.db';b=tmp_path/'thursday/chat.db';c=tmp_path/'friday/chat.db'
    append(a,[mon]);old={p.name:p.read_bytes() for p in a.parent.iterdir()}
    shutil.copytree(a.parent,b.parent);receipt=append(b,[thu]);shutil.copytree(b.parent,c.parent);append(c,[fri])
    assert {p.name:p.read_bytes() for p in a.parent.iterdir()}==old
    assert roster(a)==[identity(x)['handle'] for x in ['Sam','Robin']]
    assert roster(b)==roster(c)==[identity(x)['handle'] for x in ['Sam','Robin','Nora']]
    assert receipt['added']==1 and receipt['membership_notices_added']==1
    chats=[]
    for p in [a,b,c]:
        with sqlite3.connect(p) as db:chats.append(db.execute('SELECT ROWID,guid,chat_identifier FROM chat').fetchall())
    assert chats[0]==chats[1]==chats[2]
    with sqlite3.connect(c) as db:
        source=db.execute('SELECT text,date FROM message WHERE item_type=0 ORDER BY ROWID').fetchall()
        assert source==[(m['text'],apple_ns(m['written_at'])) for m in before]
        notice=db.execute('SELECT m.ROWID,m.text,m.date,m.handle_id,m.item_type,m.group_action_type,h.id,m.is_from_me FROM message m JOIN handle h ON h.ROWID=m.other_handle WHERE m.item_type=1').fetchone()
        assert notice[1:] == (None,apple_ns(thu['written_at']),None,1,0,identity('Nora')['handle'],0)
        assert notice[0]<db.execute('SELECT ROWID FROM message WHERE text=?',(thu['text'],)).fetchone()[0]
        assert db.execute('SELECT COUNT(DISTINCT chat_id) FROM chat_message_join').fetchone()[0]==1
    # The actor is unspecified, never Nora (the native phone-number-change case).
    assert notice[3] is None
    assert [mon,thu,fri]==before


def test_full_append_and_replay_are_idempotent(tmp_path):
    p=tmp_path/'chat.db';append(p,rows());before={x.name:x.read_bytes() for x in tmp_path.iterdir()}
    receipt=append(p,rows());assert receipt['added']==receipt['membership_notices_added']==0
    assert {x.name:x.read_bytes() for x in tmp_path.iterdir()}==before


def test_legacy_default_and_unknown_conditions_stay_closed(tmp_path):
    with pytest.raises(ValueError,match='historical membership'):append_messages(tmp_path/'old.db',rows())
    with pytest.raises(ValueError,match='Unknown'):append_messages(tmp_path/'new.db',[],membership_condition='anything')
    p=tmp_path/'chat.db';append_messages(p,rows()[:1])
    with pytest.raises(ValueError,match='implicitly'):append(p,rows()[1:])


@pytest.mark.parametrize('change',['removal','reorder','past','changed-body','invalid-speaker','direct-growth','duplicate'])
def test_bad_replay_or_roster_cannot_change_existing_snapshot(tmp_path,change):
    p=tmp_path/'chat.db';mon,thu,fri=rows()
    if change=='direct-growth':mon['structured']['participants']=['Me','Sam']
    append(p,[mon]);bad=deepcopy(thu)
    if change=='removal':bad['structured']['participants']=['Me','Sam']
    if change=='reorder':bad['structured']['participants']=['Me','Robin','Sam','Nora']
    if change=='past':bad['written_at']='2026-10-04T18:00:00-04:00'
    if change=='changed-body':bad=deepcopy(mon);bad['text']='Edited'
    if change=='invalid-speaker':bad['sender']='Unknown'
    batch=[bad,bad] if change=='duplicate' else [bad]
    before={x.name:x.read_bytes() for x in tmp_path.iterdir()}
    with pytest.raises(ValueError):append(p,batch)
    assert {x.name:x.read_bytes() for x in tmp_path.iterdir()}==before


def test_interrupted_bundle_install_fails_closed(tmp_path,monkeypatch):
    import pacbench.imessage_fixture as module
    p=tmp_path/'chat.db';append(p,rows()[:1]);replace=module.os.replace;calls=[]
    def interrupted(src,dst):
        calls.append(str(dst))
        if len(calls)==2:raise OSError('synthetic interruption after database install')
        replace(src,dst)
    with monkeypatch.context() as m:
        m.setattr(module.os,'replace',interrupted)
        with pytest.raises(OSError,match='interruption'):append(p,rows()[1:2])
    with pytest.raises(ValueError,match='Incomplete or modified'):append(p,rows()[1:2])


def test_target_schema_digest_and_notice_fields_are_known_columns():
    # DDL only, no upstream source rows. Semantic mapping is separately pinned
    # to the independent reader's ParticipantAdded and PhoneNumberChanged tests.
    provenance=json.loads(Path('pacbench/source_schemas/apple_messages.provenance.json').read_text())
    assert hashlib.sha256(SCHEMA.encode()).hexdigest()==provenance['schema_sha256']
    with sqlite3.connect(':memory:') as db:
        db.executescript(SCHEMA)
        fields={r[1]:r for r in db.execute('PRAGMA table_info(message)')}
        assert {'item_type','group_action_type','handle_id','other_handle','date'}<=fields.keys()
        assert fields['handle_id'][3]==0  # Nullable: unspecified actor, no fabricated adder.


def test_no_future_append_after_checkpoint_and_exact_sender_join(tmp_path):
    mon,thu,fri=rows();first=deepcopy(thu);first['source_id']='before-nora';first['sender']='Me';first['from_owner']=True
    first['structured']['participants']=['Me','Sam','Robin']
    thu['written_at']='2026-10-08T17:01:00-04:00';thu['sender']='Nora'
    p=tmp_path/'chat.db';append(p,[mon,first,thu]);append(p,[fri])
    with sqlite3.connect(p) as db:
        before,notice,after=db.execute('SELECT text,item_type FROM message ORDER BY ROWID').fetchall()[1:4]
        assert before==(first['text'],0) and notice==(None,1) and after==(thu['text'],0)
    with pytest.raises(ValueError,match='past'):append(p,[message('late','2026-10-06T12:00:00-04:00')])


def test_native_read_counter_distinguishes_verified_notice_from_dialogue(tmp_path):
    from pacbench.imessage_fixture import native_control_read_witness
    mon,thu,_=rows();source=tmp_path/'source';source.mkdir();append(source/'chat.db',[mon]);append(source/'chat.db',[thu])
    (source/'messages.json').write_text(json.dumps([thu]))
    witness=native_control_read_witness(source,1)
    assert witness['control_rows']==1 and witness['protocol']==CONDITION
    with sqlite3.connect(source/'chat.db') as db:db.execute('UPDATE message SET text="Invented source" WHERE item_type=1')
    with pytest.raises(ValueError,match='artifact changed'):native_control_read_witness(source,1)


def test_read_witness_binds_interleaved_joins_to_their_source_chats(tmp_path):
    from pacbench.imessage_fixture import native_control_read_witness

    monday, thursday, _ = rows()
    first = {**monday, 'source_id': 'first', 'thread': 'first'}
    second = {**monday, 'source_id': 'second', 'thread': 'second'}
    path = tmp_path / 'chat.db'
    append(path, [first, second])
    joins = [
        {**thursday, 'source_id': 'a', 'thread': 'second'},
        {**thursday, 'source_id': 'b', 'thread': 'first'},
    ]
    append(path, joins)
    (tmp_path / 'messages.json').write_text(json.dumps(joins))
    assert native_control_read_witness(tmp_path, 2)['control_rowids'] == [3, 5]

    # A fixture producer could publish a consistent file digest while wiring the
    # two identical notices to the wrong chats.
    with sqlite3.connect(path) as db:
        db.execute('UPDATE chat_message_join SET chat_id=3-chat_id WHERE message_id IN (3,5)')
    sidecar = path.with_suffix('.fixture.json')
    manifest = json.loads(sidecar.read_text())
    manifest['artifact_sha256'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='membership control rows'):
        native_control_read_witness(tmp_path, 2)
