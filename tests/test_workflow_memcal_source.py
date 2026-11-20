"""Small synthetic source and SQLite controls for the controller's read-only joins."""
from copy import deepcopy
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

import pytest

from pacbench.workflow_memcal_source import MemCalSourceWitness, digest, frozen_messages_digest
from pacbench.workflow_spec import nightly_documents


@pytest.fixture
def staged(tmp_path, monkeypatch):
    from pacbench.provider_versions import memcal_source
    checkout = memcal_source(Path(__file__).resolve().parents[1])
    if not checkout.is_dir():
        pytest.skip('Pinned stock MemCal email shaping implementation is unavailable')
    monkeypatch.syspath_prepend(str(checkout))
    from memcal.sources import proton
    messages = []
    obs = {'imessage': [], 'email': []}
    bindings = {'account': 'test', 'records': {}, 'threads': {}, 'people': {}}
    for i, ch in enumerate(('imessage', 'email'), start=1):
        stamp = f'2026-10-0{4+i}T18:00:00-04:00'
        body = 'Dinner at six.' if i == 1 else 'Dinner moved.\n\n> Older conversation.'
        people = {'Me': 'me@example.test', 'Sam': 'sam@example.test'}
        meta = {'participants': list(people)}
        if ch == 'email':
            meta['email_subject'] = 'Dinner'
        sid, external = f's{i}', f'native-{i}@example.test'
        messages.append(dict(source_id=sid, sender='Sam', from_owner=False, channel=ch, thread='dinner',
                             text=body, structured=meta, written_at=stamp, arrived_at=stamp, collected_at=stamp))
        bindings['records'][sid] = {'external_id': external, 'archive_thread': 'native-chat',
                                    'archive_handle': people['Sam']}
        bindings['threads'][ch] = {'dinner': 'native-chat'}
        bindings['people'][ch] = people
        r = dict(external_id=external, sender=people['Sam'], from_owner=False, thread='native-chat',
                 written_at=stamp, arrived_at=stamp)
        if ch == 'imessage':
            r.update(text=body, participants_at_message=list(people.values()))
        else:
            email = EmailMessage(policy=SMTP)
            email['Subject'], email['From'], email['Message-ID'] = 'Dinner', 'Sam <sam@example.test>', f'<{external}>'
            email['Date'] = 'Tue, 06 Oct 2026 18:00:00 -0400'
            email.set_content(body)
            r.update(rfc822=email.as_bytes().decode(), folder='INBOX', folder_role='INBOX', uid=1, uidvalidity='1')
        if ch == 'email':  # Headerless mail is a standalone native thread.
            r['thread'] = bindings['records'][sid]['archive_thread'] = external
        obs[ch].append(r)
    docs = nightly_documents(messages, 'America/New_York', '21:00')
    dates = [d.cutoff for d in docs]
    store = tmp_path / 'memcal.db'
    db = sqlite3.connect(store)
    db.executescript("""
        CREATE TABLE archive(channel,external_id,ts,thread,handle,from_me,text,meta,collection_id);
        CREATE TABLE meta(key PRIMARY KEY,value);
        CREATE TABLE collections(id,finished_at,error);
        CREATE TABLE runs(id,finished_at,mode,error);
        CREATE TABLE collection_sources(collection_id,channel,archived,status,error);
    """)
    bindings['frozen_messages_sha256'] = frozen_messages_digest(messages)
    path = tmp_path / 'separate.json'
    path.write_text(json.dumps(bindings))
    state = [None]
    snapshots = {}
    for n, at in enumerate([None, *dates]):
        mail = obs['email'] if n == 2 else []
        channels = {'imessage': {'as_of': at, 'records': obs['imessage'] if n else [], 'cursor': {'last_rowid': 1 if n else 0}},
                    'email': {'as_of': at, 'records': mail, 'folders': {k: k for k in ('INBOX','Sent')},
                             'cursor': {k: {'uidvalidity': 1, 'highest_uid': int(bool(mail) and k == 'INBOX')}
                                        for k in ('INBOX','Sent')}}}
        snapshots[at] = {'account': 'test', 'as_of': at, 'cutoff': at, 'service_at': at, 'channels': channels}

    def release(at):
        state[0] = at
        return deepcopy(snapshots[at])

    def observe(at):
        return deepcopy(snapshots[state[0]])

    def fresh():
        return MemCalSourceWitness(db, SimpleNamespace(account='test', memcal_home=str(tmp_path), imessage_backend='chatdb'),
                                   messages, docs, bindings_path=path, bindings_sha256=digest(path.read_bytes()),
                                   release=release, observe=observe)

    def insert(n):
        m = messages[n-1]
        ch = m['channel']
        data = obs[ch][0]
        text, meta = m['text'], {}
        if ch == 'email':
            raw = data['rfc822']
            from email import message_from_bytes
            text, meta = proton._shape_body(proton.extract_text(message_from_bytes(raw.encode())), 'Dinner')
            meta.update(folder='INBOX', uid=1, uidvalidity='1', subject='Dinner', message_id=data['external_id'],
                        in_reply_to='', references=[])
            db.execute("INSERT INTO meta VALUES ('watermark.proton.INBOX','1:1')")
        else:
            db.execute("INSERT INTO meta VALUES ('imessage.rowid','1')")
        db.execute('INSERT INTO archive VALUES (?,?,?,?,?,?,?,?,?)',
                   (ch, data['external_id'], m['written_at'], data['thread'], data['sender'], 0,text.strip(),json.dumps(meta),n))
        db.execute('INSERT INTO collections VALUES (?,?,NULL)', (n, dates[n-1]))
        readers = {}
        for channel in obs:
            count = int(ch == channel)
            db.execute("INSERT INTO collection_sources VALUES (?,?,?,'complete',NULL)", (n,channel,count))
            readers[channel] = {'archived':count, 'more':False,'error':False}
        db.commit()
        return {'collection_id': n, 'readers': readers}
    yield SimpleNamespace(dates=dates, docs=docs, snapshots=snapshots, fresh=fresh, insert=insert, db=db)
    db.close()


def test_separate_site_ledger_archive_cursor_and_read_only_restore(staged):
    w = staged.fresh()
    assert w.source_inventory() == []
    effects = {}
    for n, at in enumerate(staged.dates, start=1):
        effects['source-clock', at] = w.collection_clock(at)
        raw = staged.insert(n)
        accepted = effects['sync', at] = w.connector_acceptance(raw, at)
        with staged.db:
            staged.db.execute("INSERT INTO runs VALUES (?,?,'nightly', NULL)", (n, at))
        dream = effects['dream', at] = {'run_id': n, 'finished_at': at, 'pending_spool': 0}
        readback = w.connector_readback(accepted, processing=dream)
        assert readback['new_source_ids'] == [f's{n}']
        assert w.source_inventory() == [d.filename for d in staged.docs[:n]]
        if n == 2:
            assert accepted['rows']['s2']['source_sha256'] != accepted['rows']['s2']['archive_sha256']
            assert accepted['native_cursors']['email']['watermark.proton.INBOX'] == '1:1'
    progress = SimpleNamespace(last_cutoff=lambda: at, get_effect=lambda p, k: effects.get((p, k)))
    other = staged.fresh()
    other.release = lambda _: pytest.fail('resume replayed upstream release')
    proof = other.verify_source_resume(progress)
    assert proof['source_ids'] == ['s1','s2'] and proof['cutoff'] == at
    assert other.source_inventory() == w.source_inventory()
    with staged.db:
        staged.db.execute("UPDATE meta SET value='9' WHERE key='imessage.rowid'")
    with pytest.raises(ValueError, match='cursor'):
        other.verify_source_resume(progress)


def test_restore_accepted_night_before_dream_without_recollection(staged):
    w = staged.fresh()
    at = staged.dates[0]
    clock = w.collection_clock(at)
    accepted = w.connector_acceptance(staged.insert(1), at)
    effects = {('source-clock',at):clock, ('sync',at):accepted}
    restored = staged.fresh()
    restored.release = lambda _: pytest.fail('a release was replayed')
    proof = restored.verify_source_resume(SimpleNamespace(last_cutoff=lambda:None, get_effect=lambda p,k:effects.get((p,k))))
    assert proof['cutoff'] is None and proof['upstream_cutoff'] == at and proof['source_ids'] == ['s1']
    ready = restored.connector_readback(accepted, processing={'run_id':1,'finished_at':at,'pending_spool':0})
    assert ready['new_source_ids'] == ['s1']


@pytest.mark.parametrize('fault', ['future','roster','body','cursor','thread','owner','missing-mail-status'])
def test_input_or_archive_fault_prevents_nightly_readiness(staged, fault):
    w = staged.fresh()
    at, later = staged.dates
    snap = staged.snapshots[at]['channels']
    if fault == 'future':
        snap['email']['records'] = staged.snapshots[later]['channels']['email']['records']
    if fault == 'roster':
        snap['imessage']['records'][0]['participants_at_message'] = ['sam@example.test','other@example.test']
    if fault in ('future','roster'):
        with pytest.raises(ValueError):
            w.collection_clock(at)
        return
    w.collection_clock(at)
    raw = staged.insert(1)
    if fault == 'body':
        staged.db.execute("UPDATE archive SET text='incorrect'")
    if fault == 'cursor':
        staged.db.execute("UPDATE meta SET value='99'")
    if fault == 'owner':
        staged.db.execute('UPDATE archive SET from_me=1')
    if fault == 'thread':
        staged.db.execute("UPDATE archive SET thread='later thread'")
    if fault == 'missing-mail-status':
        staged.db.execute("DELETE FROM collection_sources WHERE channel='email'")
    with pytest.raises(ValueError):
        w.connector_acceptance(raw, at)


def test_same_connection_and_external_commits_invalidate_archive_scan(staged):
    import sqlite3
    w = staged.fresh()
    at = staged.dates[0]
    w.collection_clock(at)
    accepted = w.connector_acceptance(staged.insert(1), at)
    w.connector_readback(accepted, processing={'run_id':1, 'finished_at':at, 'pending_spool':0})
    statements = []
    staged.db.set_trace_callback(statements.append)
    try:
        w.source_inventory()
        w.source_inventory()
        assert not any('FROM archive WHERE' in stmt for stmt in statements)
        with staged.db:
            staged.db.execute('UPDATE archive SET text=text')
        w.source_inventory()
        assert sum('FROM archive WHERE' in stmt for stmt in statements) == 1
        with sqlite3.connect(w.store) as other:
            other.execute("UPDATE archive SET text='modified after readback'")
        with pytest.raises(ValueError, match='archive differs'):
            w.source_inventory()
    finally:
        staged.db.set_trace_callback(None)


def test_live_mutation_of_same_site_observation_object_is_detected(staged):
    w = staged.fresh()
    at = staged.dates[0]
    w.collection_clock(at)
    accepted = w.connector_acceptance(staged.insert(1), at)
    w.connector_readback(accepted, processing={'run_id':1, 'finished_at':at, 'pending_spool':0})
    staged.snapshots[at]['channels']['imessage']['records'][0]['text'] = 'modified outside the release'
    with pytest.raises(ValueError, match='Live site source inventory changed'):
        w.source_inventory()
